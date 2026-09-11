"""Tests for DeviceSink's discipline around PortAudio.

Every call into the stream runs on a worker thread, and cancelling the
coroutine awaiting one does not stop its thread. On a barge-in the write
carried on, `stop` wrote its fade on a second thread and `close` freed the
stream on a third -- a heap corruption on 2026-09-11. The fake stream below
fails the test if two calls are ever inside it at once.
"""

from __future__ import annotations

import asyncio
import threading
import time

from amanda.audio.sink import DeviceSink

RATE = 16_000
BLOCK_BYTES = RATE * 20 // 1000 * 2


class OneAtATimeStream:
    """Blocks in `write` like a device would, and records any overlap."""

    def __init__(self) -> None:
        self._inside = threading.Lock()
        self.overlapped = False
        self.closed = False
        self.written = 0
        self.calls: list[str] = []

    def _enter(self, name: str) -> None:
        if not self._inside.acquire(blocking=False):
            self.overlapped = True
            self._inside.acquire()
        self.calls.append(name)
        if self.closed:
            self.overlapped = True  # used after being freed

    def write(self, pcm: bytes) -> bool:
        self._enter("write")
        try:
            time.sleep(len(pcm) / 2 / RATE)
            self.written += len(pcm)
            return False
        finally:
            self._inside.release()

    def stop(self) -> None:
        self._enter("stop")
        self._inside.release()

    def start(self) -> None:
        self._enter("start")
        self._inside.release()

    def close(self) -> None:
        self._enter("close")
        self.closed = True
        self._inside.release()


def sink_on(stream: OneAtATimeStream) -> DeviceSink:
    sink = DeviceSink()
    sink._stream = stream
    sink._sample_rate = RATE
    return sink


def phrase(seconds: float) -> bytes:
    return b"\x10\x00" * round(RATE * seconds)


async def test_a_barge_in_never_has_two_threads_in_the_stream():
    """The crash: write in flight, then stop and close at the same moment."""
    stream = OneAtATimeStream()
    sink = sink_on(stream)

    writing = asyncio.create_task(sink.write(phrase(2.0)))
    await asyncio.sleep(0.1)
    writing.cancel()  # the coroutine goes; its thread does not
    await asyncio.gather(sink.stop(80), sink.close(), return_exceptions=True)
    await asyncio.sleep(0.1)

    assert not stream.overlapped
    assert stream.closed
    assert stream.calls[-1] == "close"


async def test_a_stop_lands_within_a_block_not_after_the_phrase():
    stream = OneAtATimeStream()
    sink = sink_on(stream)

    writing = asyncio.create_task(sink.write(phrase(2.0)))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    await sink.stop(80)
    await writing

    assert time.monotonic() - started < 0.3
    assert stream.written < len(phrase(0.5)), "the rest of the phrase was not played"


async def test_close_after_stop_frees_the_stream_once():
    stream = OneAtATimeStream()
    sink = sink_on(stream)

    await sink.write(phrase(0.1))
    await sink.stop(20)
    await sink.close()
    await sink.close()

    assert stream.calls.count("close") == 1
    assert not stream.overlapped


async def test_stop_after_close_does_not_touch_the_freed_stream():
    stream = OneAtATimeStream()
    sink = sink_on(stream)

    await sink.close()
    await sink.stop(80)

    assert stream.calls == ["close"]

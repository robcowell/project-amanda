"""Tests for the delayed monitor: the cable drives the face, the monitor is heard.

Each test is one way this goes wrong without anyone noticing at first: sync that
holds for a sentence and then drifts, a speaker device that stalls the face, the
end of every sentence clipped from what is heard, a barge-in that lands late.
"""

from __future__ import annotations

import asyncio

import pytest

from amanda.audio.sink import MonitorSink, NullSink

RATE = 24_000
CHUNK = b"\1\0" * 240  # 10ms


class TimedSink:
    """Records when each chunk arrived. Optionally slow, optionally broken."""

    underruns = 0

    def __init__(self, write_seconds: float = 0.0, fail: bool = False) -> None:
        self.write_seconds = write_seconds
        self.fail = fail
        self.times: list[float] = []
        self.chunks: list[bytes] = []
        self.stopped = False
        self.closed = False

    async def open(self, sample_rate: int) -> None:
        return None

    async def write(self, pcm: bytes) -> None:
        if self.fail:
            raise OSError("device unplugged")
        if self.write_seconds:
            await asyncio.sleep(self.write_seconds)
        self.times.append(asyncio.get_running_loop().time())
        self.chunks.append(pcm)

    async def drain(self) -> None:
        return None

    async def stop(self, fade_ms: int = 80) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.closed = True


async def test_the_monitor_hears_everything_the_cable_does():
    primary, monitor = NullSink(), NullSink()
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=20)
    await sink.open(RATE)
    for index in range(5):
        await sink.write(bytes([index, 0]) * 240)
    await sink.drain()
    await sink.close()

    assert monitor.pcm == primary.pcm
    assert primary.closed and monitor.closed


async def test_the_monitor_lags_the_cable_by_the_delay():
    primary, monitor = TimedSink(), TimedSink()
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=100)
    await sink.open(RATE)
    await sink.write(CHUNK)
    await sink.drain()

    lag = monitor.times[0] - primary.times[0]
    assert 0.09 <= lag < 0.3


async def test_the_delay_survives_a_pause():
    """The whole reason the delay is anchored per chunk rather than at open.

    Silence prepended once would be caught up by the first pause, after which
    the monitor plays in step with the cable and the sync is gone.
    """
    primary, monitor = TimedSink(), TimedSink()
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=100)
    await sink.open(RATE)

    await sink.write(CHUNK)
    await sink.drain()
    await asyncio.sleep(0.25)  # longer than the delay: the monitor is idle
    await sink.write(CHUNK)
    await sink.drain()

    assert monitor.times[1] - primary.times[1] >= 0.09


async def test_a_slow_monitor_never_holds_up_the_cable():
    primary, monitor = TimedSink(), TimedSink(write_seconds=0.2)
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=0)
    await sink.open(RATE)

    started = asyncio.get_running_loop().time()
    for _ in range(3):
        await sink.write(CHUNK)
    assert asyncio.get_running_loop().time() - started < 0.1, "the cable waited on the monitor"
    await sink.drain()
    assert len(monitor.chunks) == 3


async def test_drain_waits_for_the_delayed_tail():
    """Otherwise close() clips the last delay_ms of every sentence."""
    primary, monitor = TimedSink(), TimedSink()
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=80)
    await sink.open(RATE)
    await sink.write(CHUNK)
    await sink.drain()

    assert monitor.chunks == [CHUNK]


async def test_stopping_drops_what_the_monitor_had_not_played():
    """A barge-in should be heard to land promptly, not delay_ms later."""
    primary, monitor = TimedSink(), TimedSink()
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=500)
    await sink.open(RATE)
    await sink.write(CHUNK)
    await sink.stop(fade_ms=20)
    await asyncio.sleep(0.6)

    assert primary.chunks == [CHUNK]
    assert monitor.chunks == [], "the backlog should have been discarded"
    assert primary.stopped and monitor.stopped

    await sink.write(CHUNK)
    assert primary.chunks == [CHUNK], "nothing is written after a stop"


async def test_a_failing_monitor_does_not_take_the_cable_with_it():
    primary, monitor = TimedSink(), TimedSink(fail=True)
    sink = MonitorSink(primary=primary, monitor=monitor, delay_ms=0)
    await sink.open(RATE)
    for _ in range(3):
        await sink.write(CHUNK)
    await asyncio.wait_for(sink.drain(), timeout=1.0)

    assert len(primary.chunks) == 3


def test_a_negative_delay_is_refused():
    with pytest.raises(ValueError):
        MonitorSink(primary=NullSink(), monitor=NullSink(), delay_ms=-1)

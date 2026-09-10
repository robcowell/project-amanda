"""Microphone capture: one stream, many consumers.

The build plan needs the microphone live *while the avatar speaks*, because
that is what barge-in means (§13). That single requirement decides the shape of
this module.

The obvious design — whoever needs the microphone opens it, uses it, and closes
it — does not survive that requirement. It is what J.A.R.V.I.S. does, and its
wake listener has to destroy its recorder and hand the device over before speech
capture can start, with eight retries because the device does not always release
cleanly. See `docs/jarvis-overlap.md`.

So the stream is opened once and every consumer subscribes to it. Endpointing,
barge-in detection and (later) a wake word all see the same frames at the same
time, and none of them owns the device.

Frames are signed 16-bit little-endian mono PCM, as everywhere else in this
package. Input runs at 16 kHz because that is what speech recognition wants;
it has nothing to do with the output rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from amanda.audio.tts import CHANNELS, SAMPLE_WIDTH

log = logging.getLogger(__name__)

#: What speech recognition expects. Unrelated to the TTS output rate.
CAPTURE_RATE = 16_000

#: How much audio each frame carries. Short enough that barge-in lands
#: promptly, long enough that an RMS measurement over it is not just noise.
FRAME_MS = 32

#: Frames a subscriber may fall behind before the oldest are dropped. A slow
#: consumer should lose audio rather than stall the capture callback, which
#: runs on PortAudio's thread and must never block.
QUEUE_FRAMES = 64


class MicrophoneError(RuntimeError):
    """The input device could not be opened."""


def frame_bytes(sample_rate: int = CAPTURE_RATE, frame_ms: int = FRAME_MS) -> int:
    return round(sample_rate * frame_ms / 1000) * SAMPLE_WIDTH * CHANNELS


def list_input_devices() -> list[tuple[int, str]]:
    """Every device that can capture audio, as (index, name)."""
    import sounddevice

    return [
        (index, device["name"])
        for index, device in enumerate(sounddevice.query_devices())
        if device["max_input_channels"] > 0
    ]


def resolve_device(name: str | int | None) -> int | None:
    """Find an input device by index or by a fragment of its name."""
    if name is None or isinstance(name, int):
        return name

    devices = list_input_devices()
    needle = name.casefold()
    for index, device_name in devices:
        if needle in device_name.casefold():
            return index

    available = ", ".join(repr(device_name) for _, device_name in devices)
    raise MicrophoneError(f"no input device matching {name!r}. Available: {available}")


@dataclass
class Microphone:
    """A capture stream that fans out to every subscriber.

        async with Microphone() as mic:
            async with mic.listen() as frames:
                async for frame in frames:
                    ...

    Several `listen()` blocks can be open at once and each sees every frame.
    """

    device: str | int | None = None
    sample_rate: int = CAPTURE_RATE
    frame_ms: int = FRAME_MS

    #: An iterable of frames to use instead of a real device. Lets the whole
    #: input path -- endpointing, barge-in, the state machine -- be tested
    #: without a microphone, which is also the only way it runs in CI.
    source: Iterable[bytes] | None = None

    _stream: Any = field(default=None, init=False, repr=False)
    _subscribers: set[asyncio.Queue[bytes | None]] = field(default_factory=set, init=False)
    _feeder: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)

    #: Frames dropped because a subscriber was not keeping up.
    dropped: int = field(default=0, init=False)

    @property
    def frame_size(self) -> int:
        return frame_bytes(self.sample_rate, self.frame_ms)

    @property
    def running(self) -> bool:
        return self._running

    # ----------------------------------------------------------------- #

    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True

        if self.source is not None:
            self._feeder = asyncio.create_task(self._feed(), name="mic-source")
            return

        import sounddevice

        try:
            self._stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype="int16",
                device=resolve_device(self.device),
                blocksize=round(self.sample_rate * self.frame_ms / 1000),
                callback=self._on_frames,
            )
            self._stream.start()
        except Exception as exc:
            self._running = False
            raise MicrophoneError(f"could not open input device {self.device!r}: {exc}") from exc

    async def stop(self) -> None:
        self._running = False

        if self._feeder is not None:
            self._feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feeder
            self._feeder = None

        if self._stream is not None:
            stream, self._stream = self._stream, None
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()

        for queue in list(self._subscribers):
            self._close(queue)
        self._subscribers.clear()

    async def __aenter__(self) -> Microphone:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # ----------------------------------------------------------------- #

    @contextlib.asynccontextmanager
    async def listen(self) -> AsyncIterator[AsyncIterator[bytes]]:
        """Subscribe for as long as the block runs.

        A context manager rather than a bare iterator so a consumer that stops
        early is unsubscribed rather than left accumulating frames nobody reads.
        """
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_FRAMES)
        self._subscribers.add(queue)
        try:
            yield self._drain(queue)
        finally:
            self._subscribers.discard(queue)

    async def _drain(self, queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
        while True:
            frame = await queue.get()
            if frame is None:
                return
            yield frame

    # ----------------------------------------------------------------- #

    def _on_frames(self, indata, frames: int, time_info: Any, status: Any) -> None:
        """PortAudio callback. Runs on its own thread and must not block.

        Hence `call_soon_threadsafe` rather than touching the queues directly:
        asyncio queues are not thread-safe, and blocking here drops audio at
        the driver level rather than at ours.
        """
        if status:
            log.debug("input stream status: %s", status)
        if self._loop is None or not self._running:
            return
        self._loop.call_soon_threadsafe(self._publish, bytes(indata))

    def _publish(self, frame: bytes) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Drop the oldest rather than the newest: for voice detection
                # the most recent audio is the audio that matters.
                self.dropped += 1
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(frame)

    @staticmethod
    def _close(queue: asyncio.Queue[bytes | None]) -> None:
        """Signal end of stream, making room if the queue is full.

        The sentinel matters more than any frame it displaces: a dropped frame
        costs 32ms of audio, a dropped sentinel leaves the consumer awaiting a
        stream that will never produce anything again.
        """
        while True:
            try:
                queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()

    async def _feed(self) -> None:
        """Replay a scripted source in real time, for tests and offline runs."""
        assert self.source is not None
        seconds = self.frame_ms / 1000
        for frame in self.source:
            if not self._running:
                return
            self._publish(frame)
            await asyncio.sleep(seconds)

        # The source ran out. Tell subscribers rather than hanging.
        for queue in list(self._subscribers):
            self._close(queue)

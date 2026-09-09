"""Audio output.

Separate from synthesis on purpose. On the target machine the avatar's voice
does not go to the speakers at all -- it goes into a virtual audio cable that
Unreal reads as though it were a microphone, which is how MetaHuman's real-time
audio solver gets fed. Choosing *which* device receives the audio is therefore a
first-class concern rather than a detail, and it is why `DeviceSink` takes a
device name.

On the development machine the same sink plays to the speakers, so the pipeline
can be heard without Unreal.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from amanda.audio.tts import CHANNELS, SAMPLE_WIDTH, pcm_duration_ms

log = logging.getLogger(__name__)

#: Kept small so an interruption is heard promptly. Latency through a sink is
#: buffer plus fade, and the buffer is the part the user cannot hear coming.
DEFAULT_BLOCK_MS = 20


class AudioSinkError(RuntimeError):
    """The output device could not be opened or written to."""


@runtime_checkable
class AudioSink(Protocol):
    """Where synthesised audio goes."""

    async def open(self, sample_rate: int) -> None: ...

    async def write(self, pcm: bytes) -> None: ...

    async def drain(self) -> None:
        """Wait for buffered audio to finish playing."""
        ...

    async def stop(self, fade_ms: int = 80) -> None:
        """Stop now, ramping to silence rather than cutting to zero."""
        ...

    async def close(self) -> None: ...


def _ramp_to_silence(tail: bytes, fade_ms: int, sample_rate: int) -> bytes:
    """A fade that starts at the amplitude the audio is currently at.

    Ramping from zero would itself be a discontinuity -- the click this exists
    to avoid. The last sample written is the starting point.
    """
    frames = max(1, round(fade_ms * sample_rate / 1000))
    last = 0
    if tail:
        samples = array.array("h")
        samples.frombytes(tail[-SAMPLE_WIDTH:])
        last = samples[0]

    ramp = array.array("h", bytes(frames * SAMPLE_WIDTH))
    for index in range(frames):
        ramp[index] = int(last * (1.0 - (index + 1) / frames))
    return ramp.tobytes()


# --------------------------------------------------------------------------- #
# Null
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NullSink:
    """Records what it was given and plays nothing.

    Used by the tests, and by any run that wants the pipeline's timing and
    protocol behaviour without a sound card.

    `realtime` matters more than it looks. A real device sink blocks on buffer
    space, so writing is paced by playback and a cancellation lands partway
    through. Writing to memory does not, so without pacing an utterance
    "finishes" the instant it is synthesised and there is nothing left to
    interrupt -- which quietly makes any barge-in test or demo meaningless.
    """

    realtime: bool = False
    sample_rate: int = 0
    written: list[bytes] = field(default_factory=list)
    stopped: bool = False
    faded_ms: int = 0
    opened: bool = False
    closed: bool = False

    @property
    def pcm(self) -> bytes:
        return b"".join(self.written)

    @property
    def played_ms(self) -> int:
        return pcm_duration_ms(self.pcm, self.sample_rate) if self.sample_rate else 0

    async def open(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.opened = True

    async def write(self, pcm: bytes) -> None:
        if self.stopped:
            return
        self.written.append(pcm)
        if self.realtime and self.sample_rate:
            await asyncio.sleep(pcm_duration_ms(pcm, self.sample_rate) / 1000)

    async def drain(self) -> None:
        return None

    async def stop(self, fade_ms: int = 80) -> None:
        if not self.stopped and self.written:
            self.written.append(_ramp_to_silence(self.pcm, fade_ms, self.sample_rate))
        self.stopped = True
        self.faded_ms = fade_ms

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Device
# --------------------------------------------------------------------------- #


def list_output_devices() -> list[tuple[int, str]]:
    """Every device that can play audio, as (index, name).

    On Windows this is how you find "CABLE Input" -- the virtual cable's
    playback half, which Unreal then reads from "CABLE Output".
    """
    import sounddevice

    return [
        (index, device["name"])
        for index, device in enumerate(sounddevice.query_devices())
        if device["max_output_channels"] > 0
    ]


def resolve_device(name: str | int | None) -> int | None:
    """Find a device by index, or by a case-insensitive substring of its name.

    Substring matching is deliberate: device names carry driver decoration that
    varies between machines, and asking someone to type
    "CABLE Input (VB-Audio Virtual Cable)" exactly is a good way to generate a
    support question. "cable input" is enough.
    """
    if name is None or isinstance(name, int):
        return name

    devices = list_output_devices()
    needle = name.casefold()
    for index, device_name in devices:
        if needle in device_name.casefold():
            return index

    available = ", ".join(repr(device_name) for _, device_name in devices)
    raise AudioSinkError(f"no output device matching {name!r}. Available: {available}")


@dataclass
class DeviceSink:
    """Plays PCM to a sound device via PortAudio.

    Writes happen on a worker thread, because PortAudio's write blocks until
    there is buffer space and blocking the event loop would stall the Claude
    stream and the avatar bridge along with it.
    """

    device: str | int | None = None
    block_ms: int = DEFAULT_BLOCK_MS

    _stream: Any = field(default=None, repr=False)
    _tail: bytes = field(default=b"", repr=False)
    _sample_rate: int = 0
    _stopped: bool = False

    async def open(self, sample_rate: int) -> None:
        import sounddevice

        self._sample_rate = sample_rate
        self._stopped = False
        resolved = resolve_device(self.device)
        try:
            self._stream = sounddevice.RawOutputStream(
                samplerate=sample_rate,
                channels=CHANNELS,
                dtype="int16",
                device=resolved,
                blocksize=max(1, round(sample_rate * self.block_ms / 1000)),
                latency="low",
            )
            await asyncio.to_thread(self._stream.start)
        except Exception as exc:
            raise AudioSinkError(f"could not open output device {self.device!r}: {exc}") from exc

    async def write(self, pcm: bytes) -> None:
        if self._stream is None or self._stopped or not pcm:
            return
        self._tail = pcm[-SAMPLE_WIDTH:] or self._tail
        await asyncio.to_thread(self._stream.write, pcm)

    async def drain(self) -> None:
        if self._stream is not None and not self._stopped:
            await asyncio.to_thread(self._stream.stop)
            await asyncio.to_thread(self._stream.start)

    async def stop(self, fade_ms: int = 80) -> None:
        """Ramp down, then let the short remaining buffer play out.

        Perceived cut-off is the device buffer plus the fade -- roughly
        `block_ms + fade_ms`. `abort()` would be instant but ends on whatever
        sample happened to be current, which clicks.
        """
        if self._stream is None or self._stopped:
            return
        self._stopped = True
        try:
            if fade_ms > 0:
                ramp = _ramp_to_silence(self._tail, fade_ms, self._sample_rate)
                await asyncio.to_thread(self._stream.write, ramp)
            await asyncio.wait_for(asyncio.to_thread(self._stream.stop), timeout=1.0)
        except (TimeoutError, Exception) as exc:  # noqa: B014 - TimeoutError is an Exception
            log.warning("output device did not stop cleanly: %s", exc)

    async def close(self) -> None:
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        try:
            await asyncio.to_thread(stream.close)
        except Exception as exc:  # noqa: BLE001
            log.warning("output device did not close cleanly: %s", exc)


# --------------------------------------------------------------------------- #
# Command
# --------------------------------------------------------------------------- #

#: Players that take raw PCM on stdin, in preference order.
RAW_PLAYERS: dict[str, Sequence[str]] = {
    "pw-play": ("pw-play", "--format=s16", "--rate={rate}", "--channels=1", "-"),
    "paplay": ("paplay", "--raw", "--format=s16le", "--rate={rate}", "--channels=1"),
    "aplay": ("aplay", "-q", "-f", "S16_LE", "-r", "{rate}", "-c", "1", "-"),
}


@dataclass
class CommandSink:
    """Pipes PCM to an external player.

    A fallback for machines without a working PortAudio binding. It cannot
    select a device the way `DeviceSink` can, and the player buffers audio out
    of our reach, so an interruption is heard later than it should be -- use
    `DeviceSink` for anything where barge-in timing matters.
    """

    argv: Sequence[str] = RAW_PLAYERS["paplay"]

    _process: Any = field(default=None, repr=False)
    _tail: bytes = field(default=b"", repr=False)
    _sample_rate: int = 0
    _stopped: bool = False

    async def open(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._stopped = False
        argv = [argument.format(rate=sample_rate) for argument in self.argv]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise AudioSinkError(f"player not found: {argv[0]}") from exc

    async def write(self, pcm: bytes) -> None:
        if self._process is None or self._stopped or not pcm:
            return
        self._tail = pcm[-SAMPLE_WIDTH:] or self._tail
        self._process.stdin.write(pcm)
        await self._process.stdin.drain()

    async def drain(self) -> None:
        return None

    async def stop(self, fade_ms: int = 80) -> None:
        if self._process is None or self._stopped:
            return
        self._stopped = True
        try:
            if fade_ms > 0:
                self._process.stdin.write(
                    _ramp_to_silence(self._tail, fade_ms, self._sample_rate)
                )
                await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        await self.close()

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            process.kill()
            await process.wait()

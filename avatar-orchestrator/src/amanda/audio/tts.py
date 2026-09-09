"""Text-to-speech behind a provider-neutral interface (epic 3).

The build plan fixes the shape (section 4):

    TTS.speak(text, performance_state) -> audio_stream + timing_metadata

and the reason for the abstraction is stated plainly: TTS provider lock-in is
listed as a risk whose consequence is an expensive redesign. So nothing outside
this module knows which engine is speaking, and the engine is chosen in config.

One thing to understand before reading further: **audio does not travel over the
avatar protocol.** Protocol v1's speech events are cues about audio arriving by a
completely separate route -- a virtual audio cable feeding a MetaHuman Audio
Live Link source. What this module produces is PCM for an audio *device*, and
`speech.started` merely tells the renderer to expect it.

Every provider must supply:

  * streaming or low-latency synthesis;
  * a consistent voice identity;
  * controllable pace;
  * cancellation -- not optional, because barge-in depends on it.

Timing metadata is optional, because engines differ wildly in whether they
offer it, and the renderer's lip sync does not depend on it.
"""

from __future__ import annotations

import array
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Every provider emits signed 16-bit little-endian mono PCM. One format, chosen
#: once: it is what every engine can produce, what PortAudio takes directly, and
#: what a virtual audio cable expects.
SAMPLE_WIDTH = 2
CHANNELS = 1


class SynthesisError(RuntimeError):
    """The engine failed. Distinct from a cancellation, which is not an error."""


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Voice identity plus the delivery the performance layer asked for.

    `pace` is the build plan's `performance_state` reaching TTS: a considered
    reply is spoken slightly slower than an enthusiastic one. It is a multiplier
    on the engine's natural rate, and staying near 1.0 is the norm -- a voice
    that audibly changes speed between sentences draws attention to itself in
    exactly the way the performance principles warn against.
    """

    voice_id: str | None = None
    sample_rate: int = 24_000
    pace: float = 1.0
    pitch: float = 1.0

    def with_pace(self, pace: float) -> VoiceSettings:
        return VoiceSettings(self.voice_id, self.sample_rate, pace, self.pitch)


@dataclass(frozen=True, slots=True)
class WordTiming:
    """When a word is spoken, relative to the start of the utterance.

    Only some engines provide this. Nothing in the renderer depends on it --
    MetaHuman solves the face from the audio itself -- but it is useful for
    subtitles and for checking that a phrase took as long as expected.
    """

    text: str
    start_ms: int
    end_ms: int


def pcm_duration_ms(pcm: bytes, sample_rate: int) -> int:
    """Milliseconds of audio in a PCM buffer."""
    frames = len(pcm) // (SAMPLE_WIDTH * CHANNELS)
    return round(frames * 1000 / sample_rate)


def fade_out(pcm: bytes, fade_ms: int, sample_rate: int) -> bytes:
    """Ramp the tail of a buffer to silence.

    Barge-in cuts audio mid-word, and stopping a waveform at a non-zero sample
    is an audible click -- which is precisely the "stopping a media player"
    feeling the build plan wants interruption to avoid. A few tens of
    milliseconds of ramp is enough to make it read as someone stopping
    mid-sentence.
    """
    samples = array.array("h")
    samples.frombytes(pcm)
    ramp = min(len(samples), max(1, round(fade_ms * sample_rate / 1000)))
    start = len(samples) - ramp
    for index in range(ramp):
        samples[start + index] = int(samples[start + index] * (1.0 - (index + 1) / ramp))
    return samples.tobytes()


class SynthesisStream:
    """One in-flight synthesis: PCM as it is produced, timings once known.

    Deliberately shaped like `StreamedTurn` in the Claude client -- iterate for
    output, cancel to abandon -- so the two halves of a spoken turn behave the
    same way under interruption.

    Providers subclass this and implement `_produce`.
    """

    def __init__(self, text: str, voice: VoiceSettings) -> None:
        self.text = text
        self.voice = voice

        self.cancelled = False
        self.timings: list[WordTiming] | None = None
        self.bytes_produced = 0

        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None

    @property
    def duration_ms(self) -> int:
        """How much audio has been produced so far."""
        frames = self.bytes_produced // (SAMPLE_WIDTH * CHANNELS)
        return round(frames * 1000 / self.voice.sample_rate)

    def cancel(self) -> None:
        """Abandon synthesis now.

        Cancels the producing task rather than setting a flag, for the same
        reason the Claude client does: a flag only takes effect at the next
        chunk, and a network engine may be mid-request.
        """
        self.cancelled = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="tts-synthesis")

        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk

        if self._error is not None:
            raise self._error

    async def _pump(self) -> None:
        try:
            async for chunk in self._produce():
                self.bytes_produced += len(chunk)
                self._queue.put_nowait(chunk)
        except asyncio.CancelledError:
            self.cancelled = True
        except BaseException as exc:  # noqa: BLE001 - re-raised to the consumer
            self._error = exc
        finally:
            self._queue.put_nowait(None)

    def _produce(self) -> AsyncIterator[bytes]:
        raise NotImplementedError


@runtime_checkable
class SpeechSynthesizer(Protocol):
    """What every engine must offer. The whole provider-neutral surface."""

    @property
    def name(self) -> str:
        """Identifies the engine in logs and telemetry."""
        ...

    def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisStream:
        """Begin synthesising. Nothing runs until the result is iterated."""
        ...


@dataclass(slots=True)
class SpokenUtterance:
    """The record of one phrase that reached the speakers.

    `spoken_ms` is what was actually played, which after a barge-in is less than
    what was synthesised -- and it is the shorter number that belongs in the
    conversation history and the telemetry.
    """

    utterance_id: str
    text: str
    sample_rate: int
    synthesized_ms: int = 0
    spoken_ms: int = 0
    cancelled: bool = False
    timings: list[WordTiming] | None = field(default=None)

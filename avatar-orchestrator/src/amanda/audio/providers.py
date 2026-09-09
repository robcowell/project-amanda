"""Speech synthesis providers.

Two here, and neither is the engine this project will ship with:

  * `ToneSynthesizer` makes audible, correctly-timed audio out of nothing, so
    the whole pipeline -- segmenter, queue, sink, protocol events, latency
    marks -- can be run and heard before any engine is chosen.
  * `CommandSynthesizer` runs any command-line engine, which covers espeak-ng,
    Piper, macOS `say` and most local engines without writing code per engine.

A streaming cloud engine is the likely production choice and is deliberately
absent: writing an API client that has never been run against the real service
would produce code that looks finished and is not. The interface it has to
satisfy is `SpeechSynthesizer` in `tts.py`; the notes at the bottom of this file
say what such a provider must get right.
"""

from __future__ import annotations

import array
import asyncio
import math
import re
import struct
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from amanda.audio.tts import (
    CHANNELS,
    SAMPLE_WIDTH,
    SynthesisError,
    SynthesisStream,
    VoiceSettings,
    WordTiming,
)

#: How much audio to emit per chunk. Small enough that cancellation lands
#: promptly, large enough not to thrash the event loop.
CHUNK_MS = 40

#: Roughly 150 words per minute in characters, which is unhurried conversational
#: English. The pace multiplier scales it.
CHARS_PER_SECOND = 13.0

_VOWEL_GROUP = re.compile(r"[aeiouy]+", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Tone
# --------------------------------------------------------------------------- #


class _ToneStream(SynthesisStream):
    def __init__(self, text: str, voice: VoiceSettings, fundamental: float) -> None:
        super().__init__(text, voice)
        self._fundamental = fundamental
        self.timings = _word_timings(text, voice.pace)

    async def _produce(self) -> AsyncIterator[bytes]:
        rate = self.voice.sample_rate
        total_ms = _speaking_duration_ms(self.text, self.voice.pace)
        chunk_frames = max(1, round(rate * CHUNK_MS / 1000))
        total_frames = max(1, round(rate * total_ms / 1000))

        syllables = max(1, _count_syllables(self.text))
        syllable_hz = syllables / max(0.001, total_ms / 1000)

        produced = 0
        phase = 0.0
        while produced < total_frames:
            frames = min(chunk_frames, total_frames - produced)
            samples = array.array("h", bytes(frames * SAMPLE_WIDTH))

            for index in range(frames):
                position = (produced + index) / total_frames
                seconds = (produced + index) / rate

                # A syllable-rate amplitude envelope, so the placeholder has the
                # rhythm of speech even though it has none of the content. That
                # rhythm is the point: it is what makes a barge-in cutting in
                # mid-word sound wrong when the timing is wrong.
                envelope = 0.35 + 0.65 * abs(math.sin(math.pi * seconds * syllable_hz))
                envelope *= _phrase_envelope(position)

                # A falling pitch contour across the phrase, as in a statement.
                hz = self._fundamental * (1.0 + 0.10 * (1.0 - position)) * self.voice.pitch
                phase += 2 * math.pi * hz / rate

                value = math.sin(phase) + 0.30 * math.sin(2 * phase) + 0.12 * math.sin(3 * phase)
                samples[index] = int(max(-1.0, min(1.0, value / 1.42)) * envelope * 9000)

            produced += frames
            yield samples.tobytes()
            await asyncio.sleep(0)


@dataclass(slots=True)
class ToneSynthesizer:
    """An audible stand-in with realistic duration and word timings.

    It is not speech and is not trying to be. It exists so the pipeline can be
    exercised end to end -- including hearing what an interruption sounds like --
    without downloading a voice model or holding an API key.
    """

    fundamental: float = 118.0

    @property
    def name(self) -> str:
        return "tone"

    def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisStream:
        return _ToneStream(text, voice, self.fundamental)


def _phrase_envelope(position: float) -> float:
    """Ease in and out so phrases do not start or end on a click."""
    edge = 0.06
    if position < edge:
        return position / edge
    if position > 1.0 - edge:
        return (1.0 - position) / edge
    return 1.0


def _count_syllables(text: str) -> int:
    return sum(len(_VOWEL_GROUP.findall(word)) or 1 for word in text.split())


def _speaking_duration_ms(text: str, pace: float) -> int:
    """How long this phrase would take a person to say.

    Punctuation earns a pause -- without it, phrases butt against each other and
    the queue sounds like a list being read rather than someone talking.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    seconds = len(stripped) / (CHARS_PER_SECOND * max(0.25, pace))
    seconds += 0.32 if stripped[-1] in ".!?" else 0.14 if stripped[-1] in ",;:" else 0.0
    return max(60, round(seconds * 1000))


def _word_timings(text: str, pace: float) -> list[WordTiming]:
    """Even distribution weighted by word length. Approximate, and honest about
    it -- an engine that reports real timings should be preferred."""
    words = text.split()
    if not words:
        return []

    total_ms = _speaking_duration_ms(text, pace)
    weights = [len(word) + 1 for word in words]
    span = sum(weights)

    timings: list[WordTiming] = []
    elapsed = 0.0
    for word, weight in zip(words, weights, strict=True):
        duration = total_ms * weight / span
        timings.append(WordTiming(word, round(elapsed), round(elapsed + duration)))
        elapsed += duration
    return timings


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


class _CommandStream(SynthesisStream):
    def __init__(
        self,
        text: str,
        voice: VoiceSettings,
        argv: Sequence[str],
        expects_wav: bool,
    ) -> None:
        super().__init__(text, voice)
        self._argv = list(argv)
        self._expects_wav = expects_wav
        self._process: asyncio.subprocess.Process | None = None

    async def _produce(self) -> AsyncIterator[bytes]:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SynthesisError(f"engine not found: {self._argv[0]}") from exc

        assert self._process.stdout is not None
        header = _WavHeader() if self._expects_wav else None
        read_size = max(1, round(self.voice.sample_rate * CHUNK_MS / 1000)) * SAMPLE_WIDTH

        try:
            while chunk := await self._process.stdout.read(read_size):
                if header is not None:
                    chunk = header.consume(chunk)
                    if header.sample_rate and header.sample_rate != self.voice.sample_rate:
                        # Resampling is out of scope, and silently playing at the
                        # wrong rate produces a chipmunk that is easy to blame on
                        # the engine rather than the config.
                        raise SynthesisError(
                            f"{self._argv[0]} produced {header.sample_rate} Hz but the "
                            f"voice is configured for {self.voice.sample_rate} Hz -- "
                            f"align config/voices.yaml"
                        )
                    if not chunk:
                        continue
                yield chunk

            if await self._process.wait() != 0:
                stderr = (await self._process.stderr.read()).decode(errors="replace").strip()
                raise SynthesisError(f"{self._argv[0]} failed: {stderr or 'no output'}")
        finally:
            await self._terminate()

    async def _terminate(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.kill()
        await process.wait()


@dataclass(slots=True)
class CommandSynthesizer:
    """Runs a command-line engine and reads audio from its stdout.

    The argv template takes `{text}`, `{rate}` and `{voice}`. One entry per
    argument -- no shell -- so text containing quotes or semicolons is passed
    through as data rather than parsed.

        CommandSynthesizer(
            argv=["espeak-ng", "--stdout", "-s", "{rate}", "-v", "{voice}", "{text}"],
        )

    Cancellation kills the process, which is the only reliable way to stop a
    local engine that has already committed to a phrase.
    """

    argv: Sequence[str]
    #: Most CLI engines write a WAV header before the samples; a few emit raw
    #: PCM. Getting this wrong prepends 44 bytes of noise to every phrase.
    expects_wav: bool = True
    #: Words per minute passed as `{rate}`, before the pace multiplier.
    base_rate: int = 165
    label: str = "command"
    _: dict = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return self.label

    def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisStream:
        substitutions = {
            "text": text,
            "rate": str(round(self.base_rate * max(0.25, voice.pace))),
            "voice": voice.voice_id or "",
        }
        argv = [argument.format(**substitutions) for argument in self.argv]
        return _CommandStream(text, voice, argv, self.expects_wav)


class _WavHeader:
    """Strips a RIFF header, tolerating it arriving split across reads."""

    def __init__(self) -> None:
        self._buffer = b""
        self.done = False
        self.sample_rate: int | None = None

    def consume(self, chunk: bytes) -> bytes:
        if self.done:
            return chunk

        self._buffer += chunk
        if len(self._buffer) < 12:
            return b""
        if not self._buffer.startswith(b"RIFF"):
            # Raw PCM after all. Pass it through rather than failing -- the flag
            # being wrong should not be fatal when the audio is usable.
            self.done = True
            return self._release()

        offset = 12
        while offset + 8 <= len(self._buffer):
            chunk_id = self._buffer[offset : offset + 4]
            size = struct.unpack_from("<I", self._buffer, offset + 4)[0]
            body = offset + 8

            if chunk_id == b"fmt " and body + 16 <= len(self._buffer):
                channels, rate = struct.unpack_from("<HI", self._buffer, body + 2)
                self.sample_rate = rate
                if channels != CHANNELS:
                    raise SynthesisError(
                        f"expected mono audio, engine produced {channels} channels"
                    )
            elif chunk_id == b"data":
                self.done = True
                self._buffer = self._buffer[body:]
                return self._release()

            offset = body + size + (size % 2)

        return b""  # header still incomplete

    def _release(self) -> bytes:
        released, self._buffer = self._buffer, b""
        return released


# --------------------------------------------------------------------------- #
# Notes for a streaming cloud provider
# --------------------------------------------------------------------------- #

#: What a hosted engine has to get right, beyond implementing the protocol:
#:
#:   * **Emit the first chunk before the last.** The entire point of streaming is
#:     T5. A provider that awaits the complete response and then yields it has
#:     the interface but not the behaviour, and the latency budget will show it.
#:   * **Cancel the request, not just the iteration.** Abandoning the generator
#:     while the HTTP request runs leaves the engine synthesising -- and billing
#:     -- audio nobody will hear. `_produce` is cancelled as a task, so cleanup
#:     belongs in a `finally`.
#:   * **Pin the output format.** Ask for 16-bit mono PCM at the configured rate
#:     rather than accepting a default and converting. The mismatch check in
#:     `_CommandStream` exists because that lesson is cheap to relearn.
#:   * **Keep the voice identity in config.** A voice ID hard-coded in a provider
#:     is the lock-in the abstraction exists to prevent.
PROVIDER_NOTES = __doc__

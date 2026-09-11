"""Whisper via faster-whisper, loaded in-process.

Same reasoning as Piper: the model is slow to load and fast to run, so it is
loaded once at startup rather than per utterance. `warm()` exists so the cost
lands before anyone is waiting.

Measured on a 2017 ultrabook (i7-8550U, 8 threads, int8), transcribing a 2-second
utterance:

| model      | load  | transcribe | notes                                  |
|------------|-------|------------|----------------------------------------|
| `tiny.en`  | 3.9s  | ~590ms     | heard "folks down" for "Folkestone"    |
| `base.en`  | 7.7s  | ~880ms     | correct, and the default here          |
| `small.en` | 14.2s | ~2370ms    | 2.5x the cost for no accuracy gain     |

`tiny` is tempting for the 300ms and loses proper nouns, which is the wrong
trade for a conversational assistant — place names and people's names are
exactly what a reply hinges on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from amanda.audio.stt import RecognitionError, Transcript
from amanda.audio.vad import Utterance

log = logging.getLogger(__name__)

#: Big enough to hear a place name, small enough to keep up. See the table above.
DEFAULT_MODEL = "base.en"

#: Segments the model is this sure contain no speech are discarded.
#:
#: Whisper hallucinates confidently on silence -- a second of digital zero
#: transcribes as "You", and other well-known ones are "Thank you." and
#: "Thanks for watching!". Left in, a door closing that got past the endpointer
#: becomes a user turn and Claude answers it. Measured here: silence scores
#: 0.768 and real speech 0.000, so anywhere in between separates them.
NO_SPEECH_THRESHOLD = 0.6


@dataclass
class WhisperRecognizer:
    """Local transcription with a Whisper model held in memory."""

    model: str = DEFAULT_MODEL
    #: int8 quantisation. On a CPU with no AVX-512 this is roughly twice as fast
    #: as float32 and the accuracy difference did not show up in testing.
    compute_type: str = "int8"
    device: str = "cpu"
    language: str | None = "en"

    #: Greedy rather than the library's default beam of 5. Beam search buys
    #: accuracy this workload does not need -- `base.en` transcribed the test
    #: utterances correctly at beam 1 -- and costs latency it cannot afford.
    beam_size: int = 1

    #: CPU threads for CTranslate2. 0 is the library's default of 4, whatever
    #: the machine -- tuned on an 8-thread ultrabook, and a quarter of the cores
    #: on the 32-thread renderer PC, where 12 measured ~20% faster.
    cpu_threads: int = 0

    #: Temperatures to decode at, in order, moving on when a decode looks wrong
    #: (repetitive, or low confidence). The library's default climbs 0.0 to 1.0
    #: in six steps, and on the renderer PC, 2026-09-11, that was every slow
    #: turn: 3.6-5.3s at temperature 1.0, on background noise transcribed as
    #: "Okay. All right." for twenty seconds and on her own echo as "That's
    #: it.", while every clean turn decoded first time in ~530ms. Retries buy
    #: nothing on audio that was never speech, so there is one. 0.4 rather than
    #: 0.2 so it has a chance of leaving the loop the first decode fell into.
    temperatures: tuple[float, ...] = (0.0, 0.4)

    #: Per-segment detail from the last transcription, for diagnostics:
    #: (temperature, avg_logprob, compression_ratio, no_speech_prob). A
    #: temperature above 0 means Whisper was not confident and decoded again.
    last_segments: list[tuple[float, float, float, float]] = field(
        default_factory=list, init=False, repr=False
    )

    _model: Any = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        # Import here so `stt.build` can catch it and fall back, rather than the
        # package failing to import on a machine without the weights.
        import faster_whisper  # noqa: F401

    @property
    def name(self) -> str:
        return f"whisper:{self.model}"

    async def warm(self) -> None:
        """Load the model. Several seconds, so not on the first utterance."""
        await self._loaded()

    async def _loaded(self) -> Any:
        async with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("loading whisper model %s (%s)", self.model, self.compute_type)
                started = time.monotonic()
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self.model,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.cpu_threads,
                )
                log.info("whisper ready in %.1fs", time.monotonic() - started)
            return self._model

    async def transcribe(self, utterance: Utterance) -> Transcript:
        model = await self._loaded()
        started = time.monotonic()

        try:
            text, language, confidence = await asyncio.to_thread(
                self._run, model, utterance
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a recognition failure
            raise RecognitionError(f"{self.name} failed: {exc}") from exc

        return Transcript(
            text=text,
            duration_ms=utterance.duration_ms,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            language=language,
            confidence=confidence,
            max_temperature=max((t for t, *_ in self.last_segments), default=0.0),
        )

    def _run(self, model: Any, utterance: Utterance) -> tuple[str, str | None, float | None]:
        """Runs on a worker thread: transcription is CPU-bound, and on the event
        loop it would stall the Claude stream and the avatar bridge."""
        import numpy

        if utterance.sample_rate != 16_000:
            raise RecognitionError(
                f"whisper expects 16000 Hz, got {utterance.sample_rate}. "
                "Capture runs at 16 kHz for exactly this reason."
            )

        samples = numpy.frombuffer(utterance.pcm, dtype=numpy.int16)
        audio = samples.astype(numpy.float32) / 32768.0

        segments, info = model.transcribe(
            audio,
            beam_size=self.beam_size,
            language=self.language,
            # The endpointer already trimmed this utterance and deliberately
            # kept a pre-roll so the first syllable survives. A second VAD here
            # would trim exactly that back off again.
            vad_filter=False,
            # Off for short utterances: conditioning on previous text is what
            # makes Whisper loop, repeating a phrase until it fills the buffer.
            condition_on_previous_text=False,
            temperature=list(self.temperatures),
        )

        # A generator: decoding happens as it is consumed, so materialise it
        # once and read both the text and the diagnostics from the same pass.
        segments = list(segments)
        self.last_segments = [
            (
                getattr(segment, "temperature", 0.0),
                getattr(segment, "avg_logprob", 0.0),
                getattr(segment, "compression_ratio", 0.0),
                getattr(segment, "no_speech_prob", 0.0),
            )
            for segment in segments
        ]

        kept = [
            segment
            for segment in segments
            if getattr(segment, "no_speech_prob", 0.0) < NO_SPEECH_THRESHOLD
        ]
        text = "".join(segment.text for segment in kept).strip()
        confidence = getattr(info, "language_probability", None)
        return text, getattr(info, "language", None), confidence

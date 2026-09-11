"""Speech-to-text behind a swappable interface (epic 5).

The build plan is explicit about the priority: "optimise for **latency and
reliable endpoint detection**, not theoretical transcription perfection" (§4).
Endpointing lives in `vad.py`; this is the other half.

Transcription sits between T0 and T1 and is pure added latency — nothing
downstream can start until it finishes, and unlike Claude's first token there is
no streaming to hide behind. On this machine it costs about 900ms of a roughly
2.3s turn, which makes it the second largest term after time to first token.

Local rather than hosted, because the only credential this project holds is an
Anthropic one and Anthropic has no transcription API. Adding a cloud recogniser
means adding a second vendor; the interface below is what makes that a config
change rather than a rewrite.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from amanda.audio.vad import Utterance

log = logging.getLogger(__name__)


class RecognitionError(RuntimeError):
    """The recogniser failed. Distinct from hearing nothing, which is empty text."""


@dataclass(frozen=True, slots=True)
class Transcript:
    """What was heard, and what it cost to hear it."""

    text: str
    #: Length of the audio, against `elapsed_ms` — the ratio is what tells you
    #: whether the recogniser can keep up with someone talking.
    duration_ms: int = 0
    elapsed_ms: int = 0
    language: str | None = None
    #: Provider-specific and not comparable between engines. Useful for
    #: rejecting noise the endpointer let through, not for ranking.
    confidence: float | None = None
    #: The highest temperature any segment was accepted at, where the engine
    #: reports one. Above 0 means it re-decoded audio it was not sure of --
    #: the multiplier behind a slow transcription.
    max_temperature: float | None = None

    @property
    def empty(self) -> bool:
        """Silence, breathing, or a door closing that got past the endpointer."""
        return not self.text.strip()

    @property
    def realtime_factor(self) -> float:
        """Audio seconds transcribed per wall second. Above 1.0 keeps up."""
        return self.duration_ms / self.elapsed_ms if self.elapsed_ms else 0.0


@runtime_checkable
class SpeechRecognizer(Protocol):
    """What every recogniser must offer."""

    @property
    def name(self) -> str: ...

    async def warm(self) -> None:
        """Load whatever is slow to load, before anyone is waiting on it."""
        ...

    async def transcribe(self, utterance: Utterance) -> Transcript: ...


@dataclass
class ScriptedRecognizer:
    """Returns canned text, for tests and for running without a model.

    Same role as `ScriptedClient` and `ToneSynthesizer`: the rest of the input
    path — endpointing, the state machine, the turn loop — is worth exercising
    on a machine that has not downloaded a few hundred megabytes of weights.
    """

    replies: list[str] = field(default_factory=lambda: ["hello there"])
    #: Stands in for transcription time. Zero would make the latency budget
    #: look better than it is.
    latency: float = 0.6

    _index: int = field(default=0, init=False)

    @property
    def name(self) -> str:
        return "scripted"

    async def warm(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance) -> Transcript:
        import asyncio

        started = time.monotonic()
        await asyncio.sleep(self.latency)
        text = self.replies[self._index % len(self.replies)] if self.replies else ""
        self._index += 1
        return Transcript(
            text=text,
            duration_ms=utterance.duration_ms,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            language="en",
        )


def build(name: str | None = None, **options) -> SpeechRecognizer:
    """A recogniser by name, or the best one installed.

    "auto" prefers a real model and falls back to canned text, so a machine
    without the weights still runs the pipeline rather than failing at the first
    thing anybody says. Settings not passed explicitly come from
    config/avatar.yaml; unknown keys are dropped with a warning rather than
    raising, so config naming something the code has dropped is survivable.
    """
    from amanda.config import stt_settings

    configured = stt_settings()
    name = name or configured.pop("engine", None) or "auto"
    configured.pop("engine", None)
    options = {**configured, **options}

    if name in {"auto", "whisper"}:
        try:
            import dataclasses

            from amanda.audio.whisper_provider import WhisperRecognizer

            fields = {field.name for field in dataclasses.fields(WhisperRecognizer)}
            for key in set(options) - fields:
                log.warning("ignoring unknown stt setting %r in config", key)
            return WhisperRecognizer(**{k: v for k, v in options.items() if k in fields})
        except ImportError:
            if name == "whisper":
                raise RecognitionError(
                    "faster-whisper is not installed. `pip install faster-whisper`, "
                    "or use --stt scripted"
                ) from None

    if name in {"auto", "scripted"}:
        return ScriptedRecognizer()

    raise RecognitionError(f"unknown recogniser {name!r}. Known: whisper, scripted, auto")

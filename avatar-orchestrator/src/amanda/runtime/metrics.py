"""Latency telemetry (build plan 11 and 24).

Perceived latency is what matters, but you cannot tune what you cannot see. The
seven marks below split one conversational turn into the stages that can each be
slow for different reasons, so "it feels sluggish" becomes a number with a name
attached.

The headline metric is T6 - T0: the gap between the user finishing their
sentence and the avatar starting to speak. Everything else exists to explain it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    """The seven marks. Names match the build plan so the two can be read together."""

    USER_SPEECH_ENDED = "T0"
    TRANSCRIPT_FINAL = "T1"
    REQUEST_SENT = "T2"
    FIRST_TOKEN = "T3"
    FIRST_PHRASE = "T4"
    FIRST_AUDIO = "T5"
    SPEECH_STARTED = "T6"


#: Stage pairs worth reporting, and what a slow one usually means.
SPANS: dict[str, tuple[Stage, Stage]] = {
    "stt_ms": (Stage.USER_SPEECH_ENDED, Stage.TRANSCRIPT_FINAL),
    "dispatch_ms": (Stage.TRANSCRIPT_FINAL, Stage.REQUEST_SENT),
    "claude_first_token_ms": (Stage.REQUEST_SENT, Stage.FIRST_TOKEN),
    "phrase_ms": (Stage.FIRST_TOKEN, Stage.FIRST_PHRASE),
    "tts_first_audio_ms": (Stage.FIRST_PHRASE, Stage.FIRST_AUDIO),
    "playback_ms": (Stage.FIRST_AUDIO, Stage.SPEECH_STARTED),
}


@dataclass(slots=True)
class TurnMetrics:
    """Timing for one conversational turn.

    Marks are recorded against a monotonic clock, which is injectable so tests
    can assert on exact numbers rather than on ranges.
    """

    clock: Callable[[], float] = time.monotonic
    marks: dict[Stage, float] = field(default_factory=dict)

    interrupted: bool = False
    refused: bool = False
    preset: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None

    def mark(self, stage: Stage) -> float:
        """Record a stage, keeping the first timestamp if it fires twice.

        First-wins matters: T3 is the first token, and a naive implementation
        that overwrites would quietly report the *last* one instead.
        """
        return self.marks.setdefault(stage, self.clock())

    def elapsed_ms(self, start: Stage, end: Stage) -> int | None:
        """Milliseconds between two marks, or None if either is missing."""
        if start not in self.marks or end not in self.marks:
            return None
        return round((self.marks[end] - self.marks[start]) * 1000)

    @property
    def response_latency_ms(self) -> int | None:
        """T6 - T0. The one number that describes whether this feels broken."""
        return self.elapsed_ms(Stage.USER_SPEECH_ENDED, Stage.SPEECH_STARTED)

    def as_dict(self) -> dict[str, object]:
        """The per-turn log line from build plan 24.

        Spans that never happened are omitted rather than logged as zero -- a
        turn started from typed input has no T0, and reporting that as 0ms would
        make the numbers lie.
        """
        record: dict[str, object] = {
            name: value
            for name, (start, end) in SPANS.items()
            if (value := self.elapsed_ms(start, end)) is not None
        }
        if (total := self.response_latency_ms) is not None:
            record["total_response_ms"] = total
        record["interrupted"] = self.interrupted
        if self.refused:
            record["refused"] = True
        for name, value in (
            ("performance", self.preset),
            ("model", self.model),
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_tokens", self.cached_tokens),
        ):
            if value is not None:
                record[name] = value
        return record

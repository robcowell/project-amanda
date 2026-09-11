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
    """The marks. T0-T6 match the build plan so the two can be read together."""

    USER_SPEECH_ENDED = "T0"
    TRANSCRIPT_FINAL = "T1"
    REQUEST_SENT = "T2"
    FIRST_TOKEN = "T3"
    FIRST_PHRASE = "T4"
    FIRST_AUDIO = "T5"
    SPEECH_STARTED = "T6"

    #: Ours, not the plan's. The last token of the reply, which normally lands
    #: well *after* T6 -- the avatar starts speaking while Claude is still
    #: writing. So it measures generation, not latency, which is why it is kept
    #: out of SPANS below.
    LAST_TOKEN = "T7"


#: The latency budget: consecutive stages on the path from T0 to T6.
#:
#: Only spans a listener is *waiting* through belong here. A duration that runs
#: alongside speech is not latency and would make the printed budget stop
#: adding up -- see `claude_stream_ms` in `as_dict`.
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
    #: Set when the turn raised. The most interesting line in the log, and the
    #: one the console print scrolls away fastest.
    failed: str | None = None
    #: What the performance director decided, once it has decided it.
    preset: str | None = None
    intensity: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None

    # The rest of build plan 24's list, as far as the orchestrator can honestly
    # answer it. FPS and GPU frame time are the renderer's to report and are
    # deliberately absent here rather than guessed at.

    #: How long the user spoke, and how long the avatar did.
    heard_ms: int | None = None
    spoken_ms: int | None = None
    #: Whisper's own compute time, beside `stt_ms` (T1 - T0): if they differ,
    #: the difference was waiting rather than transcribing. And the highest
    #: temperature it needed -- above 0 means it re-decoded audio it was unsure
    #: of, which multiplies the cost.
    stt_compute_ms: int | None = None
    stt_temperature: float | None = None
    #: Whether the transcript was started during the pause before the
    #: endpointer confirmed the turn -- in which case most of `stt_compute_ms`
    #: happened before T0 and `stt_ms` is only what was left of it.
    stt_speculative: bool | None = None
    #: Phrases synthesised, and the deepest the queue got waiting for them.
    phrases: int | None = None
    peak_queue_depth: int | None = None
    #: Times the output device ran dry mid-utterance.
    underruns: int | None = None
    #: Whether a renderer was attached at all, and how many messages it was too
    #: slow to take. Protocol v1 has no ack, so there is no round trip to
    #: measure and no honest "websocket latency" to report -- backpressure is
    #: the signal that actually exists.
    renderer_connected: bool | None = None
    renderer_backpressure_drops: int | None = None

    def mark(self, stage: Stage) -> float:
        """Record a stage, keeping the first timestamp if it fires twice.

        First-wins matters: T3 is the first token, and a naive implementation
        that overwrites would quietly report the *last* one instead.
        """
        return self.marks.setdefault(stage, self.clock())

    def mark_at(self, stage: Stage, when: float) -> float:
        """Record a stage that happened elsewhere.

        Voice input knows when the user stopped speaking and when the transcript
        was ready; it should not have to know about this class to say so.
        """
        return self.marks.setdefault(stage, when)

    def elapsed_ms(self, start: Stage, end: Stage) -> int | None:
        """Milliseconds between two marks, or None if either is missing."""
        if start not in self.marks or end not in self.marks:
            return None
        return round((self.marks[end] - self.marks[start]) * 1000)

    @property
    def stream_ms(self) -> int | None:
        """How long Claude spent writing the reply, T2 to T7."""
        return self.elapsed_ms(Stage.REQUEST_SENT, Stage.LAST_TOKEN)

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
            ("failed", self.failed),
            # Generation duration, not latency, so it sits outside SPANS -- but
            # it is still a `_ms` the plan asks for.
            ("claude_stream_ms", self.stream_ms),
            ("performance", self.preset),
            ("intensity", self.intensity),
            ("model", self.model),
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_tokens", self.cached_tokens),
            ("heard_ms", self.heard_ms),
            ("spoken_ms", self.spoken_ms),
            ("stt_compute_ms", self.stt_compute_ms),
            ("stt_temperature", self.stt_temperature),
            ("stt_speculative", self.stt_speculative),
            ("phrases", self.phrases),
            ("peak_queue_depth", self.peak_queue_depth),
            ("underruns", self.underruns),
            ("renderer_connected", self.renderer_connected),
            ("renderer_backpressure_drops", self.renderer_backpressure_drops),
        ):
            if value is not None:
                record[name] = value
        return record

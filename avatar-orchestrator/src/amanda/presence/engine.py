"""Combines the schedulers into one renderer-side presence model.

Consumes protocol v1 payloads and produces a FaceState per frame. This is the
Python stand-in for what ABP_Avatar will do: take the orchestrator's direction,
add the behaviour the orchestrator does not own, and blend the result.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from amanda.avatar.protocol import (
    AssistantThinkingEnded,
    AssistantThinkingStarted,
    AvatarReset,
    GazeSetTarget,
    GazeTarget,
    Payload,
    PerformanceUpdate,
    Preset,
    SpeechCancelled,
    SpeechCompleted,
    SpeechPrepare,
    SpeechStarted,
    UserDetected,
    UserSpeechEnded,
    UserSpeechStarted,
)
from amanda.performance.smoothing import PerformanceSmoother
from amanda.presence.schedulers import (
    BlinkScheduler,
    BreathScheduler,
    DriftScheduler,
    GazeScheduler,
)


@dataclass(frozen=True, slots=True)
class FaceState:
    """Everything needed to draw one frame."""

    t: float = 0.0

    eye_yaw: float = 0.0
    eye_pitch: float = 0.0
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    head_roll: float = 0.0

    lid_open: float = 1.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    smile_left: float = 0.0
    smile_right: float = 0.0
    jaw: float = 0.0
    breath: float = 0.0

    preset: str = Preset.NEUTRAL_ATTENTIVE.value
    intensity: float = 0.1
    eye_contact: float = 0.55
    gaze_target: str = GazeTarget.USER.value

    speaking: bool = False
    thinking: bool = False
    listening: bool = False
    user_present: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class PresenceEngine:
    """The renderer's behaviour, minus the rendering.

    Deterministic for a given seed, and driven by an explicit clock, so a test
    can run twenty minutes of behaviour instantly and reproducibly.
    """

    seed: int | None = None

    rng: random.Random = field(init=False)
    blink: BlinkScheduler = field(init=False)
    gaze: GazeScheduler = field(init=False)
    breath: BreathScheduler = field(init=False)
    drift: DriftScheduler = field(init=False)
    smoother: PerformanceSmoother = field(init=False)

    speaking: bool = False
    thinking: bool = False
    listening: bool = False
    user_present: bool = False
    utterance_id: str | None = None

    #: Onset times of every blink, for the rhythm diagnostics.
    blink_times: list[float] = field(default_factory=list)

    _asymmetry: float = 0.0
    _last_now: float | None = None
    _lid_previous: float = 1.0
    _speech_started_at: float = 0.0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.blink = BlinkScheduler(rng=self.rng)
        self.gaze = GazeScheduler(rng=self.rng)
        self.breath = BreathScheduler(rng=self.rng)
        self.drift = DriftScheduler(rng=self.rng)
        self.smoother = PerformanceSmoother()
        # A small constant left/right bias, drawn once. Perfectly symmetrical
        # facial movement reads as synthetic (build plan 18), and a fixed bias
        # costs nothing while a per-frame random one would look like a twitch.
        self._asymmetry = self.rng.uniform(0.10, 0.20) * self.rng.choice((-1.0, 1.0))

    # ----------------------------------------------------------------- #
    # Protocol
    # ----------------------------------------------------------------- #

    def handle(self, payload: Payload, now: float) -> None:
        """Apply one protocol v1 event. Unknown payload types are ignored."""
        match payload:
            case PerformanceUpdate():
                self.smoother.apply(payload, now)
            case GazeSetTarget():
                self.gaze.command(now, payload.target, payload.hold_ms)
            case SpeechPrepare():
                self.utterance_id = payload.utterance_id
            case SpeechStarted():
                self.speaking = True
                self.utterance_id = payload.utterance_id
                self._speech_started_at = now
            case SpeechCompleted() | SpeechCancelled():
                self.speaking = False
                self.utterance_id = None
            case AssistantThinkingStarted():
                self.thinking = True
            case AssistantThinkingEnded():
                self.thinking = False
            case UserSpeechStarted():
                self.listening = True
            case UserSpeechEnded():
                self.listening = False
            case UserDetected():
                self.user_present = payload.present
            case AvatarReset():
                self.reset(now)
            case _:
                pass

    def reset(self, now: float) -> None:
        """Drop performance state and settle to neutral, keeping the schedulers
        running -- a reconnect should not make the character freeze and restart."""
        self.smoother = PerformanceSmoother()
        self.speaking = self.thinking = self.listening = False
        self.utterance_id = None

    # ----------------------------------------------------------------- #
    # Frame
    # ----------------------------------------------------------------- #

    def tick(self, now: float) -> FaceState:
        dt = 0.0 if self._last_now is None else max(0.0, now - self._last_now)
        self._last_now = now

        coefficients = self.smoother.tick(now, dt)

        self.blink.set_rate_scale(coefficients.blink_rate_scale)
        self.gaze.set_eye_contact(coefficients.eye_contact)

        self.gaze.update(now, dt)
        shift = self.gaze.consume_shift()
        if shift:
            self.blink.on_gaze_shift(now, shift)

        lid = self.blink.update(now)
        if self._lid_previous >= 1.0 > lid:
            self.blink_times.append(now)
        self._lid_previous = lid

        breath = self.breath.update(now)
        drift_yaw, drift_pitch, drift_roll = self.drift.update(now)

        eye_yaw, eye_pitch = self.gaze.eye_angles
        head_yaw, head_pitch = self.gaze.head_angles
        motion = coefficients.head_motion

        bias = self._asymmetry
        brow = coefficients.brow_activity
        smile = coefficients.smile

        return FaceState(
            t=now,
            eye_yaw=eye_yaw,
            eye_pitch=eye_pitch,
            head_yaw=head_yaw + drift_yaw * (0.5 + motion),
            head_pitch=head_pitch + drift_pitch * (0.5 + motion) + breath * 0.35,
            head_roll=drift_roll * (0.5 + motion),
            lid_open=lid,
            brow_left=_clamp(brow * (1.0 - bias)),
            brow_right=_clamp(brow * (1.0 + bias)),
            smile_left=_clamp(smile * (1.0 + bias * 0.5)),
            smile_right=_clamp(smile * (1.0 - bias * 0.5)),
            jaw=self._jaw(now),
            breath=breath,
            preset=coefficients.preset.value,
            intensity=coefficients.intensity,
            eye_contact=coefficients.eye_contact,
            gaze_target=self.gaze.target.value,
            speaking=self.speaking,
            thinking=self.thinking,
            listening=self.listening,
            user_present=self.user_present,
        )

    def _jaw(self, now: float) -> float:
        """A stand-in speech envelope, not lip sync.

        Real mouth movement comes from MetaHuman's audio solver, which the previz
        has no access to -- no audio reaches this process. This exists so the
        speaking state is legible on screen, and should not be ported anywhere.
        """
        if not self.speaking:
            return 0.0
        elapsed = now - self._speech_started_at
        syllable = math.sin(elapsed * math.tau * 3.4) * 0.5 + 0.5
        phrase = math.sin(elapsed * math.tau * 0.7) * 0.25 + 0.75
        return _clamp(syllable * phrase * 0.8)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))

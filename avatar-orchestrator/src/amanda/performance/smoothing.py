"""Emotional inertia (build plan 18).

Three separate mechanisms, often confused:

  * **Transition** -- coefficients move toward a new target over
    `transition_ms` rather than snapping. Stops the face changing between
    frames.
  * **Hysteresis** -- a new preset is refused if the last change was too
    recent, unless it arrives with enough intensity to justify overriding the
    hold. Stops the face flapping between two moods on alternating sentences.
  * **Decay** -- intensity falls back toward a resting floor over time. Stops a
    single surprised classification leaving the character wide-eyed for the
    rest of the conversation.

The avatar must not snap from concerned to happy because the next sentence
contains a joke.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from amanda.avatar.protocol import PerformanceUpdate, Preset
from amanda.performance.schema import PRESET_SHAPES, REFERENCE_INTENSITY, PresetShape


@dataclass(frozen=True, slots=True)
class Coefficients:
    """The smoothed animation controls at one instant."""

    preset: Preset
    intensity: float
    eye_contact: float
    head_motion: float
    brow_activity: float
    smile: float
    blink_rate_scale: float


@dataclass(slots=True)
class PerformanceSmoother:
    """Turns a stream of performance directions into values safe to animate."""

    min_hold_ms: int = 900
    hysteresis: float = 0.12
    decay_per_second: float = 0.35
    resting_intensity: float = 0.10

    preset: Preset = Preset.NEUTRAL_ATTENTIVE
    intensity: float = 0.10

    _target_intensity: float = 0.10
    _overrides: dict[str, float] = field(default_factory=dict)
    _current: dict[str, float] = field(default_factory=dict)
    _changed_at: float = float("-inf")
    _transition_s: float = 0.45
    #: Directions refused by hysteresis. Worth watching -- a high count means the
    #: director is flapping and wants tuning, not that the smoother is working.
    refused: int = 0

    def __post_init__(self) -> None:
        if not self._current:
            self._current = dict(self._resolve_targets())

    # ----------------------------------------------------------------- #

    def apply(self, update: PerformanceUpdate, now: float) -> bool:
        """Accept or refuse a performance direction. Returns whether it landed.

        Intensity changes within the current preset are always accepted -- it is
        only *preset* changes that flap.
        """
        if update.preset != self.preset and not self._may_change(update, now):
            self.refused += 1
            return False

        if update.preset != self.preset:
            self.preset = update.preset
            self._changed_at = now

        self._target_intensity = update.intensity
        self._transition_s = max(0.001, update.transition_ms / 1000.0)
        self._overrides = {
            name: value
            for name, value in (
                ("eye_contact", update.eye_contact),
                ("head_motion", update.head_motion),
                ("brow_activity", update.brow_activity),
                ("smile", update.smile),
            )
            if value is not None
        }
        return True

    def tick(self, now: float, dt: float) -> Coefficients:
        """Advance toward the target and decay intensity. Call every frame."""
        if dt > 0:
            decay = math.exp(-self.decay_per_second * dt)
            self._target_intensity = (
                self.resting_intensity + (self._target_intensity - self.resting_intensity) * decay
            )
            self.intensity += (self._target_intensity - self.intensity) * self._factor(dt)

            targets = self._resolve_targets()
            factor = self._factor(dt)
            for name, target in targets.items():
                self._current[name] += (target - self._current[name]) * factor

        return Coefficients(
            preset=self.preset,
            intensity=self.intensity,
            eye_contact=self._current["eye_contact"],
            head_motion=self._current["head_motion"],
            brow_activity=self._current["brow_activity"],
            smile=self._current["smile"],
            blink_rate_scale=self._current["blink_rate_scale"],
        )

    # ----------------------------------------------------------------- #

    def _may_change(self, update: PerformanceUpdate, now: float) -> bool:
        held_for_ms = (now - self._changed_at) * 1000.0
        if held_for_ms >= self.min_hold_ms:
            return True
        # A markedly stronger signal overrides the hold: a genuine surprise
        # mid-sentence should land, a mild reclassification should not.
        #
        # Compared against the intensity last *asked for*, not the smoothed
        # value. The smoothed one lags near rest for a few hundred milliseconds
        # after a direction lands, which is precisely the window the hold
        # covers -- so comparing against it let almost any follow-up look like a
        # strong signal and defeated the hysteresis in the common case.
        return update.intensity >= self._target_intensity + self.hysteresis

    def _resolve_targets(self) -> dict[str, float]:
        shape: PresetShape = PRESET_SHAPES[self.preset]
        gain = self.intensity / REFERENCE_INTENSITY
        targets = {
            # Postural: taken as-is, not scaled by intensity.
            "eye_contact": shape.eye_contact,
            "head_motion": shape.head_motion,
            "blink_rate_scale": shape.blink_rate_scale,
            # Expressive: intensity is exactly what scales these.
            "brow_activity": min(1.0, shape.brow_activity * gain),
            "smile": min(1.0, shape.smile * gain),
        }
        targets.update(self._overrides)
        return targets

    def _factor(self, dt: float) -> float:
        """Exponential approach, framerate-independent."""
        return 1.0 - math.exp(-dt / (self._transition_s / 3.0))

"""The four idle subsystems from build plan section 9.

They run at different frequencies on purpose. The effect being chased is "this
character is currently doing nothing", not "an idle animation loop is playing",
and the difference is almost entirely whether the timings repeat.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from amanda.avatar.protocol import GazeTarget

# Where each gaze target sits, in degrees of (yaw, pitch) from straight ahead.
# Small numbers: these are conversational glances, not head turns.
GAZE_ANGLES: dict[GazeTarget, tuple[float, float]] = {
    GazeTarget.USER: (0.0, 0.0),
    GazeTarget.SLIGHTLY_LEFT: (-11.0, 1.0),
    GazeTarget.SLIGHTLY_RIGHT: (11.0, 1.0),
    GazeTarget.DOWN: (-2.0, -9.0),
    GazeTarget.DISTANT: (4.0, 3.0),
    GazeTarget.OBJECT_OF_INTEREST: (-16.0, -4.0),
}

# Relative likelihood of each target when the gaze controller is choosing for
# itself. USER is handled separately because its weight is the eye-contact
# fraction the conversation state asks for.
IDLE_TARGET_WEIGHTS: dict[GazeTarget, float] = {
    GazeTarget.SLIGHTLY_LEFT: 1.0,
    GazeTarget.SLIGHTLY_RIGHT: 1.0,
    GazeTarget.DOWN: 0.7,
    GazeTarget.DISTANT: 1.3,
}


def _lognormal_in(rng: random.Random, low: float, high: float) -> float:
    """A right-skewed interval within [low, high].

    Uniform intervals are the classic tell: they produce a suspiciously even
    rhythm because short and long gaps are equally likely. Real inter-blink and
    inter-saccade times cluster low with an occasional long tail, so a
    log-normal shape reads as human where a flat one does not.
    """
    span = high - low
    mu = math.log(span * 0.42)
    for _ in range(8):
        value = low + rng.lognormvariate(mu, 0.55)
        if value <= high:
            return value
    return low + span * 0.5


# --------------------------------------------------------------------------- #
# Blink
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class BlinkScheduler:
    """Eyelids. Independent of speech (build plan 18).

    Rate responds to cognitive state -- people blink less while concentrating --
    but never to sentence boundaries, which is the thing that reads as
    animation.
    """

    rng: random.Random
    interval_range: tuple[float, float] = (2.4, 6.8)
    close_ms: float = 62.0
    open_ms: float = 118.0
    double_blink_probability: float = 0.12
    saccade_blink_probability: float = 0.22

    _next_at: float = 0.0
    _blink_started: float | None = None
    _queued: int = 0
    _rate_scale: float = 1.0
    _last_interval: float = 0.0
    _started: bool = False

    def start(self, now: float) -> None:
        self._started = True
        self._schedule(now)

    def set_rate_scale(self, scale: float) -> None:
        """Above 1.0 means longer gaps -- less blinking, more concentration."""
        self._rate_scale = max(0.25, min(4.0, scale))

    def on_gaze_shift(self, now: float, magnitude_deg: float) -> None:
        """A large saccade often carries a blink with it. Not always, and never
        on small corrective movements."""
        if magnitude_deg < 6.0 or self._blink_started is not None:
            return
        if self.rng.random() < self.saccade_blink_probability:
            self._trigger(now)

    def update(self, now: float) -> float:
        """Return lid openness: 1.0 fully open, 0.0 fully closed."""
        if not self._started:
            self.start(now)

        if self._blink_started is None and now >= self._next_at:
            if self.rng.random() < self.double_blink_probability:
                self._queued = 1
            self._trigger(now)

        if self._blink_started is None:
            return 1.0

        elapsed_ms = (now - self._blink_started) * 1000.0
        if elapsed_ms < self.close_ms:
            return 1.0 - _ease_out(elapsed_ms / self.close_ms)
        if elapsed_ms < self.close_ms + self.open_ms:
            return _ease_out((elapsed_ms - self.close_ms) / self.open_ms)

        self._blink_started = None
        if self._queued:
            self._queued -= 1
            self._trigger(now + 0.04)
        else:
            self._schedule(now)
        return 1.0

    @property
    def last_interval(self) -> float:
        return self._last_interval

    def _trigger(self, now: float) -> None:
        self._blink_started = now

    def _schedule(self, now: float) -> None:
        low, high = self.interval_range
        self._last_interval = _lognormal_in(self.rng, low, high) * self._rate_scale
        self._next_at = now + self._last_interval


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) * (1.0 - t)


# --------------------------------------------------------------------------- #
# Gaze
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class GazeScheduler:
    """Eyes and head aim (build plan 8).

    Two things make this read as alive rather than mechanical. Micro-saccades:
    the eyes never sit perfectly still even on a held target. And head lag: the
    eyes arrive first and the head follows, taking only a fraction of the angle.
    Moving them together in lockstep is one of the most reliable ways to look
    synthetic.

    `gaze.set_target` from the orchestrator is a request, not a command --
    protocol v1 makes `hold_ms` advisory precisely so the stochastic timing can
    live here.
    """

    rng: random.Random
    shift_interval: tuple[float, float] = (1.2, 4.5)
    saccade_interval: tuple[float, float] = (0.35, 1.6)
    saccade_amplitude: float = 1.4
    eye_speed: float = 22.0  # degrees per frame-second; saccades are fast
    head_follow: float = 0.34  # fraction of the eye angle the head takes
    head_speed: float = 3.2

    target: GazeTarget = GazeTarget.USER
    eye_contact: float = 0.7

    _aim: tuple[float, float] = (0.0, 0.0)
    _eye: tuple[float, float] = (0.0, 0.0)
    _head: tuple[float, float] = (0.0, 0.0)
    _next_shift_at: float = 0.0
    _next_saccade_at: float = 0.0
    _offset: tuple[float, float] = (0.0, 0.0)
    _history: list[GazeTarget] = field(default_factory=list)
    _last_shift_magnitude: float = 0.0
    _started: bool = False

    def start(self, now: float) -> None:
        self._started = True
        self._aim = GAZE_ANGLES[self.target]
        self._eye = self._aim
        self._history.append(self.target)
        self._next_shift_at = now + _lognormal_in(self.rng, *self.shift_interval)
        self._next_saccade_at = now + _lognormal_in(self.rng, *self.saccade_interval)

    def command(self, now: float, target: GazeTarget, hold_ms: int) -> None:
        """Honour an orchestrator request, then resume self-scheduling.

        `hold_ms` of 0 means "until told otherwise", which here means "until the
        scheduler's own next shift" -- the renderer keeps owning the timing.
        """
        self._set_target(now, target)
        self._next_shift_at = now + ((hold_ms / 1000.0) if hold_ms else self._draw_hold())

    def set_eye_contact(self, fraction: float) -> None:
        self.eye_contact = max(0.0, min(1.0, fraction))

    def update(self, now: float, dt: float) -> None:
        if not self._started:
            self.start(now)

        if now >= self._next_shift_at:
            self._set_target(now, self._choose_target())
            self._next_shift_at = now + self._draw_hold()

        if now >= self._next_saccade_at:
            amp = self.saccade_amplitude
            self._offset = (
                self.rng.uniform(-amp, amp),
                self.rng.uniform(-amp * 0.6, amp * 0.6),
            )
            self._next_saccade_at = now + _lognormal_in(self.rng, *self.saccade_interval)

        goal = (self._aim[0] + self._offset[0], self._aim[1] + self._offset[1])
        self._eye = _approach(self._eye, goal, self.eye_speed * dt)

        head_goal = (self._eye[0] * self.head_follow, self._eye[1] * self.head_follow)
        self._head = _approach(self._head, head_goal, self.head_speed * dt)

    @property
    def eye_angles(self) -> tuple[float, float]:
        return self._eye

    @property
    def head_angles(self) -> tuple[float, float]:
        return self._head

    @property
    def history(self) -> list[GazeTarget]:
        return list(self._history)

    def consume_shift(self) -> float:
        """Magnitude of a shift that began since the last call, then zero.

        One-shot on purpose. Exposing it as a level meant every frame after a
        saccade looked like a fresh shift, and the blink scheduler fired on
        each one -- six times the human blink rate, in a pattern that came from
        the frame clock rather than anything human.
        """
        magnitude = self._last_shift_magnitude
        self._last_shift_magnitude = 0.0
        return magnitude

    def _set_target(self, now: float, target: GazeTarget) -> None:
        previous = GAZE_ANGLES[self.target]
        self.target = target
        self._aim = GAZE_ANGLES[target]
        self._last_shift_magnitude = math.dist(previous, self._aim)
        self._history.append(target)
        del self._history[:-256]

    def _draw_hold(self) -> float:
        """How long to hold the current target.

        Eye contact is a fraction of *time*, so it belongs here as much as in
        the choice of target: looking at someone means resting on them longer,
        not returning to them more often.
        """
        base = _lognormal_in(self.rng, *self.shift_interval)
        if self.target is GazeTarget.USER:
            return base * (1.0 + 1.6 * self.eye_contact)
        return base * (1.3 - 0.5 * self.eye_contact)

    def _choose_target(self) -> GazeTarget:
        """Weighted choice, with USER weighted by the eye-contact fraction.

        Never returns the current target -- holding by re-picking the same place
        would show up as a suspiciously long fixation.

        USER's weight is deliberately modest even at high eye contact. Weighting
        it by contact/(1-contact) makes the sequence alternate user, away, user,
        away almost perfectly, which is a visible pattern however random each
        individual choice was. People glance away twice in a row; the time
        fraction is recovered in `_draw_hold` instead.
        """
        weights: dict[GazeTarget, float] = dict(IDLE_TARGET_WEIGHTS)
        total_idle = sum(weights.values())
        weights[GazeTarget.USER] = total_idle * (0.5 + self.eye_contact)
        weights.pop(self.target, None)

        options = list(weights)
        return self.rng.choices(options, weights=[weights[o] for o in options])[0]


def _approach(
    current: tuple[float, float], goal: tuple[float, float], rate: float
) -> tuple[float, float]:
    factor = 1.0 - math.exp(-max(0.0, rate))
    return (
        current[0] + (goal[0] - current[0]) * factor,
        current[1] + (goal[1] - current[1]) * factor,
    )


# --------------------------------------------------------------------------- #
# Breath
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class BreathScheduler:
    """Chest and shoulders, plus a trace of it in the head.

    Inhale is quicker than exhale, and the period wanders slightly between
    cycles. A fixed-period sine is visible within about twenty seconds.
    """

    rng: random.Random
    period: float = 4.2
    period_jitter: float = 0.5
    inhale_fraction: float = 0.38

    _cycle_started: float = 0.0
    _current_period: float = 4.2
    _started: bool = False

    def start(self, now: float) -> None:
        self._started = True
        self._cycle_started = now
        self._current_period = self._draw()

    def update(self, now: float) -> float:
        """Return chest expansion, 0.0 empty to 1.0 full."""
        if not self._started:
            self.start(now)

        phase = (now - self._cycle_started) / self._current_period
        if phase >= 1.0:
            self._cycle_started = now
            self._current_period = self._draw()
            phase = 0.0

        if phase < self.inhale_fraction:
            return _ease_in_out(phase / self.inhale_fraction)
        return 1.0 - _ease_in_out((phase - self.inhale_fraction) / (1.0 - self.inhale_fraction))

    def _draw(self) -> float:
        return self.period + self.rng.uniform(-self.period_jitter, self.period_jitter)


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class DriftScheduler:
    """Microscopic head drift that never repeats.

    Three sines whose frequencies are in irrational ratios, so the sum has no
    period. A single sine, or several with rational ratios, produces a loop the
    eye finds within a minute -- which is exactly the failure the build plan's
    sixty-second stillness test is designed to catch.
    """

    rng: random.Random
    amplitude: float = 0.9  # degrees

    _phases: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _rates: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _started: bool = False

    def start(self, now: float) -> None:
        self._started = True
        self._phases = (
            self.rng.uniform(0, math.tau),
            self.rng.uniform(0, math.tau),
            self.rng.uniform(0, math.tau),
        )
        base = self.rng.uniform(0.055, 0.085)
        self._rates = (base, base * math.sqrt(2), base * math.sqrt(5))

    def update(self, now: float) -> tuple[float, float, float]:
        """Return (yaw, pitch, roll) offsets in degrees."""
        if not self._started:
            self.start(now)

        a, b, c = (
            math.sin(now * math.tau * rate + phase)
            for rate, phase in zip(self._rates, self._phases, strict=True)
        )
        return (
            self.amplitude * (a * 0.6 + c * 0.4),
            self.amplitude * 0.7 * (b * 0.7 + a * 0.3),
            self.amplitude * 0.4 * c,
        )

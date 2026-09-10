"""Tests for the presence schedulers and the performance smoother.

The point of having this logic in Python is that claims like "blinking looks
natural" become measurable. These tests run tens of minutes of behaviour in
milliseconds by driving an explicit clock.
"""

from __future__ import annotations

import pytest

from amanda.avatar.protocol import (
    AvatarReset,
    GazeSetTarget,
    GazeTarget,
    PerformanceUpdate,
    Preset,
    SpeechCancelled,
    SpeechStarted,
)
from amanda.performance.schema import PRESET_SHAPES
from amanda.performance.smoothing import PerformanceSmoother
from amanda.presence import PresenceEngine
from amanda.presence.analysis import (
    dwell_fractions,
    interval_report,
    longest_repeated_run,
)

FRAME = 1.0 / 60.0


def run(engine: PresenceEngine, seconds: float, start: float = 0.0):
    """Drive the engine at 60 Hz and collect every frame."""
    states = []
    now = start
    while now < start + seconds:
        states.append(engine.tick(now))
        now += FRAME
    return states


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_same_seed_gives_identical_behaviour():
    """Without this, none of the other measurements are reproducible."""
    first = [s.to_dict() for s in run(PresenceEngine(seed=42), 60)]
    second = [s.to_dict() for s in run(PresenceEngine(seed=42), 60)]
    assert first == second


def test_different_seeds_diverge():
    first = PresenceEngine(seed=1)
    second = PresenceEngine(seed=2)
    assert [s.to_dict() for s in run(first, 60)] != [s.to_dict() for s in run(second, 60)]


# --------------------------------------------------------------------------- #
# Blinking
# --------------------------------------------------------------------------- #


def test_blink_rate_is_humanly_plausible():
    """Adults blink roughly 10-20 times a minute at rest.

    The lower bound was 12 until the schedulers were slowed on 2026-09-10. What
    changed was not the science but the face: the previsualiser draws eyelids as
    a shape opening and closing, where 21 a minute looks unremarkable, and on a
    rendered MetaHuman the same rate reads as nervous. Rob watched it and said
    so; this band now spans what a person does at rest rather than what looked
    right on a schematic.
    """
    engine = PresenceEngine(seed=3)
    run(engine, 600)
    per_minute = len(engine.blink_times) / 10.0
    assert 9 <= per_minute <= 22, f"{per_minute:.1f} blinks/min is not a human rate"


def test_blink_timing_is_not_metronomic():
    """The failure this guards against is a visible rhythm, which the eye finds
    long before the viewer could describe it."""
    engine = PresenceEngine(seed=4)
    run(engine, 600)
    report = interval_report(engine.blink_times)
    assert report.verdict == "irregular", f"cv {report.cv:.2f}, rhythm {report.rhythm:.2f}"


def test_blinks_are_not_driven_by_the_frame_clock():
    """Regression: a gaze-shift magnitude exposed as a level rather than an edge
    made the blink scheduler fire on every frame after a saccade."""
    slow = PresenceEngine(seed=5)
    run(slow, 300)
    assert len(slow.blink_times) < 120, "far more blinks than five minutes should contain"


def test_concentrating_slows_blinking():
    """Blink rate responds to cognitive state but never to sentence boundaries."""
    counts = {}
    for preset in (Preset.LISTENING, Preset.CONSIDERING):
        engine = PresenceEngine(seed=6)
        engine.handle(PerformanceUpdate(preset=preset, intensity=0.3), 0.0)
        run(engine, 600)
        counts[preset] = len(engine.blink_times)
    assert counts[Preset.CONSIDERING] < counts[Preset.LISTENING]


def test_a_blink_fully_closes_and_reopens():
    engine = PresenceEngine(seed=7)
    lids = [s.lid_open for s in run(engine, 60)]
    assert min(lids) < 0.05, "eyes never actually close"
    assert lids[-1] > 0.9, "eyes left half shut"


# --------------------------------------------------------------------------- #
# Gaze
# --------------------------------------------------------------------------- #


def test_gaze_never_shifts_to_where_it_already_is():
    engine = PresenceEngine(seed=8)
    run(engine, 600)
    history = engine.gaze.history
    assert all(a != b for a, b in zip(history, history[1:], strict=False))


def test_gaze_does_not_strictly_alternate_with_the_user():
    """Regression: weighting USER by contact/(1-contact) produced a perfect
    user, away, user, away sequence -- random in each choice, patterned overall."""
    engine = PresenceEngine(seed=9)
    run(engine, 600)
    history = [t.value for t in engine.gaze.history]
    away_runs = 0
    for first, second in zip(history, history[1:], strict=False):
        if first != "user" and second != "user":
            away_runs += 1
    assert away_runs > 10, "the eyes never glance away twice in a row"


def test_gaze_sequence_repetition_stays_within_chance():
    engine = PresenceEngine(seed=10)
    run(engine, 900)
    report = longest_repeated_run([t.value for t in engine.gaze.history])
    assert report.verdict == "within chance", (
        f"repeat of {report.length} vs baseline {report.baseline:.1f}: {report.pattern}"
    )


def test_eye_contact_setting_moves_dwell_time():
    """Eye contact is a fraction of time, so raising it must lengthen the time
    spent on the user, not merely the number of glances toward them."""
    dwell = {}
    for preset in (Preset.CONSIDERING, Preset.LISTENING):
        engine = PresenceEngine(seed=11)
        engine.handle(PerformanceUpdate(preset=preset, intensity=0.3), 0.0)
        states = run(engine, 900)
        dwell[preset] = dwell_fractions([(s.t, s.gaze_target) for s in states]).get("user", 0.0)

    assert dwell[Preset.LISTENING] > dwell[Preset.CONSIDERING] + 0.2, dwell
    assert dwell[Preset.LISTENING] > 0.5
    assert dwell[Preset.CONSIDERING] < 0.4


def test_the_head_lags_the_eyes():
    """Eyes arrive first and the head follows, taking a fraction of the angle.
    Moving them together is one of the most reliable ways to look synthetic."""
    engine = PresenceEngine(seed=12)
    states = run(engine, 120)
    moving = [s for s in states if abs(s.eye_yaw) > 4]
    assert moving, "no sizeable gaze excursions to test"
    assert all(abs(s.head_yaw) < abs(s.eye_yaw) for s in moving)


def test_a_commanded_target_is_honoured():
    engine = PresenceEngine(seed=13)
    engine.handle(GazeSetTarget(target=GazeTarget.DOWN, hold_ms=1500), 0.0)
    assert engine.gaze.target is GazeTarget.DOWN
    state = engine.tick(0.4)
    assert state.eye_pitch < -2, "the eyes did not travel toward the commanded target"


# --------------------------------------------------------------------------- #
# Breath and drift
# --------------------------------------------------------------------------- #


def test_breathing_stays_in_range_and_cycles():
    engine = PresenceEngine(seed=14)
    breaths = [s.breath for s in run(engine, 120)]
    assert all(0.0 <= b <= 1.0 for b in breaths)
    assert min(breaths) < 0.1 and max(breaths) > 0.9


def test_head_drift_never_repeats():
    """Three sines in irrational frequency ratios have no period. A loop here is
    exactly what the sixty-second stillness test is meant to catch."""
    engine = PresenceEngine(seed=15)
    states = run(engine, 600)
    early = [round(s.head_roll, 4) for s in states[:600]]
    late = [round(s.head_roll, 4) for s in states[-600:]]
    assert early != late
    assert len({round(s.head_roll, 3) for s in states}) > 200


# --------------------------------------------------------------------------- #
# Performance smoothing
# --------------------------------------------------------------------------- #


def test_a_rapid_preset_change_is_refused():
    """The avatar must not snap from concerned to happy because the next
    sentence contains a joke."""
    smoother = PerformanceSmoother(min_hold_ms=900)
    assert smoother.apply(PerformanceUpdate(preset=Preset.CONCERNED, intensity=0.3), 0.0)
    smoother.tick(0.0, 0.0)
    assert not smoother.apply(PerformanceUpdate(preset=Preset.WARM, intensity=0.3), 0.3)
    assert smoother.preset is Preset.CONCERNED
    assert smoother.refused == 1


def test_the_hold_expires():
    smoother = PerformanceSmoother(min_hold_ms=900)
    smoother.apply(PerformanceUpdate(preset=Preset.CONCERNED, intensity=0.3), 0.0)
    assert smoother.apply(PerformanceUpdate(preset=Preset.WARM, intensity=0.3), 1.0)
    assert smoother.preset is Preset.WARM


def test_a_strong_signal_overrides_the_hold():
    """A genuine surprise mid-sentence should land; a mild reclassification
    should not."""
    smoother = PerformanceSmoother(min_hold_ms=900, hysteresis=0.12)
    smoother.apply(PerformanceUpdate(preset=Preset.WARM, intensity=0.2), 0.0)
    smoother.tick(0.0, 0.0)
    assert smoother.apply(PerformanceUpdate(preset=Preset.SURPRISED, intensity=0.6), 0.2)
    assert smoother.preset is Preset.SURPRISED


def test_intensity_decays_toward_rest():
    smoother = PerformanceSmoother(resting_intensity=0.1)
    smoother.apply(PerformanceUpdate(preset=Preset.SURPRISED, intensity=0.6), 0.0)
    for step in range(1, 121):
        coefficients = smoother.tick(step * 0.1, 0.1)
    assert coefficients.intensity == pytest.approx(0.1, abs=0.02)


def test_transitions_do_not_snap():
    smoother = PerformanceSmoother()
    smoother.apply(
        PerformanceUpdate(preset=Preset.ENTHUSIASTIC, intensity=0.4, transition_ms=600), 0.0
    )
    first = smoother.tick(FRAME, FRAME)
    assert first.smile < PRESET_SHAPES[Preset.ENTHUSIASTIC].smile * 0.5


def test_overrides_replace_the_preset_shape():
    smoother = PerformanceSmoother()
    smoother.apply(
        PerformanceUpdate(preset=Preset.WARM, intensity=0.3, eye_contact=0.05), 0.0
    )
    for step in range(1, 60):
        coefficients = smoother.tick(step * 0.05, 0.05)
    assert coefficients.eye_contact == pytest.approx(0.05, abs=0.02)


# --------------------------------------------------------------------------- #
# Engine and protocol
# --------------------------------------------------------------------------- #


def test_the_face_is_never_symmetrical():
    """Perfectly symmetrical facial movement reads as synthetic."""
    engine = PresenceEngine(seed=16)
    engine.handle(PerformanceUpdate(preset=Preset.SURPRISED, intensity=0.4), 0.0)
    states = run(engine, 3)
    assert all(s.brow_left != s.brow_right for s in states[10:])


def test_speech_state_follows_the_protocol():
    engine = PresenceEngine(seed=17)
    engine.handle(SpeechStarted(utterance_id="u_1"), 0.0)
    assert engine.tick(0.1).speaking
    engine.handle(SpeechCancelled(utterance_id="u_1"), 0.2)
    assert not engine.tick(0.3).speaking


def test_reset_settles_the_performance_but_keeps_the_character_alive():
    """A reconnect should not make the avatar freeze and start over."""
    engine = PresenceEngine(seed=18)
    engine.handle(PerformanceUpdate(preset=Preset.ENTHUSIASTIC, intensity=0.5), 0.0)
    run(engine, 2)
    blinks_before = len(engine.blink_times)

    engine.handle(AvatarReset(), 2.0)
    state = engine.tick(2.1)
    assert state.preset == Preset.NEUTRAL_ATTENTIVE.value
    assert not state.speaking

    run(engine, 60, start=2.1)
    assert len(engine.blink_times) > blinks_before, "the schedulers stopped on reset"


def test_unknown_payloads_are_ignored():
    from amanda.avatar.protocol import SessionStarted

    engine = PresenceEngine(seed=19)
    engine.handle(SessionStarted(session_id="s_1"), 0.0)
    assert engine.tick(0.1) is not None


# --------------------------------------------------------------------------- #
# The diagnostics themselves
# --------------------------------------------------------------------------- #


def test_interval_report_flags_a_metronome():
    report = interval_report([i * 3.0 for i in range(40)])
    assert report.verdict == "metronomic"


def test_interval_report_accepts_irregular_timing():
    import random

    rng = random.Random(1)
    times, clock = [], 0.0
    for _ in range(80):
        clock += rng.lognormvariate(1.0, 0.6)
        times.append(clock)
    assert interval_report(times).verdict == "irregular"


def test_repetition_report_flags_a_real_cycle():
    report = longest_repeated_run(["a", "b", "c", "d"] * 12)
    assert report.verdict == "visibly repeating"


def test_repetition_report_tolerates_chance_collisions():
    """Draw a few hundred symbols from five options and some run of six will
    repeat every time. Without a baseline the metric only produces alarm."""
    import random

    rng = random.Random(2)
    sequence = [rng.choice("abcde") for _ in range(300)]
    assert longest_repeated_run(sequence).verdict == "within chance"


def test_dwell_fractions_measure_time_not_visits():
    samples = [(0.0, "user"), (9.0, "away"), (10.0, "user")]
    fractions = dwell_fractions(samples)
    assert fractions["user"] == pytest.approx(0.9)

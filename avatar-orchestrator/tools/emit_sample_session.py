#!/usr/bin/env python3
"""Emit one realistic conversational turn as newline-delimited protocol v1.

Lets the renderer be built and tuned against the real wire format before the
orchestrator can produce it -- no Claude, no TTS, no microphone. Timestamps are
relative to a fixed start so replays are deterministic.

    python3 tools/emit_sample_session.py > session.ndjson
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amanda.avatar import protocol as p  # noqa: E402

START = 1788967200.0

# (offset seconds, payload). The offsets are a plausible latency budget:
# the user stops speaking at t=6.0 and the avatar starts at t=7.4, so
# T6 - T0 is 1.4s, with the thinking behaviour covering the gap.
SESSION: list[tuple[float, p.Payload]] = [
    (0.000, p.SessionStarted(session_id="s_demo")),
    (0.010, p.AvatarReset()),
    (0.020, p.PerformanceUpdate(preset=p.Preset.NEUTRAL_ATTENTIVE, intensity=0.10)),
    (0.030, p.GazeSetTarget(target=p.GazeTarget.DISTANT, hold_ms=0)),

    # The user arrives.
    (3.000, p.UserDetected(present=True)),
    (3.050, p.PerformanceUpdate(preset=p.Preset.LISTENING, intensity=0.18, eye_contact=0.80)),
    (3.100, p.GazeSetTarget(target=p.GazeTarget.USER, hold_ms=2600)),

    # The user speaks for six seconds.
    (3.400, p.UserSpeechStarted()),
    (5.100, p.GazeSetTarget(target=p.GazeTarget.SLIGHTLY_LEFT, hold_ms=700)),
    (5.800, p.GazeSetTarget(target=p.GazeTarget.USER, hold_ms=0)),
    (6.000, p.UserSpeechEnded(duration_ms=2600)),

    # ACKNOWLEDGING. A nod here marks a specific acknowledgement -- it is not a
    # perpetual listening loop (build plan 18).
    (6.060, p.GestureTrigger(gesture="small_nod", intensity=0.14)),

    # T0. Gaze goes off-axis and the head stills -- this is what covers the
    # latency, not a verbal filler.
    (6.120, p.AssistantThinkingStarted()),
    (6.150, p.PerformanceUpdate(
        preset=p.Preset.CONSIDERING,
        intensity=0.28,
        transition_ms=520,
        eye_contact=0.25,
        head_motion=0.06,
        brow_activity=0.10,
    )),
    (6.200, p.GazeSetTarget(target=p.GazeTarget.SLIGHTLY_RIGHT, hold_ms=900, transition_ms=340)),

    # First speakable phrase. Attention returns *before* audio starts.
    (7.150, p.SpeechPrepare(
        utterance_id="u_1042",
        preset=p.Preset.WARM,
        text="It rained most of the morning, but it's cleared up now.",
    )),
    (7.180, p.GazeSetTarget(target=p.GazeTarget.USER, hold_ms=0, transition_ms=260)),
    (7.200, p.AssistantThinkingEnded()),
    (7.220, p.PerformanceUpdate(
        preset=p.Preset.WARM, intensity=0.22, transition_ms=400, eye_contact=0.62, smile=0.08
    )),

    # T6.
    (7.400, p.SpeechStarted(utterance_id="u_1042", sample_rate=24_000)),
    (8.900, p.GazeSetTarget(target=p.GazeTarget.SLIGHTLY_LEFT, hold_ms=600)),
    (9.500, p.GazeSetTarget(target=p.GazeTarget.USER, hold_ms=0)),
    (10.800, p.SpeechCompleted(utterance_id="u_1042")),

    # Settling.
    (10.900, p.PerformanceUpdate(
        preset=p.Preset.NEUTRAL_ATTENTIVE, intensity=0.12, transition_ms=900
    )),

    # A second turn, interrupted mid-sentence.
    (13.000, p.UserSpeechStarted()),
    (14.900, p.UserSpeechEnded(duration_ms=1900)),
    (15.000, p.AssistantThinkingStarted()),
    (15.900, p.SpeechPrepare(utterance_id="u_1043", preset=p.Preset.EXPLAINING)),
    (15.950, p.AssistantThinkingEnded()),
    (16.100, p.SpeechStarted(utterance_id="u_1043")),

    # Barge-in: the visual transition leads, the audio fade follows.
    (17.600, p.UserSpeechStarted()),
    (17.620, p.PerformanceUpdate(preset=p.Preset.LISTENING, intensity=0.20, transition_ms=140)),
    (17.640, p.SpeechCancelled(
        utterance_id="u_1043", reason=p.CancelReason.BARGE_IN, fade_ms=80
    )),
    (17.660, p.GazeSetTarget(target=p.GazeTarget.USER, hold_ms=0, transition_ms=160)),

    # The user leaves.
    (25.000, p.UserSpeechEnded(duration_ms=7400)),
    (40.000, p.UserDetected(present=False)),
    (40.100, p.PerformanceUpdate(
        preset=p.Preset.NEUTRAL_ATTENTIVE, intensity=0.06, transition_ms=2000
    )),
    (40.200, p.GazeSetTarget(target=p.GazeTarget.DISTANT, hold_ms=0, transition_ms=1200)),
    (45.000, p.SessionEnded(session_id="s_demo", reason="user absent")),
]


def main() -> int:
    for offset, payload in SESSION:
        print(p.encode(payload, timestamp=round(START + offset, 3)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

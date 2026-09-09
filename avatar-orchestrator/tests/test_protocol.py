"""Protocol v1 conformance tests.

These double as the specification the renderer has to satisfy: if a change here
needs editing, it is probably a protocol version bump rather than a fix.
"""

from __future__ import annotations

import json

import pytest

from amanda.avatar import protocol as p

# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #


def test_envelope_matches_the_documented_shape():
    raw = p.encode(
        p.PerformanceUpdate(preset=p.Preset.MILDLY_AMUSED, intensity=0.22, transition_ms=450),
        timestamp=1788967200.125,
    )
    assert json.loads(raw) == {
        "version": 1,
        "event": "performance.update",
        "timestamp": 1788967200.125,
        "payload": {"preset": "mildly_amused", "intensity": 0.22, "transition_ms": 450},
    }


def test_encode_stamps_the_current_time_by_default():
    envelope = p.decode(p.encode(p.AvatarReset()))
    assert envelope.timestamp > 0


def test_encode_is_single_line():
    """The transport may be newline-delimited; a payload must never wrap."""
    assert "\n" not in p.encode(p.SpeechPrepare(utterance_id="u_1", text="hello\nthere"))


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[1, 2, 3]",
        '"a string"',
        "null",
    ],
)
def test_decode_rejects_non_objects(raw):
    with pytest.raises(p.MalformedMessageError):
        p.decode(raw)


@pytest.mark.parametrize("version", [0, 2, "1", None, 1.5])
def test_decode_rejects_other_protocol_versions(version):
    raw = json.dumps({"version": version, "event": "avatar.reset", "payload": {}})
    with pytest.raises(p.UnsupportedVersionError):
        p.decode(raw)


@pytest.mark.parametrize(
    "message",
    [
        {"version": 1, "payload": {}},
        {"version": 1, "event": 7, "payload": {}},
        {"version": 1, "event": "avatar.reset", "payload": []},
        {"version": 1, "event": "avatar.reset", "timestamp": "soon"},
    ],
)
def test_decode_rejects_bad_envelope_fields(message):
    with pytest.raises(p.MalformedMessageError):
        p.decode(json.dumps(message))


def test_payload_and_timestamp_are_optional_on_the_wire():
    envelope = p.decode('{"version": 1, "event": "avatar.reset"}')
    assert envelope.payload == {}
    assert envelope.timestamp == 0.0
    assert isinstance(envelope.parse(), p.AvatarReset)


# --------------------------------------------------------------------------- #
# Round-tripping
# --------------------------------------------------------------------------- #


ALL_PAYLOADS = [
    p.SessionStarted(session_id="s_1"),
    p.SessionEnded(session_id="s_1", reason="user quit"),
    p.SessionEnded(session_id="s_1"),
    p.UserDetected(present=True),
    p.UserDetected(present=False),
    p.UserSpeechStarted(),
    p.UserSpeechEnded(duration_ms=2400),
    p.UserSpeechEnded(),
    p.AssistantThinkingStarted(),
    p.AssistantThinkingEnded(),
    p.SpeechPrepare(utterance_id="u_1", preset=p.Preset.WARM, text="Morning, Rob."),
    p.SpeechPrepare(utterance_id="u_1"),
    p.SpeechStarted(utterance_id="u_1", sample_rate=48_000, duration_ms=1800),
    p.SpeechStarted(utterance_id="u_1"),
    p.SpeechCompleted(utterance_id="u_1"),
    p.SpeechCancelled(
        utterance_id="u_1", reason=p.CancelReason.BARGE_IN, fade_ms=60
    ),
    p.PerformanceUpdate(preset=p.Preset.CONSIDERING, intensity=0.28),
    p.PerformanceUpdate(
        preset=p.Preset.CONSIDERING,
        intensity=0.28,
        eye_contact=0.55,
        head_motion=0.18,
        brow_activity=0.12,
        smile=0.03,
        gesture_probability=0.08,
    ),
    p.GazeSetTarget(target=p.GazeTarget.DISTANT, hold_ms=1800),
    p.GestureTrigger(gesture="small_nod", intensity=0.15),
    p.AvatarReset(),
]


@pytest.mark.parametrize("payload", ALL_PAYLOADS, ids=lambda payload: type(payload).__name__)
def test_every_payload_survives_a_round_trip(payload):
    assert p.decode(p.encode(payload)).parse() == payload


def test_every_event_type_has_a_payload_class():
    assert set(p.PAYLOAD_TYPES) == set(p.EventType)


def test_every_payload_class_is_covered_by_the_round_trip_cases():
    covered = {type(payload).event for payload in ALL_PAYLOADS}
    assert covered == set(p.EventType)


# --------------------------------------------------------------------------- #
# Forward compatibility
# --------------------------------------------------------------------------- #


def test_unknown_payload_fields_are_ignored():
    """A newer orchestrator may add optional fields without a version bump."""
    payload = {"preset": "warm", "intensity": 0.3, "jaw_tension": 0.4}
    raw = json.dumps(
        {"version": 1, "event": "performance.update", "timestamp": 0.0, "payload": payload}
    )
    assert p.decode(raw).parse() == p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.3)


def test_unknown_events_decode_but_do_not_parse():
    """A receiver can route on `event` and skip what it does not implement."""
    raw = json.dumps({"version": 1, "event": "breath.hold", "timestamp": 0.0, "payload": {}})
    envelope = p.decode(raw)
    assert envelope.event == "breath.hold"
    assert not envelope.known
    with pytest.raises(p.UnknownEventError):
        envelope.parse()


def test_optional_fields_are_omitted_rather_than_sent_as_null():
    payload = json.loads(p.encode(p.SpeechPrepare(utterance_id="u_1")))["payload"]
    assert payload == {"utterance_id": "u_1"}


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [-0.1, 1.1, 42])
def test_animation_coefficients_outside_0_to_1_are_rejected(value):
    """Rejected, not clamped -- a director emitting 1.4 has a bug worth seeing."""
    payload = {"preset": "warm", "intensity": value}
    raw = json.dumps({"version": 1, "event": "performance.update", "payload": payload})
    with pytest.raises(p.InvalidPayloadError, match="intensity"):
        p.decode(raw).parse()


@pytest.mark.parametrize(
    ("event", "payload", "missing"),
    [
        ("session.started", {}, "session_id"),
        ("user.detected", {}, "present"),
        ("speech.started", {}, "utterance_id"),
        ("performance.update", {"intensity": 0.2}, "preset"),
        ("performance.update", {"preset": "warm"}, "intensity"),
        ("gaze.set_target", {}, "target"),
        ("gesture.trigger", {}, "gesture"),
    ],
)
def test_missing_required_fields_are_rejected(event, payload, missing):
    raw = json.dumps({"version": 1, "event": event, "payload": payload})
    with pytest.raises(p.InvalidPayloadError, match=missing):
        p.decode(raw).parse()


def test_unknown_enum_values_are_rejected_with_the_allowed_set():
    payload = {"preset": "smug", "intensity": 0.2}
    raw = json.dumps({"version": 1, "event": "performance.update", "payload": payload})
    with pytest.raises(p.InvalidPayloadError) as caught:
        p.decode(raw).parse()
    assert "smug" in str(caught.value)
    assert "neutral_attentive" in str(caught.value)


def test_booleans_are_not_accepted_as_numbers():
    """bool is an int in Python; the wire format should not inherit that."""
    payload = {"preset": "warm", "intensity": True}
    raw = json.dumps({"version": 1, "event": "performance.update", "payload": payload})
    with pytest.raises(p.InvalidPayloadError):
        p.decode(raw).parse()


def test_negative_durations_are_rejected():
    payload = {"target": "user", "hold_ms": -5}
    raw = json.dumps({"version": 1, "event": "gaze.set_target", "payload": payload})
    with pytest.raises(p.InvalidPayloadError, match="hold_ms"):
        p.decode(raw).parse()


def test_presence_must_be_a_real_boolean():
    raw = json.dumps({"version": 1, "event": "user.detected", "payload": {"present": 1}})
    with pytest.raises(p.InvalidPayloadError, match="present"):
        p.decode(raw).parse()


# --------------------------------------------------------------------------- #
# The replayable sample session
# --------------------------------------------------------------------------- #


def test_sample_session_is_valid_protocol_v1():
    """The file the renderer is built against must never drift from the spec."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "tools" / "emit_sample_session.py"
    lines = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert lines, "sample session emitted nothing"
    events = set()
    previous = 0.0
    for line in lines:
        envelope = p.decode(line)
        envelope.parse()
        assert envelope.timestamp >= previous, "sample session events must be ordered in time"
        previous = envelope.timestamp
        events.add(envelope.event)

    missing = set(p.EventType) - events
    assert not missing, f"sample session never exercises: {missing}"

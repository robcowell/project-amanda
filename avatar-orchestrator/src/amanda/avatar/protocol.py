"""Amanda avatar protocol, version 1.

The wire format between the orchestrator and whatever is rendering the avatar.
Deliberately engine-neutral: nothing here knows about Unreal, MetaHuman or
Blueprints, and nothing here imports a third-party package. The renderer side is
written in another language, so this module is the reference implementation
rather than the shared one -- see PROTOCOL.md for the language-neutral spec.

Every message is a single JSON object:

    {"version": 1, "event": "performance.update", "timestamp": 1788967200.125,
     "payload": {"preset": "mildly_amused", "intensity": 0.22,
                 "transition_ms": 450}}

Compatibility rules, fixed for the life of v1:

  * Receivers MUST ignore payload fields they do not recognise. New optional
    fields can therefore be added without a version bump.
  * Receivers SHOULD ignore whole events they do not recognise rather than
    dropping the connection, so a newer orchestrator can talk to an older
    renderer. `decode` supports this: it validates the envelope and leaves
    `Envelope.parse` to the caller.
  * Removing a field, renaming an event or changing a unit is a version bump.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

PROTOCOL_VERSION = 1


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ProtocolError(Exception):
    """Base class for anything wrong with a message on the wire."""


class MalformedMessageError(ProtocolError):
    """Not JSON, not an object, or missing/ill-typed envelope fields."""


class UnsupportedVersionError(ProtocolError):
    """Envelope carries a protocol version this build cannot interpret."""

    def __init__(self, got: object) -> None:
        super().__init__(f"unsupported protocol version {got!r}, expected {PROTOCOL_VERSION}")
        self.got = got


class UnknownEventError(ProtocolError):
    """Envelope is well-formed but names an event this build does not know."""

    def __init__(self, event: str) -> None:
        super().__init__(f"unknown event {event!r}")
        self.event = event


class InvalidPayloadError(ProtocolError):
    """Payload is missing a required field or a value is out of range."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"

    USER_DETECTED = "user.detected"
    USER_SPEECH_STARTED = "user.speech_started"
    USER_SPEECH_ENDED = "user.speech_ended"

    ASSISTANT_THINKING_STARTED = "assistant.thinking_started"
    ASSISTANT_THINKING_ENDED = "assistant.thinking_ended"

    SPEECH_PREPARE = "speech.prepare"
    SPEECH_STARTED = "speech.started"
    SPEECH_COMPLETED = "speech.completed"
    SPEECH_CANCELLED = "speech.cancelled"

    PERFORMANCE_UPDATE = "performance.update"
    GAZE_SET_TARGET = "gaze.set_target"
    GESTURE_TRIGGER = "gesture.trigger"
    AVATAR_RESET = "avatar.reset"


class Preset(StrEnum):
    """The restrained performance vocabulary (build plan 5.2).

    Phase 4 starts with a subset -- neutral_attentive, warm, considering,
    mildly_amused, concerned, uncertain, surprised -- and the renderer may map
    any preset it has not authored onto NEUTRAL_ATTENTIVE.
    """

    NEUTRAL_ATTENTIVE = "neutral_attentive"
    LISTENING = "listening"
    CONSIDERING = "considering"
    MILDLY_AMUSED = "mildly_amused"
    WARM = "warm"
    CONCERNED = "concerned"
    UNCERTAIN = "uncertain"
    CONFUSED = "confused"
    SURPRISED = "surprised"
    EXPLAINING = "explaining"
    ENTHUSIASTIC = "enthusiastic"
    SERIOUS = "serious"


class GazeTarget(StrEnum):
    """Where the eyes go (build plan 8). Angles stay renderer-side."""

    USER = "user"
    SLIGHTLY_LEFT = "slightly_left"
    SLIGHTLY_RIGHT = "slightly_right"
    DOWN = "down"
    DISTANT = "distant"
    OBJECT_OF_INTEREST = "object_of_interest"


class CancelReason(StrEnum):
    BARGE_IN = "barge_in"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    SUPERSEDED = "superseded"


# --------------------------------------------------------------------------- #
# Field coercion
# --------------------------------------------------------------------------- #


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise InvalidPayloadError(f"missing required field {key!r}")
    return payload[key]


def _as_str(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise InvalidPayloadError(f"{key!r} must be a string, got {type(value).__name__}")
    return value


def _as_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidPayloadError(f"{key!r} must be a boolean, got {type(value).__name__}")
    return value


def _as_unit(value: Any, key: str) -> float:
    """A 0.0-1.0 animation coefficient.

    Out-of-range values are rejected rather than clamped: a director emitting
    1.4 has a bug, and silently clamping it hides the bug behind an avatar that
    merely looks slightly wrong. Restraint (build plan 18) is the director's
    job -- the protocol only enforces the range.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPayloadError(f"{key!r} must be a number, got {type(value).__name__}")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise InvalidPayloadError(f"{key!r} must be within 0.0-1.0, got {value}")
    return value


def _as_duration_ms(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPayloadError(f"{key!r} must be a number, got {type(value).__name__}")
    if value < 0:
        raise InvalidPayloadError(f"{key!r} must not be negative, got {value}")
    return int(value)


def _as_enum[E: StrEnum](enum: type[E], value: Any, key: str) -> E:
    text = _as_str(value, key)
    try:
        return enum(text)
    except ValueError:
        allowed = ", ".join(sorted(m.value for m in enum))
        raise InvalidPayloadError(f"{key!r} must be one of: {allowed}; got {text!r}") from None


def _compact(**fields: Any) -> dict[str, Any]:
    """Drop keys whose value is None so optional fields stay off the wire."""
    return {key: value for key, value in fields.items() if value is not None}


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Payload:
    """Base class for every event payload.

    Subclasses declare `event` and implement `to_dict`/`from_dict`. `from_dict`
    reads only the fields it knows, which is what makes the ignore-unknown-fields
    rule above true by construction.
    """

    event: ClassVar[EventType]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Payload":
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _Empty(Payload):
    """An event that carries no data beyond its name and timestamp."""

    def to_dict(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "_Empty":
        return cls()


@dataclass(frozen=True, slots=True)
class SessionStarted(Payload):
    event = EventType.SESSION_STARTED

    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionStarted":
        return cls(session_id=_as_str(_require(payload, "session_id"), "session_id"))


@dataclass(frozen=True, slots=True)
class SessionEnded(Payload):
    event = EventType.SESSION_ENDED

    session_id: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(session_id=self.session_id, reason=self.reason)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionEnded":
        reason = payload.get("reason")
        return cls(
            session_id=_as_str(_require(payload, "session_id"), "session_id"),
            reason=None if reason is None else _as_str(reason, "reason"),
        )


@dataclass(frozen=True, slots=True)
class UserDetected(Payload):
    """Presence, not identity. `present` false is the user having walked away."""

    event = EventType.USER_DETECTED

    present: bool

    def to_dict(self) -> dict[str, Any]:
        return {"present": self.present}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserDetected":
        return cls(present=_as_bool(_require(payload, "present"), "present"))


@dataclass(frozen=True, slots=True)
class UserSpeechStarted(_Empty):
    event = EventType.USER_SPEECH_STARTED


@dataclass(frozen=True, slots=True)
class UserSpeechEnded(Payload):
    event = EventType.USER_SPEECH_ENDED

    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(duration_ms=self.duration_ms)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserSpeechEnded":
        duration = payload.get("duration_ms")
        return cls(
            duration_ms=None if duration is None else _as_duration_ms(duration, "duration_ms")
        )


@dataclass(frozen=True, slots=True)
class AssistantThinkingStarted(_Empty):
    event = EventType.ASSISTANT_THINKING_STARTED


@dataclass(frozen=True, slots=True)
class AssistantThinkingEnded(_Empty):
    event = EventType.ASSISTANT_THINKING_ENDED


@dataclass(frozen=True, slots=True)
class SpeechPrepare(Payload):
    """The renderer should expect audio for this utterance shortly.

    Sent at T4 (first speakable phrase) so the avatar can return its gaze to the
    user before the first sample plays, rather than snapping to attention on
    `speech.started`. `text` is for subtitles and debugging -- the renderer must
    not depend on it and the orchestrator may omit it.
    """

    event = EventType.SPEECH_PREPARE

    utterance_id: str
    preset: Preset | None = None
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            utterance_id=self.utterance_id,
            preset=None if self.preset is None else self.preset.value,
            text=self.text,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpeechPrepare":
        preset = payload.get("preset")
        text = payload.get("text")
        return cls(
            utterance_id=_as_str(_require(payload, "utterance_id"), "utterance_id"),
            preset=None if preset is None else _as_enum(Preset, preset, "preset"),
            text=None if text is None else _as_str(text, "text"),
        )


@dataclass(frozen=True, slots=True)
class SpeechStarted(Payload):
    """Audio playback for `utterance_id` has begun -- the avatar's T6.

    Audio itself does not travel over this protocol; the renderer is told which
    channel to read. `duration_ms` is absent while a stream is still being
    synthesised.
    """

    event = EventType.SPEECH_STARTED

    utterance_id: str
    audio_channel: str = "stream"
    sample_rate: int = 24_000
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            utterance_id=self.utterance_id,
            audio_channel=self.audio_channel,
            sample_rate=self.sample_rate,
            duration_ms=self.duration_ms,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpeechStarted":
        duration = payload.get("duration_ms")
        sample_rate = payload.get("sample_rate", 24_000)
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
            raise InvalidPayloadError(f"'sample_rate' must be a positive integer, got {sample_rate!r}")
        return cls(
            utterance_id=_as_str(_require(payload, "utterance_id"), "utterance_id"),
            audio_channel=_as_str(payload.get("audio_channel", "stream"), "audio_channel"),
            sample_rate=sample_rate,
            duration_ms=None if duration is None else _as_duration_ms(duration, "duration_ms"),
        )


@dataclass(frozen=True, slots=True)
class SpeechCompleted(Payload):
    event = EventType.SPEECH_COMPLETED

    utterance_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"utterance_id": self.utterance_id}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpeechCompleted":
        return cls(utterance_id=_as_str(_require(payload, "utterance_id"), "utterance_id"))


@dataclass(frozen=True, slots=True)
class SpeechCancelled(Payload):
    """Stop speaking now. `fade_ms` should stay short -- barge-in has to feel
    like being interrupted, not like a media player fading out."""

    event = EventType.SPEECH_CANCELLED

    utterance_id: str
    reason: CancelReason = CancelReason.BARGE_IN
    fade_ms: int = 80

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance_id": self.utterance_id,
            "reason": self.reason.value,
            "fade_ms": self.fade_ms,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpeechCancelled":
        return cls(
            utterance_id=_as_str(_require(payload, "utterance_id"), "utterance_id"),
            reason=_as_enum(CancelReason, payload.get("reason", CancelReason.BARGE_IN), "reason"),
            fade_ms=_as_duration_ms(payload.get("fade_ms", 80), "fade_ms"),
        )


@dataclass(frozen=True, slots=True)
class PerformanceUpdate(Payload):
    """A performance direction (build plan 5.3).

    `preset` and `intensity` are the whole contract; the per-region
    coefficients are optional overrides for when the director wants to say more
    than the preset does. A renderer that only implements presets can ignore
    them entirely. Low values are the normal case.
    """

    event = EventType.PERFORMANCE_UPDATE

    preset: Preset
    intensity: float
    transition_ms: int = 450
    eye_contact: float | None = None
    head_motion: float | None = None
    brow_activity: float | None = None
    smile: float | None = None
    gesture_probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            preset=self.preset.value,
            intensity=self.intensity,
            transition_ms=self.transition_ms,
            eye_contact=self.eye_contact,
            head_motion=self.head_motion,
            brow_activity=self.brow_activity,
            smile=self.smile,
            gesture_probability=self.gesture_probability,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PerformanceUpdate":
        def optional_unit(key: str) -> float | None:
            value = payload.get(key)
            return None if value is None else _as_unit(value, key)

        return cls(
            preset=_as_enum(Preset, _require(payload, "preset"), "preset"),
            intensity=_as_unit(_require(payload, "intensity"), "intensity"),
            transition_ms=_as_duration_ms(payload.get("transition_ms", 450), "transition_ms"),
            eye_contact=optional_unit("eye_contact"),
            head_motion=optional_unit("head_motion"),
            brow_activity=optional_unit("brow_activity"),
            smile=optional_unit("smile"),
            gesture_probability=optional_unit("gesture_probability"),
        )


@dataclass(frozen=True, slots=True)
class GazeSetTarget(Payload):
    """Where to look and for roughly how long.

    `hold_ms` is advisory: the renderer's gaze controller owns the stochastic
    timing (build plan 8) and may hold longer or shorter. Zero means "until
    told otherwise".
    """

    event = EventType.GAZE_SET_TARGET

    target: GazeTarget
    hold_ms: int = 0
    transition_ms: int = 220

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.value,
            "hold_ms": self.hold_ms,
            "transition_ms": self.transition_ms,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GazeSetTarget":
        return cls(
            target=_as_enum(GazeTarget, _require(payload, "target"), "target"),
            hold_ms=_as_duration_ms(payload.get("hold_ms", 0), "hold_ms"),
            transition_ms=_as_duration_ms(payload.get("transition_ms", 220), "transition_ms"),
        )


@dataclass(frozen=True, slots=True)
class GestureTrigger(Payload):
    """Deferred to phase 6, but on the wire from v1 so adding gestures later is
    not a protocol change. Gesture names are renderer-defined."""

    event = EventType.GESTURE_TRIGGER

    gesture: str
    intensity: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return {"gesture": self.gesture, "intensity": self.intensity}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GestureTrigger":
        return cls(
            gesture=_as_str(_require(payload, "gesture"), "gesture"),
            intensity=_as_unit(payload.get("intensity", 0.2), "intensity"),
        )


@dataclass(frozen=True, slots=True)
class AvatarReset(_Empty):
    """Drop all performance state and settle to neutral attentive. Sent on
    reconnect, so the renderer never inherits a mood from a dead session."""

    event = EventType.AVATAR_RESET


PAYLOAD_TYPES: dict[EventType, type[Payload]] = {
    payload_type.event: payload_type
    for payload_type in (
        SessionStarted,
        SessionEnded,
        UserDetected,
        UserSpeechStarted,
        UserSpeechEnded,
        AssistantThinkingStarted,
        AssistantThinkingEnded,
        SpeechPrepare,
        SpeechStarted,
        SpeechCompleted,
        SpeechCancelled,
        PerformanceUpdate,
        GazeSetTarget,
        GestureTrigger,
        AvatarReset,
    )
}

assert set(PAYLOAD_TYPES) == set(EventType), "every event needs a payload type"


# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Envelope:
    """A decoded message. `payload` stays a plain dict until `parse` is called,
    so a receiver can route on `event` and skip what it does not implement."""

    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    version: int = PROTOCOL_VERSION

    @property
    def known(self) -> bool:
        return self.event in PAYLOAD_TYPES

    def parse(self) -> Payload:
        """Typed payload for this envelope.

        Raises UnknownEventError for events this build does not know and
        InvalidPayloadError if a required field is missing or out of range.
        """
        try:
            payload_type = PAYLOAD_TYPES[EventType(self.event)]
        except (KeyError, ValueError):
            raise UnknownEventError(self.event) from None
        return payload_type.from_dict(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event": self.event,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


def envelope(payload: Payload, *, timestamp: float | None = None) -> Envelope:
    """Wrap a typed payload for sending."""
    return Envelope(
        event=payload.event.value,
        payload=payload.to_dict(),
        timestamp=time.time() if timestamp is None else timestamp,
        version=PROTOCOL_VERSION,
    )


def encode(payload: Payload | Envelope, *, timestamp: float | None = None) -> str:
    """Serialise a payload or envelope to a single-line JSON string."""
    wrapper = payload if isinstance(payload, Envelope) else envelope(payload, timestamp=timestamp)
    return json.dumps(wrapper.to_dict(), separators=(",", ":"))


def decode(raw: str | bytes) -> Envelope:
    """Parse and validate one message.

    Validates the envelope only. The event name is not checked against
    EventType here -- an unrecognised event is a decodable message that the
    receiver may choose to ignore, and `Envelope.parse` is where that becomes
    an error.
    """
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedMessageError(f"not valid JSON: {exc}") from exc

    if not isinstance(message, dict):
        raise MalformedMessageError(f"message must be a JSON object, got {type(message).__name__}")

    version = message.get("version")
    if version != PROTOCOL_VERSION:
        raise UnsupportedVersionError(version)

    if "event" not in message:
        raise MalformedMessageError("missing required field 'event'")
    event = message["event"]
    if not isinstance(event, str):
        raise MalformedMessageError(f"'event' must be a string, got {type(event).__name__}")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise MalformedMessageError(f"'payload' must be an object, got {type(payload).__name__}")

    timestamp = message.get("timestamp", 0.0)
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise MalformedMessageError(f"'timestamp' must be a number, got {type(timestamp).__name__}")

    return Envelope(event=event, payload=payload, timestamp=float(timestamp), version=version)

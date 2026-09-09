# Avatar protocol v1

The wire format between the Amanda orchestrator and whatever renders the
avatar. Engine-neutral on purpose: the renderer happens to be Unreal, but
nothing here depends on that.

The Python reference implementation is `src/amanda/avatar/protocol.py`, and
`tests/test_protocol.py` is the conformance suite. This document is the spec
for implementations in other languages.

## Transport

A local WebSocket connection, orchestrator as server, renderer as client, bound
to loopback. Each message is one JSON object sent as a single text frame. No
message ever contains a raw newline, so the same encoding works over a
newline-delimited stream for testing and replay.

Audio does not travel over this protocol. `speech.started` names the channel the
renderer should read.

## Envelope

```json
{
  "version": 1,
  "event": "performance.update",
  "timestamp": 1788967200.125,
  "payload": { "preset": "mildly_amused", "intensity": 0.22, "transition_ms": 450 }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | integer | yes | Must be `1`. Any other value is a hard error. |
| `event` | string | yes | One of the events below. |
| `timestamp` | number | no | Unix seconds, float. Defaults to `0.0`. |
| `payload` | object | no | Defaults to `{}`. |

## Compatibility rules

Fixed for the life of v1:

1. Receivers **must** ignore payload fields they do not recognise, so new
   optional fields can be added without a version bump.
2. Receivers **should** ignore whole events they do not recognise rather than
   dropping the connection, so a newer orchestrator can drive an older
   renderer.
3. Removing a field, renaming an event, or changing a unit is a **version
   bump**, not a patch.

Absent optional fields are omitted rather than sent as `null`.

## Value conventions

- Every animation coefficient is a float in `0.0`–`1.0`. Out-of-range values
  are rejected, not clamped — a director emitting `1.4` has a bug worth seeing.
  Low values are the normal case; see build plan §18.
- Every duration is a non-negative integer in milliseconds.
- Unknown enum values are rejected.

## Events

### Session

| Event | Payload | |
|---|---|---|
| `session.started` | `session_id` (string, required) | |
| `session.ended` | `session_id` (string, required), `reason` (string) | |

### User

| Event | Payload | |
|---|---|---|
| `user.detected` | `present` (boolean, required) | Presence, not identity. `false` is the user having walked away. |
| `user.speech_started` | — | |
| `user.speech_ended` | `duration_ms` (integer) | The T0 of the latency budget. |

### Assistant

| Event | Payload | |
|---|---|---|
| `assistant.thinking_started` | — | Renderer should reduce eye contact and go still — not look theatrically puzzled. |
| `assistant.thinking_ended` | — | |

### Speech

| Event | Payload | |
|---|---|---|
| `speech.prepare` | `utterance_id` (string, required), `preset` (Preset), `text` (string) | Sent at first speakable phrase so gaze can return to the user *before* the first sample plays. `text` is for subtitles and debugging only; the renderer must not depend on it. |
| `speech.started` | `utterance_id` (string, required), `audio_channel` (string, default `"stream"`), `sample_rate` (positive integer, default `24000`), `duration_ms` (integer) | The avatar's T6. `duration_ms` is absent while still synthesising. |
| `speech.completed` | `utterance_id` (string, required) | |
| `speech.cancelled` | `utterance_id` (string, required), `reason` (`barge_in` \| `error` \| `shutdown` \| `superseded`, default `barge_in`), `fade_ms` (integer, default `80`) | Keep the fade short. Barge-in must feel like being interrupted, not like a media player fading out. |

### Performance

| Event | Payload | |
|---|---|---|
| `performance.update` | `preset` (Preset, required), `intensity` (unit, required), `transition_ms` (integer, default `450`), `eye_contact`, `head_motion`, `brow_activity`, `smile`, `gesture_probability` (units) | `preset` and `intensity` are the whole contract. The per-region coefficients are optional overrides; a renderer that only implements presets may ignore them. |
| `gaze.set_target` | `target` (GazeTarget, required), `hold_ms` (integer, default `0` = until told otherwise), `transition_ms` (integer, default `220`) | `hold_ms` is **advisory**. The renderer's gaze controller owns stochastic timing and may hold longer or shorter. |
| `gesture.trigger` | `gesture` (string, required), `intensity` (unit, default `0.2`) | Deferred to phase 6, but on the wire from v1 so adding gestures later is not a protocol change. Gesture names are renderer-defined. |
| `avatar.reset` | — | Drop all performance state, settle to neutral attentive. **Sent on every reconnect**, before anything else, so the renderer never inherits a mood from a dead session. |

## Vocabularies

**Preset** — `neutral_attentive`, `listening`, `considering`, `mildly_amused`,
`warm`, `concerned`, `uncertain`, `confused`, `surprised`, `explaining`,
`enthusiastic`, `serious`.

Phase 4 uses a subset. A renderer may map any preset it has not authored onto
`neutral_attentive` rather than failing.

**GazeTarget** — `user`, `slightly_left`, `slightly_right`, `down`, `distant`,
`object_of_interest`.

Angles stay renderer-side; the protocol names intent, not degrees.

## Connecting

The orchestrator listens; the renderer connects. On every connection the
renderer receives, before anything else:

1. `avatar.reset`
2. the current `session.started`, `performance.update` and `gaze.set_target`,
   if the conversation has produced them yet.

That is the whole reconnect contract. Speech events are never replayed, so a
renderer that reconnects mid-utterance stays silent rather than resuming
lip-sync for audio that has already played.

A renderer that stops draining its socket will be **disconnected** rather than
having messages dropped, and should simply reconnect — which resynchronises it.

Protocol v1 is one-directional. Anything the renderer sends is ignored, never
fatal; there is no renderer-to-orchestrator event in this version.

## Testing without an orchestrator

`tools/emit_sample_session.py` defines one realistic turn — greeting, listening,
thinking, speaking, an acknowledgement nod, barge-in, the user leaving —
exercising every event without Claude, TTS or a microphone.

Replay it over a live bridge and point the renderer at `ws://127.0.0.1:8765`:

```sh
python3 tools/serve_sample_session.py     # --speed 4, --loop
python3 tools/mock_renderer.py            # printing stand-in renderer
```

Or dump it as newline-delimited JSON for offline replay:

```sh
python3 tools/emit_sample_session.py > session.ndjson
```

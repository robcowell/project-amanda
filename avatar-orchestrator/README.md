# Amanda orchestrator

The conversation orchestrator for **Project Amanda** — a real-time
conversational digital human driven by Claude. This process owns everything
except rendering: microphone, STT, Claude, TTS, the performance director, and
the bridge that drives the avatar.

The renderer (Unreal Engine + MetaHuman) is a separate process downstream of
this one and never calls Claude itself. See
`../claude-digital-human-avatar-build-plan.md` for the full plan.

> Claude decides what to say. The performance director decides how the avatar
> should inhabit the moment.

## Status

Scaffolded. **Protocol v1 is the only finished piece** — everything else is a
stub with its responsibilities and backlog items written down.

| Area | State |
|---|---|
| `avatar/protocol.py` | Implemented, 58 conformance tests |
| `avatar/websocket.py` | Stub — next step (epic 4) |
| `claude/` | Stub (epic 2) |
| `audio/tts.py` | Stub (epic 3) |
| `audio/{microphone,vad,stt}.py` | Stub, phase 2 (epic 5) |
| `performance/` | Stub, phase 4 |
| `runtime/` | Stub |

## Layout

```
src/amanda/
  audio/        microphone, VAD, STT, TTS -- each behind a swappable interface
  claude/       Messages API client, conversation state, prompts
  performance/  the performance director, its schema and smoothing
  avatar/       protocol v1 and the local WebSocket bridge
  runtime/      conversation state machine, interruption, telemetry
config/         avatar.yaml, voices.yaml -- no secrets
tools/          emit_sample_session.py
tests/
```

The build plan sketches these as top-level directories under `src/`. They live
under an `amanda` package instead so that `src/claude/` does not become an
importable top-level `claude` module, which would collide confusingly with the
`anthropic` SDK and with any package of that name. Every module path from the
plan is otherwise preserved.

## Protocol v1

`PROTOCOL.md` is the language-neutral spec — read that to implement the
renderer side. `src/amanda/avatar/protocol.py` is the Python reference
implementation and `tests/test_protocol.py` is the conformance suite.

Fifteen events across five groups: session, user, assistant, speech and
performance. Every message is one JSON object:

```json
{"version": 1, "event": "performance.update", "timestamp": 1788967200.125,
 "payload": {"preset": "mildly_amused", "intensity": 0.22, "transition_ms": 450}}
```

Two decisions worth knowing before writing renderer code:

- **Unknown payload fields are ignored; unknown events should be skipped, not
  fatal.** That is what lets the protocol grow without lockstep releases of
  both processes.
- **Out-of-range animation coefficients are rejected, not clamped.** A director
  emitting `1.4` has a bug, and clamping hides it behind an avatar that merely
  looks slightly wrong.

## Driving the renderer without an orchestrator

The renderer can be built and tuned before any of the rest of this exists.
`tools/emit_sample_session.py` writes one realistic turn — greeting, listening,
thinking, speaking, an acknowledgement nod, barge-in, the user leaving — as
newline-delimited protocol v1, exercising every event:

```sh
python3 tools/emit_sample_session.py > session.ndjson
```

The timings encode a plausible latency budget: the user stops speaking at
t=6.0s and the avatar starts at t=7.4s, with the thinking behaviour covering
the 1.4s gap rather than a verbal filler.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Audio dependencies are a separate extra (`.[audio]`) so the protocol and the
Claude spine stay testable on a headless machine.

## Secrets

`ANTHROPIC_API_KEY` comes from the environment — see `.env.example`. Nothing
secret goes in `config/`, and the bridge binds to loopback by default.

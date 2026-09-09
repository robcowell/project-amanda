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

The renderer-facing half is built: protocol v1, the bridge that carries it, and
a tested presence layer with a previsualiser to tune it. Everything upstream —
Claude, speech, the performance director — is still a stub with its
responsibilities and backlog items written down.

| Area | State |
|---|---|
| `avatar/protocol.py` | Implemented, 58 conformance tests |
| `avatar/websocket.py` | Implemented, 20 tests (epic 4) |
| `presence/` | Implemented, 30 tests (phase 3, reference for Unreal) |
| `performance/{schema,smoothing}.py` | Implemented (phase 4) |
| `performance/director.py` | Stub, needs the classifier call |
| `claude/` | Stub — next step (epic 2) |
| `audio/tts.py` | Stub (epic 3) |
| `audio/{microphone,vad,stt}.py` | Stub, phase 2 (epic 5) |
| `runtime/` | Stub |

## Layout

```
src/amanda/
  audio/        microphone, VAD, STT, TTS -- each behind a swappable interface
  claude/       Messages API client, conversation state, prompts
  performance/  the performance director, its schema and smoothing
  presence/     blink, gaze, breath and drift -- reference logic for the renderer
  avatar/       protocol v1 and the local WebSocket bridge
  runtime/      conversation state machine, interruption, telemetry
config/         avatar.yaml, voices.yaml -- no secrets
tools/          previz, sample session emitter and server, mock renderer
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

## The bridge

`amanda.avatar.websocket.AvatarBridge` serves protocol v1 over a local
WebSocket. The orchestrator is the **server** and the renderer is the client,
because the conversation outlives the renderer: Unreal can be restarted or
attached to a debugger mid-conversation without taking Claude, the microphone
or the conversation state down with it.

```python
async with AvatarBridge() as bridge:            # ws://127.0.0.1:8765
    bridge.send(PerformanceUpdate(preset=Preset.WARM, intensity=0.2))
```

`send` is a plain method, not a coroutine, and only enqueues — a wedged
renderer can never stall a turn. With nothing connected, events are counted and
discarded; the conversation runs whether or not anything is drawing it.

**On connect the renderer is resynchronised**: `avatar.reset` first, then the
current session, performance and gaze. So a renderer that restarts mid-conversation
neither inherits a mood from the dead connection nor sits in neutral while the
conversation has moved on. Speech and other one-shot events are deliberately
*not* replayed — resuming lip-sync for audio that already played would be worse
than missing the utterance.

**A renderer that stops keeping up is dropped** rather than having individual
messages discarded. Silently losing a `speech.completed` would leave the avatar
stuck talking; dropping the client forces a reconnect, and the reconnect
resynchronises it from a known state.

## Driving the renderer without an orchestrator

The renderer can be built and tuned before any of the rest of this exists.
`tools/emit_sample_session.py` defines one realistic turn — greeting, listening,
thinking, speaking, an acknowledgement nod, barge-in, the user leaving —
exercising every event in the protocol.

Replay it over a live bridge and point Unreal at `ws://127.0.0.1:8765`:

```sh
python3 tools/serve_sample_session.py           # or --speed 4, --loop
```

Disconnect and reconnect the renderer mid-replay to exercise resync. To check
the wire without Unreal, run the printing stand-in renderer against it:

```sh
python3 tools/mock_renderer.py
```

Or dump the session as newline-delimited JSON for offline replay:

```sh
python3 tools/emit_sample_session.py > session.ndjson
```

The timings encode a plausible latency budget: the user stops speaking at
t=6.0s and the avatar starts at t=7.4s, with the thinking behaviour covering
the 1.4s gap rather than a verbal filler.

## The presence layer and the previz

`src/amanda/presence/` holds blink, gaze, breathing and head-drift scheduling.
At runtime this logic belongs in Unreal (`BP_IdleController`,
`BP_GazeController`), and that is still the plan — it lives here as a **reference
implementation**, because these are stochastic processes whose constants have to
be tuned by watching them, which is miserable in a Blueprint graph and ordinary
in Python. Tune here, port the tuned algorithm later.

Nothing in it reads a clock or touches the network: every scheduler takes an
explicit `now` and an injected `random.Random`, so a test runs ten minutes of
behaviour in milliseconds and gets the same answer twice.

`tools/previz.py` is a renderer that draws diagrams instead of a face. It
attaches to the bridge as an ordinary protocol v1 client, runs the schedulers,
and streams the result to a browser:

```sh
python3 tools/previz.py                          # then open http://127.0.0.1:8766/previz.html
python3 tools/serve_sample_session.py --loop     # in another terminal, to drive it
```

It cannot tell you whether the character looks alive. It can tell you whether
the timings have fallen into a rhythm, which is the failure the build plan's
sixty-second stillness test is designed to catch. For tuning, skip the browser
entirely:

```sh
python3 tools/previz.py --audit 30 --seed 11     # 30 minutes of behaviour, ~1 second
```

Two things the diagnostics caught on their first run, both now regression-tested:

- Blinks were firing at six times the human rate, because a gaze-shift magnitude
  was exposed as a level rather than an edge — so every frame after a saccade
  looked like a fresh shift.
- Gaze alternated user, away, user, away almost perfectly. Each choice was
  random; the sequence was not. Eye contact is a fraction of *time*, so it
  belongs in how long a target is held, not only in how often it is chosen.

The repetition metric reports a chance baseline alongside the measurement.
Without it the number is only alarming: draw a few hundred symbols from five
options and some run of six repeats every time.

## The Unreal side

`../unreal/AmandaBridge/` is an Unreal plugin that speaks the other end of this
protocol. Its conformance-test fixture is generated from the sample session
here, so both implementations are checked against the same bytes. Regenerate it
after any protocol change:

```sh
python3 tools/regenerate_unreal_fixture.py
```

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

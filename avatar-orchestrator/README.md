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

Phase 1 is complete end to end: protocol v1 and the bridge that carries it, a
tested presence layer with a previsualiser, streaming Claude turns with
cancellation, and speech synthesis with a provider-neutral interface. What
remains is the microphone loop — VAD, STT and barge-in detection.

| Area | State |
|---|---|
| `avatar/protocol.py` | Implemented, 58 conformance tests |
| `avatar/websocket.py` | Implemented, 20 tests (epic 4) |
| `presence/` | Implemented, 30 tests (phase 3, reference for Unreal) |
| `performance/{schema,smoothing}.py` | Implemented (phase 4) |
| `performance/director.py` | Stub, needs the classifier call |
| `claude/` | Implemented, 47 tests (epic 2) |
| `runtime/metrics.py` | Implemented (T0–T6) |
| `audio/{tts,providers,sink,speech}.py` | Implemented, 42 tests (epic 3) |
| `audio/{microphone,vad,stt}.py` | Stub — next step, phase 2 (epic 5) |
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

## What you can run today

Everything except the microphone, on a machine with no GPU and no API key.

```sh
python3 tools/previz.py                # http://127.0.0.1:8766/previz.html
python3 -m amanda.main --scripted      # in another terminal
```

The voice defaults to the best engine installed on the machine — `espeak-ng` or
Piper if present, and the built-in stand-in only if nothing else is. The
stand-in is a drone with the rhythm of a sentence: fine for judging timing and
where an interruption lands, useless for judging anything else. `--engine`
overrides it and `--engine auto` reports what it picked.

Type a message and the schematic face thinks, looks away, returns its gaze and
speaks; type again while it is speaking and it is interrupted. `--scripted`
uses canned replies so no API call is made — drop it once `ANTHROPIC_API_KEY`
is set and the same pipeline runs against Claude. `--no-audio` runs it silently,
`--device "cable input"` sends the voice into a virtual cable instead of the
speakers, and `--engine espeak-ng` swaps the stand-in voice for a real one if
you have it installed.

A turn prints its own latency breakdown, which is the number Phase 2 has to
protect:

```
[stt_ms=0  dispatch_ms=0  claude_first_token_ms=601  phrase_ms=501
 tts_first_audio_ms=5  playback_ms=0  total_response_ms=1108]
```

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

## The Claude client

`claude/client.py` streams a turn from the Messages API and hands text to the
phrase segmenter, so TTS can start on phrase one while Claude is still writing
phrase two. Everything about the request lives in `config/avatar.yaml`.

```python
turn = client.start_turn(conversation.messages())
async for chunk in turn:
    for phrase in segmenter.feed(chunk):
        await tts.speak(phrase)
turn.cancel()          # barge-in: immediate, not at the next token
```

Four decisions worth knowing before you change anything:

- **`claude-opus-5`, and effort is the latency lever.** Thinking is on by
  default on this model and `effort` governs how much; the config ships at
  `low`, which suits conversation. Explicitly setting `thinking: disabled` is a
  documented footgun on Opus 5 — it can write tool calls into visible text and
  leak thinking tags — so the client never sends the parameter at all. Tune
  effort and watch `claude_first_token_ms`.
- **Refusal fallbacks are on.** On a policy decline the API re-runs the request
  on a fallback model inside the same call, routed by category. A decline
  before any output is not billed. `stop_reason: "refusal"` on the final
  response means the whole chain declined, and surfaces as `turn.refusal`.
- **Fast mode is available and off.** The same model at up to 2.5x output
  tokens per second, at premium pricing — a real lever on `T6 - T0`, and a
  spending decision rather than a technical one. Set `claude.fast: true` to try
  it.
- **Cancellation cancels a task, not a flag.** A flag only takes effect at the
  next token, and during a barge-in there may not be one for a while. What was
  already streamed stays in `turn.text`, because that is what the user heard.

`conversation.py` records that partial text rather than what Claude generated —
the tail was cancelled before synthesis, so as far as the conversation is
concerned it was never said — and follows it with a mid-conversation system
message telling Claude it was cut off. That note needs a model that supports
mid-conversation system messages: Opus 5 does, **Sonnet 5 returns a 400**, so
set `interruption_notes: false` if you change model.

## Speech

`audio/tts.py` is the provider-neutral interface; `audio/speech.py` turns a
stream of phrases into an utterance and owns the protocol's speech lifecycle.

```python
session = SpeechSession("u_1042", synthesizer, sink, voice, emit=bridge.send)
await session.start()
async for chunk in turn:
    for phrase in segmenter.feed(chunk):
        await session.add(phrase)
session.close_input()
await session.wait()          # or session.cancel() on barge-in
```

**Audio does not travel over the avatar protocol.** `speech.started` is a cue
about audio arriving by a completely separate route — a virtual audio cable
that Unreal reads as a microphone. So `audio/sink.py` exists to put PCM on a
named *device*, and choosing that device is a first-class concern rather than a
detail:

```sh
python3 tools/speak.py --devices
python3 tools/speak.py "It rained most of the morning." --device "cable input"
python3 tools/speak.py "A longer sentence to cut into." --interrupt-after 700
```

That last one is worth running with `--wav` and listening to. Interruption
fades over `fade_ms` rather than cutting to zero, because a waveform stopped at
a non-zero sample clicks — which is exactly the "stopping a media player"
feeling the build plan wants barge-in to avoid.

### Engines

Two providers ship, and neither is the one this will run on:

| Provider | What it is |
|---|---|
| `ToneSynthesizer` | Audible, correctly-timed audio that is not speech. Lets the whole pipeline be run and heard before an engine is chosen. |
| `CommandSynthesizer` | Any CLI engine — espeak-ng, Piper, macOS `say` — via an argv template. No code per engine. |

### Piper

Local neural voices, and the best thing available before a cloud engine:

```sh
pip install piper-tts
python -m piper.download_voices --download-dir voices en_GB-jenny_dioco-medium
python -m amanda.main --scripted          # auto-detects it
```

Models go in `voices/` (gitignored). The voice is chosen, in order of
precedence, by `--voice cori` (a name fragment), `$AMANDA_VOICE`, the `model:`
key in `config/voices.yaml`, and only then the first model alphabetically —
which is arbitrary and picked a male voice for a character named Amanda until
somebody noticed.

Voices differ in more than timbre. Measuring word-sized silences in the same
sentence: `cori-high` 0.57/sec, `cori-medium` 0.79, `southern_english_female-low`
1.01, `alba-medium` 1.14, `jenny_dioco-medium` 1.29. The gappier voices read as
staccato — words separated rather than flowing. `cori-medium` is the best
combination here: 0.79 gaps/sec at 9.4x realtime, against `cori-high`'s smoother
0.57 at only 2.0x.

Piper runs **in-process, not as a subprocess**, and that is a measurement rather
than a preference. Each `piper` CLI invocation spends about 3.5 seconds loading
the interpreter, onnxruntime and the model before producing a sample — a
four-character phrase costs 3.68s wall for 0.50s of audio. Spawning one per
phrase would put that on every phrase. Loaded once and kept, the same model runs
at 8–9x realtime on a 2017 ultrabook. The load happens at startup via `warm()`,
because paying it on the first phrase of the first conversation is the one place
it would actually be felt.

Quality tiers are not free: `medium` voices run at ~9x realtime, `high` at
~1.8x. Prefer `medium` unless you have measured that you can afford otherwise.

`audio/engines.py` is the registry: an argv template and, critically, the sample
rate each engine actually emits. A mismatch is **refused rather than resampled**
— playing 22050 Hz audio at 24000 makes a chipmunk that is easy to blame on the
engine when it is really a config error — so the voice defaults to whatever the
chosen engine produces. `espeak-ng` is 22050 Hz; Piper depends on the model you
downloaded, so pass `--rate` if yours differs.

A streaming cloud engine is the likely production choice and is deliberately
absent: an API client that has never run against the real service is code that
looks finished and is not. `PROVIDER_NOTES` in `audio/providers.py` says what
such a provider has to get right — chiefly that it must emit the first chunk
before the last, or it has the interface without the behaviour.

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

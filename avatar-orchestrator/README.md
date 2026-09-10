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
cancellation, and speech synthesis with a provider-neutral interface. Phase 2
added the microphone — VAD, STT, barge-in and a wake word — and phase 4 the
performance director. The orchestrator half is built; the renderer half is not.

| Area | State |
|---|---|
| `avatar/protocol.py` | Implemented, 58 conformance tests |
| `avatar/websocket.py` | Implemented, 20 tests (epic 4) |
| `presence/` | Implemented, 30 tests (phase 3, reference for Unreal) |
| `performance/{schema,smoothing}.py` | Implemented (phase 4) |
| `performance/director.py` | Implemented, 45 tests (phase 4) |
| `claude/` | Implemented, 47 tests (epic 2) |
| `runtime/{metrics,telemetry}.py` | Implemented, 27 tests (T0–T7, build plan 24) |
| `audio/{tts,providers,sink,speech}.py` | Implemented, 42 tests (epic 3) |
| `audio/{microphone,vad}.py` | Implemented, 32 tests (phase 2, epic 5) |
| `audio/{stt,whisper_provider}.py` | Implemented, 17 tests |
| `runtime/{state_machine,input,metrics}.py` | Implemented, 24 tests |

## Layout

```
src/amanda/
  audio/        microphone, VAD, STT, TTS -- each behind a swappable interface
  claude/       Messages API client, conversation state, prompts
  performance/  the performance director, its schema and smoothing
  presence/     blink, gaze, breath and drift -- reference logic for the renderer
  avatar/       protocol v1 and the local WebSocket bridge
  runtime/      conversation state machine, where turns come from, telemetry
turns.jsonl     one JSON object per turn, gitignored
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
python3 -m amanda.main --scripted      # typed, in another terminal
python3 -m amanda.main --voice         # or speak to it
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

Part-way through each reply the face changes expression: that is the performance
director landing. Without a key it is a scripted stand-in cycling a fixed list —
enough to exercise the bridge and the smoother, and emphatically not a cheap
classifier. `--director none` turns it off entirely and leaves the conversation
states driving the face on their own, which is the comparison worth making.

The first phrase is allowed to be shorter than the rest, because it alone
decides when speech *starts* — everything after it is synthesised while earlier
audio still plays, so its cost is hidden, while the first one's sits on the
critical path twice: once waiting for a clause boundary, again waiting for the
engine. Lowering that one threshold from 40 to 24 characters took 482ms off the
start of every reply.

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

## Latency, measured

Time to first token dominates a turn, and it is the only stage nothing
downstream can hide — the avatar cannot start speaking until Claude has said
something. Measured on one prompt through this pipeline, September 2026:

| configuration | first token | total |
|---|---|---|
| `claude-opus-5`, effort low | 2234 ms | 3006 ms |
| `claude-opus-5`, thinking disabled | 1542 ms | 3071 ms |
| `claude-sonnet-5`, effort low *(shipped default)* | 669 ms | 1695 ms |
| `claude-haiku-4-5` | 658 ms | 1053 ms |

Three things worth reading off that. Opus spends about 2.2 seconds before the
first word, which is most of a turn and the thing the thinking animation exists
to cover. Disabling thinking buys ~700 ms of it but leaves the total unchanged —
it moves the wait rather than removing it, and on Opus 5 it risks `<thinking>`
tags leaking into text that is about to be spoken aloud. And the cheaper models
are not marginally faster but **three times** faster to first token.

Sonnet 5 is the shipped default on those grounds: for a conversational
companion, three times quicker to speak beats the deeper reasoning nobody is
waiting for. Change `claude.model` to go back. Fast mode was not measurable here: it has a rate limit
separate from standard Opus and this key had no allocation, returning 429.

Single samples on one prompt — re-measure before trusting the small
differences. The threefold gaps are well clear of noise.

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
message telling Claude it was cut off. That note needs a model supporting
mid-conversation system messages. Tested against the live API on 2026-09-10,
Opus 5 and Sonnet 5 both accept one — the documentation lists Sonnet 5 as
unsupported and is wrong, or was. Older models do reject it, so
`interruption_notes: false` remains the escape hatch.

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

Delivery speed is `pace:` in the same file. Piper's `length_scale` is markedly
non-linear — a pace of 1.1 shortens a phrase by under 3%, and it takes about 1.4
before the change is clearly audible — so the useful range is higher than it
looks.

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

### Sample rates, and the conversion on the Windows side

Every Piper voice in `voices/` emits **22050 Hz**, `cori-high` included — it is a
property of the models, not of the quality tier. VB-CABLE presents its endpoint
at **48000 Hz, 2 channel, float**. So a resample happens on the way to the face
solver no matter what is configured; the only question is whose resampler runs,
and today it is Windows' shared-mode mixer.

Measured here on 4.16s of real Piper speech, resampling 22050 → 48000 (a ratio
of 320/147, so genuinely a filter rather than an interpolation):

| | imaging above 11.1 kHz | round-trip SNR | cost |
|---|---|---|---|
| polyphase (`resample_poly`) | −49.2 dB | 44.7 dB | 9.09 ms |
| linear interpolation | −34.7 dB | 30.0 dB | 2.42 ms |
| nearest sample | −24.2 dB | 18.6 dB | 1.14 ms |

Imaging is energy above 11.1 kHz, which the 22050 Hz source cannot contain — so
all of it is the resampler's own invention.

**The conclusion is to leave it alone**, for two reasons rather than one. A good
resample puts its artefacts 49 dB down, which is inaudible and far below
anything a face solver would react to, and Windows' shared-mode SRC has been a
proper polyphase implementation for years — so the ceiling on what we could gain
is the gap between good and good. And audio reaches the sink in *chunks*, so
doing it here would need a **stateful** resampler carrying filter state across
chunk boundaries; get that wrong and there is a click at every boundary, which
is much worse than the thing being fixed.

What would change the answer: a face that articulates visibly worse from the
cable than from a WAV file played into it by something else. That is one A/B on
the Windows box, and it is the measurement to take before writing any DSP here.

### `--rate` declares, it cannot request

Because nothing resamples, `--rate` tells the pipeline what the engine emits.
It cannot ask an engine for a different rate. For Piper the rate is read from
the model file, so the flag is only needed if that file is missing or wrong;
for the stand-in tone engine, which generates its own samples, it really is a
request and is honoured.

Declaring a rate Piper does not emit now fails at startup with that sentence.
It used to be accepted, and the run would announce the wrong rate and then fail
on the first phrase of the first turn — by which point somebody had already
spoken to it.

## The conversation loop

`runtime/state_machine.py` owns the states and their animation envelopes;
`runtime/input.py` owns where turns come from. Typed and spoken input differ in
exactly two places — what produces a turn, and what signals an interruption —
so Phase 2 added a microphone rather than a second turn loop.

The two disagree about what interruption *means*, and the interface says so.
Typed: the interrupting line **is** the next turn, complete the moment it
arrives. Spoken: barge-in fires part-way through a sentence, long before the
endpointer knows where it ends, so the cancel happens first and the utterance
arrives later on its own. Hence `wait_for_barge_in` is a bare signal and
`next_turn` is the only thing that produces text.

Typing *ahead* of the avatar is not interrupting it, and typed input has to
check the clock to tell the difference. Voice input gets it free: the barge-in
detector is only fed while armed, so nothing said before the avatar started
counts against it.

## The performance director

`performance/director.py`. Claude decides what to say; a second, deliberately
cheap call decides how the avatar inhabits the moment (§6, option A). Keeping
them apart is the whole point — one call asked to do both writes stage
directions into text that is about to be spoken aloud.

It sees the user's message and Claude's reply, and returns one of the phase 4
presets and an intensity. That is all. `PRESET_SHAPES` already says what each
preset looks like and the renderer's smoother already interpolates toward it, so
asking the classifier for per-region coefficients would be asking it to
re-derive a table we have — and inviting it to over-act while doing so.

**When it runs is the design.** Not before the turn: the reply is what is being
classified. It fires once speech has begun and there is enough reply to judge
(120 characters — the first phrase is deliberately short, and "It rained most of
the morning," does not tell warm from concerned), then runs concurrently with
synthesis and playback. It costs nothing from the T0–T6 budget.

Landing a beat late is not a compromise. The renderer transitions over
`transition_ms` rather than snapping, so a direction arriving a second into an
utterance reads as an expression settling in — which is what a face does. One
that snapped to the correct emotion on the first syllable would look like a mask
being swapped.

When it fails it returns nothing and the conversation-state envelope stands. An
avatar driven by state alone still looks like it is participating; that is why
the states own envelopes in the first place. `--director none` makes that the
permanent behaviour, which is how you find out whether the classifier is
earning its place.

### Measured, September 2026

`claude-haiku-4-5`, seven exchanges, structured output, this laptop:

| | |
|---|---|
| classification | ~1300 ms (min 805) |
| first call of a run | ~2050 ms — the schema is compiled once and cached 24h |
| output | 17 tokens |

The first-call penalty is why `warm()` exists and is called at startup: the
first turn of a conversation is the one where the avatar most needs to look
alive, and without it that turn pays the compile.

Structured output is not a tax here, it is the fast path. The same prompt
without a schema took ~1640 ms and 71 output tokens; with a schema *and* an
explicit instruction to answer in one line, ~1730 ms and 92 tokens — the model
went on to explain its reasoning both times, in prose that would then have
needed parsing. Constraining the output shape is what keeps it to 17 tokens.

The classifications themselves were restrained without being inert: four of
seven `neutral_attentive`, `concerned` at 0.35 on both pieces of bad news,
`mildly_amused` at 0.25 on the cat and the pot plant, nothing above 0.35 against
a configured ceiling of 0.45.

### The ceiling is enforced, not requested

The prompt asks for intensity below 0.45. `max_intensity` in config is what
holds when the model ignores it — structured outputs support `enum` but not
`minimum`/`maximum`, so the range is ours to keep. This is the one place the
project clamps rather than rejects: an out-of-range coefficient from our own
code is a bug worth surfacing, but one from a model is a model ignoring an
instruction, and the ceiling exists precisely for that. The `clamped` counter is
the loud part.

## The wake word

Without one, `--voice` sends every utterance in earshot to Claude — the telly,
someone on the phone next door, a conversation the avatar was not part of. So
turns are gated:

```sh
python3 -m amanda.main --voice              # gated (the default)
python3 -m amanda.main --voice --wake none  # listen to everything
```

**The shipped keyword is a placeholder.** openWakeWord needs no account and
ships its models, but its vocabulary is fixed — `alexa`, `hey_jarvis`,
`hey_marvin`, `hey_mycroft` — and none of them is the character's name. For a
real "Amanda": generate a `.ppn` in the Picovoice console (free for personal
use), set `wake.backend` to `porcupine` and put the path in `wake.keyword`.

It is needed **once**, not before every sentence — each turn holds the
conversation open for `awake_seconds`. Being made to say it every time is what
makes an assistant feel like a vending machine rather than someone in the room.
While asleep the renderer is put in IDLE, so the face settles and stops
tracking rather than merely going quiet.

Measured: the wake phrase scored 0.854 and an ordinary sentence 0.000, and
detection costs 0.4–1.0 ms per 32 ms frame — about 2% of a core to run
continuously.

Two things about openWakeWord worth knowing. Its `Model.reset()` clears a
prediction buffer its scoring depends on, leaving it **deaf for ~2.4 seconds**;
ours deliberately does not call it. And its streaming context is process-wide
and carries whatever ran before — a burst of loud non-speech immediately before
the wake word measurably suppresses it, which matters if the room has music in.

## Hearing

`audio/microphone.py` opens one capture stream and fans it out; `audio/vad.py`
turns frames into utterances and separately watches for barge-in;
`audio/stt.py` transcribes. The constants for the first two came from
`~/code/jarvis` — see `docs/jarvis-overlap.md`.

Whisper runs in-process for the same reason Piper does: the model is slow to
load and fast to run. Measured here on a 2s utterance:

| model | load | transcribe | |
|---|---|---|---|
| `tiny.en` | 3.9 s | ~590 ms | heard "folks down" for "Folkestone" |
| **`base.en`** | 7.7 s | **~880 ms** | correct, and the default |
| `small.en` | 14.2 s | ~2370 ms | 2.5x the cost for no accuracy gain |

Two things about that. `tiny` is tempting for the 300 ms and loses proper
nouns, which is the wrong trade when place names are what a reply hinges on.
And ~880 ms makes transcription the second largest term in a turn after time to
first token — it is pure added latency, because unlike Claude's first token
there is no streaming to hide behind.

**An optimisation not taken yet.** The endpointer waits 750 ms of silence
before declaring an utterance over. Transcription could start on the audio
so far during that window instead of after it, hiding most of its cost — worth
roughly 750 ms, at the price of re-transcribing when more speech arrives.

**Whisper hallucinates on silence.** A second of digital zero transcribes as
"You" with `no_speech_prob` 0.768, against 0.000 for real speech. Segments above
0.6 are dropped, because otherwise a door closing that got past the endpointer
becomes a user turn and Claude answers it.

## Turn telemetry

Every turn appends one JSON object to `turns.jsonl` (§24). `tail -f` it while
you talk to the avatar — the point is the shape *across* turns, because one slow
reply tells you nothing and thirty tell you which stage moved.

```json
{"at": 1789035541.457, "stt_ms": 0, "claude_first_token_ms": 1058,
 "phrase_ms": 482, "tts_first_audio_ms": 379, "total_response_ms": 1919,
 "interrupted": false, "claude_stream_ms": 1655, "performance": "neutral_attentive",
 "intensity": 0.1, "model": "claude-sonnet-5", "input_tokens": 363,
 "output_tokens": 64, "spoken_ms": 8487, "phrases": 2, "peak_queue_depth": 1,
 "underruns": 0, "renderer_connected": false, "renderer_backpressure_drops": 0}
```

**Stages that never happened are omitted, not zeroed.** A typed turn has no T0,
and reporting that as `0ms` would make the numbers lie. A counter that is
genuinely zero — `underruns` — is kept, because zero underruns is a measurement
and a missing one is not.

**Generation time is recorded but stays out of the latency budget.** Claude keeps
writing while the avatar is already speaking, so `claude_stream_ms` is not
something the listener waits through. Counting it in the printed budget would
make the stages stop adding up, which is why the console line is built from
`SPANS` by name rather than from anything ending in `_ms`.

### The rest of §24's list, honestly

The plan also asks for FPS, GPU frame time, audio underruns, WebSocket latency,
TTS queue depth, Claude stream duration and utterance length. What the
orchestrator can actually answer:

| plan asks for | what is recorded |
|---|---|
| FPS, GPU frame time | **nothing** — the renderer's to report, and absent rather than guessed at |
| audio underruns | `underruns`, from PortAudio's blocking write, which returns the flag on every call |
| WebSocket latency | **no such number.** Protocol v1 has no ack, so there is no round trip. `renderer_connected` and `renderer_backpressure_drops` are the signals that exist |
| TTS queue depth | `peak_queue_depth` — phrases are synthesised one at a time, so a queue that climbs means the engine, not the model, is the bottleneck |
| Claude stream duration | `claude_stream_ms` |
| utterance length | `heard_ms` (the user) and `spoken_ms` (the avatar, after a barge-in what was *played*) |

### It never writes what was said unless asked

`privacy.log_transcripts` is false by default (§25), and this is the only thing
in the orchestrator that could put a conversation on disk. The turn loop passes
the text in either way and `TurnLog` drops it, so no caller has to remember the
rule and every caller gets it right.

`turns.jsonl` is gitignored regardless: it is a record of conversations even
without the words. `--no-telemetry` turns it off for a run, and any write
failure disables the log for the rest of the session and says so once — a turn
that worked must never be reported as failed because a disk filled up.

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

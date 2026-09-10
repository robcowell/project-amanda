# Project Amanda

A real-time conversational digital human driven by Claude. Two processes: a
Python orchestrator and an Unreal renderer, speaking **protocol v1** over a
local WebSocket.

`claude-digital-human-avatar-build-plan.md` is the plan; section numbers below
refer to it. `docs/jarvis-overlap.md` cross-references `~/code/jarvis`, an
earlier voice assistant of Rob's that several pieces here were ported from. `avatar-orchestrator/README.md` covers how to run things,
`avatar-orchestrator/PROTOCOL.md` is the wire format.

> Claude decides what to say. The performance director decides how the avatar
> should inhabit the moment.

## Two machines

| | |
|---|---|
| **Linux laptop** | i7-8550U, integrated graphics. Runs the orchestrator. **Cannot run Unreal at any useful quality.** |
| **Windows PC** | Discrete GPU. The renderer target. Phase 0 not started as of 2026-09-10. |

The orchestrator is deliberately built to run headless and keyless so progress
was never blocked on hardware that wasn't set up. `--scripted` replaces Claude
with canned replies through the real segmenter, TTS and bridge.

## Where things stand (2026-09-10)

Built and tested, 269 tests: protocol v1, the WebSocket bridge, the presence
layer with a previsualiser, the streaming Claude client, TTS with Piper, and the
Phase 1 demo wiring typed input to a spoken, animated reply.

Written but **never compiled**: `unreal/AmandaBridge/`, the C++ plugin. There is
no Unreal install on the machine it was written on.

Not started: Phase 0 — Unreal, a MetaHuman, lighting, look-dev. **The visual
premise is entirely unproven.** Everything so far is the half testable without a
GPU, and §17 is blunt that if the neutral render is unconvincing, animation does
not rescue it.

## The one open risk

MetaHuman's real-time audio solver is a **Live Link source that reads an audio
capture device**. The plan assumes a virtual audio cable (VB-CABLE) can feed it —
orchestrator plays TTS into it, Unreal consumes it as a microphone. **That is an
inference, not a confirmed fact**; Epic's setup page is JavaScript-rendered and
could not be read directly.

It is the only step whose outcome could change the architecture, and it is an
hour to test: install VB-CABLE, play a WAV into it, see whether a Live Link
source moves the face. **Do this before building anything in Unreal that depends
on it.** Fallbacks: a runtime ONNX audio-to-face plugin from Fab, or baking each
utterance offline (kills latency, proves everything else).

This is why audio does **not** travel over the avatar protocol: `speech.started`
is a cue about audio arriving by a completely separate route.

## Decisions not to silently reverse

Each of these cost something to learn.

- **Cancellation cancels a task, never sets a flag.** A flag only takes effect
  at the next token or chunk, and during a barge-in there may not be one for a
  while. Applies to `StreamedTurn`, `SynthesisStream` and `SpeechSession`.
- **The renderer is told before the audio fades.** §13: the visual transition
  leads. It is what makes an interruption feel like being interrupted.
- **After a barge-in, record what was *spoken*, not what was generated.** The
  tail was cancelled before synthesis, so as far as the conversation is
  concerned it was never said.
- **The client never sends `thinking`.** On Opus 5 that means adaptive thinking
  governed by `effort`. Explicitly disabling it can put tool calls into visible
  text and leak `<thinking>` tags into speech.
- **Out-of-range coefficients: Python rejects, Unreal clamps.** Python is the
  producer, where failing loudly is how a director bug gets fixed. Unreal is the
  consumer, where refusing a message only means the avatar misses a direction.
- **Piper runs in-process.** Each CLI invocation costs ~3.5s of load; loaded
  once it runs at ~9x realtime. `warm()` pays that at startup.
- **The first phrase may be shorter than the rest** (24 chars vs 40). It alone
  decides when speech *starts*; later phrases synthesise while earlier audio
  plays. Worth ~480ms.
- **Config must never block startup.** A missing or unparseable file degrades to
  defaults with a warning.
- **Whisper and Piper both load once, never per use.** Same lesson twice:
  loading costs seconds, running costs hundreds of milliseconds. `warm()` pays
  it at startup.
- **Silence is suppressed before it becomes a turn.** Whisper transcribes
  digital zero as "You" with high confidence; segments scoring above 0.6 on
  `no_speech_prob` are dropped.
- **One microphone stream, many consumers.** Endpointing, barge-in and later a
  wake word all subscribe to the same capture stream. Sequential exclusive
  ownership — each opening and closing the device in turn — cannot support
  barge-in, which needs the microphone live while the avatar speaks. See
  `docs/jarvis-overlap.md`.

- **Warm before opening the microphone.** A capture stream running with
  nothing subscribed discards what it hears, so loading a model first would
  leave a cold start deaf for as long as the load took, without saying so.
- **Filter noise on voiced audio, not buffer length.** An utterance always
  carries its pre-roll and the silence that ended it, so a 0.4s cough arrives
  as a 1.3s buffer. `Utterance.voiced_ms` is the number to threshold.

## Measured, not assumed

Re-measure rather than trusting these; they are dated, single-sample, and this
machine.

- **Time to first token dominates a turn.** `claude-sonnet-5` ~670ms against
  `claude-opus-5` ~2200ms on the same prompt. Sonnet is the shipped default on
  those grounds. Full table in the orchestrator README.
- **Sonnet 5 accepts mid-conversation system messages**, which the interruption
  note needs — the docs say it does not, and are wrong as of 2026-09-10.
- **Transcription costs ~880ms** with `base.en` on this laptop, the second
  largest term in a turn. `tiny.en` saves 300ms and mishears proper nouns.
- **Voices differ in word-gap rate**, which reads as staccato: `cori-medium`
  0.79/sec against `jenny_dioco` 1.29. `cori-medium` at pace 1.55 is the
  configured voice. Piper's `length_scale` is markedly non-linear.

## Working conventions

- `cd avatar-orchestrator && .venv/bin/python -m pytest -q` and
  `.venv/bin/ruff check src tests tools` before committing. Both should be
  clean.
- Config lives in `config/*.yaml` and is read, not decorative. A config file
  nothing loads is dead weight that drifts — that happened twice here.
- Credentials come from the environment or `.env` (gitignored). Never in config,
  never logged, never in a commit.
- `voices/` holds Piper models and is gitignored.
- The Unreal fixture is generated: after a protocol change run
  `python3 tools/regenerate_unreal_fixture.py` so both implementations are
  tested against the same bytes.

## What tends to be wrong

Proxy metrics disagreed with Rob's ear three times, and the ear was right each
time: words-per-minute said the delivery was already fast when it sounded slow;
peak amplitude said audio was loud when it was inaudible on laptop speakers;
a gap-rate metric was only ever a proxy for staccato. Measure to *locate* a
problem, not to overrule the person listening.

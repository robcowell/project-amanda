# Project Amanda

A real-time conversational digital human driven by Claude — aiming for
restrained facial presence rather than a talking head that performs constantly.

> Claude decides what to say. The performance director decides how the avatar
> should inhabit the moment.

`claude-digital-human-avatar-build-plan.md` is the plan this follows.

## Two processes

| | |
|---|---|
| **`avatar-orchestrator/`** | Python. Microphone, STT, Claude, TTS, the performance director, and the bridge that drives the avatar. |
| **`unreal/AmandaBridge/`** | An Unreal plugin. Receives protocol v1 over a local WebSocket and broadcasts it to Blueprints. |

They speak **protocol v1**, specified in
[`avatar-orchestrator/PROTOCOL.md`](avatar-orchestrator/PROTOCOL.md). The
orchestrator is the server and the renderer is the client, because the
conversation outlives the renderer: Unreal can be restarted mid-conversation
without taking Claude or the conversation state down with it.

Audio does **not** travel over that protocol. It reaches Unreal by a separate
route — a virtual audio cable into a MetaHuman Audio Live Link source — so the
speech events are cues about audio arriving elsewhere.

## Where things stand

**The orchestrator half is built; the renderer half is not.** Speak to it and
Claude answers aloud, with the face driven by the same protocol Unreal will
receive — but that face is currently a schematic one in a browser.

Built and tested, 427 tests:

- protocol v1, and the WebSocket bridge that carries it;
- the presence layer — blink, gaze, breathing, drift — with a previsualiser
  for tuning it;
- the Claude client: streaming turns, phrase segmentation, cancellation;
- speech synthesis behind a provider-neutral interface, with Piper as the
  default engine;
- the input path: microphone fan-out, voice activity detection, endpointing,
  Whisper, barge-in and a wake word;
- the conversation state machine, and its animation envelope per state;
- the performance director — a second, cheap call classifying how each reply
  should be delivered — and the smoothing that keeps it restrained;
- per-turn telemetry: one JSON object per turn, appended to a gitignored file,
  with transcripts off by default.

On the renderer side, against UE 5.8.2:

- the C++ bridge subsystem compiles, and its conformance suites pass in the
  editor's automation runner, decoding a fixture the Python side generates;
- MetaHuman Live Link accepts a virtual audio cable as a real-time audio
  source -- the assumption the whole architecture rested on, tested at last.

Not started, and the reason the premise is still unproven: **the visual half.**
No MetaHuman, no lighting, no look-dev. Audio reaches a running solver; whether
a face moves convincingly from it is a different question, and section 17 of the
plan is blunt that if the neutral render is unconvincing, animation does not
rescue it. See [`unreal/PHASE0.md`](unreal/PHASE0.md).

## Getting started

```sh
cd avatar-orchestrator
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest

.venv/bin/python tools/previz.py                 # http://127.0.0.1:8766/previz.html
.venv/bin/python tools/serve_sample_session.py --loop   # in another terminal
```

That drives a stand-in renderer with a realistic conversational turn, exercising
every event in the protocol without Claude, TTS or a microphone. To hold a real
conversation instead, set `ANTHROPIC_API_KEY` and run `python -m amanda.main`
(add `--voice` to speak to it, `--scripted` to run with no key at all);
[`avatar-orchestrator/README.md`](avatar-orchestrator/README.md) covers the rest.

## The assumption everything rested on

The plan assumed a virtual audio cable could carry TTS from the orchestrator
into a MetaHuman Audio Live Link source. It was the only open question that
could have changed the architecture, and on 2026-09-10 it was answered: yes.
VB-CABLE enumerates in MetaHuman Live Link at 48 kHz stereo float, and a
subject created on it starts the solver pipeline and runs.

Repeat it on any new machine, half of it without an engine at all:

```sh
.venv/bin/python tools/audio_route_check.py --list
.venv/bin/python tools/audio_route_check.py --say "Can you see my face move?"
```

If audio played by this process cannot be captured from a recording device,
there is no point installing anything else until it can. The Unreal half is
`unreal/Amanda/Scripts/list_audio_devices.py`; see
[`unreal/PHASE0.md`](unreal/PHASE0.md).

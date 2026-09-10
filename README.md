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

Phase 1 is complete end to end: type a message and hear Claude answer through
the avatar, with the face driven by the same protocol Unreal will receive.

Built and tested:

- protocol v1, and the WebSocket bridge that carries it;
- the presence layer — blink, gaze, breathing, drift — with a previsualiser
  for tuning it;
- the Claude client: streaming turns, phrase segmentation, cancellation;
- speech synthesis behind a provider-neutral interface, with Piper as the
  default engine;
- performance smoothing: transitions, hysteresis and decay.

Written but never compiled (no Unreal on the development machine):

- the C++ bridge subsystem and its conformance tests.

Still stubs: the microphone loop — capture, voice activity detection and STT —
the performance director's classifier call, and the conversation state machine.

## Getting started

```sh
cd avatar-orchestrator
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest

.venv/bin/python tools/previz.py                 # http://127.0.0.1:8766/previz.html
.venv/bin/python tools/serve_sample_session.py --loop   # in another terminal
```

That drives a stand-in renderer with a realistic conversational turn, exercising
every event in the protocol without Claude, TTS or a microphone.

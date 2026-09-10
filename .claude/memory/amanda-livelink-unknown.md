---
name: amanda-livelink-unknown
description: Project Amanda's pivotal unverified assumption — that MetaHuman's real-time audio Live Link can be fed by a virtual audio cable
metadata:
  type: project
---

Project Amanda's whole real-time lip-sync story rests on an assumption nobody
has tested.

MetaHuman's "audio-driven animation" is two different features. The offline one
bakes a SoundWave into a facial animation track in the editor. The real-time one
is a **MetaHuman Audio Live Link source**, and Epic's docs say it takes input
from an *audio capture device*. The inference is that a virtual audio cable
(VB-CABLE on Windows) will work — the orchestrator plays TTS into it, Unreal
consumes it as a microphone. That is **not confirmed anywhere**, and Epic's
setup page is JavaScript-rendered so it could not be read directly.

This is why `audio/sink.py` puts PCM on a *named device* rather than sending it
over the avatar protocol: audio and control events reach Unreal by completely
separate routes.

**Why:** it is the only step whose outcome could change the architecture, and it
is cheap to test — install VB-CABLE, play a WAV into it, see whether a Live Link
source moves the face. An hour or two.

**How to apply:** spike this before building anything in Unreal that depends on
it. Fallback ladder if it fails: a runtime ONNX audio-to-face plugin from Fab,
or baking each utterance offline (kills latency, proves everything else). Full
plan at https://claude.ai/code/artifact/a7cb1947-dd1a-4ab7-bcbc-3039a3c9751c —
see [[amanda-machine-split]].

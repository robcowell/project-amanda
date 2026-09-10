# Overlap with J.A.R.V.I.S.

A cross-reference against `~/code/jarvis`, a working voice assistant of Rob's
that predates this project. Read on 2026-09-10; roughly 3,900 lines of Python,
OpenAI-based, Raspberry Pi console talking to a Windows core over HTTP.

The overlap is real but **asymmetric**: Jarvis has a complete version of the
half Amanda has not built, and an architecture that is the opposite of what
Amanda needs. Take its constants and its device-handling lore; do not take its
ownership model.

## What Jarvis has that Amanda lacks

Amanda's Phase 2 (epic 5) is entirely stubs. Jarvis has all of it, working and
tuned by use.

| Jarvis | Amanda equivalent |
|---|---|
| `console/record.py` | `audio/microphone.py`, `audio/vad.py` |
| `transcribe.py` | `audio/stt.py` |
| `console/wake_listener.py`, `wakeword.py` | nothing — wake word is not in the build plan |
| `console/speech_manager.py` | `audio/speech.py` |
| `core/skills/` (registry, manifest, router) | nothing — §28 "environmental awareness" |
| `shared/memory/` | nothing — §2 persistent memory, Phase 7 |

### Endpointing constants worth taking as-is

From `console/record.py`. These are the kind of numbers nobody derives; they
come from listening and adjusting.

| Setting | Value | What it does |
|---|---|---|
| speech threshold | 0.012 RMS | above this a frame counts as voiced |
| silence to end | 0.75 s | quiet for this long ends the utterance |
| minimum speech | 0.35 s | shorter than this is a cough, not a turn |
| pre-roll | 2 frames (~128 ms) | see below |
| no-speech timeout | 2.0 s | give up if nothing is said |
| max duration | 6.0 s | **too short for Amanda** — see caveats |

**Pre-roll is the non-obvious one.** Jarvis keeps a small ring buffer of frames
from *before* speech was detected and prepends them once it triggers. Without
it the first syllable is clipped, because detection necessarily lags onset. It
is four lines and it is the difference between "urn the lights on" and "turn
the lights on".

### The generation counter

`SpeechManager.stop_speech()` bumps a counter that invalidates everything
queued; runners call `assert_not_interrupted(generation)` and stale queue
entries are dropped on dequeue. Cleaner than a per-item cancellation flag if
turns can ever overlap. Amanda serialises turns so it does not need this today.

## What does not transfer, and why

**Jarvis cannot do duplex audio, and its code says so:**

```python
# Explicit mic handoff: release wake recorder before main speech capture starts.
destroy_recorder(recorder)
```

with eight retries and a 0.15 s delay, because the device does not release
cleanly. One owner of the microphone at a time, strictly sequential: wake →
release → record → transcribe → think → speak → back to wake.

Amanda's §13 barge-in **requires the microphone live while the avatar speaks**.
So the ownership model inverts: Amanda needs one continuously-open capture
stream fanned out to several consumers — endpointing, barge-in, and later a
wake word — rather than each taking exclusive possession in turn.

Everything upstream is batch too. `transcribe.py` writes a WAV and uploads it;
`brain.py` uses non-streaming `responses.create`. Amanda streams throughout,
which is most of why a turn is ~1.2 s.

### Other caveats

- **`max_duration` of 6 s** suits commands ("turn on the kitchen light") and is
  far too short for conversation. Amanda should allow ~20 s and rely on silence
  detection rather than a hard cap.
- **16 kHz mono** throughout, which is right for STT and unrelated to Amanda's
  22050 Hz output rate. Input and output rates need not match.
- **PvRecorder** is Picovoice's, bundled with Porcupine. Amanda already depends
  on `sounddevice`/PortAudio for output; using it for input as well avoids a
  second audio stack.

## What Amanda can give back

`console/speak.py` spawns a fresh `piper` subprocess **per utterance**:

```python
piper_cmd = [piper_path, "--model", model_path, "--output-raw"]
piper_process = subprocess.Popen(piper_cmd, ...)
```

Measured here on 2026-09-10, each `piper` invocation spends about **3.5 seconds**
loading the interpreter, onnxruntime and the model before producing a sample —
a four-character phrase costs 3.68 s of wall time for 0.50 s of audio. Jarvis
pays that on every reply. `audio/piper_provider.py` loads once and runs at ~9x
realtime; porting it is the largest speed-up available to Jarvis and the code
already exists.

The phrase segmenter would help too: Jarvis waits for a complete reply before
speaking, so its time-to-first-audio includes the entire generation.

## Worth unifying rather than duplicating

- **`core/skills/`** — a working registry/manifest/router for tool dispatch.
  Amanda's §28 environmental awareness (time, weather, calendar, media) is that
  problem, unsolved.
- **`shared/memory/`** — preferences, configuration and storage, mapping onto
  §2's persistent memory and Phase 7's conversation history.
- **`docs/distributed-architecture.md`** — console on a Pi, core on a Windows
  PC, HTTP between them, automatic fallback to local when core is unreachable.
  Structurally the same problem as Amanda's Linux-orchestrator /
  Windows-renderer split, already solved once.
- **`_env_float(name, config_key, fallback, minimum)`** — env var, then config,
  then default, with clamping. More mature than Amanda's config handling.

## Echo, which neither project has solved

If the microphone hears the avatar's own voice through the speakers, barge-in
fires on every reply. Jarvis avoids this by construction: it is not listening
while it speaks.

Amanda avoids it **by accident of the target architecture** — output goes to a
virtual audio cable that Unreal reads, not to speakers, so the microphone never
hears it. That safety disappears the moment anyone develops with the default
output device selected, which is exactly what happens on a laptop. Raising the
barge-in threshold helps and does not fix it; acoustic echo cancellation is the
real answer and is not written.

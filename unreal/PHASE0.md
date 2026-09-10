# Phase 0 — the renderer, from nothing

Everything built so far is the half that could be tested without a GPU. This is
the other half, and the build plan is blunt about why it comes first (§17): if
the neutral, unanimated render is unconvincing, no amount of animation rescues
it. A MetaHuman that looks wrong standing still looks wrong talking.

**Exit criterion:** a MetaHuman in a lit scene, driven by the orchestrator over
protocol v1, with its face moving in real time from audio the orchestrator
played. Everything below is in the order that answers the riskiest question
soonest.

## Where this has got to (2026-09-10)

The steps below were written before any of them had been done, and the first
day on the renderer machine overtook several without amending them. As things
stand:

| Step | |
|---|---|
| 1. Virtual cable | **Blocked.** VB-CABLE not installed, and the machine has no active capture device at all — paired-but-disconnected Bluetooth headsets do not count. Install, reboot, then `--list`. |
| 2. Unreal 5.8.2 | Done. `D:\unreal\UE_5.8`. Accepted MSVC 14.50; the 14.44 fallback below was not needed. |
| 3. The project | Done. `unreal/Amanda`, with `Plugins/AmandaBridge` a junction back to `unreal/AmandaBridge`. |
| 4. Build the plugin | Done. Compiles against 5.8.2. |
| 5. Prove the bridge | Half. The four conformance suites pass in the automation runner; the live socket into a running editor has not been tried. |
| 6. A MetaHuman in a lit scene | Not started. MetaHuman ships inside 5.8.2 and is enabled. |
| 7. The Live Link audio spike | Not started, and gated on step 1. Narrowed, though: see CLAUDE.md — the source enumerates WASAPI endpoints, which is what a virtual cable registers as, and it can be created from script. |

The riskiest question is still unanswered, and it is still step 1.

## The machine

Measured 2026-09-10 on the Windows PC, before any of the above:

| | |
|---|---|
| GPU | RTX 2080 Ti, 11 GB, driver 616.92 |
| Compilers | VS Community 2026 (MSVC 14.50), VS Build Tools 2019 (14.29) |
| Windows SDK | 10.0.26100 and 10.0.19041 |
| Disk | 351 GB free on D:, 140 GB on C: |
| Installed | Epic Games Launcher |
| Not installed | Unreal Engine, MetaHuman content, any virtual audio cable |

UE 5.7 documents MSVC **14.44** as its preferred toolchain, and 14.50 is newer
than that. If the build refuses the toolchain, the fix is to add the v14.44
component in the Visual Studio Installer — a few GB, not a reinstall. Do not
pre-empt it; 5.8 shipped after VS 2026 and may be happy with what is here.

---

## 1. The virtual cable — do this before downloading an engine

The one assumption that could change the architecture: MetaHuman's real-time
audio solver is a **Live Link source that reads an audio capture device**, and
the plan feeds it by playing TTS into a virtual audio cable that Unreal reads as
a microphone. It has never been tested.

It is really two assumptions, and only the second needs Unreal:

1. audio can travel from the orchestrator into a *capture* device;
2. the MetaHuman Audio Live Link source will accept *that* device.

Step 1 costs ten minutes and no bandwidth. If it fails, nothing downstream can
work and finding out now is worth a great deal.

```sh
# The driver package is already downloaded and unpacked; run the installer as
# Administrator, then reboot. It installs a kernel audio driver -- the two
# halves do not appear in the device list until the machine has restarted.
#   VBCABLE_Setup_x64.exe   ->  right click, Run as administrator

cd avatar-orchestrator
.venv/Scripts/python tools/audio_route_check.py --list
```

Expect **CABLE Input** in the output list and **CABLE Output** in the input
list. One without the other means the driver installed but did not enumerate;
reboot again.

Then route real audio through it:

```sh
.venv/Scripts/python tools/audio_route_check.py --save captured.wav
.venv/Scripts/python tools/audio_route_check.py --say "Hello. Can you see my face move?"
```

A PASS means the orchestrator can put audio somewhere a recording device picks
it up, which is the whole of assumption 1. Listen to `captured.wav` rather than
trusting the number — a level check cannot tell clean audio from a stutter, and
this project has been wrong about audio three times by trusting a metric over an
ear.

## 2. Unreal Engine 5.8

Epic Games Launcher → sign in → *Unreal Engine* → *Library* → **+** → 5.8.

Install **to D:**. Options worth setting at install time:

- **Starter Content** — not needed, and it is several GB of furniture.
- **Engine source / debug symbols** — skip. They roughly double the install and
  are only useful when debugging engine internals, which this project does not.
- **Target platforms** — Windows only.

Budget 100 GB and an evening of downloading. While it runs, step 1 above is
already done and step 5 can be read.

## 3. The project

Create it from the editor: **Games → Blank → C++** (not Blueprint), named
`Amanda`, with ray tracing left off for now.

It must be a **C++ project**: a Blueprint-only project has no build pipeline, so
there is nothing to compile `AmandaBridge` with. Almost no C++ gets written by
hand — the rule stays "Blueprint first, C++ only where justified" — but the
pipeline has to exist.

Plugins to enable (*Edit → Plugins*, restart when asked):

- **MetaHuman** — the character tooling.
- **MetaHuman Live Link** — required for real-time animation from an audio
  source. This is the one the whole spike depends on.
- **Live Link** — the framework the above plugs into.

Epic's requirements for the real-time path: an Unreal Engine 5.6 or later
project with the MetaHuman Live Link plugin enabled, and a connected audio or
mono video device.

## 4. Build the bridge plugin

`AmandaBridge` has been written but **never compiled** — there was no Unreal on
the machine that wrote it. This is its first contact with a compiler, so expect
to fix something.

```sh
# From the repo, into the new project:
#   unreal/AmandaBridge  ->  <Project>/Plugins/AmandaBridge
```

Then right-click `Amanda.uproject` → *Generate Visual Studio project files*, and
build. `unreal/AmandaBridge/README.md` lists the three places most likely to
need a nudge, in order of likelihood — automation test flags moved in 5.5, a
JSON out-parameter signature, and thread affinity on WebSocket callbacks.

Fixes belong back in the repo, not only in the project's copy. Copy the folder
in rather than editing it in place, or symlink it and edit through the link:

```powershell
New-Item -ItemType SymbolicLink -Path "<Project>\Plugins\AmandaBridge" `
         -Target "D:\code\project-amanda\unreal\AmandaBridge"
```

## 5. Prove the bridge before touching a face

Two checks, in this order, and neither needs a MetaHuman.

**The conformance tests.** *Tools → Session Frontend → Automation*, filter
`Amanda`, run. The sample-session fixture is generated by the Python reference
implementation, so this decoder is tested against the exact bytes the
orchestrator sends.

**A real conversation's worth of events.** On this machine:

```sh
cd avatar-orchestrator
.venv/Scripts/python tools/serve_sample_session.py --loop --speed 2
```

Press Play. The output log should show the bridge connecting and 39 events per
loop. Stop and restart the Python side mid-replay: the bridge should reconnect
and receive a fresh `avatar.reset` without anyone touching Unreal. That
behaviour is deliberate — the conversation outlives the renderer.

## 6. A MetaHuman in a lit scene

Get a character (MetaHuman Creator, or a preset from Fab) into a level, and
spend real time on lighting before judging anything. §17 of the build plan is
the standard to hold it to: the neutral render is the product, and animation is
an amplifier of whatever it already is.

Judge it **still**, with no animation running at all. If it reads as a
waxwork at rest, fix that before wiring anything up.

## 7. The Live Link audio spike

The remaining half of the risk, and now a narrow question: does the device
picker list a virtual device?

*Window → Virtual Production → Live Link* → **Add Source** → **MetaHuman
(Audio)** → pick **CABLE Output** from the audio device list.

Then, from this machine:

```sh
.venv/Scripts/python tools/audio_route_check.py --say "Hello. Can you see my face move?"
```

Watch the face, not the log. A moving mouth is the answer.

### If CABLE Output is not in the list

The fallback ladder, best first:

1. **Another virtual driver.** Voicemeeter (`winget install VB-Audio.Voicemeeter`)
   presents its cables differently and may enumerate where VB-CABLE does not.
2. **A runtime audio-to-face plugin** from Fab — an ONNX solver taking a buffer
   rather than a device. Keeps real-time, changes which component does the
   solving.
3. **Bake each utterance offline.** Kills the latency story, but proves
   everything else in the pipeline and is a real fallback rather than a defeat.

If it comes to 2 or 3, the orchestrator changes very little: audio already
travels by its own route and `speech.started` is only ever a *cue* about audio
arriving elsewhere. That separation was built for exactly this.

## What to write down

Anything measured here goes in `CLAUDE.md` under **Measured, not assumed**, with
a date. Particularly: whether the cable enumerated, whether Live Link accepted
it, what the toolchain turned out to want, and what the render looked like
before any animation. The first three are facts that stop being true; the last
one is the reason the project exists.

## Sources

- [Real-Time Animation for MetaHumans in Unreal Engine](https://dev.epicgames.com/documentation/metahuman/realtime-animation-for-metahumans-in-unreal-engine)
- [Using a MetaHuman Audio Source](https://dev.epicgames.com/documentation/en-us/metahuman/using-a-metahuman-audio-source)
- [Audio Driven Animation](https://dev.epicgames.com/documentation/metahuman/audio-driven-animation)

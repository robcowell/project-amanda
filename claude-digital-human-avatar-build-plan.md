# Claude Digital Human Avatar --- Technical Build Plan

**Status:** Concept / prototype plan\
**Goal:** Build a real-time, photorealistic conversational avatar driven
by Claude, aiming for the restrained facial presence and performance
quality associated with *Detroit: Become Human* rather than a
conventional "AI talking head".

> The target is the *quality and interaction style*, not the reuse of
> any *Detroit: Become Human* character, mesh, animation, voice, or
> other protected asset.

------------------------------------------------------------------------

## 1. Product vision

Create a desktop conversational companion in which:

1.  The user speaks naturally through a microphone.
2.  Speech is transcribed locally or through an STT service.
3.  Claude receives the conversation and streams its response.
4.  A separate performance layer converts conversational context into a
    small set of non-verbal directions.
5.  A TTS engine speaks Claude's response.
6.  Unreal Engine renders a persistent MetaHuman-like character.
7.  Speech drives lip movement while the performance layer drives gaze,
    expression, posture, blinks, pauses and other subtle behaviours.

The desired result is not an avatar that constantly "performs". It
should often simply **be present**.

The central design principle is:

> **Claude decides what to say. The performance director decides how the
> avatar should inhabit the moment.**

------------------------------------------------------------------------

## 2. Success criteria

### MVP

A successful first prototype should:

-   run locally on a Windows PC;
-   render one high-quality MetaHuman in Unreal Engine;
-   accept microphone input;
-   send transcribed speech to Claude;
-   stream Claude's text response;
-   synthesize that response into speech;
-   produce convincing real-time lip sync;
-   maintain natural blinking and breathing while idle;
-   shift gaze rather than stare continuously at the camera;
-   support a small emotional/performance vocabulary;
-   return to a neutral attentive state after speaking;
-   achieve conversational latency low enough that the interaction does
    not feel broken.

### Longer-term target

The mature system should add:

-   interruption / barge-in;
-   persistent conversational memory;
-   subtle emotional continuity across turns;
-   gesture and upper-body animation;
-   awareness of whether the user is present;
-   optional camera-based eye contact;
-   expressive pauses during generation;
-   local environmental context;
-   multiple voices/personas;
-   a "quiet presence" mode where the avatar remains on screen without
    demanding attention.

------------------------------------------------------------------------

## 3. High-level architecture

``` text
┌─────────────────┐
│   Microphone    │
└────────┬────────┘
         │ audio
         ▼
┌─────────────────┐
│ Speech-to-Text  │
└────────┬────────┘
         │ transcript
         ▼
┌───────────────────────────────────────┐
│        Conversation Orchestrator      │
│                                       │
│  conversation state                   │
│  Claude API client                    │
│  performance director                 │
│  interruption handling                │
│  logging / metrics                    │
└───────┬───────────────────┬───────────┘
        │                   │
        │ text stream       │ performance state
        ▼                   ▼
┌───────────────┐    ┌──────────────────┐
│      TTS      │    │ Avatar Controller│
└───────┬───────┘    └────────┬─────────┘
        │ audio               │ expression/gaze/
        │                     │ posture events
        └──────────┬──────────┘
                   ▼
          ┌─────────────────┐
          │  Unreal Engine  │
          │    MetaHuman    │
          │                 │
          │ facial solver   │
          │ animation graph │
          │ gaze controller │
          │ idle behaviour  │
          └─────────────────┘
```

------------------------------------------------------------------------

## 4. Recommended technology stack

### Avatar and rendering

**Unreal Engine 5 + MetaHuman**

This is the most direct route to the visual target. Epic describes
MetaHuman as its framework for creating and animating fully rigged
photoreal digital humans. MetaHuman Creator is integrated into Unreal
Engine from UE 5.6 onward.

Recommended starting point:

-   Unreal Engine 5.6 or later;
-   MetaHuman Creator;
-   MetaHuman Animator;
-   MetaHuman Live Link;
-   Control Rig;
-   Animation Blueprints;
-   Niagara only where genuinely useful for environmental effects ---
    not facial animation.

Epic's current MetaHuman tooling supports real-time animation from audio
and mono video sources, while its audio-driven workflow can generate
facial animation, blinks, head movement and selectable mood states.

### LLM

**Claude Messages API**

Use Anthropic's Messages API rather than trying to automate the Claude
web UI.

Important capabilities:

-   streamed responses over SSE;
-   structured tool use;
-   system prompts;
-   multi-turn conversational state managed by the application;
-   incremental text delivery suitable for starting TTS before the
    entire response has completed.

The orchestrator should hide the specific Claude model behind
configuration rather than hard-code the application to one model
generation.

### Speech-to-text

Implement STT behind an interface so it can be swapped.

Candidate approaches:

-   local Whisper-family implementation;
-   cloud transcription service;
-   OS/native speech service for an early prototype.

For the first build, optimise for **latency and reliable endpoint
detection**, not theoretical transcription perfection.

### Text-to-speech

Treat TTS as a separate service. Do not couple the architecture to
Claude for voice generation.

Required TTS features:

-   streaming or low-latency synthesis;
-   timestamps or phoneme/viseme information if available;
-   consistent voice identity;
-   controllable pace;
-   preferably controllable prosody;
-   interruption/cancellation support.

Abstract it as:

``` text
TTS.speak(text, performance_state) -> audio_stream + timing_metadata
```

This allows experimentation with cloud and local speech engines without
changing the rest of the system.

### Middleware / orchestrator

Recommended:

**Python for prototype → TypeScript or Python retained for production
depending on results.**

Python is attractive initially because it makes experimentation with
audio, local ML models and orchestration extremely quick.

Responsibilities:

-   microphone capture;
-   VAD;
-   STT;
-   Claude API;
-   TTS;
-   performance-state generation;
-   WebSocket communication with Unreal;
-   conversation state;
-   latency telemetry;
-   cancellation.

### Unreal communication

Use a **local WebSocket connection** between the orchestrator and
Unreal.

Example messages:

``` json
{
  "type": "performance",
  "state": "thoughtful",
  "intensity": 0.32,
  "gaze": "away_then_user",
  "duration_ms": 1800
}
```

``` json
{
  "type": "speech_start",
  "utterance_id": "u_1042",
  "audio": "stream",
  "mood": "warm_neutral"
}
```

``` json
{
  "type": "speech_stop",
  "utterance_id": "u_1042"
}
```

Do not make Unreal responsible for calling Claude directly. Keep
rendering and cognition separated.

------------------------------------------------------------------------

## 5. The performance model

This is the part that determines whether the result feels like a
character or a lip-synced mannequin.

### 5.1 Performance state

Maintain a compact state object:

``` json
{
  "attention": 0.9,
  "engagement": 0.7,
  "valence": 0.1,
  "arousal": 0.2,
  "confidence": 0.8,
  "thoughtfulness": 0.6,
  "amusement": 0.1,
  "surprise": 0.0,
  "confusion": 0.0
}
```

These are **animation controls**, not claims that Claude literally
experiences emotions.

### 5.2 Named performance presets

Map continuous state into restrained presets:

-   neutral_attentive
-   listening
-   considering
-   mildly_amused
-   warm
-   concerned
-   uncertain
-   confused
-   surprised
-   explaining
-   enthusiastic
-   serious

Avoid a simplistic one-emotion-per-sentence system.

### 5.3 Performance directions

The director may emit:

``` json
{
  "preset": "considering",
  "intensity": 0.28,
  "eye_contact": 0.55,
  "head_motion": 0.18,
  "brow_activity": 0.12,
  "smile": 0.03,
  "gesture_probability": 0.08
}
```

**Low values should be normal.**

A major failure mode of AI avatars is exaggerated activity. The avatar
should be capable of spending several seconds doing almost nothing.

------------------------------------------------------------------------

## 6. Separate cognition from performance

Do **not** ask Claude to fill every response with stage directions such
as:

``` text
[smiles thoughtfully]
Well, Rob...
```

Instead use one of two architectures.

### Option A --- second lightweight model call

1.  Claude generates the actual answer.
2.  A fast model receives the user's message plus the response.
3.  It emits a tiny structured performance description.

Advantages:

-   clean separation;
-   easy to tune;
-   no contamination of spoken text;
-   performance schema can evolve independently.

Disadvantage:

-   another inference call.

### Option B --- structured side-channel

Prompt Claude to emit machine-readable performance metadata separately
from user-visible speech.

This can reduce calls but risks coupling linguistic generation and
animation too tightly.

### Recommendation

Start with **Option A**.

Performance classification should be cheap, fast and heavily
constrained.

------------------------------------------------------------------------

## 7. Conversation state machine

``` text
IDLE
 │
 ├── user detected ──────────────► ATTENTIVE
 │
 ▼
LISTENING
 │
 ├── speech endpoint
 ▼
ACKNOWLEDGING
 │
 ▼
THINKING
 │
 ├── Claude begins streaming
 ▼
PREPARING_SPEECH
 │
 ├── first playable audio chunk
 ▼
SPEAKING
 │
 ├── user interrupts ────────────► INTERRUPTED
 │
 └── speech completes
 ▼
SETTLING
 │
 ▼
ATTENTIVE / IDLE
```

Each state should have its own animation envelope.

For example, **THINKING** should not mean "look theatrically puzzled".
It may mean:

-   eye contact drops slightly;
-   gaze moves off-axis;
-   head becomes still;
-   occasional blink;
-   perhaps a very small brow movement.

When speech begins, gaze can naturally return.

------------------------------------------------------------------------

## 8. Gaze is critical

A digital human that stares continuously into the virtual camera will
feel wrong even if the face is excellent.

Implement a dedicated gaze controller.

### Gaze targets

-   user/camera;
-   slightly left;
-   slightly right;
-   down;
-   distant neutral point;
-   object of interest, later.

### Rules

During listening:

-   mostly user-facing;
-   occasional brief natural gaze shifts.

During thinking:

-   reduce direct eye contact;
-   permit longer off-axis fixation.

During speaking:

-   return to the user for important phrases;
-   allow small departures while constructing longer explanations.

During idle:

-   do not continuously track the user.

Use stochastic timing within controlled bounds so the same sequence does
not repeat mechanically.

------------------------------------------------------------------------

## 9. Idle behaviour

Idle animation deserves its own subsystem.

Possible layers:

1.  breathing;
2.  microscopic head drift;
3.  blink scheduler;
4.  gaze scheduler;
5.  posture adjustment;
6.  occasional swallow;
7.  tiny facial tension changes.

These should operate at different frequencies.

The desired effect is:

> "This character is currently doing nothing."

not:

> "An idle animation loop is playing."

Avoid obvious repeated cycles.

------------------------------------------------------------------------

## 10. Speech and facial animation

There are three possible implementation levels.

### Level 1 --- quickest prototype

Feed completed TTS audio into MetaHuman's audio-driven facial animation.

Use this to validate:

-   character appearance;
-   framing;
-   speech quality;
-   overall emotional effect.

### Level 2 --- real-time speech

Stream TTS audio into Unreal and use real-time audio-driven MetaHuman
animation / Live Link.

This is the target for interactive conversation.

### Level 3 --- enhanced performance

Combine:

-   audio-driven mouth/jaw movement;
-   performance-state facial curves;
-   independent gaze;
-   independent blink system;
-   head/neck animation;
-   upper-body gestures.

The lip solver should control the parts it understands well. The
performance layer should **add**, not fight, the speech solve.

------------------------------------------------------------------------

## 11. Latency strategy

Perceived latency matters more than raw benchmark latency.

Instrument:

``` text
T0 user stops speaking
T1 transcript finalised
T2 Claude request sent
T3 first Claude token
T4 first speakable phrase
T5 TTS first audio available
T6 avatar begins speaking
```

Primary metric:

``` text
response_latency = T6 - T0
```

### Hide unavoidable latency with behaviour

Between T0 and T6:

-   acknowledge the end of the user's speech visually;
-   shift into a thinking pose;
-   blink;
-   briefly move gaze;
-   return attention just before speech.

This converts "the computer is waiting" into "the character is
considering".

Do not insert fake verbal fillers merely to conceal latency.

------------------------------------------------------------------------

## 12. Streaming text into TTS

Do not wait for Claude's entire answer.

Buffer streamed tokens until reaching a safe phrase boundary.

Example:

``` text
Claude stream
   │
   ▼
token accumulator
   │
   ├── comma + sufficient length
   ├── sentence boundary
   └── timeout
          │
          ▼
      TTS queue
```

The synthesizer can begin speaking phrase 1 while Claude generates
phrase 2.

Need safeguards for:

-   abbreviations;
-   numbers;
-   quotations;
-   code;
-   Markdown;
-   corrections caused by premature chunking.

A simple sentence/phrase segmenter is adequate for MVP.

------------------------------------------------------------------------

## 13. Barge-in / interruption

This is essential for the system eventually to feel conversational.

When user speech is detected while the avatar is speaking:

1.  confirm sustained voice activity rather than a cough/noise;
2.  cancel the Claude stream if still running;
3.  cancel queued TTS;
4.  fade current audio rapidly;
5.  transition face from speaking to listening;
6.  retain the interrupted assistant response in conversation state
    appropriately;
7.  transcribe the user's interruption;
8.  begin the next turn.

The visual transition should happen almost immediately.

------------------------------------------------------------------------

## 14. Unreal project structure

Suggested layout:

``` text
Content/
  Avatar/
    Characters/
    Animation/
    ControlRig/
    Materials/
  Behaviour/
    ABP_Avatar
    BP_GazeController
    BP_IdleController
    BP_PerformanceController
    BP_SpeechController
  Networking/
    BP_AvatarBridge
  Environment/
  UI/
```

Core Unreal components:

### `BP_AvatarBridge`

Receives local WebSocket events and dispatches them.

### `BP_PerformanceController`

Interpolates high-level performance states into animation parameters.

### `BP_GazeController`

Selects gaze targets and controls eye/head contribution.

### `BP_IdleController`

Schedules low-frequency natural behaviour.

### `BP_SpeechController`

Handles audio playback, utterance lifecycle and facial speech animation.

### `ABP_Avatar`

Combines the animation layers and ensures smooth transitions.

------------------------------------------------------------------------

## 15. Orchestrator project structure

``` text
avatar-orchestrator/
  src/
    audio/
      microphone.py
      vad.py
      stt.py
      tts.py

    claude/
      client.py
      conversation.py
      prompts.py

    performance/
      director.py
      schema.py
      smoothing.py

    avatar/
      websocket.py
      protocol.py

    runtime/
      state_machine.py
      interruption.py
      metrics.py

    main.py

  config/
    avatar.yaml
    voices.yaml

  tests/
```

------------------------------------------------------------------------

## 16. Avatar protocol

Keep the protocol engine-neutral.

Example:

``` json
{
  "version": 1,
  "event": "performance.update",
  "timestamp": 1788967200.125,
  "payload": {
    "preset": "mildly_amused",
    "intensity": 0.22,
    "transition_ms": 450
  }
}
```

Other events:

``` text
session.started
session.ended

user.detected
user.speech_started
user.speech_ended

assistant.thinking_started
assistant.thinking_ended

speech.prepare
speech.started
speech.completed
speech.cancelled

performance.update
gaze.set_target
gesture.trigger
avatar.reset
```

Version the protocol from day one.

------------------------------------------------------------------------

## 17. Character direction

Do not start by trying to reproduce Connor, Kara or Chloe.

Build an original character.

Desired qualities:

-   adult;
-   believable rather than idealised;
-   expressive eyes;
-   skin with visible texture;
-   restrained makeup/styling;
-   clothing with a clean contemporary silhouette;
-   neutral background initially;
-   soft cinematic lighting;
-   head-and-upper-torso framing.

The character should look credible while **silent and neutral** before
animation work begins.

If the neutral render is unconvincing, animation will not rescue it.

------------------------------------------------------------------------

## 18. Performance tuning principles

### Less is more

Start every animation coefficient lower than feels necessary.

### Asymmetry

Perfectly symmetrical facial movement reads as synthetic.

### Don't animate everything together

A smile does not require:

-   eyebrow lift;
-   head tilt;
-   eye squint;
-   nod;
-   shoulder movement;

all at once.

### Allow stillness

Human conversation contains surprisingly long periods of low movement.

### Avoid automatic nodding

Use nods to signal specific acknowledgement, not as a perpetual
listening loop.

### Blink independently

Do not tie every blink to speech boundaries.

### Emotion has inertia

Do not snap from "concerned" to "happy" because the next sentence has a
joke.

Use a decaying performance state.

------------------------------------------------------------------------

## 19. Phase plan

### Phase 0 --- feasibility spike

**Timebox: 1--2 evenings**

-   install current Unreal Engine;
-   create/import one MetaHuman;
-   establish cinematic close-up scene;
-   test MetaHuman audio-driven animation with prerecorded speech;
-   determine GPU performance at desired resolution.

**Exit criterion:** a photoreal character convincingly speaks arbitrary
prerecorded audio.

------------------------------------------------------------------------

### Phase 1 --- Claude speaks through the avatar

**Timebox: 2--4 evenings**

Build:

``` text
text input
  → Claude
  → TTS
  → Unreal
  → MetaHuman speech
```

No microphone yet.

**Exit criterion:** type a message and receive a spoken, animated Claude
response.

------------------------------------------------------------------------

### Phase 2 --- full voice loop

Add:

``` text
microphone
  → VAD
  → STT
  → Claude
  → TTS
  → avatar
```

Measure every latency stage.

**Exit criterion:** sustain a natural five-minute spoken conversation.

------------------------------------------------------------------------

### Phase 3 --- presence

Add:

-   idle breathing;
-   blink scheduler;
-   gaze controller;
-   listening state;
-   thinking state;
-   settling state.

**Exit criterion:** avatar looks plausible for 60 seconds while saying
nothing.

This is deliberately a hard requirement.

------------------------------------------------------------------------

### Phase 4 --- performance director

Add structured performance classification and state smoothing.

Start with only:

``` text
neutral
warm
thoughtful
amused
concerned
uncertain
surprised
```

**Exit criterion:** blind observation shows visible differences without
the performance looking theatrical.

------------------------------------------------------------------------

### Phase 5 --- interruption

Add:

-   duplex microphone handling;
-   barge-in detection;
-   Claude cancellation;
-   TTS cancellation;
-   animation cancellation/transition.

**Exit criterion:** interrupting the avatar feels normal rather than
like stopping a media player.

------------------------------------------------------------------------

### Phase 6 --- upper-body performance

Only after the face works.

Add:

-   posture;
-   occasional hand gesture;
-   weight shift;
-   head/neck emphasis.

Keep gesture frequency low.

------------------------------------------------------------------------

### Phase 7 --- persistent companion mode

Add:

-   launch on login;
-   borderless/windowed presentation;
-   optional always-on-top mode;
-   conversation history;
-   presence detection;
-   sleep/away behaviour;
-   configuration UI.

------------------------------------------------------------------------

## 20. First prototype backlog

### Epic 1 --- Unreal

-   [ ] Install UE and MetaHuman components.
-   [ ] Create original MetaHuman.
-   [ ] Build close-up lighting environment.
-   [ ] Lock camera/framing.
-   [ ] Validate frame rate.
-   [ ] Test audio-driven facial animation.
-   [ ] Create basic neutral idle state.

### Epic 2 --- Claude

-   [ ] Create Anthropic API project/key.
-   [ ] Implement Messages API client.
-   [ ] Enable streaming.
-   [ ] Store conversation history.
-   [ ] Implement cancellation.
-   [ ] Add latency instrumentation.

### Epic 3 --- TTS

-   [ ] Define provider-neutral interface.
-   [ ] Select initial engine.
-   [ ] Generate WAV/PCM.
-   [ ] Stream audio where possible.
-   [ ] Implement cancellation.
-   [ ] Capture timing metadata.

### Epic 4 --- bridge

-   [ ] Start local WebSocket server.
-   [ ] Connect Unreal client.
-   [ ] Define protocol v1.
-   [ ] Send state events.
-   [ ] Send speech lifecycle events.
-   [ ] Add reconnect behaviour.

### Epic 5 --- voice input

-   [ ] Capture microphone.
-   [ ] Add VAD.
-   [ ] Add STT.
-   [ ] Detect end of utterance.
-   [ ] Add interruption detection.

### Epic 6 --- presence

-   [ ] Blink scheduler.
-   [ ] Gaze scheduler.
-   [ ] Thinking gaze.
-   [ ] Listening gaze.
-   [ ] Breathing.
-   [ ] Micro head movement.
-   [ ] Performance smoothing.

------------------------------------------------------------------------

## 21. Deliberately deferred features

Do **not** begin with:

-   full-body locomotion;
-   room-scale 3D environment;
-   VR;
-   elaborate hand gestures;
-   multiple avatars;
-   custom face scanning;
-   emotion detection from the user's face;
-   autonomous screen watching;
-   long-term agent autonomy;
-   smart-home control.

Each is interesting. None proves the core idea.

The first problem is simply:

> **Can a photoreal digital human, Claude, speech synthesis and
> restrained non-verbal behaviour combine into a conversation that feels
> qualitatively different from a voice assistant?**

------------------------------------------------------------------------

## 22. Key technical risks

  -----------------------------------------------------------------------
  Risk                    Consequence             Mitigation
  ----------------------- ----------------------- -----------------------
  TTS latency             Long dead air           streaming synthesis +
                                                  thinking animation

  LLM latency             character appears       explicit THINKING state
                          frozen                  

  uncanny facial motion   destroys illusion       reduce animation
                                                  intensity

  excessive eye contact   unsettling avatar       independent stochastic
                                                  gaze

  poor lip sync           obvious artificiality   test MetaHuman audio
                                                  solver early

  animation layer         facial twitching        strict ownership of
  conflicts                                       facial regions/curves

  TTS provider lock-in    expensive redesign      provider-neutral
                                                  interface

  Unreal complexity       prototype stalls        Blueprint first; C++
                                                  only where justified

  GPU load                poor real-time          profile MetaHuman
                          experience              quality levels
                                                  immediately

  performance metadata    mood flicker            smoothing, hysteresis
  instability                                     and emotional inertia

  IP temptation           unusable public project original character and
                                                  assets only
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 23. Testing

Create repeatable test conversations covering:

1.  neutral factual question;
2.  joke;
3.  sad or serious subject;
4.  disagreement;
5.  uncertainty;
6.  rapid follow-up;
7.  interruption;
8.  long explanation;
9.  silence;
10. user walks away.

Record the avatar output.

Review specifically for:

-   eye behaviour;
-   blinking;
-   head movement;
-   expression intensity;
-   timing of expression changes;
-   lip sync;
-   dead-air handling;
-   repeated loops;
-   emotional discontinuities.

The question is not "does the animation work?"

It is:

> **What movement drew attention to itself as animation?**

Anything that repeatedly does should be reduced or removed.

------------------------------------------------------------------------

## 24. Performance telemetry

Log each turn:

``` json
{
  "stt_ms": 410,
  "claude_first_token_ms": 620,
  "tts_first_audio_ms": 290,
  "total_response_ms": 1480,
  "interrupted": false,
  "performance": "thoughtful"
}
```

Also capture:

-   FPS;
-   GPU frame time;
-   audio underruns;
-   WebSocket latency;
-   TTS queue depth;
-   Claude stream duration;
-   utterance length.

This turns "it feels sluggish" into something diagnosable.

------------------------------------------------------------------------

## 25. Security and privacy

For a local desktop prototype:

-   keep API keys outside source control;
-   use environment variables or OS credential storage;
-   bind the avatar WebSocket to localhost by default;
-   do not retain microphone audio unless explicitly enabled;
-   make transcript logging configurable;
-   visibly indicate when microphone capture is active;
-   define a clear retention policy before adding persistent memory.

------------------------------------------------------------------------

## 26. Recommended first implementation

The shortest useful path is:

``` text
Windows PC
   │
   ├── Python orchestrator
   │     ├── Claude Messages API
   │     ├── simple TTS provider
   │     └── local WebSocket
   │
   └── Unreal Engine
         ├── MetaHuman
         ├── MetaHuman Animator
         ├── audio-driven facial animation
         └── basic gaze/idle Blueprint
```

Initially use **typed input rather than microphone input**.

That removes STT and voice-activity detection from the first experiment
and lets the project answer the most important question immediately:

**Does Claude embodied in a convincing, restrained digital human
actually feel compelling?**

If yes, add the microphone loop.

------------------------------------------------------------------------

## 27. Definition of "Detroit-like"

For this project, "Detroit-like" should mean:

-   high-quality digital human rendering;
-   excellent eyes;
-   believable skin;
-   subtle facial movement;
-   restrained acting;
-   convincing stillness;
-   cinematic lighting;
-   strong facial animation;
-   emotional legibility without caricature.

It should **not** mean:

-   copied characters;
-   extracted game assets;
-   cloned actor likenesses;
-   copied voices;
-   reverse-engineered proprietary animation data.

This keeps the project both technically cleaner and creatively more
interesting.

------------------------------------------------------------------------

## 28. Future experiments

Once the core system works:

### Camera-aware eye contact

Use a webcam to estimate whether the user is looking toward the avatar
and adjust gaze behaviour.

### Environmental awareness

Allow selected tools to expose:

-   local time;
-   weather;
-   calendar;
-   currently playing media;
-   active application;

without giving the avatar unrestricted machine access.

### Object attention

If Claude discusses something visible on screen, allow the avatar's gaze
to move toward it.

### Emotional continuity

Maintain a slowly changing conversational performance state rather than
classifying each utterance independently.

### Local speech stack

Experiment with fully local STT/TTS to reduce latency and cloud
dependency.

### Dedicated display

Eventually run the avatar on a portrait monitor or secondary screen as a
persistent presence.

------------------------------------------------------------------------

## 29. Immediate next action

Build **Phase 0 only**.

1.  Install/configure Unreal Engine.
2.  Create an original MetaHuman.
3.  Put the character in a simple dark studio environment.
4.  Frame head and upper torso.
5.  Feed it arbitrary recorded speech.
6.  Tune lighting and facial animation until it is convincing.

Only then connect Claude.

That prevents the project becoming an integration exercise before
establishing whether the visual premise works.

------------------------------------------------------------------------

## 30. Reference documentation

-   Anthropic --- Claude API overview:
    https://platform.claude.com/docs/en/api/overview
-   Anthropic --- Messages API:
    https://platform.claude.com/docs/en/api/messages/create
-   Anthropic --- Streaming Messages:
    https://platform.claude.com/docs/en/build-with-claude/streaming
-   Epic Games --- MetaHuman documentation:
    https://dev.epicgames.com/documentation/en-us/metahuman/metahuman-documentation
-   Epic Games --- MetaHuman Animator:
    https://dev.epicgames.com/documentation/metahuman/metahuman-animator-in-unreal-engine
-   Epic Games --- Real-Time Animation:
    https://dev.epicgames.com/documentation/metahuman/realtime-animation-for-metahumans-in-unreal-engine
-   Epic Games --- Audio Driven Animation:
    https://dev.epicgames.com/documentation/metahuman/audio-driven-animation

------------------------------------------------------------------------

## 31. Working title

**Project Amanda**

A nod to the opening *Detroit: Become Human* menu experience without
copying the character itself.

Alternative, more technically descriptive:

**Project Presence**

That may ultimately fit the real objective better: not merely giving
Claude a face, but giving the conversation a sense of physical presence.

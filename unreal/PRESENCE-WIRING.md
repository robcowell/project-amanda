# Presence on the face

**This is done and automated.** It was going to be a hand-wiring job in the
animation graph; it is not, and this document is now a description rather than
a set of instructions. Kept because the *why* is worth having when it next
needs changing, and because the failure modes are still real.

```
Scripts/wire_presence_to_face.py     # reparent the face blueprint, set curve names
Scripts/add_presence_component.py    # attach presence to the character
Scripts/bind_face_blueprint.py       # point the face at ABP_AmandaFace
```

All three must run after any character rebuild: `BP_MHC_Seo` and its face live
under `/Game/MetaHumans`, which `assemble_metahuman.py` regenerates and git
ignores.

---

## How it works

The presence layer — blink, gaze, breath, drift — runs as
`UAmandaPresenceComponent` on the character, ticking and subscribed to the
orchestrator's bridge. It is a port of
`avatar-orchestrator/src/amanda/presence/schedulers.py`, which is the reference
implementation and holds the reasoning behind every constant.

Getting those numbers onto the rig looked like animation-graph work, which
cannot be scripted: graphs are authored by hand and Python cannot reach them.
It isn't. `UAnimInstance::AddCurveValue` sets a named curve on the evaluated
pose directly, so `UAmandaFaceAnimInstance` — a native parent class for
`ABP_AmandaFace` — writes presence into the rig every frame with no nodes
wired at all. The blueprint keeps every graph Epic shipped in it; only its
parent changed.

`NativeUpdateAnimation` runs before the graph evaluates and the curves it sets
apply to the result, so presence layers *after* the Live Link pose. The mouth
is untouched: nothing here writes a jaw or lip curve, and nothing should. The
mouth is solved from audio by MetaHuman and presence has no business overriding
it.

## The curves it drives

Not guesses. `UAmandaFaceCurveLibrary::ListCurveNames` reports 2038 curves on
this rig; these are the eight that matter. The rig also carries ARKit aliases
(`EyeBlinkLeft`), but the `CTRL_expressions_*` controls are what the face board
actually drives.

| Purpose | Curves |
|---|---|
| Blink | `CTRL_expressions_eyeBlinkL`, `CTRL_expressions_eyeBlinkR` |
| Look left | `CTRL_expressions_eyeLookLeftL`, `CTRL_expressions_eyeLookLeftR` |
| Look right | `CTRL_expressions_eyeLookRightL`, `CTRL_expressions_eyeLookRightR` |
| Look up | `CTRL_expressions_eyeLookUpL`, `CTRL_expressions_eyeLookUpR` |
| Look down | `CTRL_expressions_eyeLookDownL`, `CTRL_expressions_eyeLookDownR` |

They are configuration, not constants, on the blueprint's class defaults — a
different MetaHuman may name them differently. To find out what a rig offers:

```
unreal.AmandaFaceCurveLibrary.list_curve_names(mesh, "eyeBlink")
```

`LidOpen` is 1.0 for **open**; blink curves want the inverse, and the anim
instance does that conversion. If she ever sits with her eyes serenely shut,
that inversion is the first place to look.

## Verifying

Animation blueprints do not evaluate in the editor viewport, so **Simulate or
Play**, never the viewport. Verifying by eye in the viewport cost an afternoon
here.

```
Scripts/simulate_and_shoot.py    # burst of frames while the world ticks
```

Then measure rather than squint — the eye region against the median frame. Last
run, 24 frames: baseline 0.7–1.0, one frame at **6.53** (a blink caught
mid-closure) and a seven-frame plateau at ~3.9 (a held gaze shift). Presence
sampled directly over 40 seconds: 14 blinks (21/min, humans do 10–20), lid
range 0.000–1.000, eye yaw −12.3° to +11.9°, all five gaze targets visited.

## What is not done

**Brows and the rest of the performance.** The director's preset and intensity
reach the component and stop there. `FaceState.Preset` and `.Intensity` are
published and nothing consumes them, so the face has presence but no
*expression*.

**Breath.** Published, unused. It belongs on the body — chest and shoulders in
`ABP_Body_PostProcess` — not the face.

**Head rotation.** `PresenceHeadRotation` is computed and exposed but not
applied; the head needs either the anim graph's `ARKit_HeadRotation` or a bone
modifier, and neither is wired.

## If it stops working

**Nothing moves.** Check you are in Simulate or Play. Then check `bHasPresence`
on the anim instance — false means the component was not found on the actor.

**It worked, then stopped after a rebuild.** The three scripts at the top.
`/Game/MetaHumans` is regenerated wholesale.

**Eyes shut permanently.** The `LidOpen` inversion.

**Head twitches.** Presence and Live Link both claiming it. `LLink_Face_Head`
should be false if presence ever drives the head.

## What the values mean

| Field | Range | Notes |
|---|---|---|
| `LidOpen` | 1.0 open to 0.0 closed | Blink curves take the inverse |
| `EyeYaw` / `EyePitch` | +/-16 deg | Includes micro-saccades; never perfectly still |
| `HeadYaw` / `HeadPitch` / `HeadRoll` | +/-3 deg | Gaze lag plus drift. Deliberately tiny |
| `Breath` | 0.0 to 1.0 | Chest, not face |
| `Preset` / `Intensity` | — | From the performance director. Unconsumed |
| `GazeTarget` | enum | Diagnostics |

Change behaviour in `schedulers.py` first and port it, so the reference
implementation and the renderer do not drift apart.

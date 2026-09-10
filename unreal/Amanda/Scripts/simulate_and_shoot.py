"""Photograph the character while the world is actually ticking.

The editor viewport does not evaluate an animation blueprint, so a Live Link
face holds its rest pose there no matter how correctly it is wired -- which
looks exactly like a broken subject and cost an afternoon. The flag that would
change that, `bUpdateAnimationInEditor`, cannot be set on a Blueprint's
components: "cannot be edited on templates".

Simulate ticks the world without possessing a pawn, which is enough: animation
blueprints run, Live Link evaluates, and the face moves if anything is driving
it. This starts Simulate, lets it settle, takes a burst through the level's
portrait camera, and stops.

Play speech into the cable *before* running this, and compare the frames
afterwards -- by measuring them, not by looking at one and hoping:

    python - <<'PY'
    import numpy as np; from PIL import Image; from pathlib import Path
    f = sorted(Path('Saved/Screenshots/WindowsEditor').glob('*.png'))
    a = [np.asarray(Image.open(p).convert('L'), dtype=np.int16) for p in f]
    print(max(np.abs(a[i]-a[j]).mean() for i in range(len(a)) for j in range(i+1, len(a))))
    PY

Anti-aliasing jitter alone measures about 0.4 per pixel. A moving jaw is an
order of magnitude more than that.
"""

import os

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
CAMERA_LABEL = "PortraitCamera"

OPEN_TICKS = 240
#: Time for the world to settle once it is ticking, and for audio to arrive.
SETTLE_TICKS = 600
SHOTS = int(os.environ.get("AMANDA_SHOTS", "8"))
SHOT_GAP_TICKS = int(os.environ.get("AMANDA_SHOT_GAP", "20"))

state = {"ticks": 0, "handle": None, "phase": "opening", "taken": 0, "next": 0}


def world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def levels():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def compose():
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if isinstance(actor, unreal.CineCameraActor):
            levels().pilot_level_actor(actor)
            levels().editor_set_game_view(True)
            print(f"### piloting {actor.get_actor_label()}")
            return True
    print("### no cine camera in the level")
    return False


def tick(delta_seconds):
    state["ticks"] += 1

    if state["phase"] == "opening":
        if state["ticks"] < OPEN_TICKS:
            return
        levels().load_level(LEVEL)
        print(f"### loaded {LEVEL}")
        if not compose():
            state["phase"] = "quitting"
            return
        levels().editor_play_simulate()
        print("### simulating -- the world is ticking now")
        state["phase"] = "settling"
        return

    if state["phase"] == "settling":
        if state["ticks"] < OPEN_TICKS + SETTLE_TICKS:
            return
        state["phase"] = "shooting"
        state["next"] = state["ticks"]
        return

    if state["phase"] == "shooting":
        if state["ticks"] < state["next"]:
            return
        unreal.SystemLibrary.execute_console_command(world(), "HighResShot 1920x1080")
        state["taken"] += 1
        print(f"### frame {state['taken']}/{SHOTS}")
        state["next"] = state["ticks"] + SHOT_GAP_TICKS
        if state["taken"] >= SHOTS:
            state["phase"] = "stopping"
            state["next"] = state["ticks"] + 120
        return

    if state["phase"] == "stopping":
        if state["ticks"] < state["next"]:
            return
        levels().editor_request_end_play()
        print("### simulation stopped")
        state["phase"] = "quitting"
        state["next"] = state["ticks"] + 120
        return

    if state["ticks"] < state.get("next", 0):
        return
    unreal.unregister_slate_post_tick_callback(state["handle"])
    print("### done")
    unreal.SystemLibrary.execute_console_command(world(), "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### simulate-and-shoot scheduled")

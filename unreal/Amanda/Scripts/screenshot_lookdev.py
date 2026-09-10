"""Photograph the look-dev level through its own camera.

Section 17's judgement is made on a still, neutral frame, and it should be made
on the *same* still frame every time -- same lights, same lens, same exposure --
so that "better" and "worse" mean something. This pilots the level's portrait
camera, waits for the render to settle, and writes a high-resolution shot to
`Saved/Screenshots`.

Run it against a real editor rather than a commandlet: a commandlet has no
renderer, and screenshots from one are either blank or refused.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\screenshot_lookdev.py"

The wait is not politeness. A MetaHuman on first load is a queue of shader
compiles, streamed textures and hair grooms, and a screenshot taken early shows
grey skin and no eyelashes -- which would read as a damning verdict on the
render rather than on the timing of the screenshot.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
CAMERA_LABEL = "PortraitCamera"

#: Roughly twenty seconds at 60fps of doing nothing while the engine catches up.
SETTLE_TICKS = 1200
#: And a moment after the shot is requested, before quitting out from under it.
FINISH_TICKS = 180

#: Nothing happens until the editor has been ticking for a while. `-ExecCmds`
#: fires during startup, when the level's actors do not exist yet -- looking for
#: the camera then finds an empty world and gives up.
OPEN_TICKS = 180

state = {"ticks": 0, "handle": None, "phase": "opening"}


def world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def find_camera():
    """The portrait camera, by label, or any cine camera in the level.

    The fallback matters: actor labels are an editor nicety and one that did not
    survive being set from a commandlet, and a screenshot from the wrong camera
    is still more use than no screenshot.
    """
    level_actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    for actor in level_actors:
        if actor.get_actor_label() == CAMERA_LABEL:
            return actor
    cameras = [a for a in level_actors if isinstance(a, unreal.CineCameraActor)]
    if cameras:
        print(f"### no actor labelled {CAMERA_LABEL}; using {cameras[0].get_actor_label()}")
        return cameras[0]
    print(f"### {len(level_actors)} actors in the level and no cine camera among them")
    return None


def compose():
    """Look through the portrait camera, with the editor's gizmos out of shot."""
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    camera = find_camera()
    if camera is None:
        print(f"### no actor labelled {CAMERA_LABEL} in {LEVEL}")
        return False
    levels.pilot_level_actor(camera)
    levels.editor_set_game_view(True)
    print(f"### piloting {CAMERA_LABEL}")
    return True


def shoot():
    unreal.SystemLibrary.execute_console_command(world(), "HighResShot 1920x1080")
    print("### screenshot requested -- written to Saved/Screenshots")


def tick(delta_seconds):
    state["ticks"] += 1

    if state["phase"] == "opening":
        if state["ticks"] < OPEN_TICKS:
            return
        levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        levels.load_level(LEVEL)
        print(f"### loaded {LEVEL}")
        state["phase"] = "settling" if compose() else "quitting"
        return

    if state["phase"] == "settling":
        if state["ticks"] < OPEN_TICKS + SETTLE_TICKS:
            if state["ticks"] % 300 == 0:
                print(f"### settling, {state['ticks'] - OPEN_TICKS}/{SETTLE_TICKS} ticks")
            return
        shoot()
        state["phase"] = "finishing"
        return

    if state["ticks"] >= OPEN_TICKS + SETTLE_TICKS + FINISH_TICKS:
        unreal.unregister_slate_post_tick_callback(state["handle"])
        print("### done")
        unreal.SystemLibrary.execute_console_command(world(), "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### screenshot scheduled once the editor is up")

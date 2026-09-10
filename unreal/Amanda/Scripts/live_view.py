"""Open the look-dev level, look through its camera, and start it running.

For watching rather than measuring. The editor viewport does not evaluate
animation blueprints, so "open the level and look" shows a still face however
well everything is wired -- this starts Simulate, which does tick, and then
gets out of the way. It does not quit; close the editor when you have seen
enough.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\live_view.py"

Play speech into the cable first if you want the mouth moving as well as the
eyes; without it you get presence alone, which is worth seeing on its own.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"

#: `-ExecCmds` fires during startup, before the level's actors exist.
OPEN_TICKS = 240

state = {"ticks": 0, "handle": None, "done": False}


def run():
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    levels.load_level(LEVEL)

    camera = None
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if isinstance(actor, unreal.CineCameraActor):
            camera = actor
            break

    if camera is not None:
        levels.pilot_level_actor(camera)
        levels.editor_set_game_view(True)
        print(f"### looking through {camera.get_actor_label()}")

    levels.editor_play_simulate()
    print("### simulating -- she is live. Close the editor when you are done.")


def tick(delta_seconds):
    state["ticks"] += 1
    if state["done"] or state["ticks"] < OPEN_TICKS:
        return
    state["done"] = True
    unreal.unregister_slate_post_tick_callback(state["handle"])
    try:
        run()
    except Exception as error:  # noqa: BLE001 - diagnostic, report anything
        print(f"### raised: {error!r}")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### live view scheduled")

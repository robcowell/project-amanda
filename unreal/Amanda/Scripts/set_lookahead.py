r"""Set how far ahead the speech solver listens, keep it, and show the result.

Rob, watching her speak: "the lips never touch for consonants". The MetaHuman
audio subject has one setting aimed squarely at that, `Lookahead`, which Epic's
header describes as "the amount of time, in milliseconds, that the audio solver
looks ahead into the audio stream to produce the current frame of animation. A
larger value will produce higher quality animation but will come at the cost of
increased latency." It ranges 80 to 240, and ours sat at the minimum.

Why it is a plausible cause rather than just a knob: p, b and m are closures
that happen *before* the sound that identifies them. With 80ms of lookahead the
solver has to commit to a mouth shape before it has heard the burst that says
the lips should have been shut. That is a hypothesis; this is the test.

The cost is latency, roughly one for one: 240 puts the mouth about 160ms further
behind the audio than 80 does. Whether that is worth it is for the eye, not for
this script -- start high to find out whether closure is achievable at all, then
walk it down until it goes.

It is persisted the only way a Live Link preset can be written: set it on the
live subject, then rebuild the preset from the client. The editor then stays
open in Simulate, looking through the portrait camera, so it can be watched.

    set AMANDA_LOOKAHEAD_MS=240
    UnrealEditor.exe Amanda.uproject -ExecCmds="py Scripts/set_lookahead.py"
"""

import os

import unreal

SUBJECT = "Amanda"
LOOKAHEAD_MS = int(os.environ.get("AMANDA_LOOKAHEAD_MS", "240"))
PRESET = "/Game/Amanda/LL_AmandaAudio"
LEVEL = "/Game/Amanda/Maps/LookDev"

#: The preset is applied during startup and the subject appears some seconds
#: later. Give up, and quit, rather than sit open doing nothing.
GIVE_UP_TICKS = 3000

state = {"ticks": 0, "handle": None, "phase": "subject", "next": 0}


def quit_editor():
    unreal.unregister_slate_post_tick_callback(state["handle"])
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, "QUIT_EDITOR")


def subject_settings():
    for key in unreal.LiveLinkBlueprintLibrary.get_live_link_subjects(True, True):
        if str(key.subject_name.get_editor_property("name")) == SUBJECT:
            return unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_subject_settings(key)
    return None


def set_and_save(settings):
    before = settings.get_editor_property("Lookahead")
    settings.set_lookahead(LOOKAHEAD_MS)
    after = settings.get_editor_property("Lookahead")
    print(f"### lookahead {before} -> {after} ms")
    if after != LOOKAHEAD_MS:
        print(f"### refused: asked for {LOOKAHEAD_MS}, subject kept {after}")
        return False

    preset = unreal.load_asset(PRESET)
    preset.build_from_client()
    saved = unreal.EditorAssetLibrary.save_loaded_asset(preset, only_if_is_dirty=False)
    print(f"### preset rebuilt from the client and saved: {saved}")
    return saved


def watch():
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    levels.load_level(LEVEL)
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if isinstance(actor, unreal.CineCameraActor):
            levels.pilot_level_actor(actor)
            levels.editor_set_game_view(True)
            break


def tick(delta_seconds):
    state["ticks"] += 1

    if state["phase"] == "subject":
        settings = subject_settings()
        if settings is None:
            if state["ticks"] >= GIVE_UP_TICKS:
                print(f"### subject {SUBJECT} never appeared -- is the preset applied?")
                quit_editor()
            return
        # Move on *before* saving: a save pumps the editor's tick, which
        # re-enters this callback, and with the phase unchanged it set and
        # saved three times over.
        state["phase"] = "level"
        state["next"] = state["ticks"] + 120
        if not set_and_save(settings):
            quit_editor()
        return

    if state["phase"] == "level":
        if state["ticks"] < state["next"]:
            return
        watch()
        state["phase"] = "simulate"
        state["next"] = state["ticks"] + 120
        return

    if state["phase"] == "simulate":
        if state["ticks"] < state["next"]:
            return
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_play_simulate()
        unreal.unregister_slate_post_tick_callback(state["handle"])
        print(f"### watching -- simulating with lookahead {LOOKAHEAD_MS} ms")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print(f"### lookahead {LOOKAHEAD_MS} ms scheduled")

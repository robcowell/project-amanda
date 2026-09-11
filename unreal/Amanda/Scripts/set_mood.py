r"""Set the speech solver's mood, keep it, and show the result.

Rob, watching her speak: "a little open-mouthed over many phonemes". The mouth
curves said why. At the default mood, AutoDetect, silence left her with dimples
(~0.11) and the lower lip pulled down enough to show teeth (~0.16) -- a slight
smile laid over everything, and speech built on top of it. Three moods were
measured in one session on 2026-09-11, same passage each time:

    at rest             AutoDetect 1.0   Neutral 1.0   AutoDetect 0.3
    dimples             0.10-0.12        0.03-0.05     0.11-0.13
    lower lip depress   0.15-0.17        0.05-0.07     0.12-0.16
    lips drawn apart    0.37-0.50        0.38-0.45     0.39-0.52

Neutral removed the smile; turning AutoDetect's intensity down did almost
nothing. Rob judged Neutral best by eye. What mood does not touch is the floor
under the lips and jaw (~0.4 and ~0.14 in silence) -- that is the solver's own
rest pose, and a dead-zone remap is the tool for it if it still reads as open.

Persisted the only way a Live Link preset can be written: set on the live
subject, then rebuild the preset from the client. The lookahead already in the
preset is carried through, and printed to prove it. The editor then stays open
in Simulate, looking through the portrait camera.

    set AMANDA_MOOD=NEUTRAL
    UnrealEditor.exe Amanda.uproject -ExecCmds="py Scripts/set_mood.py"
"""

import os

import unreal

SUBJECT = "Amanda"
MOOD_NAME = os.environ.get("AMANDA_MOOD", "NEUTRAL").upper()
MOOD_INTENSITY = float(os.environ.get("AMANDA_MOOD_INTENSITY", "1.0"))
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
    mood = getattr(unreal.AudioDrivenAnimationMood, MOOD_NAME, None)
    if mood is None:
        print(f"### no mood called {MOOD_NAME}")
        return False

    before = settings.get_editor_property("Mood")
    settings.set_mood(mood)
    settings.set_mood_intensity(MOOD_INTENSITY)
    after = settings.get_editor_property("Mood")
    intensity = settings.get_editor_property("MoodIntensity")
    print(f"### mood {before} -> {after}, intensity {intensity:.2f}")
    if after != mood:
        print(f"### refused: asked for {MOOD_NAME}, subject kept {after}")
        return False
    print(f"### lookahead carried through: {settings.get_editor_property('Lookahead')} ms")

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
        # re-enters this callback -- set_lookahead.py saved three times over
        # before learning this.
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
        print(f"### watching -- simulating with mood {MOOD_NAME}")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print(f"### mood {MOOD_NAME} at {MOOD_INTENSITY:.2f} scheduled")

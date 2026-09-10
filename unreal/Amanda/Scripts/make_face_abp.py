"""Give the project its own copy of the Live Link face blueprint.

The subject name lives on an `AnimNode_LiveLinkPose` inside the animation
graph, and Python cannot reach it: `AnimGraphNode_LiveLinkPose` exposes its
position and its title and nothing else, and `AnimBlueprint` does not expose its
graphs at all. So that one field has to be set by hand, once.

Which makes *where* it is set the important part. Assembly writes
`ABP_MH_LiveLink` into `/Game/MetaHumans/Common/Animation`, and that whole tree
is rebuilt by `assemble_metahuman.py` and gitignored as derived data -- a hand
edit there would be silently destroyed the next time the character is rebuilt,
and would never reach the repo.

This copies it to `/Game/Amanda/ABP_AmandaFace`, which is neither, and points
the character's face component at the copy. The edit then survives a rebuild
and travels with the project.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\make_face_abp.py"

Afterwards, by hand and only once: open `ABP_AmandaFace`, select the **Live Link
Pose** node, set **Subject Name** to `Amanda`, compile, save.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
SOURCE_ABP = "/Game/MetaHumans/Common/Animation/ABP_MH_LiveLink"
FACE_ABP_PATH = "/Game/Amanda"
FACE_ABP_NAME = "ABP_AmandaFace"

OPEN_TICKS = 240

state = {"ticks": 0, "handle": None, "done": False}


def our_copy():
    full = f"{FACE_ABP_PATH}/{FACE_ABP_NAME}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        print(f"### using existing {full}")
        return unreal.load_asset(full)

    if not unreal.EditorAssetLibrary.does_asset_exist(SOURCE_ABP):
        print(f"### {SOURCE_ABP} missing -- assemble the character first")
        return None

    copied = unreal.EditorAssetLibrary.duplicate_asset(SOURCE_ABP, full)
    print(f"### copied {SOURCE_ABP} -> {full}" if copied else "### copy failed")
    return copied


def character_face():
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if actor.get_actor_label() != "Amanda":
            continue
        for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
            if "face" in component.get_name().lower():
                return actor, component
    return None, None


def run():
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(LEVEL)

    blueprint = our_copy()
    if blueprint is None:
        return

    actor, face = character_face()
    if face is None:
        print("### no face component found on the character")
        return

    face.set_editor_property("anim_class", blueprint.generated_class())
    print(f"### {actor.get_actor_label()} face -> {FACE_ABP_NAME}")

    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    print(f"### saved level: {unreal.EditorLoadingAndSavingUtils.save_map(world, LEVEL)}")
    print(f"### now set the Live Link Pose node's Subject Name to 'Amanda' in {FACE_ABP_NAME}")


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
    print("### done")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### face blueprint copy scheduled once the editor is up")

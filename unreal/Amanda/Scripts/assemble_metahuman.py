"""Assemble imported MetaHuman characters, in an editor that can render.

Assembly cannot run in a commandlet. It builds the skeletal meshes happily and
then dies baking textures:

    Unhandled Exception: EXCEPTION_ACCESS_VIOLATION
    UnrealEditor-TextureGraph.dll
    UnrealEditor-MetaHumanDefaultEditorPipeline.dll

TextureGraph wants a real rendering device and a commandlet has none, so the
whole thing has to happen inside a running editor. Same shape as the Live Link
subject: register a tick callback, let the engine finish starting, then work.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\assemble_metahuman.py"

It quits the editor when it is finished, so it can be left to run.
"""

import os

import unreal

DESTINATION = "/Game/MetaHumans"

#: HIGH: one character filling the screen, no crowd to budget for.
QUALITY = os.environ.get("AMANDA_MH_QUALITY", "HIGH")

#: Let the editor finish opening before asking it to bake textures.
SETTLE_TICKS = 240

state = {"ticks": 0, "handle": None, "done": False}


def characters():
    found = []
    for path in unreal.EditorAssetLibrary.list_assets(DESTINATION, recursive=True):
        asset = unreal.load_asset(path)
        if isinstance(asset, unreal.MetaHumanCharacter):
            found.append(asset)
    return found


def assemble(character):
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    if not subsystem.try_add_object_to_edit(character):
        print(f"### {character.get_name()} is already open for edit")
        return

    try:
        params = unreal.MetaHumanCharacterEditorBuildParameters()
        params.pipeline_type = unreal.MetaHumanDefaultPipelineType.OPTIMIZED
        params.pipeline_quality = getattr(
            unreal.MetaHumanQualityLevel, QUALITY, unreal.MetaHumanQualityLevel.MEDIUM
        )
        params.absolute_build_path = DESTINATION
        params.common_folder_path = f"{DESTINATION}/Common"
        params.enable_wardrobe_item_validation = False
        print(f"### assembling {character.get_name()} at quality {QUALITY}")
        subsystem.build_meta_human(character=character, params=params)
        print(f"### assembled {character.get_name()}")
    finally:
        if subsystem.is_object_added_for_editing(character):
            subsystem.remove_object_to_edit(character)


def report():
    blueprints = []
    for path in unreal.EditorAssetLibrary.list_assets(DESTINATION, recursive=True):
        if isinstance(unreal.load_asset(path), unreal.Blueprint):
            blueprints.append(path)
    print(f"### {len(blueprints)} blueprints under {DESTINATION}:")
    for path in blueprints:
        print(f"###   {path}")


def run():
    found = characters()
    print(f"### {len(found)} MetaHuman characters to assemble")
    for character in found:
        assemble(character)

    unreal.EditorAssetLibrary.save_directory(DESTINATION, only_if_is_dirty=False)
    report()


def tick(delta_seconds):
    state["ticks"] += 1
    if state["done"] or state["ticks"] < SETTLE_TICKS:
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
print("### assembly scheduled once the editor is up")

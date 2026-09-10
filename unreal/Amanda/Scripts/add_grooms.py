"""Give the character hair, eyebrows, eyelashes and peach fuzz.

The Fab preset arrives with a face and no hair of any kind, and a browless bald
head reads as a mannequin however good the skin underneath is -- which made the
first look-dev render useless as a judgement of section 17. It was not
measuring the renderer, it was measuring a missing asset.

The grooms come from `MetaHuman Creator Core Data`, an optional component of
the *engine* install rather than anything in this project: Epic Games Launcher
-> Unreal Engine -> Library -> the engine tile's chevron -> Options. Roughly
6 GB, and well hidden. Without it `/MetaHumanCharacter/Optional/` does not
exist and everything below fails to load.

Run it in a real editor -- assembly bakes textures and needs a GPU:

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\add_grooms.py"

Peach fuzz is included deliberately. It is the one nobody thinks to add and the
one that does the most for close-up realism: real skin at conversational
distance is covered in fine vellus hair that catches the rim light, and its
absence is part of why CG faces look like polished stone.
"""

import os

import unreal

CHARACTER = os.environ.get("AMANDA_MH_ASSET", "/Game/MetaHumans/MHC_Seo")
DESTINATION = "/Game/MetaHumans"
QUALITY = os.environ.get("AMANDA_MH_QUALITY", "HIGH")

GROOMS = "/MetaHumanCharacter/Optional/Grooms/Bindings"

#: Slot name -> wardrobe item. Chosen for an ordinary adult woman in
#: conversation rather than for anything striking: the character has to survive
#: being looked at for a long time.
WARDROBE = {
    "Hair": f"{GROOMS}/Hair/WI_Hair_M_Layered.WI_Hair_M_Layered",
    "Eyebrows": f"{GROOMS}/Eyebrows/WI_Eyebrows_M_Natural.WI_Eyebrows_M_Natural",
    "Eyelashes": f"{GROOMS}/Eyelashes/WI_Eyelashes_L_SlightCurl.WI_Eyelashes_L_SlightCurl",
    "Peachfuzz": f"{GROOMS}/Peachfuzz/WI_Peachfuzz_M_Thin.WI_Peachfuzz_M_Thin",
}

OPEN_TICKS = 240

state = {"ticks": 0, "handle": None, "done": False}


def add_groom(character, slot, asset_path):
    item = unreal.load_asset(asset_path)
    if item is None:
        print(f"###   {slot}: {asset_path} did not load")
        return False

    collection = character.internal_collection
    key = collection.try_add_item_from_wardrobe_item(slot, item)
    if key is None:
        print(f"###   {slot}: refused as a wardrobe item")
        return False

    selection = unreal.MetaHumanPipelineSlotSelection(slot_name=slot, selected_item=key)
    if not collection.default_instance.try_add_slot_selection(selection):
        print(f"###   {slot}: added but not selected")
        return False

    print(f"###   {slot}: {asset_path.rsplit('/', 1)[-1].split('.')[0]}")
    return True


def assemble(subsystem, character):
    params = unreal.MetaHumanCharacterEditorBuildParameters()
    params.pipeline_type = unreal.MetaHumanDefaultPipelineType.OPTIMIZED
    params.pipeline_quality = getattr(
        unreal.MetaHumanQualityLevel, QUALITY, unreal.MetaHumanQualityLevel.MEDIUM
    )
    params.absolute_build_path = DESTINATION
    params.common_folder_path = f"{DESTINATION}/Common"
    params.enable_wardrobe_item_validation = False
    print(f"### reassembling at quality {QUALITY}")
    subsystem.build_meta_human(character=character, params=params)


def run():
    character = unreal.load_asset(CHARACTER)
    if character is None:
        print(f"### no character at {CHARACTER}")
        return

    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    if not subsystem.try_add_object_to_edit(character):
        print("### character already open for edit")
        return

    try:
        print(f"### dressing {character.get_name()}")
        added = sum(add_groom(character, slot, path) for slot, path in WARDROBE.items())
        print(f"### {added}/{len(WARDROBE)} grooms attached")
        if added:
            assemble(subsystem, character)
    finally:
        if subsystem.is_object_added_for_editing(character):
            subsystem.remove_object_to_edit(character)

    unreal.EditorAssetLibrary.save_directory(DESTINATION, only_if_is_dirty=False)

    grooms = [
        path
        for path in unreal.EditorAssetLibrary.list_assets(DESTINATION, recursive=True)
        if isinstance(unreal.load_asset(path), unreal.GroomAsset)
    ]
    print(f"### {len(grooms)} groom assets in the build")


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
print("### groom pass scheduled once the editor is up")

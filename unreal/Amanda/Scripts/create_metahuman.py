"""Create and assemble the MetaHuman, from the archetype the engine ships.

Phase 0's remaining question is the one the whole project rests on: does a
neutral, unanimated render read as a person? Section 17 of the build plan is
blunt that if it does not, animation will not rescue it. Nothing can be judged
until there is a character to look at.

Epic ships Python examples for every step of this
(`Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/Python/examples`), so
none of it needs clicking. This is those steps with the project's own names and
enough reporting to tell what actually happened:

  1. create a `MetaHumanCharacter` asset from the default archetype;
  2. fetch its texture sources, which may need an Epic login;
  3. run the assembly pipeline, which turns the character into the skeletal
     meshes, materials and Blueprint that go in a level.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -run=pythonscript -script="Scripts/create_metahuman.py" ^
        -unattended -nopause -nosplash

Assembly is not quick -- it rigs a face, bakes textures and compiles materials.
Expect minutes, not seconds, and expect the first run to be the slow one.

This creates a character from the *archetype*: the neutral starting point,
nobody in particular. That is deliberate for a first look. Sculpting a face that
suits the character comes after we know the pipeline produces something worth
sculpting, and it is the one part of this that genuinely wants a human eye and
the Creator's UI.
"""

import os

import unreal

NAME = os.environ.get("AMANDA_MH_NAME", "Amanda")
CHARACTER_PATH = "/Game/Characters/MetaHumans"
BUILD_PATH = "/Game/MetaHumans"

#: MEDIUM is what Epic's example uses. HIGH is the right target for a single
#: character filling the screen on a 2080 Ti -- there is no crowd to budget for,
#: and the face is the entire product.
QUALITY = os.environ.get("AMANDA_MH_QUALITY", "HIGH")


def character_asset():
    """The character asset, created if it is not already there."""
    full_path = f"{CHARACTER_PATH}/{NAME}.{NAME}"
    if unreal.EditorAssetLibrary.does_asset_exist(full_path):
        print(f"### using existing {full_path}")
        return unreal.load_asset(full_path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    character = tools.create_asset(
        asset_name=NAME,
        package_path=CHARACTER_PATH,
        asset_class=unreal.MetaHumanCharacter,
        factory=unreal.new_object(type=unreal.MetaHumanCharacterFactoryNew),
    )
    print(f"### created {full_path}" if character else "### could not create the character")
    return character


def fetch_textures(subsystem, character):
    """Ask for the character's texture sources.

    Separated and caught: this is the step most likely to want an Epic account,
    and a character with placeholder textures is still worth assembling. Better
    to report that than to fail the whole run on it.
    """
    try:
        request = unreal.MetaHumanCharacterTextureRequestParams()
        request.blocking = True          # required when running in batch
        request.report_progress = False
        subsystem.request_texture_sources(character, request)
        print("### texture sources fetched")
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        print(f"### texture sources unavailable: {error!r}")
        print("### continuing; the assembly below still produces a face to look at")


def assemble(subsystem, character):
    params = unreal.MetaHumanCharacterEditorBuildParameters()
    params.pipeline_type = unreal.MetaHumanDefaultPipelineType.OPTIMIZED
    params.pipeline_quality = getattr(
        unreal.MetaHumanQualityLevel, QUALITY, unreal.MetaHumanQualityLevel.MEDIUM
    )
    params.absolute_build_path = BUILD_PATH
    params.common_folder_path = f"{BUILD_PATH}/Common"
    params.enable_wardrobe_item_validation = False

    print(f"### assembling at quality {QUALITY} into {BUILD_PATH}")
    subsystem.build_meta_human(character=character, params=params)


def main():
    character = character_asset()
    if character is None:
        return

    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    if not subsystem.try_add_object_to_edit(character):
        print("### the character is already open for edit somewhere -- close it and retry")
        return

    try:
        fetch_textures(subsystem, character)
        assemble(subsystem, character)
    finally:
        if subsystem.is_object_added_for_editing(character):
            subsystem.remove_object_to_edit(character)

    unreal.EditorAssetLibrary.save_directory(CHARACTER_PATH, only_if_is_dirty=False)
    unreal.EditorAssetLibrary.save_directory(BUILD_PATH, only_if_is_dirty=False)

    built = unreal.EditorAssetLibrary.list_assets(BUILD_PATH, recursive=True)
    print(f"### {len(built)} assets under {BUILD_PATH}")
    for path in built:
        if any(hint in path for hint in ("BP_", "SK_", "Face", "Body")):
            print(f"###   {path}")
    print("### done")


main()

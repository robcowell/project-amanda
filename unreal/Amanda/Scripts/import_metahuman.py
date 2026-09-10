"""Import a MetaHuman package from Fab, and assemble it into usable assets.

The archetype route is gated on a cloud auto-rig service and an Epic login
inside the editor (see `create_metahuman.py`). A character bought or downloaded
from Fab arrives already rigged, as a `.mhpkg`, which the engine can import
without asking anyone to sign in to anything.

Fab exports to a folder rather than into the project -- its export-target list
has no Unreal option, only DCC applications -- so the package lands in
`Import/` (gitignored: it is large, redownloadable, and not ours to
redistribute) and this brings it the rest of the way.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -run=pythonscript -script="Scripts/import_metahuman.py" ^
        -unattended -nopause -nosplash

Assembly is the slow half: it builds skeletal meshes, compiles materials and
bakes textures. Minutes, and the first run is the worst.
"""

import os
from pathlib import Path

import unreal

IMPORT_DIR = Path(
    os.environ.get("AMANDA_MH_IMPORT", r"D:\code\project-amanda\unreal\Amanda\Import")
)
DESTINATION = "/Game/MetaHumans"

#: HIGH because there is one character filling the screen and no crowd to
#: budget for. The face is the entire product.
QUALITY = os.environ.get("AMANDA_MH_QUALITY", "HIGH")


def find_package():
    packages = sorted(IMPORT_DIR.rglob("*.mhpkg"))
    if not packages:
        print(f"### no .mhpkg under {IMPORT_DIR}")
        return None
    if len(packages) > 1:
        print(f"### {len(packages)} packages found, taking the first:")
        for package in packages:
            print(f"###   {package.name}")
    return packages[0]


def import_package(path):
    task = unreal.AssetImportTask()
    task.filename = str(path)
    task.destination_path = DESTINATION
    # Automated so it cannot stop on a dialog nobody is there to answer.
    task.automated = True
    task.replace_existing = True
    task.save = True

    print(f"### importing {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    imported = list(task.get_editor_property("imported_object_paths") or [])
    print(f"### imported {len(imported)} objects")
    for asset_path in imported:
        print(f"###   {asset_path}")
    return imported


def characters_under(path):
    found = []
    for asset_path in unreal.EditorAssetLibrary.list_assets(path, recursive=True):
        asset = unreal.load_asset(asset_path)
        if isinstance(asset, unreal.MetaHumanCharacter):
            found.append(asset)
    return found


def assemble(character):
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    if not subsystem.try_add_object_to_edit(character):
        print(f"### {character.get_name()} is already open for edit somewhere")
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
    finally:
        if subsystem.is_object_added_for_editing(character):
            subsystem.remove_object_to_edit(character)


def main():
    package = find_package()
    if package is None:
        return

    import_package(package)
    unreal.EditorAssetLibrary.save_directory(DESTINATION, only_if_is_dirty=False)

    characters = characters_under(DESTINATION)
    print(f"### {len(characters)} MetaHuman characters in {DESTINATION}")
    for character in characters:
        assemble(character)

    unreal.EditorAssetLibrary.save_directory(DESTINATION, only_if_is_dirty=False)

    blueprints = [
        path
        for path in unreal.EditorAssetLibrary.list_assets(DESTINATION, recursive=True)
        if isinstance(unreal.load_asset(path), unreal.Blueprint)
    ]
    print(f"### {len(blueprints)} blueprints built:")
    for path in blueprints:
        print(f"###   {path}")
    print("### done")


main()

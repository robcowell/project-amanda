"""Bind the face to our Live Link blueprint on the *Blueprint*, not the actor.

Setting `anim_class` on the placed actor's face component looks like it works.
The editor reports the override, the live instance is ours, and every still
taken in the viewport agrees. Then the world starts ticking, the MetaHuman
Blueprint reconstructs its components, and the face quietly reverts to
`ABP_Face_C`:

    Face: anim_class=ABP_Face_C  instance=ABP_Face_C   (in the game world)

So the override has to live on the Blueprint's component template, which is
what actually gets constructed. This edits `BP_MHC_Seo` through the subobject
subsystem, compiles it and saves.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\bind_face_blueprint.py"

Note that `BP_MHC_Seo` lives under /Game/MetaHumans, which assembly rebuilds and
git ignores. So this is part of the pipeline rather than a one-off: run it after
`assemble_metahuman.py` or `add_grooms.py`, every time.
"""

import os

import unreal

CHARACTER_BP = os.environ.get("AMANDA_CHARACTER_BP", "/Game/MetaHumans/MHC_Seo/BP_MHC_Seo")
FACE_ABP = "/Game/Amanda/ABP_AmandaFace"

OPEN_TICKS = 240
state = {"ticks": 0, "handle": None, "done": False}


def face_template(blueprint):
    """The Face skeletal mesh component template inside the Blueprint."""
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)
    print(f"### {len(handles)} subobjects in {blueprint.get_name()}")

    for handle in handles:
        data = subsystem.k2_find_subobject_data_from_handle(handle)
        obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
        if obj is None:
            continue
        print(f"###   {obj.get_name()} ({obj.get_class().get_name()})")
        if isinstance(obj, unreal.SkeletalMeshComponent) and "face" in obj.get_name().lower():
            return obj
    return None


def run():
    blueprint = unreal.load_asset(CHARACTER_BP)
    if blueprint is None:
        print(f"### {CHARACTER_BP} not found -- assemble the character first")
        return

    face_abp = unreal.load_asset(FACE_ABP)
    if face_abp is None:
        print(f"### {FACE_ABP} not found -- run make_face_abp.py first")
        return

    face = face_template(blueprint)
    if face is None:
        print("### no Face component template found")
        return

    before = face.get_editor_property("anim_class")
    face.set_editor_property("anim_class", face_abp.generated_class())
    after = face.get_editor_property("anim_class")
    print(
        f"### face anim class: {before.get_name() if before else None}"
        f" -> {after.get_name() if after else None}"
    )

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    print(f"### compiled and saved {CHARACTER_BP}: {saved}")


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
print("### blueprint face binding scheduled")

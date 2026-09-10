"""Put the head rotation after the face's control rig, where it survives.

The main animation graph turns the head correctly and the renderer shows the
reference pose, because `ABP_Face_PostProcess` runs a Control Rig afterwards and
that rig writes every bone it owns -- the head included. Measured rather than
assumed: `probe_head_bone.py` reports `bDrivingHead=True`, meaning the bone was
found and rotated in the graph's output pose, while the component's head bone
stays bit-identical to the reference pose frame after frame.

So the rotation has to happen on the far side of that rig. This takes our own
copy of Epic's post-process blueprint, reparents it onto
`UAmandaFaceAnimInstance` -- keeping the control rig graph exactly as shipped --
and binds it as the face's post-process override. The copy lives in
/Game/Amanda, so unlike the original it is not derived data and survives a
rebuild of the character.

The two copies split the work: the main blueprint writes curves, which are
*input* to the rig, and this one poses the head, which is output.

    "D:\unreal\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" ^
        "D:\code\project-amanda\unreal\Amanda\Amanda.uproject" ^
        -ExecCmds="py D:\code\project-amanda\unreal\Amanda\Scripts\bind_face_post.py"

Like `bind_face_blueprint.py`, this edits `BP_MHC_Seo` under /Game/MetaHumans,
which assembly rebuilds and git ignores -- so it is part of the pipeline, not a
one-off. Run it after the character is assembled, every time.
"""

import os

import unreal

CHARACTER_BP = os.environ.get("AMANDA_CHARACTER_BP", "/Game/MetaHumans/MHC_Seo/BP_MHC_Seo")
SOURCE_POST_ABP = "/Game/MetaHumans/Common/Face/ABP_Face_PostProcess"
POST_ABP = "/Game/Amanda/ABP_AmandaFacePost"
MAIN_ABP = "/Game/Amanda/ABP_AmandaFace"

OPEN_TICKS = 240
state = {"ticks": 0, "handle": None, "done": False}


def our_copy():
    if unreal.EditorAssetLibrary.does_asset_exist(POST_ABP):
        print(f"### using existing {POST_ABP}")
        return unreal.load_asset(POST_ABP)

    if not unreal.EditorAssetLibrary.does_asset_exist(SOURCE_POST_ABP):
        print(f"### {SOURCE_POST_ABP} missing -- assemble the character first")
        return None

    copied = unreal.EditorAssetLibrary.duplicate_asset(SOURCE_POST_ABP, POST_ABP)
    print(f"### copied {SOURCE_POST_ABP} -> {POST_ABP}" if copied else "### copy failed")
    return copied


def reparent(blueprint, halves):
    """Reparent onto our class and set which half of the job it does."""
    parent = unreal.AmandaFaceAnimInstance.static_class()
    if blueprint.get_editor_property("parent_class") != parent:
        unreal.BlueprintEditorLibrary.reparent_blueprint(blueprint, parent)
        print(f"### reparented {blueprint.get_name()} -> AmandaFaceAnimInstance")

    defaults = unreal.BlueprintEditorLibrary.get_blueprint_class_default_object(blueprint)
    for name, value in halves.items():
        defaults.set_editor_property(name, value)
    print(f"### {blueprint.get_name()}: {halves}")

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)


def face_template(blueprint):
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    for handle in subsystem.k2_gather_subobject_data_for_blueprint(blueprint):
        data = subsystem.k2_find_subobject_data_from_handle(handle)
        obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
        if isinstance(obj, unreal.SkeletalMeshComponent) and "face" in obj.get_name().lower():
            return obj
    return None


def run():
    post = our_copy()
    if post is None:
        return
    # The rig overwrites bones, so the head belongs here and nowhere else.
    reparent(post, {"b_apply_curves": False, "b_apply_head_rotation": True})

    main = unreal.load_asset(MAIN_ABP)
    if main is not None:
        # And here it would only be thrown away, so do not spend the frame.
        reparent(main, {"b_apply_curves": True, "b_apply_head_rotation": False})

    character = unreal.load_asset(CHARACTER_BP)
    if character is None:
        print(f"### {CHARACTER_BP} not found -- assemble the character first")
        return

    face = face_template(character)
    if face is None:
        print("### no Face component template found")
        return

    # On the Blueprint's template, not the placed actor: the MetaHuman
    # reconstructs its components when the world starts ticking and a
    # per-instance override is discarded at exactly that moment.
    face.set_editor_property("override_post_process_anim_bp", post.generated_class())
    got = face.get_editor_property("override_post_process_anim_bp")
    print(f"### face post-process override -> {got.get_name() if got else None}")

    unreal.BlueprintEditorLibrary.compile_blueprint(character)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
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
print("### face post-process binding scheduled")

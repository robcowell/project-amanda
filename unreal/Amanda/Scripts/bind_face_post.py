r"""Put the head rotation after the face's control rig, where it survives.

The main animation graph turns the head correctly and the renderer shows the
reference pose, because `ABP_Face_PostProcess` runs a Control Rig afterwards and
that rig writes every bone it owns -- the head included. Measured rather than
assumed: `probe_head_bone.py` reports `bDrivingHead=True`, meaning the bone was
found and rotated in the graph's output pose, while the component's head bone
stays bit-identical to the reference pose frame after frame.

So the rotation has to happen on the far side of that rig. This takes our own
copy of Epic's post-process blueprint, reparents it onto
`UAmandaFaceAnimInstance` -- keeping the control rig graph exactly as shipped --
and makes it the face mesh's post-process blueprint. The copy lives in
/Game/Amanda, so unlike the original it is not derived data.

The two copies split the work: the main blueprint writes curves, which are
*input* to the rig, and this one poses the head, which is output.

Why the mesh asset and not the component: the component's
`OverridePostProcessAnimBP` is `UPROPERTY(transient)`. It is never saved, so
setting it on the Blueprint template would be discarded the moment the
MetaHuman reconstructs its components -- the same trap `bind_face_blueprint.py`
documents for `anim_class`, one level down.

    UnrealEditor.exe Amanda.uproject -ExecCmds="py Scripts/bind_face_post.py"

The face mesh lives under /Game/MetaHumans, which assembly rebuilds and git
ignores -- so this is part of the pipeline, not a one-off. Run it after the
character is assembled, every time.
"""

import os

import unreal

FACE_MESH = os.environ.get(
    "AMANDA_FACE_MESH", "/Game/MetaHumans/MHC_Seo/Face/SKM_MHC_Seo_FaceMesh"
)
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
    # The current parent cannot be read from Python, and reparenting to the
    # class it already has is a no-op -- so just do it, and verify afterwards by
    # reading back properties that only exist on our class.
    unreal.BlueprintEditorLibrary.reparent_blueprint(blueprint, unreal.AmandaFaceAnimInstance)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)

    defaults = unreal.get_default_object(blueprint.generated_class())
    for name, value in halves.items():
        defaults.set_editor_property(name, value)

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)

    check = unreal.get_default_object(blueprint.generated_class())
    read = {name: check.get_editor_property(name) for name in halves}
    print(f"### {blueprint.get_name()}: {read} saved={saved}")


def run():
    post = our_copy()
    if post is None:
        return
    # The rig overwrites bones, so the head belongs here and nowhere else.
    reparent(post, {"bApplyCurves": False, "bApplyHeadRotation": True})

    main = unreal.load_asset(MAIN_ABP)
    if main is not None:
        # And here it would only be thrown away, so do not spend the frame.
        reparent(main, {"bApplyCurves": True, "bApplyHeadRotation": False})

    mesh = unreal.load_asset(FACE_MESH)
    if mesh is None:
        print(f"### {FACE_MESH} not found -- assemble the character first")
        return

    mesh.set_editor_property("post_process_anim_blueprint", post.generated_class())
    got = mesh.get_editor_property("post_process_anim_blueprint")
    saved = unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    print(f"### face mesh post-process -> {got.get_name() if got else None}: saved {saved}")


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

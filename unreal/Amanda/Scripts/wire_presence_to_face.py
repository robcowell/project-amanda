"""Wire presence onto the face, without wiring anything by hand.

The animation graph cannot be authored from Python, which made this look like
editor work. It is not: `UAmandaFaceAnimInstance` writes presence into the rig
through `AddCurveValue` every frame, so the join is a *reparent* rather than a
graph edit -- and reparenting is scriptable.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\wire_presence_to_face.py"

`ABP_AmandaFace` keeps every graph Epic shipped in it; only its parent changes,
from `AnimInstance` to ours.

The curve names below are not guesses. They came from
`UAmandaFaceCurveLibrary::ListCurveNames` against this rig, which reports 2038
curves; these are the eight control curves that matter. The rig also carries
ARKit aliases (`EyeBlinkLeft`), but the `CTRL_expressions_*` controls are what
the face board actually drives.
"""

import unreal

FACE_ABP = "/Game/Amanda/ABP_AmandaFace"

BLINK = ["CTRL_expressions_eyeBlinkL", "CTRL_expressions_eyeBlinkR"]
LOOK_LEFT = ["CTRL_expressions_eyeLookLeftL", "CTRL_expressions_eyeLookLeftR"]
LOOK_RIGHT = ["CTRL_expressions_eyeLookRightL", "CTRL_expressions_eyeLookRightR"]
LOOK_UP = ["CTRL_expressions_eyeLookUpL", "CTRL_expressions_eyeLookUpR"]
LOOK_DOWN = ["CTRL_expressions_eyeLookDownL", "CTRL_expressions_eyeLookDownR"]

# Head turn and tilt. The rig splits each into Down/Mid/Up variants for the face
# board's rows; Mid is the plain rotation, which is what presence wants.
TURN_LEFT = ["CTRL_expressions_headTurnLeftM"]
TURN_RIGHT = ["CTRL_expressions_headTurnRightM"]
TURN_UP = ["CTRL_expressions_headTurnUpM"]
TURN_DOWN = ["CTRL_expressions_headTurnDownM"]
TILT_LEFT = ["CTRL_expressions_headTiltLeftM"]
TILT_RIGHT = ["CTRL_expressions_headTiltRightM"]

OPEN_TICKS = 240
state = {"ticks": 0, "handle": None, "done": False}


def run():
    blueprint = unreal.load_asset(FACE_ABP)
    if blueprint is None:
        print(f"### {FACE_ABP} not found")
        return

    # Neither `parent_class` on the blueprint nor `get_super_class` on the
    # generated class is exposed to Python, so there is no reading the current
    # parent. Reparenting to the class it already has is a no-op, so just do
    # it, and verify afterwards by whether our properties are there.
    unreal.BlueprintEditorLibrary.reparent_blueprint(
        blueprint, unreal.AmandaFaceAnimInstance
    )
    print("### reparented to AmandaFaceAnimInstance")

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)

    default = unreal.get_default_object(blueprint.generated_class())
    names = unreal.AmandaFaceCurveNames()
    names.set_editor_property("blink", BLINK)
    names.set_editor_property("look_left", LOOK_LEFT)
    names.set_editor_property("look_right", LOOK_RIGHT)
    names.set_editor_property("look_up", LOOK_UP)
    names.set_editor_property("look_down", LOOK_DOWN)
    names.set_editor_property("turn_left", TURN_LEFT)
    names.set_editor_property("turn_right", TURN_RIGHT)
    names.set_editor_property("turn_up", TURN_UP)
    names.set_editor_property("turn_down", TURN_DOWN)
    names.set_editor_property("tilt_left", TILT_LEFT)
    names.set_editor_property("tilt_right", TILT_RIGHT)
    default.set_editor_property("CurveNames", names)
    default.set_editor_property("bApplyPresence", True)

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)

    # The functional check: if the reparent failed these properties would not
    # exist, and reading them would raise rather than quietly return nothing.
    check = unreal.get_default_object(blueprint.generated_class())
    applied = check.get_editor_property("CurveNames")
    print(f"### blink curves: {list(applied.get_editor_property('blink'))}")
    print(f"### look-left curves: {list(applied.get_editor_property('look_left'))}")
    print(f"### head turn curves: {list(applied.get_editor_property('turn_left'))}"
          f" / {list(applied.get_editor_property('turn_right'))}")
    print(f"### head range: {check.get_editor_property('HeadRangeDegrees')} degrees")
    print(f"### apply presence: {check.get_editor_property('bApplyPresence')}")
    print(f"### eye range: {check.get_editor_property('EyeRangeDegrees')} degrees")
    print(f"### saved: {saved}")


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
print("### face wiring scheduled")

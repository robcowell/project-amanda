"""Which switch in Epic's graph decides whether the head is posed at all.

The rotator is written and varies; the head bone is bit-identical every frame
and equal to the body's, which is the reference pose. So something upstream is
either overwriting the head or never applying the rotation. Two blueprint
variables are the candidates -- `bUsingCopyPoseFromMesh`, which makes the face
take the body's pose wholesale, and `HeadControlSwitch`, which selects where
head rotation comes from -- and both can be written by reflection.

This walks them and reports the head bone after each, so the answer is a
measurement rather than a reading of the graph.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
SPACE = unreal.RelativeTransformSpace.RTS_COMPONENT

#: (label, property, value) -- applied in order, each measured before the next.
TRIALS = [
    ("baseline", None, None),
    ("no copy pose", "bUsingCopyPoseFromMesh", False),
    ("switch 0", "HeadControlSwitch", 0),
    ("switch 1", "HeadControlSwitch", 1),
    ("switch 2", "HeadControlSwitch", 2),
    ("switch 3", "HeadControlSwitch", 3),
]

state = {"ticks": 0, "handle": None, "phase": "opening", "next": 0, "trial": 0}


def world():
    s = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    return s.get_game_world() or s.get_editor_world()


def face_component():
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world(), unreal.Actor):
        for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
            if "face" in component.get_name().lower():
                return component
    return None


def head_of(component):
    r = component.get_socket_transform("head", SPACE).rotation.rotator()
    return f"({r.yaw:+.3f},{r.pitch:+.3f},{r.roll:+.3f})"


def show(label):
    component = face_component()
    if component is None:
        print(f"### {label}: no face mesh")
        return
    instance = component.get_anim_instance()
    bits = []
    for name in ("HeadControlSwitch", "bUsingCopyPoseFromMesh", "LLink_Face_Head",
                 "ARKit_HeadRotation"):
        try:
            value = instance.get_editor_property(name)
        except Exception as error:  # noqa: BLE001
            value = f"<{error}>"
        if isinstance(value, unreal.Rotator):
            value = f"({value.yaw:+.2f},{value.pitch:+.2f},{value.roll:+.2f})"
        bits.append(f"{name}={value}")
    print(f"### {label}: head={head_of(component)}  " + "  ".join(bits))


def apply(prop, value):
    component = face_component()
    if component is None:
        return
    try:
        component.get_anim_instance().set_editor_property(prop, value)
        print(f"### set {prop}={value}")
    except Exception as error:  # noqa: BLE001
        print(f"### set {prop} failed: {error}")


def tick(delta_seconds):
    state["ticks"] += 1

    if state["phase"] == "opening":
        if state["ticks"] < 240:
            return
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(LEVEL)
        state["phase"] = "loaded"
        state["next"] = state["ticks"] + 120
        return

    if state["phase"] == "loaded":
        if state["ticks"] < state["next"]:
            return
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_play_simulate()
        state["phase"] = "trials"
        state["next"] = state["ticks"] + 300
        return

    if state["phase"] == "trials":
        if state["ticks"] < state["next"]:
            return
        label, prop, value = TRIALS[state["trial"]]
        if prop is not None:
            apply(prop, value)
        show(label)
        state["trial"] += 1
        state["next"] = state["ticks"] + 60
        if state["trial"] >= len(TRIALS):
            state["phase"] = "quitting"
            state["next"] = state["ticks"] + 60
        return

    if state["ticks"] < state["next"]:
        return
    unreal.unregister_slate_post_tick_callback(state["handle"])
    print("### done")
    unreal.SystemLibrary.execute_console_command(world(), "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### head switch probe scheduled")

"""Is the head *bone* moving, or only the rotator that is supposed to move it?

The rotator ARKit_HeadRotation is written every frame and varies. The rendered
head does not. Those two facts leave two very different causes: the graph never
applies the rotator, or it applies it and something downstream overwrites the
result. Reading the bone settles which.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
SAMPLES = 6
SPACE = unreal.RelativeTransformSpace.RTS_COMPONENT

state = {"ticks": 0, "handle": None, "phase": "opening", "next": 0, "taken": 0}


def world():
    s = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    return s.get_game_world() or s.get_editor_world()


def meshes():
    found = {}
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world(), unreal.Actor):
        for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
            name = component.get_name().lower()
            if "face" in name:
                found["face"] = component
            elif "body" in name:
                found["body"] = component
    return found


def bone(component, name):
    try:
        transform = component.get_socket_transform(name, SPACE)
    except Exception:  # noqa: BLE001
        return "missing"
    r = transform.rotation.rotator()
    return f"({r.yaw:+.2f},{r.pitch:+.2f},{r.roll:+.2f})"


def sample():
    found = meshes()
    if "face" not in found:
        print("### no face mesh")
        return

    face = found["face"]
    instance = face.get_anim_instance()
    bits = []
    for name in ("PresenceHeadRotation", "bDrivingHead", "bHasPresence"):
        try:
            value = instance.get_editor_property(name)
        except Exception as error:  # noqa: BLE001
            value = f"<{error}>"
        if isinstance(value, unreal.Rotator):
            value = f"({value.yaw:+.2f},{value.pitch:+.2f},{value.roll:+.2f})"
        bits.append(f"{name}={value}")

    mesh = face.get_editor_property("skeletal_mesh")
    post = mesh.get_editor_property("post_process_anim_blueprint") if mesh else None
    bits.append(f"post={post.get_name() if post else None}")
    for label, component in found.items():
        for name in ("head", "neck_01", "FACIAL_C_FacialRoot"):
            bits.append(f"{label}.{name}={bone(component, name)}")
    print("### " + "  ".join(bits))


def tick(delta_seconds):
    state["ticks"] += 1
    if state["phase"] == "opening":
        if state["ticks"] < 240:
            return
        # The editor opens its default map, not ours. Without this the world is
        # empty and the probe reports no face, which reads as a broken rig.
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(LEVEL)
        print(f"### loaded {LEVEL}")
        state["phase"] = "loaded"
        state["next"] = state["ticks"] + 120
        return

    if state["phase"] == "loaded":
        # Simulating in the same tick as the load crashes the editor: the world
        # that was just torn down is still the one under our feet.
        if state["ticks"] < state["next"]:
            return
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_play_simulate()
        state["phase"] = "waiting"
        state["next"] = state["ticks"] + 300
        return

    if state["phase"] == "waiting":
        if state["ticks"] >= state["next"]:
            state["phase"] = "sampling"
            state["next"] = state["ticks"]
        return

    if state["phase"] == "sampling":
        if state["ticks"] < state["next"]:
            return
        sample()
        state["taken"] += 1
        state["next"] = state["ticks"] + 45
        if state["taken"] >= SAMPLES:
            state["phase"] = "quitting"
            state["next"] = state["ticks"] + 60
        return

    if state["ticks"] < state["next"]:
        return
    unreal.unregister_slate_post_tick_callback(state["handle"])
    print("### done")
    unreal.SystemLibrary.execute_console_command(world(), "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### head bone probe scheduled")

"""Point the face blueprint at a Live Link subject.

The subject is a blueprint variable on `ABP_AmandaFace` -- `LLink_Face_Subj`,
of type Live Link Subject Name -- not, as first assumed, a property buried on
the `AnimNode_LiveLinkPose` inside the graph. An earlier probe declared it
unreachable because it searched property names for "subject" and this one is
spelled "subj". Hence the wider match below, and the printout of every
candidate: a search that silently finds nothing is indistinguishable from a
thing that is not there.

Epic's shipped blueprint has a leftover value in that field, so this is also a
correction rather than only a configuration.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\set_face_subject.py"
"""

import os

import unreal

FACE_ABP = "/Game/Amanda/ABP_AmandaFace"
SUBJECT = os.environ.get("AMANDA_LIVE_LINK_SUBJECT", "Amanda")

#: Anything that looks like it names a Live Link subject. Deliberately loose:
#: "subject", "subj", and the LLink_ prefix Epic uses.
HINTS = ("subj", "livelink", "llink")

OPEN_TICKS = 240

state = {"ticks": 0, "handle": None, "done": False}


#: Blueprint variables do not appear in `dir()` on the class default object --
#: they are generated properties, and enumeration only shows the native ones.
#: So they have to be asked for by name. These are what the variable is called
#: in Epic's blueprint, in the spellings Python might accept.
KNOWN_NAMES = (
    "LLink_Face_Subj",
    "llink_face_subj",
    "LLinkFaceSubj",
    "LiveLinkSubjectName",
)


def candidates(default):
    found = []
    seen = set()

    for name in KNOWN_NAMES:
        try:
            value = default.get_editor_property(name)
        except Exception:  # noqa: BLE001 - wrong spelling, try the next
            continue
        if name not in seen:
            seen.add(name)
            found.append((name, value))

    # And anything enumerable that looks right, for a blueprint that renames it.
    for name in dir(default):
        if name.startswith("_") or name in seen:
            continue
        if not any(hint in name.lower() for hint in HINTS):
            continue
        try:
            value = default.get_editor_property(name)
        except Exception:  # noqa: BLE001 - methods and non-properties
            continue
        seen.add(name)
        found.append((name, value))
    return found


def run():
    blueprint = unreal.load_asset(FACE_ABP)
    if blueprint is None:
        print(f"### {FACE_ABP} not found -- run make_face_abp.py first")
        return

    generated = blueprint.generated_class()
    default = unreal.get_default_object(generated)

    found = candidates(default)
    print(f"### {len(found)} live-link-ish properties:")
    for name, value in found:
        print(f"###   {name} = {value!r} ({type(value).__name__})")

    targets = [
        (name, value)
        for name, value in found
        if isinstance(value, unreal.LiveLinkSubjectName)
    ]
    if not targets:
        print("### no Live Link Subject Name property found; nothing changed")
        return

    for name, old in targets:
        default.set_editor_property(name, unreal.LiveLinkSubjectName(SUBJECT))
        print(f"### {name}: {old} -> {SUBJECT}")

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    print(f"### compiled and saved: {saved}")

    # Read back from a freshly loaded default, because setting a property and
    # believing it is how the last hour went.
    check = unreal.get_default_object(blueprint.generated_class())
    for name, _ in targets:
        print(f"### verify {name} = {check.get_editor_property(name)}")


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
print("### subject wiring scheduled once the editor is up")

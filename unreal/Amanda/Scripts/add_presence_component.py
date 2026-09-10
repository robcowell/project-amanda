"""Attach the presence component to the character Blueprint.

On the Blueprint, not the placed actor -- the same lesson the face binding
taught: the MetaHuman Blueprint reconstructs its components when the world
starts, and anything set on the level instance is discarded.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\add_presence_component.py"

Like `bind_face_blueprint.py`, this belongs in the rebuild pipeline: BP_MHC_Seo
lives under /Game/MetaHumans, which assembly rebuilds and git ignores.
"""

import os

import unreal

CHARACTER_BP = os.environ.get("AMANDA_CHARACTER_BP", "/Game/MetaHumans/MHC_Seo/BP_MHC_Seo")

OPEN_TICKS = 240
state = {"ticks": 0, "handle": None, "done": False}


def existing(subsystem, handles):
    for handle in handles:
        data = subsystem.k2_find_subobject_data_from_handle(handle)
        obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
        if isinstance(obj, unreal.AmandaPresenceComponent):
            return obj
    return None


def run():
    blueprint = unreal.load_asset(CHARACTER_BP)
    if blueprint is None:
        print(f"### {CHARACTER_BP} not found")
        return

    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)

    if existing(subsystem, handles) is not None:
        print("### presence component already attached")
        return

    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", unreal.AmandaPresenceComponent)
    params.set_editor_property("blueprint_context", blueprint)

    new_handle, fail_reason = subsystem.add_new_subobject(params)
    if not fail_reason.is_empty():
        print(f"### could not attach: {fail_reason}")
        return

    subsystem.rename_subobject(handle=new_handle, new_name=unreal.Text("AmandaPresence"))
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    print(f"### attached AmandaPresence and saved {CHARACTER_BP}: {saved}")


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
print("### presence attachment scheduled")

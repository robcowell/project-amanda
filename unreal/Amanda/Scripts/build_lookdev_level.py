"""A lit room to judge the character in.

Section 17 of the build plan sets the standard the whole project is measured
against: the *neutral* render has to convince before any animation runs. That
judgement is worthless under whatever lighting a default level happens to have,
so this builds a deliberate one and commits it, and every later look at the
character is a look at the same room.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -run=pythonscript -script="Scripts/build_lookdev_level.py" ^
        -unattended -nopause -nosplash

Three lights, which is portraiture rather than game lighting:

  * a **key** at 45 degrees off the nose and above the eyeline, large and soft,
    because a small source at this distance renders skin as plastic;
  * a **fill** opposite and much dimmer, which sets how harsh the face reads --
    the single most opinionated number in this file;
  * a **rim** behind and to the side, separating the head from the background.
    Without it a dark-haired head dissolves into a dark room.

Exposure is pinned rather than automatic. Auto-exposure quietly rescues bad
lighting and makes two renders incomparable, which is the opposite of what a
look-dev scene is for.
"""

import unreal

LEVEL = "/Game/Amanda/Maps/LookDev"
BUILD_PATH = "/Game/MetaHumans"

#: Roughly eye height on a standing adult, in centimetres. Everything is aimed
#: here rather than at the origin: a face lit as though it were on the floor is
#: a different face.
EYE_HEIGHT = 160.0

#: Epic's own portrait environment, shipped with the character plugin. Ambient
#: light with some direction in it beats a flat grey constant.
PORTRAIT_CUBEMAP = (
    "/MetaHumanCharacter/LightingEnvironments/Dependencies/T_PortraitSkyLightCubeMap"
)


def actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def spawn(cls, location, rotation=None):
    return actors().spawn_actor_from_class(
        cls, location, rotation or unreal.Rotator(0.0, 0.0, 0.0)
    )


def aim(actor, at):
    """Point an actor at a place, rather than guessing Euler angles."""
    rotation = unreal.MathLibrary.find_look_at_rotation(actor.get_actor_location(), at)
    actor.set_actor_rotation(rotation, teleport_physics=False)


def add_light(name, location, target, *, lumens, temperature, width, height):
    light = spawn(unreal.RectLight, location)
    light.set_actor_label(name)
    component = light.get_component_by_class(unreal.RectLightComponent)
    component.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
    component.set_editor_property("intensity", lumens)
    component.set_editor_property("use_temperature", True)
    component.set_editor_property("temperature", temperature)
    component.set_editor_property("source_width", width)
    component.set_editor_property("source_height", height)
    # Soft shadows from a large source; the default bias makes a hard edge that
    # reads as a game light.
    component.set_editor_property("cast_shadows", True)
    aim(light, target)
    print(f"###   {name}: {lumens:.0f} lm at {temperature:.0f}K")
    return light


def build_lighting(head):
    add_light(
        "Key",
        unreal.Vector(150.0, -130.0, EYE_HEIGHT + 45.0),
        head,
        lumens=6000.0,
        temperature=5600.0,
        width=90.0,
        height=120.0,
    )
    # A fill at a quarter of the key is a contrasty, characterful ratio. Raise
    # it towards the key for something flatter and friendlier; this is the dial
    # to turn first if the face reads as severe.
    add_light(
        "Fill",
        unreal.Vector(140.0, 140.0, EYE_HEIGHT - 10.0),
        head,
        lumens=1500.0,
        temperature=6500.0,
        width=140.0,
        height=140.0,
    )
    add_light(
        "Rim",
        unreal.Vector(-120.0, 90.0, EYE_HEIGHT + 70.0),
        head,
        lumens=4000.0,
        temperature=7000.0,
        width=40.0,
        height=90.0,
    )

    sky = spawn(unreal.SkyLight, unreal.Vector(0.0, 0.0, EYE_HEIGHT))
    sky.set_actor_label("Ambient")
    # By class rather than by attribute: actors expose their components under
    # names that vary, and `SkyLight` has no `sky_light_component`.
    component = sky.get_component_by_class(unreal.SkyLightComponent)
    cubemap = unreal.load_asset(PORTRAIT_CUBEMAP)
    if cubemap is not None:
        component.set_editor_property(
            "source_type", unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP
        )
        component.set_editor_property("cubemap", cubemap)
        print("###   ambient: Epic's portrait cubemap")
    else:
        print(f"###   ambient: {PORTRAIT_CUBEMAP} not found, using the default sky")
    component.set_editor_property("intensity", 1.0)
    component.set_editor_property("real_time_capture", False)


def pin_exposure():
    """Fix exposure so two renders can be compared.

    Done by clamping auto-exposure to a single value rather than switching the
    method to manual: manual exposure then depends on the camera's ISO and
    aperture, and a scene that looks right through one camera and wrong through
    another is not a look-dev scene.
    """
    volume = spawn(unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, EYE_HEIGHT))
    volume.set_actor_label("Exposure")
    volume.set_editor_property("unbound", True)
    settings = volume.get_editor_property("settings")
    settings.set_editor_property("override_auto_exposure_min_brightness", True)
    settings.set_editor_property("auto_exposure_min_brightness", 1.0)
    settings.set_editor_property("override_auto_exposure_max_brightness", True)
    settings.set_editor_property("auto_exposure_max_brightness", 1.0)
    volume.set_editor_property("settings", settings)
    print("### exposure pinned")


def find_metahuman():
    """The assembled MetaHuman Blueprint, whatever the pipeline decided to name it."""
    candidates = [
        path
        for path in unreal.EditorAssetLibrary.list_assets(BUILD_PATH, recursive=True)
        if unreal.EditorAssetLibrary.does_asset_exist(path)
        and isinstance(unreal.load_asset(path), unreal.Blueprint)
    ]
    for path in candidates:
        blueprint = unreal.load_asset(path)
        parent = blueprint.get_editor_property("parent_class")
        if parent is not None and "MetaHuman" in str(parent.get_name()):
            return path
    return candidates[0] if candidates else None


def place_character():
    path = find_metahuman()
    if path is None:
        print(f"### no assembled MetaHuman under {BUILD_PATH} -- run create_metahuman.py first")
        return None

    blueprint = unreal.load_asset(path)
    actor = actors().spawn_actor_from_object(blueprint, unreal.Vector(0.0, 0.0, 0.0))
    if actor is not None:
        actor.set_actor_label("Amanda")
        print(f"### placed {path}")
    return actor


def add_camera(head):
    """A portrait lens at conversational distance, not a game camera."""
    camera = spawn(unreal.CineCameraActor, unreal.Vector(120.0, 0.0, EYE_HEIGHT))
    camera.set_actor_label("PortraitCamera")
    aim(camera, head)

    component = camera.get_cine_camera_component()
    # 85mm at about a metre: the framing a person sees across a table, and the
    # focal length that does not distort a face.
    component.set_editor_property("current_focal_length", 85.0)
    component.set_editor_property("current_aperture", 4.0)
    focus = component.get_editor_property("focus_settings")
    focus.set_editor_property("focus_method", unreal.CameraFocusMethod.MANUAL)
    focus.set_editor_property("manual_focus_distance", 120.0)
    component.set_editor_property("focus_settings", focus)
    print("### camera: 85mm at f/4, focused on the eyes")
    return camera


def main():
    head = unreal.Vector(0.0, 0.0, EYE_HEIGHT)

    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    levels.new_level(LEVEL)
    print(f"### new level {LEVEL}")

    build_lighting(head)
    pin_exposure()
    place_character()
    add_camera(head)

    levels.save_current_level()
    print("### saved")


main()

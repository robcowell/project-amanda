"""A lit room to judge the character in.

Section 17 of the build plan sets the standard the whole project is measured
against: the *neutral* render has to convince before any animation runs. That
judgement is worthless under whatever lighting a default level happens to have,
so this builds a deliberate one and commits it, and every later look at the
character is a look at the same room.

**Run this in a real editor, not a commandlet.** A commandlet will report every
actor spawning happily and then save a 6 KB level with nothing in it: the
spawns do not land in the world that gets written. Three separate things in
this project have now needed a running editor -- baking MetaHuman textures,
starting a Live Link subject, and this -- and the pattern is the same each
time: asset operations are fine headless, anything touching a world or a
renderer is not.

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -ExecCmds="py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\build_lookdev_level.py"

It quits the editor when it has saved, so it can be left to run.

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

#: Average scene luminance the camera exposes for. Raise it to darken the
#: image, lower it to brighten -- it is what the camera assumes the scene is,
#: not a brightness dial.
EXPOSURE = 160.0

#: Which way the character faces. The assembled Blueprint does not face down
#: its own +X, so spawning it unrotated puts it in profile to a camera standing
#: in front of it. Measured off a render rather than reasoned about.
CHARACTER_YAW = -90.0

#: How far the camera stands back from the face.
#:
#: 250cm was head and shoulders with room to spare. 120cm was too tight -- an
#: 85mm frame is only 34cm tall there and her hair needs about 31 of them, so
#: the crown clipped. 145cm gives a 41cm frame: head and hair filling about
#: three quarters of it, with a hand's width of air above and below.
CAMERA_DISTANCE = 145.0

#: How far below the eye line to aim, as a fraction of the frame height.
#:
#: A sixth puts the eyes on the upper third, which is the portrait convention
#: and was cropping the crown at this distance. A sixteenth keeps them a little
#: high without pushing the top of the head out of frame.
EYE_DROP = 1.0 / 16.0

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
    # Movable, so nothing needs baking. A look-dev scene gets its lights nudged
    # constantly, and stationary lights answer that with "LIGHTING NEEDS TO BE
    # REBUILT" across the viewport until someone builds them.
    component.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
    aim(light, target)
    print(f"###   {name}: {lumens:.0f} lm at {temperature:.0f}K")
    return light


def build_lighting(head):
    add_light(
        "Key",
        unreal.Vector(150.0, -130.0, EYE_HEIGHT + 45.0),
        head,
        lumens=3000.0,
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
        lumens=1000.0,
        temperature=6500.0,
        width=140.0,
        height=140.0,
    )
    add_light(
        "Rim",
        unreal.Vector(-120.0, 90.0, EYE_HEIGHT + 70.0),
        head,
        lumens=2500.0,
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
    component.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)


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
    # Pinned to the scene's actual average luminance, not to 1.0. A key light
    # of a few thousand lumens a metre and a half from skin lands somewhere
    # around 20 cd/m2; telling the camera to expose for 1.0 asks it to brighten
    # by roughly four stops, which is exactly what the first render did.
    settings.set_editor_property("override_auto_exposure_min_brightness", True)
    settings.set_editor_property("auto_exposure_min_brightness", EXPOSURE)
    settings.set_editor_property("override_auto_exposure_max_brightness", True)
    settings.set_editor_property("auto_exposure_max_brightness", EXPOSURE)
    volume.set_editor_property("settings", settings)
    print(f"### exposure pinned at {EXPOSURE:.0f}")


def find_metahuman():
    """The assembled MetaHuman Blueprint.

    By where the assembly pipeline puts things rather than by inspecting parent
    classes: `Blueprint.parent_class` is not reachable through
    `get_editor_property`, and the layout here is not a guess -- assembly writes
    `<build path>/<Character>/BP_<Character>` and puts the shared animation
    blueprints in `<build path>/Common`, which is the only thing that has to be
    excluded.
    """
    candidates = [
        path
        for path in unreal.EditorAssetLibrary.list_assets(BUILD_PATH, recursive=True)
        if "/Common/" not in path
        and path.rsplit("/", 1)[-1].startswith("BP_")
        and isinstance(unreal.load_asset(path), unreal.Blueprint)
    ]
    if len(candidates) > 1:
        print(f"### {len(candidates)} MetaHuman blueprints; taking the first")
        for path in candidates:
            print(f"###   {path}")
    return candidates[0] if candidates else None


def place_character():
    path = find_metahuman()
    if path is None:
        print(f"### no assembled MetaHuman under {BUILD_PATH} -- run create_metahuman.py first")
        return None

    blueprint = unreal.load_asset(path)
    # From the generated class, not the Blueprint asset: spawning the asset
    # returns None without saying why, which cost a silent run.
    generated = blueprint.generated_class()
    if generated is None:
        print(f"### {path} has no generated class -- is it compiled?")
        return None

    actor = spawn(
        generated,
        unreal.Vector(0.0, 0.0, 0.0),
        unreal.Rotator(0.0, 0.0, CHARACTER_YAW),
    )
    if actor is None:
        print(f"### spawning {path} produced no actor")
        return None

    actor.set_actor_label("Amanda")

    # A skeletal mesh only evaluates its animation blueprint outside Play mode
    # when told to, so the face would otherwise hold its rest pose in the
    # viewport however correctly Live Link is wired.
    #
    # Guarded, because on a Blueprint actor these components are templates and
    # the engine refuses: "cannot be edited on templates". Unguarded it threw
    # here, which aborted the build before the camera was added and before the
    # level was saved -- leaving an empty level and a puzzle. Play mode does not
    # need this flag, and Play mode is where the product runs.
    for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
        try:
            component.set_editor_property("update_animation_in_editor", True)
            print(f"###   {component.get_name()}: animating in editor")
        except Exception as error:  # noqa: BLE001 - editor preview only
            print(f"###   {component.get_name()}: not animatable in editor ({error})")

    print(f"### placed {path}")
    return actor


def eye_line(actor):
    """Where her eyes actually are, rather than where I guessed.

    The first framing assumed a 160 cm eye line and put her chin at the centre
    of frame with the top of her head cropped. The rig knows: the eye bones sit
    at 169.5 cm and the head bone at 163.3.
    """
    if actor is None:
        return unreal.Vector(0.0, 0.0, EYE_HEIGHT)

    for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
        if "face" not in component.get_name().lower():
            continue
        sockets = {str(name) for name in component.get_all_socket_names()}
        if {"FACIAL_L_Eye", "FACIAL_R_Eye"} <= sockets:
            left = component.get_socket_location("FACIAL_L_Eye")
            right = component.get_socket_location("FACIAL_R_Eye")
            middle = (left + right) * 0.5
            print(f"###   eye line measured at {middle.z:.1f}cm")
            return middle
    return unreal.Vector(0.0, 0.0, EYE_HEIGHT)


def head_top(actor, eyes):
    """The actual top of her hair, from the actor's bounds.

    Estimating this went wrong twice -- hair is taller than a skull, and the
    crown clipped both times. `get_actor_bounds` knows: the top of the box is
    the top of the head, because nothing on her reaches higher.
    """
    if actor is None:
        return eyes.z + 14.0
    origin, extent = actor.get_actor_bounds(only_colliding_components=False)
    top = origin.z + extent.z
    print(f"###   head top measured at {top:.1f}cm")
    return top


def add_camera(actor):
    """A portrait lens at conversational distance, not a game camera.

    Framed on the eyes and tight on the face. The camera sits level with the
    eye line -- looking up at someone is a different character -- and aims a
    little below it, which puts the eyes on the upper third where a portrait
    wants them and keeps the top of the head inside the frame.
    """
    eyes = eye_line(actor)
    top = head_top(actor, eyes)

    # Eyes sit roughly 42% of the way down a head measured with its hair, so
    # the whole head is about this tall, and the frame wants it filling around
    # three quarters with air top and bottom.
    head_height = max(18.0, (top - eyes.z) / 0.42)
    frame_height = head_height / 0.72
    distance = frame_height / (2.0 * 0.1416)

    camera = spawn(
        unreal.CineCameraActor,
        unreal.Vector(eyes.x + distance, 0.0, eyes.z),
    )
    camera.set_actor_label("PortraitCamera")

    # Aim so the frame's top edge clears her hair by a little, rather than
    # putting the eyes on a rule-of-thirds line and hoping the crown fits.
    centre = top + frame_height * 0.06 - frame_height * 0.5
    aim(camera, unreal.Vector(eyes.x, 0.0, centre))
    print(f"###   frame {frame_height:.0f}cm tall at {distance:.0f}cm, centred on {centre:.1f}cm")

    component = camera.get_cine_camera_component()
    # 85mm at two and a half metres: head and shoulders, and the focal length
    # that does not distort a face the way a wide lens does.
    component.set_editor_property("current_focal_length", 85.0)
    component.set_editor_property("current_aperture", 4.0)
    focus = component.get_editor_property("focus_settings")
    focus.set_editor_property("focus_method", unreal.CameraFocusMethod.MANUAL)
    focus.set_editor_property("manual_focus_distance", distance)
    component.set_editor_property("focus_settings", focus)
    print(f"### camera: 85mm at f/4, {CAMERA_DISTANCE:.0f}cm back, focused on the eyes")
    return camera


def build():
    head = unreal.Vector(0.0, 0.0, EYE_HEIGHT)

    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    levels.new_level(LEVEL)
    print(f"### new level {LEVEL}")

    build_lighting(head)
    pin_exposure()
    character = place_character()
    add_camera(character)

    actor_count = len(
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    )
    # Not save_current_level(): a level from new_level() has no filename yet, and
    # that call fails with "Can't save the level because it doesn't have a
    # filename" while the script above it reports success. Save by path.
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    saved = unreal.EditorLoadingAndSavingUtils.save_map(world, LEVEL)
    print(f"### saved {LEVEL} with {actor_count} actors: {saved}")


#: `-ExecCmds` fires during startup, before the editor has a world worth
#: spawning into. Everything waits for it to be running.
OPEN_TICKS = 180

state = {"ticks": 0, "handle": None, "done": False}


def tick(delta_seconds):
    state["ticks"] += 1
    if state["done"] or state["ticks"] < OPEN_TICKS:
        return
    state["done"] = True
    unreal.unregister_slate_post_tick_callback(state["handle"])
    try:
        build()
    except Exception as error:  # noqa: BLE001 - diagnostic, report anything
        print(f"### raised: {error!r}")
    print("### done")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, "QUIT_EDITOR")


state["handle"] = unreal.register_slate_post_tick_callback(tick)
print("### level build scheduled once the editor is up")

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

#: The level is built from Epic's default template rather than from nothing, for
#: its sky: atmosphere, volumetric clouds and height fog, lit by a sun. An empty
#: level renders her against black, which Rob called boring -- and the cloud sky
#: he saw while the editor started up is this template. Its sun and sky light
#: are then kept off her, so the portrait lighting below is still the only light
#: on the face. See `adapt_template_sky`.
SKY_TEMPLATE = "/Engine/Maps/Templates/Template_Default"
BUILD_PATH = "/Game/MetaHumans"

#: Roughly eye height on a standing adult, in centimetres. Everything is aimed
#: here rather than at the origin: a face lit as though it were on the floor is
#: a different face.
EYE_HEIGHT = 160.0

#: Average scene luminance the camera exposes for. Raise it to darken the
#: image, lower it to brighten -- it is what the camera assumes the scene is,
#: not a brightness dial.
EXPOSURE = 200.0

#: The colour grade: white balance in kelvin, and tint. See pin_exposure for how
#: they were chosen. Lower is cooler, as on a camera; positive tint is magenta.
WHITE_BALANCE_K = 4700.0
WHITE_TINT = 0.1

#: Which way the character faces. The assembled Blueprint does not face down
#: its own +X, so spawning it unrotated puts it in profile to a camera standing
#: in front of it. Measured off a render rather than reasoned about.
CHARACTER_YAW = -90.0

#: How tall the frame is at the face, in centimetres -- the framing dial.
#:
#: Rob's reference is Chloe from Detroit: Become Human. Her close-ups put the
#: face at about half the frame's height, with hair and a little neck in shot.
#: 36cm matches that scale. 44cm, the first framing, was head and hair with air
#: around it; 28cm filled the frame edge to edge and cropped the crown.
FRAME_HEIGHT = 36.0

#: Where the eyes sit, as a fraction of the frame from the top. A third is the
#: portrait convention; at this tightness it crops the top of her hair, which is
#: intended.
EYES_FROM_TOP = 1.0 / 3.0

#: f/2.8 at about a metre keeps both eyes sharp and melts the sky behind her --
#: the reference's backgrounds are nothing but soft, pale colour.
APERTURE = 2.8

#: The sky's sun, in lux. It lights only the sky (see adapt_template_sky), so
#: this is the background's brightness dial and nothing else. Bracketed on
#: 2026-09-11 at exposure 200: 300 lux gave a sky of 56/255, 1500 gave 164, 7500
#: gave 235. The reference wants it a little brighter than her face, ~160.
SKY_SUN_LUX = 5000.0

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
    # Chloe's key: a big soft source, like a window beside the camera. Its size
    # is what makes shadows wrap rather than cut, and what puts a large
    # catchlight in each eye -- much of why eyes read as alive. The morning's
    # loop light (small, high, eighth-power fill) was a darker film's portrait.
    add_light(
        "Key",
        unreal.Vector(165.0, -115.0, EYE_HEIGHT + 70.0),
        head,
        lumens=3000.0,
        temperature=5200.0,
        width=200.0,
        height=200.0,
    )
    # High-key wants a strong fill, but not too strong: at half the key the face
    # lost its shape beside Chloe's, who keeps a shadow side and some contour
    # under the cheekbone. About a quarter of the key, by the lens, a touch cool
    # against the warm key. By the lens rather than opposite the key, where it
    # would be a second key and cancel the shape entirely.
    add_light(
        "Fill",
        unreal.Vector(220.0, 40.0, EYE_HEIGHT - 20.0),
        head,
        lumens=800.0,
        temperature=7000.0,
        width=200.0,
        height=200.0,
    )
    # An edge on the hair to separate it from the sky, not a halo. At 2500
    # lumens it blew the hair out and bloomed round her head.
    add_light(
        "Rim",
        unreal.Vector(-120.0, 90.0, EYE_HEIGHT + 70.0),
        head,
        lumens=900.0,
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
    # Ambient arrives from everywhere and fills every shadow equally. At 1.0,
    # under a hard key, it left the face no shape; at 0.6, under a soft key and
    # a strong fill, it flattened her beside the reference. 0.4.
    component.set_editor_property("intensity", 0.4)
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
    # Bloom turned a 2500-lumen rim into a glow round her head. The reference
    # has a soft glow on its highlights, so some stays; the default (0.675) is a
    # music video.
    settings.set_editor_property("override_bloom_intensity", True)
    settings.set_editor_property("bloom_intensity", 0.3)
    # Chloe's grade is cool: rose skin rather than peach, and a sky that is blue
    # rather than white. One white balance does both, because both are the same
    # shift -- more blue against red. Bracketed on 2026-09-11 against colours
    # measured from the reference (skin red/blue 1.07, hue 330): neutral 6500
    # gave 1.28 and a peach 19 degrees; 5500 gave 1.18; 4700 gave 1.10. A little
    # magenta (+0.1) took the skin from peach to her rose, hue 337; -0.1 went
    # the other way, to 2. Global desaturation was the wrong tool: it would have
    # drained a sky that needed more colour, not less.
    settings.set_editor_property("override_white_temp", True)
    settings.set_editor_property("white_temp", WHITE_BALANCE_K)
    settings.set_editor_property("override_white_tint", True)
    settings.set_editor_property("white_tint", WHITE_TINT)
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

    # Anchored on the eyes, not the crown: a close-up crops the top of the head
    # on purpose, and fitting the hair was what kept the old frame loose.
    distance = FRAME_HEIGHT / (2.0 * 0.1416)
    camera = spawn(
        unreal.CineCameraActor,
        unreal.Vector(eyes.x + distance, 0.0, eyes.z),
    )
    camera.set_actor_label("PortraitCamera")

    centre = eyes.z - FRAME_HEIGHT * (0.5 - EYES_FROM_TOP)
    aim(camera, unreal.Vector(eyes.x, 0.0, centre))
    cropped = top - (centre + FRAME_HEIGHT / 2.0)
    print(
        f"###   frame {FRAME_HEIGHT:.0f}cm tall at {distance:.0f}cm, eyes a third down,"
        f" {max(cropped, 0.0):.1f}cm of hair above the frame"
    )

    component = camera.get_cine_camera_component()
    # 85mm: the focal length that does not distort a face the way a wide lens
    # does, even this close.
    component.set_editor_property("current_focal_length", 85.0)
    component.set_editor_property("current_aperture", APERTURE)
    focus = component.get_editor_property("focus_settings")
    focus.set_editor_property("focus_method", unreal.CameraFocusMethod.MANUAL)
    focus.set_editor_property("manual_focus_distance", distance)
    component.set_editor_property("focus_settings", focus)
    # Reports the distance actually used. It used to print a constant the
    # framing no longer read -- "145cm back" for a camera at 156.
    print(f"### camera: 85mm at f/{APERTURE:g}, {distance:.0f}cm back, focused on the eyes")
    return camera


def adapt_template_sky():
    """Keep the template's sky as a backdrop and nothing more.

    Its sun must light the atmosphere and clouds but not her: a second key from
    an arbitrary direction would undo the portrait lighting. So it moves to a
    lighting channel no mesh is on, casts no shadow, and contributes nothing to
    global illumination. The atmosphere does not care about lighting channels --
    it reads the sun through `atmosphere_sun_light` -- so the sky is unchanged.

    The template's sky light captures that sky in real time and would wash her
    in blue; ours, from the portrait cubemap, replaces it. Its floor is out of
    frame and only a surface for stray light to bounce off, so it goes too.
    """
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in subsystem.get_all_level_actors():
        kind = actor.get_class().get_name()
        label = actor.get_actor_label()

        if isinstance(actor, unreal.DirectionalLight):
            light = actor.get_component_by_class(unreal.DirectionalLightComponent)
            channels = light.get_editor_property("lighting_channels")
            channels.set_editor_property("channel0", False)
            channels.set_editor_property("channel1", True)
            light.set_editor_property("lighting_channels", channels)
            light.set_editor_property("cast_shadows", False)
            light.set_editor_property("indirect_lighting_intensity", 0.0)
            light.set_editor_property("atmosphere_sun_light", True)
            light.set_editor_property("intensity", SKY_SUN_LUX)
            rotation = actor.get_actor_rotation()
            print(
                f"###   sun kept for the sky only: {light.get_editor_property('intensity'):.1f}"
                f" at pitch {rotation.pitch:.0f}, yaw {rotation.yaw:.0f}"
            )
        elif isinstance(actor, unreal.SkyLight):
            subsystem.destroy_actor(actor)
            print(f"###   removed the template's sky light ({label})")
        elif isinstance(actor, unreal.StaticMeshActor) and "floor" in label.lower():
            subsystem.destroy_actor(actor)
            print(f"###   removed the template's floor ({label})")
        else:
            print(f"###   kept {label} ({kind})")


def build():
    head = unreal.Vector(0.0, 0.0, EYE_HEIGHT)

    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    created = levels.new_level_from_template(LEVEL, SKY_TEMPLATE)
    if not created and unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
        # Unlike new_level, creating from a template will not overwrite.
        unreal.EditorAssetLibrary.delete_asset(LEVEL)
        created = levels.new_level_from_template(LEVEL, SKY_TEMPLATE)
    if not created:
        # delete_asset reports nothing when it declines to delete a map, and
        # the template call then refuses again. Deleting the file with the
        # editor closed is what works.
        print(
            f"### could not create {LEVEL} from {SKY_TEMPLATE}: an asset is already"
            " there. Close the editor, delete Content/Amanda/Maps/LookDev.umap, and"
            " run this again -- the build regenerates everything in it."
        )
        return
    print(f"### new level {LEVEL} from {SKY_TEMPLATE}")
    adapt_template_sky()

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

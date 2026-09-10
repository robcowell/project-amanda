"""What audio devices does MetaHuman Live Link actually see? (phase 0 spike)

The question that gates every piece of Unreal work in this project is whether a
*virtual* audio device -- a cable fed by the orchestrator's TTS -- can drive a
MetaHuman Audio Live Link source. Epic's documentation describes a device picker
and emphasises USB capture hardware, which left the answer unknown.

This asks the engine directly, headlessly, in about fifteen seconds:

    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -run=pythonscript -script="Scripts/list_audio_devices.py" ^
        -unattended -nopause -nosplash

It uses `UMetaHumanLocalLiveLinkSourceBlueprint`, the same API the Live Link
panel's own UI is built on, so what it prints is what the dropdown would show.

The formats matter as much as the names. Whatever the cable advertises here is
what the orchestrator should be playing into it -- a rate mismatch is resampled
by Windows silently, and this project refuses to resample anywhere else.
"""

import unreal

BANNER = "=" * 70


def describe_formats(device):
    """Tracks and formats for one device, or the reason there are none."""
    tracks, timed_out = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_tracks(
        device, timeout=5.0
    )
    if timed_out:
        return ["    (timed out asking for tracks -- device busy or unavailable)"]
    if not tracks:
        return ["    (no tracks: enumerated, but not usable as a source)"]

    lines = []
    for track in tracks:
        formats, format_timeout = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_formats(
            track, timeout=5.0
        )
        lines.append(f"    track {track.name!r}")
        if format_timeout:
            lines.append("      (timed out asking for formats)")
        for entry in formats:
            lines.append(
                f"      {entry.sample_rate} Hz, {entry.num_channels} ch, "
                f"{entry.type} -- {entry.name}"
            )
    return lines


def main():
    print(BANNER)
    print("MetaHuman Live Link -- audio devices visible to the engine")
    print(BANNER)

    devices = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_devices(
        include_media_bundles=True
    )

    if not devices:
        print("\nNo audio devices at all. Either nothing is connected, or the")
        print("MetaHumanLiveLink plugin is not enabled in this project.")
        return

    cable = []
    for device in devices:
        marker = ""
        if any(hint in device.name.casefold() for hint in ("cable", "voicemeeter", "virtual")):
            marker = "   <- virtual"
            cable.append(device.name)
        print(f"\n  {device.name}{marker}")
        print(f"    url: {device.url}   media bundle: {device.is_media_bundle}")
        for line in describe_formats(device):
            print(line)

    print("\n" + BANNER)
    if cable:
        print("A virtual device is visible to MetaHuman Live Link:")
        for name in cable:
            print(f"  {name}")
        print("That is the assumption this project was built on, confirmed.")
    else:
        print("No virtual device in the list. Either the cable driver is not")
        print("installed and rebooted, or MetaHuman Live Link filters it out --")
        print("check whether it appears in the orchestrator's own device list:")
        print("  python tools/audio_route_check.py --list")
        print("If it appears there but not here, the fallback ladder in")
        print("unreal/PHASE0.md applies.")
    print(BANNER)


main()

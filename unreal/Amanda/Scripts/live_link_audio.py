"""Create a MetaHuman Audio Live Link source from a named device (phase 0).

The second half of the gating spike. `list_audio_devices.py` answers whether the
engine can *see* a virtual audio device; this answers whether it will accept one
as a real-time animation source, which is the question the architecture rests
on.

It is also a preview of how this should work in the finished product. The Live
Link panel is a human wiring things together by hand before each session;
`UMetaHumanLocalLiveLinkSourceBlueprint` is the same functionality as an API, so
the avatar can set up its own audio source at startup from the same config that
tells the orchestrator where to play. Nobody should have to remember a dropdown
for the thing to work.

Headless, which proves the API accepts the device:

    set AMANDA_AUDIO_DEVICE=cable output
    "D:\\unreal\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" ^
        "D:\\code\\project-amanda\\unreal\\Amanda\\Amanda.uproject" ^
        -run=pythonscript -script="Scripts/live_link_audio.py" -unattended -nopause

In the editor, with a MetaHuman in the level set to the "Amanda" subject, which
proves the face moves:

    Tools -> Execute Python Script -> Scripts/live_link_audio.py

then play audio into the cable from the orchestrator machine:

    python tools/audio_route_check.py --say "Hello. Can you see my face move?"
"""

import os

import unreal

#: Name fragment of the capture device to use. The default is the capture half
#: of VB-CABLE; anything the orchestrator can play into works.
DEVICE = os.environ.get("AMANDA_AUDIO_DEVICE", "cable output")

#: What the animation graph binds to. Keep it stable -- a Blueprint referencing
#: a subject by name breaks silently when the name changes.
SUBJECT = os.environ.get("AMANDA_LIVE_LINK_SUBJECT", "Amanda")

#: The speech side of this project works in 16 kHz mono. If the device offers
#: it, take it; a rate conversion nobody asked for is how audio problems start.
PREFERRED_RATE = 16000


def find_device(fragment):
    devices = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_devices(
        include_media_bundles=True
    )
    needle = fragment.casefold()
    for device in devices:
        if needle in device.name.casefold():
            return device
    names = ", ".join(repr(device.name) for device in devices) or "none"
    raise SystemExit(f"no audio device matching {fragment!r}. Visible devices: {names}")


def choose_format(device):
    """A usable format for this device, preferring mono at the project's rate."""
    tracks, timed_out = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_tracks(
        device, timeout=5.0
    )
    if timed_out or not tracks:
        raise SystemExit(f"{device.name!r} exposed no audio tracks")

    candidates = []
    for track in tracks:
        formats, _ = unreal.MetaHumanLocalLiveLinkSourceBlueprint.get_audio_formats(
            track, timeout=5.0
        )
        candidates.extend(formats)
    if not candidates:
        raise SystemExit(f"{device.name!r} exposed no audio formats")

    def rank(entry):
        # Mono first, then the closest rate to what the orchestrator produces.
        return (entry.num_channels != 1, abs(entry.sample_rate - PREFERRED_RATE))

    chosen = sorted(candidates, key=rank)[0]
    print(
        f"  format: {chosen.sample_rate} Hz, {chosen.num_channels} ch, "
        f"{chosen.type} ({len(candidates)} available)"
    )
    return chosen


def main():
    print("=" * 70)
    print(f"MetaHuman audio Live Link -- device {DEVICE!r}, subject {SUBJECT!r}")
    print("=" * 70)

    device = find_device(DEVICE)
    print(f"  device: {device.name}")
    audio_format = choose_format(device)

    source, created = unreal.MetaHumanLocalLiveLinkSourceBlueprint.create_audio_source()
    if not created:
        raise SystemExit("the engine refused to create an audio Live Link source")
    print("  source created")

    subject, subscribed = unreal.MetaHumanLocalLiveLinkSourceBlueprint.create_audio_subject(
        source, audio_format, SUBJECT
    )
    if not subscribed:
        raise SystemExit(
            "the source was created but would not take the device as a subject.\n"
            "That is the failure the fallback ladder in unreal/PHASE0.md is for."
        )

    print(f"  subject created: {subject.subject_name}")
    print("\nPASS. MetaHuman Live Link accepted this device as a real-time audio")
    print("source. Play speech into it and the face should move:")
    print('  python tools/audio_route_check.py --say "Hello. Can you see my face move?"')
    print("=" * 70)


main()

"""Capture the current Live Link setup as a preset asset.

`CreateAudioSubject` cannot be driven from Python -- it returns false from a
commandlet, from a startup command, and from a tick callback in a fully running
editor, and it does so identically on a physical audio interface, so it is the
synchronous call that is the problem rather than anything about virtual devices.
That rules out "the avatar configures its own Live Link source at startup",
which was the tidy plan.

A preset is the way round it. Configure the source once by hand in the Live Link
panel, run this to freeze that configuration into an asset, and have the project
load it on startup -- so nobody has to remember a dropdown before a session, and
the setup lives in the repo rather than in one machine's editor state.

**Run it in the editor that already has the source configured**, from the
console at the bottom of the main window:

    py D:\\code\\project-amanda\\unreal\\Amanda\\Scripts\\save_livelink_preset.py

A fresh editor would capture an empty client, which would be worse than no
preset at all: it would look like configuration and quietly restore nothing.

Then point the project at it. `ULiveLinkSettings` is `config=Game`, so this goes
in Config/**DefaultGame**.ini, not DefaultEngine.ini:

    [/Script/LiveLink.LiveLinkSettings]
    DefaultLiveLinkPreset=/Game/Amanda/LL_AmandaAudio.LL_AmandaAudio

The preset captures *everything* in the client, so clear out any stray sources
and subjects before running it. A preset that restores three sources, two of
them dead, is worse than none: it looks configured and is not.

And it has to be cleaned up *before* the snapshot, because it cannot be cleaned
up after: `ULiveLinkPreset`'s `Sources` and `Subjects` are read-only from
Python. `BuildFromClient` is the only thing that writes them. If the preset
holds something unwanted, fix the client and run this again -- there is no
editing the asset.

A subject that the Live Link panel will not let you remove goes when its source
does. Remove the source, add it back, and connect the one subject you want.
"""

import unreal

PACKAGE_PATH = "/Game/Amanda"
ASSET_NAME = "LL_AmandaAudio"


def main():
    client_subjects = unreal.LiveLinkBlueprintLibrary.get_live_link_subjects(
        include_disabled_subject=True, include_virtual_subject=True
    )
    print(f"### subjects in the live client: {[str(s.subject_name) for s in client_subjects]}")
    if not client_subjects:
        print("### refusing to save: this editor's Live Link client has no subjects.")
        print("### Run this in the editor where the source is configured, not a new one.")
        return

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    full_path = f"{PACKAGE_PATH}/{ASSET_NAME}"

    if unreal.EditorAssetLibrary.does_asset_exist(full_path):
        preset = unreal.EditorAssetLibrary.load_asset(full_path)
        print(f"### updating existing {full_path}")
    else:
        preset = tools.create_asset(
            asset_name=ASSET_NAME,
            package_path=PACKAGE_PATH,
            asset_class=unreal.LiveLinkPreset,
            factory=None,
        )
        print(f"### created {full_path}")

    if preset is None:
        print("### could not create the asset")
        return

    # Snapshots every source and subject currently in the client, which is why
    # this must run in the editor that has them.
    preset.build_from_client()
    saved = unreal.EditorAssetLibrary.save_loaded_asset(preset, only_if_is_dirty=False)
    print(f"### saved: {saved}")
    print(f"### sources captured: {len(preset.get_editor_property('sources'))}")
    print(f"### subjects captured: {len(preset.get_editor_property('subjects'))}")
    print("### DefaultLiveLinkPreset in Config/DefaultGame.ini points at this asset")


main()

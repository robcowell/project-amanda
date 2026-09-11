"""Tests for choosing audio devices, and for following them when they change.

Device lists are the MME names from the renderer PC on 2026-09-11, truncation
and all, with and without Bluetooth headphones connected.
"""

from __future__ import annotations

import asyncio

import pytest

from amanda.audio.devices import (
    Device,
    DeviceError,
    DeviceWatcher,
    choose_route,
    device_key,
    is_virtual,
    same_device,
)
from amanda.audio.microphone import Microphone
from amanda.audio.stt import ScriptedRecognizer
from amanda.runtime.input import VoiceInput

MAPPER_IN = Device(0, "Microsoft Sound Mapper - Input", inputs=2)
CABLE_OUT = Device(1, "CABLE Output (VB-Audio Virtual ", inputs=2)
HEADSET_MIC = Device(2, "Headset (WH-1000XM5)", inputs=1)
FOCUSRITE_MIC = Device(3, "Analogue 1 + 2 (Focusrite USB A", inputs=2)
MAPPER_OUT = Device(4, "Microsoft Sound Mapper - Output", outputs=2)
HEADPHONES = Device(5, "Headphones (WH-1000XM5)", outputs=2)
CABLE_IN = Device(6, "CABLE Input (VB-Audio Virtual C", outputs=2)
SPEAKERS = Device(7, "Speakers (Focusrite USB Audio)", outputs=2)
#: Not on the renderer PC -- a stand-in for whatever a desk microphone is called.
DESK_MIC = Device(12, "Microphone (USB Desk Mic)", inputs=1)

CONNECTED = [
    MAPPER_IN,
    CABLE_OUT,
    HEADSET_MIC,
    FOCUSRITE_MIC,
    MAPPER_OUT,
    HEADPHONES,
    CABLE_IN,
    SPEAKERS,
]
DISCONNECTED = [MAPPER_IN, CABLE_OUT, FOCUSRITE_MIC, MAPPER_OUT, CABLE_IN, SPEAKERS]


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #


def test_the_device_is_the_text_in_brackets_even_when_truncated():
    assert device_key("Headset (WH-1000XM5)") == "wh-1000xm5"
    assert device_key("Analogue 1 + 2 (Focusrite USB A") == "focusrite usb a"
    assert device_key("Headphones ()") == ""
    assert same_device("Analogue 1 + 2 (Focusrite USB A", "Speakers (Focusrite USB Audio)")
    assert not same_device("Headset (WH-1000XM5)", "Speakers (Focusrite USB Audio)")


def test_cables_are_recognised_as_virtual():
    assert is_virtual(CABLE_OUT.name) and is_virtual(CABLE_IN.name)
    assert is_virtual("CABLE In 16ch (VB-Audio Virtual")
    assert not is_virtual(HEADSET_MIC.name) and not is_virtual(SPEAKERS.name)


# --------------------------------------------------------------------------- #
# Choosing
# --------------------------------------------------------------------------- #


def test_headphones_connected_are_heard_on_and_listened_through():
    """Windows moved the default output to the headphones and left the input
    on the cable -- exactly what the renderer PC did."""
    route = choose_route(CONNECTED, CABLE_OUT.index, HEADPHONES.index, cable="cable input")
    assert route.listen == HEADPHONES
    assert route.microphone == HEADSET_MIC
    assert route.cable == CABLE_IN


def test_headphones_disconnected_use_the_speakers_and_the_default_microphone():
    route = choose_route(
        [*DISCONNECTED, DESK_MIC], DESK_MIC.index, SPEAKERS.index, cable="cable input"
    )
    assert route.listen == SPEAKERS
    assert route.microphone == DESK_MIC
    assert route.cable == CABLE_IN


def test_a_headset_is_listened_through_even_when_a_desk_mic_is_the_default():
    route = choose_route([*CONNECTED, DESK_MIC], DESK_MIC.index, HEADPHONES.index)
    assert route.microphone == HEADSET_MIC


def test_speakers_are_never_paired_with_an_interface_input():
    """The Focusrite's inputs exist whether or not a microphone is plugged in.
    Rob: "no focusrite mic attached - don't assume"."""
    with pytest.raises(DeviceError, match="default recording device is 'CABLE Output"):
        choose_route(DISCONNECTED, CABLE_OUT.index, SPEAKERS.index, cable="cable input")


def test_a_cable_as_the_default_is_an_error_not_a_substitute():
    """Another input could be anything, including one with nothing attached.
    Saying which setting to change beats listening to silence."""
    devices = [
        CABLE_OUT,
        Device(9, "Microphone (USB Mic)", inputs=1),
        CABLE_IN,
        Device(8, "Speakers (Realtek)", outputs=2),
    ]
    with pytest.raises(DeviceError, match="Set your microphone as the default"):
        choose_route(devices, CABLE_OUT.index, 8, cable="cable input")


def test_the_sound_mapper_alias_is_never_the_microphone():
    """It stands for the default input, which was the cable."""
    with pytest.raises(DeviceError, match="no microphone"):
        choose_route(
            [MAPPER_IN, CABLE_OUT, FOCUSRITE_MIC, MAPPER_OUT, SPEAKERS], MAPPER_IN.index, None
        )


def test_the_default_input_is_used_when_nothing_pairs_with_the_output():
    other = Device(10, "Microphone (Webcam)", inputs=1)
    route = choose_route(
        [FOCUSRITE_MIC, other, Device(11, "Speakers (Realtek)", outputs=2)], other.index, 11
    )
    assert route.microphone == other


def test_no_real_microphone_is_an_error_not_a_feedback_loop():
    with pytest.raises(DeviceError, match="no microphone"):
        choose_route(
            [CABLE_OUT, CABLE_IN, SPEAKERS], CABLE_OUT.index, SPEAKERS.index, cable="cable input"
        )


def test_asking_for_the_cable_as_a_microphone_is_refused():
    with pytest.raises(DeviceError, match="virtual cable"):
        choose_route(CONNECTED, None, HEADPHONES.index, microphone="cable output")


def test_typed_input_needs_no_microphone():
    route = choose_route(
        [CABLE_OUT, CABLE_IN, SPEAKERS], CABLE_OUT.index, SPEAKERS.index, need_microphone=False
    )
    assert route.microphone is None


def test_without_a_cable_her_voice_goes_where_she_is_heard():
    route = choose_route(
        [FOCUSRITE_MIC, SPEAKERS], None, SPEAKERS.index, cable="cable input", need_microphone=False
    )
    assert route.cable is None
    assert route.listen == SPEAKERS


def test_a_cable_asked_for_explicitly_must_exist():
    with pytest.raises(DeviceError, match="virtual cable installed"):
        choose_route(
            [FOCUSRITE_MIC, SPEAKERS], None, SPEAKERS.index, cable="cable input", require_cable=True
        )


def test_the_cable_as_the_default_output_is_not_where_she_is_heard():
    route = choose_route(
        DISCONNECTED, None, CABLE_IN.index, cable="cable input", need_microphone=False
    )
    assert route.listen == SPEAKERS


def test_hearing_her_on_the_cable_is_refused():
    with pytest.raises(DeviceError, match="nobody hears it"):
        choose_route(CONNECTED, None, None, cable="cable input", listen="cable input")


# --------------------------------------------------------------------------- #
# Following change
# --------------------------------------------------------------------------- #


async def test_the_watcher_flags_a_change_in_the_endpoints():
    values = iter([("a",), ("a",), ("b",)] + [("b",)] * 100)
    watcher = DeviceWatcher(interval=0.01, signature=lambda: next(values))
    assert await watcher.start()
    await asyncio.wait_for(watcher.changed.wait(), timeout=1.0)
    await watcher.stop()


async def test_nothing_is_watched_where_endpoints_cannot_be_read():
    watcher = DeviceWatcher(interval=0.01, signature=lambda: None)
    assert not await watcher.start()
    await asyncio.sleep(0.05)
    assert not watcher.changed.is_set()
    await watcher.stop()


async def test_switching_the_microphone_does_not_end_the_conversation():
    """A stopped stream normally means the user has gone, and next_turn()
    returns None. A device switch stops the stream too, and must not."""
    mic = Microphone(source=[bytes(1024)] * 500)
    voice = VoiceInput(microphone=mic, recognizer=ScriptedRecognizer())
    await voice.start()

    reopened = []
    await voice.switch_microphone(lambda: reopened.append(True) or 7)

    assert reopened == [True]
    assert mic.device == 7
    assert mic.running
    assert voice._utterances.empty(), "a switch must not look like the end of input"
    await voice.stop()

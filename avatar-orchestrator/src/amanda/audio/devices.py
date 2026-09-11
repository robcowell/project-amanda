"""Which devices to use: a microphone, somewhere to hear her, and the cable.

A desktop app has to follow the person rather than a config file. Headphones
on: hear her in them and talk into their mic. Headphones off: the speakers and
the desk mic. Through all of it her voice must keep going into the virtual
cable, because that is what drives the face.

Windows gets half of this right by itself. Connecting Bluetooth headphones
makes them the default *output*; it leaves the default *input* alone -- and on
the renderer PC that input was the cable, so "use the default microphone" had
her listening to her own voice. So when you are listening on a headset, the
microphone is the headset's own. Otherwise it is Windows' default recording
device -- and never a virtual cable.

Nothing is guessed beyond that. Pairing speakers with the input of the same
audio interface looks reasonable and is wrong: an interface's inputs exist
whether or not anything is plugged into them (Rob: "no focusrite mic attached
- don't assume"). With no headset and no real default microphone, the answer
is an error that says which Windows setting to change.

`choose_route` is a pure function over a device list, so every case is tested
without hardware. Everything that touches PortAudio or the registry is below it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Name fragments that mark a virtual device. None of these is ever a microphone,
#: and none is somewhere a person hears anything.
VIRTUAL_MARKERS = ("vb-audio", "cable ", "cable-", "voicemeeter", "virtual")

#: Entries that are not devices but aliases for "whatever the default is".
#: Choosing one would quietly bring the default input back -- the cable.
ALIAS_MARKERS = ("sound mapper", "primary sound")

#: Outputs worn on the head. Their microphone, when they have one, is part of
#: the same hardware, so listening on one means talking into it.
HEADSET_MARKERS = ("headphone", "headset", "hands-free", "handsfree", "earphone", "earbud")

#: How long to let Windows settle after devices change. Headphones become the
#: default output a moment after they connect, not at the same instant.
SETTLE_SECONDS = 1.5


class DeviceError(RuntimeError):
    """No usable device, or an explicit choice that would break the routing."""


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    inputs: int = 0
    outputs: int = 0


@dataclass(frozen=True)
class AudioRoute:
    #: Where she listens. None when nothing is listening (typed input).
    microphone: Device | None
    #: Where a person hears her.
    listen: Device
    #: The virtual cable her voice goes into for the face. None without one.
    cable: Device | None


def is_virtual(name: str) -> bool:
    folded = name.casefold()
    return any(marker in folded for marker in VIRTUAL_MARKERS)


def is_alias(name: str) -> bool:
    folded = name.casefold()
    return any(marker in folded for marker in ALIAS_MARKERS)


def is_headset(name: str) -> bool:
    folded = name.casefold()
    return any(marker in folded for marker in HEADSET_MARKERS)


def device_key(name: str) -> str:
    """The physical device an endpoint belongs to: the text in its last brackets.

    "Headset (WH-1000XM5)" and "Headphones (WH-1000XM5)" share "wh-1000xm5".
    MME truncates names to 31 characters, so the bracket may never close --
    "Analogue 1 + 2 (Focusrite USB A" still gives "focusrite usb a".
    """
    start = name.rfind("(")
    if start < 0:
        return ""
    end = name.find(")", start)
    return name[start + 1 : end if end >= 0 else None].strip().casefold()


def same_device(a: str, b: str) -> bool:
    """Whether two endpoints are the same hardware, allowing for truncation."""
    key_a, key_b = device_key(a), device_key(b)
    if min(len(key_a), len(key_b)) < 4:
        return False
    return key_a.startswith(key_b) or key_b.startswith(key_a)


def _find(devices: Sequence[Device], fragment: str) -> Device | None:
    needle = fragment.casefold()
    return next((d for d in devices if needle in d.name.casefold()), None)


def choose_route(
    devices: Sequence[Device],
    default_input: int | None,
    default_output: int | None,
    *,
    cable: str | None = None,
    require_cable: bool = False,
    microphone: str | None = None,
    listen: str | None = None,
    need_microphone: bool = True,
) -> AudioRoute:
    """Pick the microphone, the output to hear her on, and the cable.

    `cable`, `microphone` and `listen` are name fragments. `microphone` and
    `listen` override the automatic choice; `cable` is looked for and used if
    present, and must be present only when `require_cable` says so.
    """
    by_index = {d.index: d for d in devices}
    outputs = [d for d in devices if d.outputs > 0 and not is_alias(d.name)]
    inputs = [d for d in devices if d.inputs > 0 and not is_alias(d.name)]

    cable_device = _find(outputs, cable) if cable else None
    if cable_device is None and require_cable:
        raise DeviceError(f"no output device matching {cable!r} -- is the virtual cable installed?")

    if listen:
        heard = _find(outputs, listen)
        if heard is None:
            raise DeviceError(f"no output device matching {listen!r}")
        if cable_device is not None and heard.index == cable_device.index:
            raise DeviceError(f"{heard.name!r} is the cable her voice goes into; nobody hears it")
    else:
        default = by_index.get(default_output) if default_output is not None else None
        candidates = [default] if default is not None and default in outputs else []
        candidates += outputs
        candidates = [d for d in candidates if not is_virtual(d.name)]
        if not candidates:
            raise DeviceError("no speakers or headphones: every output is a virtual cable")
        heard = candidates[0]

    mic: Device | None = None
    if microphone:
        mic = _find(inputs, microphone)
        if mic is None:
            raise DeviceError(f"no input device matching {microphone!r}")
        if is_virtual(mic.name):
            raise DeviceError(
                f"{mic.name!r} is a virtual cable, not a microphone -- she would hear "
                "her own voice. Choose a real microphone."
            )
    elif need_microphone:
        real = [d for d in inputs if not is_virtual(d.name)]
        default = by_index.get(default_input) if default_input is not None else None
        # A headset's own microphone -- but only a headset's. Speakers are never
        # paired with an interface input that may have nothing plugged in.
        paired = (
            [d for d in real if same_device(d.name, heard.name)] if is_headset(heard.name) else []
        )
        if paired:
            mic = paired[0]
        elif default is not None and default in real:
            mic = default
        else:
            current = f"is {default.name!r}" if default is not None else "is not set"
            raise DeviceError(
                f"she has no microphone: Windows' default recording device {current}. "
                "Set your microphone as the default recording device (Settings > "
                "System > Sound), or connect a headset."
            )

    return AudioRoute(microphone=mic, listen=heard, cable=cable_device)


# --------------------------------------------------------------------------- #
# PortAudio
# --------------------------------------------------------------------------- #


def current_devices() -> tuple[list[Device], int | None, int | None]:
    """The default audio API's devices, with its default input and output.

    One API only. Windows lists every device once per audio API -- MME,
    DirectSound, WASAPI, WDM-KS -- under slightly different names, and pairing
    a microphone with headphones by name only works within one list. The
    default API is the one sounddevice opens when given nothing else.
    """
    import sounddevice

    default_in, default_out = sounddevice.default.device
    try:
        hostapi = sounddevice.default.hostapi
    except AttributeError:
        hostapi = sounddevice.query_devices(default_out)["hostapi"]

    found = [
        Device(index, info["name"], info["max_input_channels"], info["max_output_channels"])
        for index, info in enumerate(sounddevice.query_devices())
        if info["hostapi"] == hostapi
    ]
    return (
        found,
        default_in if default_in is not None and default_in >= 0 else None,
        default_out if default_out is not None and default_out >= 0 else None,
    )


def reinitialise() -> None:
    """Make PortAudio look again.

    It lists devices once, when it starts: headphones connected later do not
    exist to it, and ones since disconnected linger as dead entries. Restarting
    it is the only refresh, and it is only safe with no stream open.
    """
    import sounddevice

    sounddevice._terminate()
    sounddevice._initialize()


# --------------------------------------------------------------------------- #
# Noticing change
# --------------------------------------------------------------------------- #

_MMDEVICES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"


def endpoint_signature() -> tuple | None:
    """Which audio endpoints Windows has and which are active, from the registry.

    Cheap, and it leaves PortAudio alone -- unlike asking PortAudio, which means
    restarting it and closing the microphone. Changes when headphones connect
    or disconnect. None off Windows, or if the registry cannot be read.

    It does not see a manual change of default with nothing plugged or
    unplugged: Windows 11 no longer records defaults there (checked on the
    renderer PC, 2026-09-11). That is picked up at the next start instead.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    signature = []
    try:
        for flow in ("Render", "Capture"):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{_MMDEVICES}\{flow}") as root:
                index = 0
                while True:
                    try:
                        guid = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, guid) as key:
                            state = winreg.QueryValueEx(key, "DeviceState")[0]
                    except OSError:
                        continue
                    signature.append((flow, guid, state))
    except OSError:
        return None
    return tuple(sorted(signature))


@dataclass
class DeviceWatcher:
    """Notices audio devices coming and going, and sets `changed`.

    Switching is the caller's job, at a moment when no stream it owns is open.
    """

    interval: float = 2.0
    signature: Callable[[], object] = endpoint_signature
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _last: object = field(default=None, init=False, repr=False)

    async def start(self) -> bool:
        """Begin watching. False where changes cannot be seen."""
        self._last = await asyncio.to_thread(self.signature)
        if self._last is None:
            return False
        self._task = asyncio.create_task(self._run(), name="device-watcher")
        return True

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            current = await asyncio.to_thread(self.signature)
            if current is not None and current != self._last:
                self._last = current
                log.info("audio devices changed")
                self.changed.set()

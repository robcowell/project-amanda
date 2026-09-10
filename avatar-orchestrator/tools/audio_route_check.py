"""Does audio actually route from this process into a capture device? (phase 0)

The pivotal unverified assumption in this project: MetaHuman's real-time audio
solver is a Live Link source that reads an *audio capture device*, and the plan
is to feed it by playing TTS into a virtual audio cable that Unreal then reads
as though it were a microphone. Nothing about that has ever been tested, and it
is the only step whose outcome could change the architecture.

That assumption is really two, and only the second one needs Unreal:

  1. **Audio can travel from this process into a capture device.** The
     orchestrator plays into "CABLE Input"; something else records from "CABLE
     Output" and hears it. That is what this script tests, and it needs no
     engine, no MetaHuman and no GPU.
  2. **Unreal's MetaHuman Audio Live Link source will accept that device.**
     Epic's docs describe a device picker and emphasise USB capture hardware,
     so whether a virtual device appears in the list is an empirical question.
     Answering it means opening the editor.

If step 1 fails there is no point downloading an engine to find out about step
2. If it passes, the remaining risk is a dropdown.

    python tools/audio_route_check.py --list
    python tools/audio_route_check.py --say "Hello. Can you see my face move?"
    python tools/audio_route_check.py --wav sample.wav --save captured.wav

Defaults play into a device matching "cable input" and record from one matching
"cable output", which is what VB-CABLE calls its two halves. Pass --out and
--in to route through anything else -- Voicemeeter, a loopback driver, or a
physical cable between two sound cards.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import math
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amanda.audio.microphone import (  # noqa: E402
    CAPTURE_RATE,
    MicrophoneError,
    list_input_devices,
    resolve_device,
)
from amanda.audio.sink import DeviceSink, list_output_devices  # noqa: E402
from amanda.audio.tts import SAMPLE_WIDTH  # noqa: E402

# CAPTURE_RATE and the device helpers are imported rather than restated, so this
# tool asks a device for exactly what the orchestrator's microphone asks it for.
# A spike that proved the route at some other rate would not have proved it.

#: Level below which the recording is silence rather than signal. A live but
#: unrouted capture device sits near -70 dBFS; anything genuinely playing sits
#: far above this.
SIGNAL_DB = -50.0

SILENT_DB = -90.0


def rms_db(pcm: bytes) -> float:
    """Level of a buffer in dBFS."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return SILENT_DB
    total = sum(sample * sample for sample in samples)
    rms = math.sqrt(total / len(samples))
    return SILENT_DB if rms < 1.0 else max(SILENT_DB, 20 * math.log10(rms / 32768.0))


def peak_db(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return SILENT_DB
    peak = max(max(samples), -min(samples))
    return SILENT_DB if peak < 1 else max(SILENT_DB, 20 * math.log10(peak / 32768.0))


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


def show_devices() -> None:
    """Print every device, marking the ones that look like virtual cables.

    Worth reading before anything else: if no virtual device appears here, the
    driver is not installed and nothing downstream can work. VB-CABLE needs a
    reboot before its two halves show up.
    """
    print("output devices (the orchestrator plays into one of these)")
    for index, name in list_output_devices():
        print(f"  {index:>3}  {name}{_mark(name)}")
    print("\ninput devices (Unreal's Live Link source reads one of these)")
    for index, name in list_input_devices():
        print(f"  {index:>3}  {name}{_mark(name)}")
    print(
        "\nA virtual cable appears in both lists: its playback half in the first,\n"
        "its capture half in the second. Both are needed."
    )


def _mark(name: str) -> str:
    lowered = name.casefold()
    if any(hint in lowered for hint in ("cable", "voicemeeter", "virtual", "loopback")):
        return "   <- looks like a virtual cable"
    return ""


# --------------------------------------------------------------------------- #
# The test
# --------------------------------------------------------------------------- #


async def record(device: int | None, seconds: float, rate: int) -> bytes:
    """Capture from an input device, exactly as a Live Link source would."""
    import sounddevice

    frames = round(rate * seconds)
    stream = sounddevice.RawInputStream(
        samplerate=rate, channels=1, dtype="int16", device=device
    )
    chunks: list[bytes] = []

    def capture() -> None:
        with stream:
            remaining = frames
            while remaining > 0:
                block = min(1024, remaining)
                data, overflowed = stream.read(block)
                if overflowed:
                    print("  (input overflow -- the recording has a gap in it)")
                chunks.append(bytes(data))
                remaining -= block

    await asyncio.to_thread(capture)
    return b"".join(chunks)


async def signal(args: argparse.Namespace) -> tuple[bytes, int]:
    """The audio to send: a spoken phrase, a WAV, or a tone.

    Speech matters for the second half of the spike -- a MetaHuman audio solver
    given a sine wave has nothing to make a mouth shape out of, so a pass there
    proves less than it looks. For testing the route itself, a tone is fine and
    needs nothing installed.
    """
    if args.wav:
        with wave.open(str(args.wav), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != SAMPLE_WIDTH:
                raise SystemExit(f"{args.wav} must be mono 16-bit")
            return handle.readframes(handle.getnframes()), handle.getframerate()

    if args.say:
        from amanda.audio.engines import build

        synthesizer, voice = build(args.engine)
        print(f"  synthesising with {synthesizer.name} at {voice.sample_rate} Hz")
        if warm := getattr(synthesizer, "warm", None):
            await warm()
        stream = synthesizer.synthesize(args.say, voice)
        pcm = b"".join([chunk async for chunk in stream])
        return pcm, voice.sample_rate

    rate = 24_000
    count = round(rate * args.seconds)
    step = 2 * math.pi * 440.0 / rate
    tone = array.array(
        "h", [int(9000 * math.sin(step * index)) for index in range(count)]
    ).tobytes()
    return tone, rate


async def run(args: argparse.Namespace) -> int:
    try:
        capture_device = resolve_device(args.into)
    except MicrophoneError as exc:
        print(f"\nFAILED to find the input device: {exc}")
        return 2

    played, play_rate = await signal(args)
    duration = len(played) / SAMPLE_WIDTH / play_rate
    print(f"\nplaying {duration:.1f}s into {args.out!r}, recording from {args.into!r}")

    sink = DeviceSink(device=args.out)
    try:
        await sink.open(play_rate)
    except Exception as exc:  # noqa: BLE001 - the whole point is to report it
        print(f"\nFAILED to open the output device: {exc}")
        return 2

    # Recording starts first and runs a little longer, so nothing is lost to
    # the device opening late or the tail arriving after playback returns.
    capture = asyncio.create_task(record(capture_device, duration + 0.6, args.rate))
    await asyncio.sleep(0.2)
    await sink.write(played)
    await sink.drain()
    await sink.close()
    recorded = await capture

    return report(recorded, duration, args)


def report(recorded: bytes, duration: float, args: argparse.Namespace) -> int:
    level = rms_db(recorded)
    peak = peak_db(recorded)
    print(f"\ncaptured {len(recorded) / SAMPLE_WIDTH / args.rate:.1f}s")
    print(f"  rms  {level:6.1f} dBFS")
    print(f"  peak {peak:6.1f} dBFS")

    if args.save:
        with wave.open(str(args.save), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(args.rate)
            handle.writeframes(recorded)
        print(f"  saved to {args.save} -- listen to it before believing any of this")

    if level > SIGNAL_DB:
        print(
            "\nPASS. Audio played by this process was captured from a recording\n"
            "device, which is the half of the Live Link assumption that does not\n"
            "need Unreal. What remains is whether the MetaHuman Audio Live Link\n"
            "source lists this device: Add Source -> MetaHuman (Audio)."
        )
        return 0

    print(
        "\nFAIL. The recording is silence, so nothing routed.\n"
        "  * Is the virtual cable installed, and has the machine rebooted since?\n"
        "  * Are --out and --in the two halves of the same cable, not the same end twice?\n"
        "  * Windows can mute a device per-application: check the volume mixer.\n"
        "  * Some cables default to a sample rate they will not convert from; try\n"
        "    --rate 48000, or match the rate in the device's Advanced properties."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="show every audio device and exit")
    parser.add_argument(
        "--out", default="cable input", help="output device to play into (name fragment)"
    )
    parser.add_argument(
        "--in", dest="into", default="cable output", help="input device to record from"
    )
    parser.add_argument("--say", help="speak this text instead of playing a tone")
    parser.add_argument("--wav", type=Path, help="play this mono 16-bit WAV instead")
    parser.add_argument("--engine", default="auto", help="TTS engine for --say")
    parser.add_argument("--seconds", type=float, default=2.0, help="tone length")
    parser.add_argument(
        "--rate",
        type=int,
        default=CAPTURE_RATE,
        help="capture rate; some cables refuse to convert and want 48000",
    )
    parser.add_argument("--save", type=Path, help="write the captured audio here")
    args = parser.parse_args()

    try:
        import sounddevice  # noqa: F401
    except ImportError:
        raise SystemExit(
            "this needs the audio extra: pip install -e '.[audio]'"
        ) from None

    if args.list:
        show_devices()
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

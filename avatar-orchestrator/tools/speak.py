#!/usr/bin/env python3
"""Speak a phrase through the TTS pipeline.

Exists mainly to answer three questions without running the whole orchestrator:
which output device to use, whether an engine is wired up correctly, and what an
interruption sounds like.

    python3 tools/speak.py --devices
    python3 tools/speak.py "It rained most of the morning."
    python3 tools/speak.py "..." --engine espeak-ng
    python3 tools/speak.py "..." --device "cable input"
    python3 tools/speak.py "..." --interrupt-after 700
    python3 tools/speak.py "..." --wav /tmp/phrase.wav

On the Windows box the useful form is `--device "cable input"`: that puts the
avatar's voice into the virtual cable Unreal reads as a microphone, rather than
out of the speakers.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amanda.audio.engines import ENGINES, build, describe  # noqa: E402
from amanda.audio.providers import CommandSynthesizer  # noqa: E402
from amanda.audio.sink import (  # noqa: E402
    RAW_PLAYERS,
    CommandSink,
    DeviceSink,
    NullSink,
    list_output_devices,
)
from amanda.audio.speech import SpeechSession  # noqa: E402
from amanda.audio.tts import VoiceSettings  # noqa: E402
from amanda.config import load_env  # noqa: E402


def build_voice(args: argparse.Namespace):
    """A synthesizer and a matching voice, from the engine registry."""
    if args.argv:
        synthesizer = CommandSynthesizer(
            argv=args.argv, expects_wav=not args.raw, label="custom"
        )
        return synthesizer, VoiceSettings(
            voice_id=args.voice, sample_rate=args.rate or 24_000, pace=args.pace
        )
    return build(
        args.engine, voice_id=args.voice, sample_rate=args.rate, pace=args.pace
    )


def build_sink(args: argparse.Namespace):
    if args.wav:
        # Paced when demonstrating an interruption: writing to memory at full
        # speed would finish the utterance before there was anything to cut.
        return NullSink(realtime=bool(args.interrupt_after))
    if args.player:
        return CommandSink(argv=RAW_PLAYERS[args.player])
    return DeviceSink(device=args.device)


async def main_async(args: argparse.Namespace) -> int:
    if args.devices:
        for index, name in list_output_devices():
            print(f"  [{index:>2}] {name}")
        return 0

    if not args.text:
        print("nothing to say -- pass some text, or --devices", file=sys.stderr)
        return 2

    synthesizer, voice = build_voice(args)
    sink = build_sink(args)

    events: list[str] = []
    session = SpeechSession(
        utterance_id="u_demo",
        synthesizer=synthesizer,
        sink=sink,
        voice=voice,
        emit=lambda payload: events.append(payload.event.value),
        fade_ms=args.fade,
    )

    print(f"engine {synthesizer.name}, {voice.sample_rate} Hz, pace {voice.pace}")
    await session.start()
    for phrase in args.text:
        await session.add(phrase)
    session.close_input()

    if args.interrupt_after:
        await asyncio.sleep(args.interrupt_after / 1000)
        print(f"--- interrupting after {args.interrupt_after}ms ---")
        await session.cancel()
    else:
        await session.wait()

    spoken = session.result
    print(f"events: {' -> '.join(events)}")
    print(
        f"synthesised {spoken.synthesized_ms}ms, played {spoken.spoken_ms}ms"
        + (" (cancelled)" if spoken.cancelled else "")
    )

    if args.wav:
        with wave.open(args.wav, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(voice.sample_rate)
            handle.writeframes(sink.pcm)
        print(f"wrote {args.wav}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="*", help="phrases to speak, one argument each")
    parser.add_argument("--devices", action="store_true", help="list output devices and exit")
    parser.add_argument(
        "--engine", default="auto", choices=["auto", *sorted(ENGINES)],
        help="; ".join(describe()),
    )
    parser.add_argument("--argv", nargs="+", help="a full engine command, overriding --engine")
    parser.add_argument("--raw", action="store_true", help="the engine emits raw PCM, not WAV")
    parser.add_argument("--device", help="output device, by index or name fragment")
    parser.add_argument("--player", choices=sorted(RAW_PLAYERS), help="pipe to a player instead")
    parser.add_argument("--wav", help="write to a file instead of playing")
    parser.add_argument("--voice", help="engine-specific voice id or model path")
    parser.add_argument("--rate", type=int, help="override the engine's native sample rate")
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--fade", type=int, default=80)
    parser.add_argument(
        "--interrupt-after", type=int, metavar="MS", help="cancel mid-phrase, to hear the fade"
    )
    args = parser.parse_args()
    load_env()

    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

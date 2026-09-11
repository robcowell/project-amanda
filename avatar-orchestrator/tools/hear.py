#!/usr/bin/env python3
"""Hear what she hears: live utterances, how long Whisper took, and why.

Opens the microphone her route would choose (see audio/devices.py), cuts
utterances with the same endpointer the conversation uses, and transcribes each
with the same recogniser. For each it prints the utterance's length and voiced
length, its level, the transcription time, and Whisper's per-segment detail --
the temperature each segment was accepted at, where anything above 0 means it
was not confident and decoded again, which multiplies the time.

Written because real turns on the renderer PC took 2-5s to transcribe while
Piper sentences, clean or band-limited to telephone quality, took ~0.5s.
Nothing is written to disk; the audio lives only in memory.

    python tools/hear.py                      # the microphone she would use
    python tools/hear.py --input-device NAME  # a particular one
    python tools/hear.py --threads 12         # compare CPU thread counts
    python tools/hear.py --count 5            # stop after five utterances
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amanda.audio import devices  # noqa: E402
from amanda.audio.microphone import Microphone  # noqa: E402
from amanda.audio.vad import Endpointer  # noqa: E402
from amanda.audio.whisper_provider import WhisperRecognizer  # noqa: E402
from amanda.config import stt_settings  # noqa: E402


def level_dbfs(pcm: bytes) -> float:
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return float("-inf")
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms else float("-inf")


def choose_microphone(name: str | None) -> tuple[int, str]:
    found, default_in, default_out = devices.current_devices()
    route = devices.choose_route(found, default_in, default_out, microphone=name)
    return route.microphone.index, route.microphone.name


async def main_async(args: argparse.Namespace) -> int:
    try:
        index, name = choose_microphone(args.input_device)
    except devices.DeviceError as exc:
        print(exc)
        return 2

    settings = stt_settings()
    recognizer = WhisperRecognizer(
        model=settings.get("model") or "base.en",
        compute_type=settings.get("compute_type") or "int8",
        cpu_threads=args.threads,
    )
    print(f"loading {recognizer.name} ({args.threads or 'default 4'} threads)...", flush=True)
    await recognizer.warm()

    endpointer = Endpointer()
    heard = 0
    print(f"listening on {name} -- speak, then pause. Ctrl+C to stop.\n", flush=True)
    async with Microphone(device=index) as mic, mic.listen() as frames:
        async for frame in frames:
            utterance = endpointer.feed(frame)
            if utterance is None:
                continue
            transcript = await recognizer.transcribe(utterance)
            temps = [round(t, 1) for t, *_ in recognizer.last_segments]
            logprob = [round(p, 2) for _, p, *_ in recognizer.last_segments]
            ratio = [round(c, 1) for _, _, c, _ in recognizer.last_segments]
            nospeech = [round(n, 2) for *_, n in recognizer.last_segments]
            heard_s, voiced_s = utterance.duration_ms / 1000, utterance.voiced_ms / 1000
            print(
                f"{heard_s:4.1f}s heard ({voiced_s:.1f}s voiced), "
                f"{level_dbfs(utterance.pcm):6.1f} dBFS -> {transcript.elapsed_ms:5d} ms\n"
                f"    temperature {temps}  logprob {logprob}  compression {ratio}  "
                f"no-speech {nospeech}\n"
                f"    {transcript.text!r}\n",
                flush=True,
            )
            heard += 1
            if args.count and heard >= args.count:
                return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input-device", help="microphone, by name fragment")
    parser.add_argument("--threads", type=int, default=0, help="CTranslate2 CPU threads")
    parser.add_argument("--count", type=int, default=0, help="stop after this many utterances")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

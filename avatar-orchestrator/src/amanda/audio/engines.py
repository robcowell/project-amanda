"""Known speech engines and how to drive them.

Epic 3's "select initial engine" step, as a table rather than a decision. Each
entry is the argv template and, critically, the sample rate the engine actually
emits -- a detail that is invisible until it is wrong, at which point the voice
plays at the wrong pitch and the engine gets blamed for a configuration
mistake.

Adding an engine here should not need code: an argv template and its native rate
is the whole contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amanda.audio.providers import CommandSynthesizer, ToneSynthesizer
from amanda.audio.tts import SpeechSynthesizer, VoiceSettings


@dataclass(frozen=True, slots=True)
class Engine:
    """How to run one engine, and what it produces."""

    #: argv template taking {text}, {rate} and {voice}. None means built-in.
    argv: tuple[str, ...] | None
    #: What the engine emits. Not negotiable -- resampling is out of scope, so
    #: the voice is configured to match rather than the other way round.
    sample_rate: int
    expects_wav: bool = True
    #: Words per minute passed as {rate}, before the pace multiplier.
    base_rate: int = 165
    note: str = ""


ENGINES: dict[str, Engine] = {
    "tone": Engine(
        argv=None,
        sample_rate=24_000,
        note="built-in stand-in: audible and correctly timed, but not speech",
    ),
    # espeak-ng emits 22050 Hz regardless of the voice, and writes a WAV header
    # with a bogus length when piped -- it cannot seek back to fix it. Harmless
    # here because the reader goes to EOF rather than trusting the declared size.
    "espeak-ng": Engine(
        argv=("espeak-ng", "--stdout", "-s", "{rate}", "-v", "{voice}", "{text}"),
        sample_rate=22_050,
        note="robotic but instant, and installs from any package manager",
    ),
    "espeak": Engine(
        argv=("espeak", "--stdout", "-s", "{rate}", "{text}"),
        sample_rate=22_050,
    ),
    # Piper's rate is a property of the downloaded model, commonly 22050 for the
    # medium voices and 16000 for the low ones. Override --rate to match yours.
    "piper": Engine(
        argv=("piper", "--model", "{voice}", "--output-raw", "--", "{text}"),
        sample_rate=22_050,
        expects_wav=False,
        note="local neural voices; --voice is a path to the .onnx model",
    ),
    "say": Engine(
        argv=("say", "-r", "{rate}", "-o", "-", "--data-format=LEI16@22050", "{text}"),
        sample_rate=22_050,
        note="macOS only",
    ),
}


def build(
    name: str,
    *,
    voice_id: str | None = None,
    sample_rate: int | None = None,
    pace: float = 1.0,
    **overrides: Any,
) -> tuple[SpeechSynthesizer, VoiceSettings]:
    """A synthesizer and a voice whose sample rate the engine will agree with.

    The rate defaults to what the engine emits rather than to a house value,
    because a mismatch is refused rather than resampled -- making the default
    correct is cheaper than making the error message good.
    """
    if name not in ENGINES:
        known = ", ".join(sorted(ENGINES))
        raise KeyError(f"unknown engine {name!r}. Known engines: {known}")

    engine = ENGINES[name]
    voice = VoiceSettings(
        voice_id=voice_id,
        sample_rate=sample_rate or engine.sample_rate,
        pace=pace,
    )

    if engine.argv is None:
        return ToneSynthesizer(**overrides), voice

    argv = list(engine.argv)
    if voice_id is None:
        # Drop the flag and its value rather than passing an empty voice, which
        # most engines reject.
        argv = _drop_voice_flag(argv)

    return (
        CommandSynthesizer(
            argv=argv,
            expects_wav=engine.expects_wav,
            base_rate=engine.base_rate,
            label=name,
        ),
        voice,
    )


def _drop_voice_flag(argv: list[str]) -> list[str]:
    try:
        index = argv.index("{voice}")
    except ValueError:
        return argv
    # Remove the placeholder and the flag immediately before it.
    start = index - 1 if index > 0 and argv[index - 1].startswith("-") else index
    return argv[:start] + argv[index + 1 :]


def describe() -> list[str]:
    """One line per engine, for --help style listings."""
    lines = []
    for name, engine in sorted(ENGINES.items()):
        detail = f"{engine.sample_rate} Hz"
        if engine.note:
            detail += f" -- {engine.note}"
        lines.append(f"{name:<10} {detail}")
    return lines

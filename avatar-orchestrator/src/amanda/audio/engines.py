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

import importlib.util
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amanda.audio.providers import CommandSynthesizer, ToneSynthesizer
from amanda.audio.tts import SpeechSynthesizer, SynthesisError, VoiceSettings
from amanda.config import default_voice


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
    #: Some engines read the text from stdin rather than argv.
    text_on_stdin: bool = False
    #: Name of a native in-process provider, when shelling out is the wrong
    #: shape -- see the piper entry for why that is not merely a preference.
    native: str | None = None
    #: Needs a model file, so being on PATH is not enough to call it installed.
    needs_model: bool = False
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
    # Run in-process, not as a subprocess: each `piper` invocation spends about
    # 3.5s loading before it synthesises, which would land on every phrase.
    # Loaded once it runs at roughly 8x realtime. The sample rate belongs to the
    # model -- 22050 for medium and high voices, 16000 for low -- so pass --rate
    # if yours differs.
    "piper": Engine(
        argv=None,
        native="piper",
        sample_rate=22_050,
        needs_model=True,
        note="local neural voices; put a .onnx model in voices/ or pass --voice",
    ),
    "say": Engine(
        argv=("say", "-r", "{rate}", "-o", "-", "--data-format=LEI16@22050", "{text}"),
        sample_rate=22_050,
        note="macOS only",
    ),
}


#: Preference order when asked for "auto", best first. The stand-in is last
#: because it is a placeholder: hearing it when a real engine is installed is a
#: confusing default, not a safe one.
PREFERENCE: tuple[str, ...] = ("piper", "say", "espeak-ng", "espeak", "tone")


#: Names the voice model to use when none is given, so the choice is not left
#: to alphabetical accident. Set it once rather than passing --voice every time.
VOICE_ENV = "AMANDA_VOICE"

#: Where to look for downloaded voice models, in order.
MODEL_DIRS: tuple[Path, ...] = (
    Path("voices"),
    Path.home() / ".local" / "share" / "piper-voices",
)


def which(command: str) -> str | None:
    """Find an executable on PATH, or beside the running interpreter.

    The second half matters: a console script installed into a virtualenv lives
    in the same bin directory as the interpreter, and running
    `.venv/bin/python -m amanda.main` does not put that directory on PATH. Without
    this, an engine pip-installed into the venv looks absent.
    """
    found = shutil.which(command)
    if found:
        return found
    candidate = Path(sys.executable).parent / command
    return str(candidate) if candidate.exists() else None


def find_model(
    prefer: str | None = None, directories: Sequence[Path] = MODEL_DIRS
) -> Path | None:
    """A voice model on disk, chosen by name fragment if one is given.

    Fragment matching for the same reason the audio device uses it: nobody
    should have to type "voices/en_GB-jenny_dioco-medium.onnx" when "jenny"
    identifies it unambiguously.

    With no preference the chain is $AMANDA_VOICE, then the voice named in
    config/voices.yaml, then the first alphabetically -- which is arbitrary, and
    picked a male voice for a character named Amanda until somebody noticed.
    """
    prefer = prefer or os.environ.get(VOICE_ENV) or default_voice() or None
    models: list[Path] = []
    for directory in directories:
        if directory.is_dir():
            models.extend(sorted(directory.glob("*.onnx")))
    if not models:
        return None

    if prefer:
        candidate = Path(prefer)
        if candidate.exists():
            return candidate
        needle = prefer.casefold()
        for model in models:
            if needle in model.stem.casefold():
                return model
        names = ", ".join(model.stem for model in models)
        raise SynthesisError(f"no voice model matching {prefer!r}. Available: {names}")

    return models[0]


def installed(name: str) -> bool:
    """Whether this engine can actually run here."""
    engine = ENGINES.get(name)
    if engine is None:
        return False
    if engine.native == "piper":
        return importlib.util.find_spec("piper") is not None and find_model() is not None
    if engine.argv is None:
        return True
    if which(engine.argv[0]) is None:
        return False
    return not engine.needs_model or find_model() is not None


def available() -> list[str]:
    return [name for name in PREFERENCE if installed(name)]


def resolve(name: str) -> str:
    """Turn "auto" into the best engine present, leaving any other name alone."""
    if name != "auto":
        return name
    for candidate in PREFERENCE:
        if installed(candidate):
            return candidate
    return "tone"


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
    name = resolve(name)
    if name not in ENGINES:
        known = ", ".join(sorted(ENGINES))
        raise KeyError(f"unknown engine {name!r}. Known engines: {known}")

    engine = ENGINES[name]
    if engine.needs_model and (voice_id is None or not Path(voice_id).exists()):
        model = find_model(voice_id)
        if model is None:
            raise SynthesisError(
                f"{name} needs a voice model. Download one into voices/ with:\n"
                f"  python -m piper.download_voices --download-dir voices "
                f"en_GB-jenny_dioco-medium"
            )
        voice_id = str(model)

    voice = VoiceSettings(
        voice_id=voice_id,
        sample_rate=sample_rate or engine.sample_rate,
        pace=pace,
    )

    if engine.native == "piper":
        from amanda.audio.piper_provider import PiperSynthesizer, model_sample_rate

        model = Path(voice.voice_id or "")
        if sample_rate is None and (native := model_sample_rate(model)) is not None:
            # The model knows its own rate; the registry's default is only a
            # guess that happens to be right for medium and high voices.
            voice = VoiceSettings(
                voice_id=voice.voice_id, sample_rate=native, pace=voice.pace
            )
        return PiperSynthesizer(model=model, **overrides), voice

    if engine.argv is None:
        return ToneSynthesizer(**overrides), voice

    argv = list(engine.argv)
    argv[0] = which(argv[0]) or argv[0]
    if voice_id is None:
        # Drop the flag and its value rather than passing an empty voice, which
        # most engines reject.
        argv = _drop_voice_flag(argv)

    return (
        CommandSynthesizer(
            argv=argv,
            expects_wav=engine.expects_wav,
            base_rate=engine.base_rate,
            text_on_stdin=engine.text_on_stdin,
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
    lines = [f"auto: pick the best installed ({resolve('auto')} here)"]
    for name, engine in sorted(ENGINES.items()):
        mark = "" if installed(name) else " [not installed]"
        detail = f"{engine.sample_rate} Hz"
        if engine.note:
            detail += f" -- {engine.note}"
        lines.append(f"{name:<10} {detail}{mark}")
    return lines

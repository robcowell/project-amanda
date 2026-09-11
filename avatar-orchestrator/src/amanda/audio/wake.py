"""Wake word detection.

Without one, `--voice` sends every utterance in earshot to Claude: the telly,
someone on the phone in the next room, a conversation the avatar was not part
of. That is a cost, a privacy and a false-trigger problem at once, and it is
what makes an always-listening assistant unsafe to leave running.

The build plan does not ask for a wake word — its ambition is presence rather
than summoning (§31), and you do not say "hey Amanda" to someone sitting across
from you. So this is a gate, not a requirement: `AlwaysAwake` keeps the
always-listening behaviour, and the choice lives in config.

Two backends, because they trade differently:

  * **openWakeWord** needs no account, and fetches its models from its own
    GitHub release the first time one is used. Its vocabulary is whatever has
    been pre-trained, and "Amanda" is not among it.
  * **Porcupine** needs a free Picovoice account and an access key, and in
    exchange its console will generate a keyword for any phrase — which is the
    only route to a wake word that is actually the character's name.

The interface is the same either way, so switching is a config change.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from amanda.audio.microphone import CAPTURE_RATE, frame_bytes
from amanda.audio.tts import SAMPLE_WIDTH

log = logging.getLogger(__name__)


class WakeWordError(RuntimeError):
    """The detector could not be built or loaded."""


@runtime_checkable
class WakeWordDetector(Protocol):
    """Watches a frame stream for the word that starts a conversation."""

    @property
    def name(self) -> str: ...

    @property
    def always_awake(self) -> bool:
        """True when there is no gate and every utterance is a turn."""
        ...

    async def warm(self) -> None: ...

    def feed(self, frame: bytes) -> bool:
        """True on the frame that completes a detection."""
        ...

    def reset(self) -> None: ...


@dataclass
class AlwaysAwake:
    """No wake word. Every utterance is a turn.

    Right for a headset or a room with one person in it; wrong for anywhere the
    microphone can hear a conversation the avatar is not part of.
    """

    @property
    def name(self) -> str:
        return "always-awake"

    @property
    def always_awake(self) -> bool:
        return True

    async def warm(self) -> None:
        return None

    def feed(self, frame: bytes) -> bool:
        return False

    def reset(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# openWakeWord
# --------------------------------------------------------------------------- #

#: Samples per prediction. openWakeWord is trained on 80ms chunks; feeding it
#: the microphone's 32ms frames directly works but scores worse.
CHUNK_SAMPLES = 1280

#: Measured against synthesised speech: the wake phrase scored 0.854 and an
#: ordinary sentence 0.000, so anywhere in between separates them. Nearer the
#: middle than the top, because a real room is noisier than a synthesiser.
DEFAULT_THRESHOLD = 0.5

#: Ignore further detections for this long after one fires. Without it a single
#: "hey Marvin" scores above threshold on several consecutive chunks.
DEFAULT_COOLDOWN_MS = 1500


@dataclass
class OpenWakeWordDetector:
    """Keyless detection using openWakeWord's pre-trained models.

    Its vocabulary is fixed -- as of 0.6: alexa, hey_jarvis, hey_mycroft,
    hey_rhasspy. `hey_marvin`, this class's first placeholder, went with 0.5.
    None of them is "Amanda", so whichever is configured is a placeholder --
    see the module docstring for the route to a real one.

    The model's streaming context carries across calls and is meant to: a burst
    of loud non-speech immediately before the wake word measurably suppresses
    it, which is worth knowing if the room has music in it.
    """

    keyword: str = "hey_jarvis"
    threshold: float = DEFAULT_THRESHOLD
    cooldown_ms: int = DEFAULT_COOLDOWN_MS

    _model: Any = field(default=None, init=False, repr=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    #: Time since the last detection. Starts high so the first one is allowed.
    _since_fired_ms: float = field(default=1e9, init=False)

    @property
    def name(self) -> str:
        return f"openwakeword:{self.keyword}"

    @property
    def always_awake(self) -> bool:
        return False

    async def warm(self) -> None:
        if self._model is not None:
            return

        import openwakeword
        from openwakeword.model import Model

        models = Path(openwakeword.__file__).parent / "resources" / "models"
        matches = sorted(models.glob(f"{self.keyword}*.onnx"))
        known = getattr(openwakeword, "MODELS", {})
        if not matches and self.keyword in known:
            # Since 0.5 the models are not in the package: the library fetches
            # them, with the two feature models every keyword needs, from its
            # GitHub release into the folder above. Once per install.
            from openwakeword.utils import download_models

            log.info("downloading openWakeWord model %s (once)", self.keyword)
            try:
                await asyncio.to_thread(download_models, [self.keyword])
            except Exception as exc:  # noqa: BLE001 - surfaced with context
                raise WakeWordError(
                    f"could not download the openWakeWord model {self.keyword!r}: {exc}"
                ) from exc
            matches = sorted(models.glob(f"{self.keyword}*.onnx"))
        if not matches:
            available = ", ".join(sorted(known)) or ", ".join(
                path.stem.rsplit("_v", 1)[0]
                for path in sorted(models.glob("*.onnx"))
                if not any(part in path.stem for part in ("melspectrogram", "embedding", "vad"))
            )
            raise WakeWordError(
                f"no openWakeWord model for {self.keyword!r}. Available: {available}"
            )

        with warnings.catch_warnings():
            # It asks for a CUDA provider on every construction and warns when
            # there isn't one, which there never is here.
            warnings.simplefilter("ignore", UserWarning)
            # onnx explicitly: the default is tflite, which has no Windows
            # build, and 0.6 only falls back to onnx after failing to import it.
            self._model = Model(wakeword_models=[str(matches[0])], inference_framework="onnx")
        log.info("wake word ready: %s", matches[0].stem)

    def reset(self) -> None:
        """Clear the framing buffer and the cooldown, and nothing else.

        Deliberately does *not* call openWakeWord's own `Model.reset()`. That
        clears a 30-entry prediction buffer its scoring depends on, which leaves
        the detector deaf for about 2.4 seconds while it refills -- longer than
        the wake phrase itself, so the next thing said is missed entirely. The
        model's streaming state is meant to be continuous; only ours is not.
        """
        self._buffer.clear()
        self._since_fired_ms = 1e9

    def feed(self, frame: bytes) -> bool:
        if self._model is None:
            return False

        self._since_fired_ms += len(frame) / (SAMPLE_WIDTH * CAPTURE_RATE) * 1000
        self._buffer += frame

        fired = False
        chunk_bytes = CHUNK_SAMPLES * SAMPLE_WIDTH
        while len(self._buffer) >= chunk_bytes:
            chunk = bytes(self._buffer[:chunk_bytes])
            del self._buffer[:chunk_bytes]

            # One spoken wake word scores above threshold on several
            # consecutive chunks, so a hit closes the door behind it.
            if self._since_fired_ms < self.cooldown_ms:
                continue
            if self._predict(chunk):
                fired = True
                self._since_fired_ms = 0.0
        return fired

    def _predict(self, chunk: bytes) -> bool:
        import numpy

        scores = self._model.predict(numpy.frombuffer(chunk, dtype=numpy.int16))
        return bool(scores) and max(scores.values()) >= self.threshold


# --------------------------------------------------------------------------- #
# Porcupine
# --------------------------------------------------------------------------- #


@dataclass
class PorcupineDetector:
    """Picovoice Porcupine. Needs an access key; supports custom keywords.

    The only route to a wake word that is actually "Amanda": the Picovoice
    console will generate a `.ppn` for any phrase, free for personal use. Pass
    its path as `keyword`; anything without a path separator is treated as one
    of the built-in words.
    """

    keyword: str = "computer"
    access_key: str | None = None
    sensitivity: float = 0.6

    _handle: Any = field(default=None, init=False, repr=False)

    @property
    def name(self) -> str:
        return f"porcupine:{Path(self.keyword).stem}"

    @property
    def always_awake(self) -> bool:
        return False

    async def warm(self) -> None:
        if self._handle is not None:
            return

        import os

        import pvporcupine

        key = self.access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
        if not key:
            raise WakeWordError(
                "Porcupine needs an access key. Set PICOVOICE_ACCESS_KEY, or use "
                "the openwakeword backend, which needs no account."
            )

        arguments: dict[str, Any] = {
            "access_key": key,
            "sensitivities": [self.sensitivity],
        }
        if any(separator in self.keyword for separator in ("/", "\\")) or self.keyword.endswith(
            ".ppn"
        ):
            arguments["keyword_paths"] = [self.keyword]
        else:
            arguments["keywords"] = [self.keyword]

        try:
            self._handle = pvporcupine.create(**arguments)
        except Exception as exc:  # noqa: BLE001 - surfaced with context
            raise WakeWordError(f"could not create Porcupine: {exc}") from exc

        if self._handle.frame_length * SAMPLE_WIDTH != frame_bytes():
            log.warning(
                "porcupine wants %d-sample frames; the microphone produces %d, so "
                "nothing will be detected -- set the microphone's frame_ms to match",
                self._handle.frame_length,
                frame_bytes() // SAMPLE_WIDTH,
            )

    def reset(self) -> None:
        return None

    def feed(self, frame: bytes) -> bool:
        if self._handle is None:
            return False

        import array

        samples = array.array("h")
        samples.frombytes(frame)
        if len(samples) != self._handle.frame_length:
            return False
        return self._handle.process(samples) >= 0


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


#: Keys in the `wake:` block that configure the conversation rather than the
#: detector. Consumed by VoiceInput; dropped here without complaint.
SESSION_KEYS = frozenset({"awake_seconds"})


def build(name: str | None = None, **options: Any) -> WakeWordDetector:
    """A detector by name, from config where not given explicitly.

    "none" is a real answer, not a failure: always-listening is the right shape
    for a headset, and the gate exists for rooms with other people in them.
    """
    from amanda.config import wake_settings

    overrides = options
    configured = dict(wake_settings())
    name = name or configured.pop("backend", None) or "auto"
    configured.pop("backend", None)
    options = {key: value for key, value in configured.items() if key not in SESSION_KEYS}
    options.update(overrides)

    if name in {"none", "off", "always"}:
        return AlwaysAwake()

    if name in {"auto", "openwakeword"}:
        try:
            import openwakeword  # noqa: F401

            return _construct(OpenWakeWordDetector, options)
        except ImportError:
            if name == "openwakeword":
                raise WakeWordError(
                    "openwakeword is not installed. `pip install openwakeword`"
                ) from None

    if name in {"auto", "porcupine"}:
        try:
            import pvporcupine  # noqa: F401

            return _construct(PorcupineDetector, options)
        except ImportError:
            if name == "porcupine":
                raise WakeWordError(
                    "pvporcupine is not installed. `pip install pvporcupine`"
                ) from None

    if name == "auto":
        # Nothing installed. Listening to everything is worse than saying so,
        # but failing to start is worse still.
        log.warning("no wake word backend installed; listening to everything")
        return AlwaysAwake()

    raise WakeWordError(
        f"unknown wake word backend {name!r}. Known: openwakeword, porcupine, none"
    )


def _construct(detector: type, options: dict[str, Any]) -> WakeWordDetector:
    import dataclasses

    fields = {field.name for field in dataclasses.fields(detector)}
    for key in set(options) - fields:
        log.warning("ignoring unknown wake setting %r in config", key)
    return detector(**{key: value for key, value in options.items() if key in fields})

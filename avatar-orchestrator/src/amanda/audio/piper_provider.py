"""Piper, loaded in-process rather than shelled out to.

Piper is a Python package and this is a Python application, so treating it as a
generic command-line engine was a mistake worth measuring: each `piper`
invocation spends about 3.5 seconds loading the interpreter, onnxruntime and the
model before it synthesises anything. Spawning one per phrase would put that on
every phrase, against a total latency budget of well under two seconds.

Loaded once and kept, the same model runs at roughly 8x realtime on a 2017
ultrabook. The 3-second load happens at startup instead, which is why `warm()`
exists -- paying it on the first phrase of the first conversation would be
worse than paying it before anyone is listening.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amanda.audio.tts import (
    CHANNELS,
    SAMPLE_WIDTH,
    SynthesisError,
    SynthesisStream,
    VoiceSettings,
)

log = logging.getLogger(__name__)

_SENTINEL = object()


class _PiperStream(SynthesisStream):
    def __init__(self, text: str, voice: VoiceSettings, owner: PiperSynthesizer) -> None:
        super().__init__(text, voice)
        self._owner = owner
        self._stop = False

    def cancel(self) -> None:
        # The worker thread cannot be killed, so it is asked to stop between
        # chunks instead. A phrase is short enough that it exits promptly.
        self._stop = True
        super().cancel()

    async def _produce(self) -> AsyncIterator[bytes]:
        from piper import SynthesisConfig

        voice = await self._owner.loaded()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        # length_scale is a duration multiplier, so it is the inverse of pace:
        # a faster delivery means shorter phonemes.
        config = SynthesisConfig(
            length_scale=1.0 / max(0.25, self.voice.pace),
            speaker_id=self._owner.speaker_id,
        )

        def work() -> None:
            try:
                for chunk in voice.synthesize(self.text, config):
                    if self._stop:
                        break
                    if chunk.sample_rate != self.voice.sample_rate:
                        raise SynthesisError(
                            f"model produced {chunk.sample_rate} Hz but the voice is "
                            f"configured for {self.voice.sample_rate} Hz"
                        )
                    if chunk.sample_channels != CHANNELS or chunk.sample_width != SAMPLE_WIDTH:
                        raise SynthesisError(
                            f"expected mono 16-bit, model produced "
                            f"{chunk.sample_channels}ch {chunk.sample_width * 8}-bit"
                        )
                    loop.call_soon_threadsafe(queue.put_nowait, chunk.audio_int16_bytes)
            except BaseException as exc:  # noqa: BLE001 - handed to the consumer
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        worker = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self._stop = True
            await asyncio.gather(worker, return_exceptions=True)


def model_sample_rate(model: Path) -> int | None:
    """The rate a voice model emits, read from its config.

    A property of the model, not of Piper: medium and high voices are 22050 Hz
    and low ones 16000, so a single registry constant is wrong for somebody.
    """
    config = model.with_suffix(model.suffix + ".json")
    if not config.exists():
        return None
    try:
        return int(json.loads(config.read_text(encoding="utf-8"))["audio"]["sample_rate"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


@dataclass
class PiperSynthesizer:
    """Local neural speech from a Piper voice model."""

    model: Path
    config: Path | None = None
    #: Which voice in a multi-speaker model. vctk has over a hundred.
    speaker_id: int | None = None

    _voice: Any = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def name(self) -> str:
        return f"piper:{self.model.stem}"

    async def loaded(self) -> Any:
        """The loaded model, loading it on first use.

        Loading is CPU-bound and takes seconds, so it happens on a thread -- on
        the event loop it would stall the Claude stream and the avatar bridge
        for the duration.
        """
        async with self._lock:
            if self._voice is None:
                from piper import PiperVoice

                if not self.model.exists():
                    raise SynthesisError(f"no such voice model: {self.model}")
                log.info("loading piper voice %s", self.model.name)
                self._voice = await asyncio.to_thread(
                    PiperVoice.load, str(self.model), str(self.config) if self.config else None
                )
            return self._voice

    async def warm(self) -> None:
        """Load the model before anyone is waiting on it."""
        await self.loaded()

    def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisStream:
        return _PiperStream(text, voice, self)

"""Turning a stream of phrases into speech (build plan 12 and 13).

One Claude response becomes one utterance, and one utterance is many phrases
arriving over several seconds while the model is still writing. This owns that:
the phrase queue, synthesis, playback ordering, the protocol's speech lifecycle,
and cancelling the lot when the user interrupts.

Phrases are synthesised one at a time rather than pipelined, and that is not
laziness. The sink's write blocks on buffer space, so by the time the last chunk
of phrase N has been *written* almost none of it has been *played* -- synthesis
of phrase N+1 therefore begins well ahead of the audio that precedes it.
Pipelining would buy little and would mean holding audio for a phrase that a
barge-in is about to discard.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from amanda.audio.sink import AudioSink
from amanda.audio.tts import (
    SpeechSynthesizer,
    SpokenUtterance,
    SynthesisStream,
    VoiceSettings,
    pcm_duration_ms,
)
from amanda.avatar.protocol import (
    CancelReason,
    Payload,
    Preset,
    SpeechCancelled,
    SpeechCompleted,
    SpeechPrepare,
    SpeechStarted,
)
from amanda.runtime.metrics import Stage, TurnMetrics

log = logging.getLogger(__name__)


@dataclass
class SpeechSession:
    """One utterance, from the first phrase to silence.

        session = SpeechSession("u_1042", synthesizer, sink, voice)
        await session.start()
        async for chunk in turn:
            for phrase in segmenter.feed(chunk):
                await session.add(phrase)
        session.close_input()
        spoken = await session.wait()
    """

    utterance_id: str
    synthesizer: SpeechSynthesizer
    sink: AudioSink
    voice: VoiceSettings = field(default_factory=VoiceSettings)

    metrics: TurnMetrics | None = None
    #: Called with protocol v1 payloads. Kept as a callback rather than a bridge
    #: reference so this stays testable and the dependency runs one way.
    emit: Callable[[Payload], None] | None = None
    preset: Preset | None = None
    fade_ms: int = 80

    _phrases: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue, init=False)
    _worker: asyncio.Task[None] | None = field(default=None, init=False)
    _current: SynthesisStream | None = field(default=None, init=False)
    _spoken_bytes: int = field(default=0, init=False)
    _synthesized_bytes: int = field(default=0, init=False)
    _texts: list[str] = field(default_factory=list, init=False)
    _prepared: bool = field(default=False, init=False)
    _speaking: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _cancelled: bool = field(default=False, init=False)
    _cancel_reason: CancelReason = field(default=CancelReason.BARGE_IN, init=False)
    _error: BaseException | None = field(default=None, init=False)

    # ----------------------------------------------------------------- #

    async def start(self) -> None:
        await self.sink.open(self.voice.sample_rate)
        self._worker = asyncio.create_task(self._run(), name=f"speech-{self.utterance_id}")

    async def add(self, phrase: str) -> None:
        """Queue a phrase for speaking."""
        if self._cancelled or not phrase.strip():
            return
        if not self._prepared:
            # The protocol's speech.prepare: sent at the first speakable phrase
            # so the renderer can bring its gaze back to the user *before* the
            # first sample plays, rather than snapping to attention on start.
            self._prepared = True
            self._send(
                SpeechPrepare(
                    utterance_id=self.utterance_id,
                    preset=self.preset,
                    text=phrase,
                )
            )
        self._texts.append(phrase)
        self._phrases.put_nowait(phrase)

    def close_input(self) -> None:
        """No more phrases are coming. Playback continues until the queue drains."""
        self._phrases.put_nowait(None)

    async def wait(self) -> SpokenUtterance:
        """Block until the utterance finishes or is cancelled."""
        if self._worker is not None:
            await self._worker
        if self._error is not None:
            raise self._error
        return self.result

    async def cancel(self, reason: CancelReason = CancelReason.BARGE_IN) -> None:
        """Stop speaking now and discard everything still queued.

        Ordering matters and is the build plan's: the renderer is told first so
        the face transitions out of speaking immediately, then the audio fades,
        then the slower cleanup happens. The visual change is what makes an
        interruption feel like being interrupted.
        """
        if self._cancelled or self._finished:
            # Already over. Sending speech.cancelled for an utterance the
            # renderer has been told completed would have it transition out of
            # speaking twice, the second time from a state it already left.
            return
        self._cancelled = True
        self._cancel_reason = reason

        self._send(
            SpeechCancelled(
                utterance_id=self.utterance_id, reason=reason, fade_ms=self.fade_ms
            )
        )

        if self._current is not None:
            self._current.cancel()
        await self.sink.stop(self.fade_ms)

        self._drain_queue()
        self._phrases.put_nowait(None)

        if self._worker is not None:
            await asyncio.gather(self._worker, return_exceptions=True)

    @property
    def announced(self) -> bool:
        """Whether the renderer has been told about this utterance at all.

        A turn that fails before the first phrase should not send
        speech.cancelled for something the renderer never heard of.
        """
        return self._prepared

    @property
    def result(self) -> SpokenUtterance:
        return SpokenUtterance(
            utterance_id=self.utterance_id,
            text=" ".join(self._texts),
            sample_rate=self.voice.sample_rate,
            synthesized_ms=pcm_duration_ms(
                b"\0" * self._synthesized_bytes, self.voice.sample_rate
            ),
            # Written, not strictly played: on a cancellation this overstates by
            # roughly the sink's buffer, which the fade discards. Close enough
            # for telemetry, and honest about which side it errs on.
            spoken_ms=pcm_duration_ms(b"\0" * self._spoken_bytes, self.voice.sample_rate),
            cancelled=self._cancelled,
        )

    # ----------------------------------------------------------------- #

    async def _run(self) -> None:
        try:
            while True:
                phrase = await self._phrases.get()
                if phrase is None or self._cancelled:
                    break
                await self._speak(phrase)

            if not self._cancelled:
                await self.sink.drain()
                if self._speaking:
                    self._send(SpeechCompleted(utterance_id=self.utterance_id))
                self._finished = True
        except asyncio.CancelledError:
            self._cancelled = True
        except BaseException as exc:  # noqa: BLE001 - re-raised from wait()
            self._error = exc
            log.exception("speech failed for %s", self.utterance_id)
        finally:
            await self.sink.close()

    async def _speak(self, phrase: str) -> None:
        stream = self.synthesizer.synthesize(phrase, self.voice)
        self._current = stream
        try:
            async for pcm in stream:
                if self._cancelled:
                    break
                self._synthesized_bytes += len(pcm)
                self._mark_first_audio()
                await self._begin_speaking()
                await self.sink.write(pcm)
                self._spoken_bytes += len(pcm)
        finally:
            self._current = None

    def _mark_first_audio(self) -> None:
        """T5: the first audio exists. Distinct from T6, when it is heard."""
        if self.metrics is not None:
            self.metrics.mark(Stage.FIRST_AUDIO)

    async def _begin_speaking(self) -> None:
        if self._speaking:
            return
        self._speaking = True
        if self.metrics is not None:
            self.metrics.mark(Stage.SPEECH_STARTED)
        self._send(
            SpeechStarted(
                utterance_id=self.utterance_id,
                sample_rate=self.voice.sample_rate,
            )
        )

    def _send(self, payload: Payload) -> None:
        if self.emit is not None:
            self.emit(payload)

    def _drain_queue(self) -> None:
        while not self._phrases.empty():
            self._phrases.get_nowait()

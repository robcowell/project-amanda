"""Tests for transcribing during the pause that may end a turn.

The endpointer waits 750ms of silence before calling an utterance finished, and
Whisper then took ~560ms more on the renderer PC. Starting transcription 250ms
into the silence overlaps the two: when the turn is confirmed, the finished
utterance is the snapshot plus nothing but silence, so the early transcript
stands. Speech resuming inside the pause throws it away.

ScriptedRecognizer returns its replies in order, so a discarded speculation
consumes one -- the tests below count on exactly that to prove it happened.
"""

from __future__ import annotations

import array
import asyncio
import math

from amanda.audio.microphone import CAPTURE_RATE, FRAME_MS, Microphone
from amanda.audio.stt import ScriptedRecognizer
from amanda.audio.vad import SPEECH_THRESHOLD
from amanda.runtime.input import VoiceInput

FRAME_SAMPLES = round(CAPTURE_RATE * FRAME_MS / 1000)
VOICE = SPEECH_THRESHOLD * 4


def tone() -> bytes:
    amplitude = VOICE * math.sqrt(2) * 32767
    return array.array(
        "h",
        [
            int(amplitude * math.sin(2 * math.pi * 200 * i / CAPTURE_RATE))
            for i in range(FRAME_SAMPLES)
        ],
    ).tobytes()


def speech(seconds: float) -> list[bytes]:
    return [tone()] * round(seconds * 1000 / FRAME_MS)


def silence(seconds: float) -> list[bytes]:
    return [bytes(FRAME_SAMPLES * 2)] * round(seconds * 1000 / FRAME_MS)


def voice_input(script: list[bytes], **kwargs) -> tuple[VoiceInput, ScriptedRecognizer]:
    recognizer = ScriptedRecognizer(replies=["first", "second", "third"], latency=0.4)
    source = VoiceInput(
        microphone=Microphone(source=script + silence(1.0)),
        recognizer=recognizer,
        **kwargs,
    )
    return source, recognizer


async def test_the_transcript_started_in_the_pause_is_the_turn():
    source, recognizer = voice_input(speech(1.0) + silence(1.0))
    await source.start()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "first"
    assert turn.stt_speculative
    assert recognizer._index == 1, "transcribed once, in the pause"
    # 0.4s of recognition started 0.25s into a 0.75s wait: about 0.1s left.
    assert turn.ready_at - turn.ended_at < 0.3


async def test_speech_resuming_throws_the_early_transcript_away():
    """A pause long enough to start transcribing, then more words."""
    source, recognizer = voice_input(speech(1.0) + silence(0.4) + speech(1.0) + silence(1.0))
    await source.start()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "second", "the transcript from the first pause was discarded"
    assert turn.stt_speculative
    assert recognizer._index == 2


async def test_without_speculation_it_transcribes_after_the_turn_ends():
    source, recognizer = voice_input(speech(1.0) + silence(1.0), speculate_after=None)
    await source.start()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "first"
    assert not turn.stt_speculative
    assert recognizer._index == 1
    assert turn.ready_at - turn.ended_at >= 0.35


async def test_transcriptions_never_overlap():
    """A short pause, a word, then the end: the second pause comes while the
    first pause's transcription is still running, and waits for it."""

    class Counting(ScriptedRecognizer):
        running: int = 0
        most: int = 0

        async def transcribe(self, utterance):
            self.running += 1
            self.most = max(self.most, self.running)
            try:
                return await super().transcribe(utterance)
            finally:
                self.running -= 1

    recognizer = Counting(replies=["first", "second"], latency=0.5)
    source = VoiceInput(
        microphone=Microphone(
            source=speech(1.0) + silence(0.3) + speech(0.2) + silence(1.0) + silence(1.0)
        ),
        recognizer=recognizer,
    )
    await source.start()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "second"
    assert recognizer.most == 1


async def test_nothing_is_transcribed_early_while_she_is_speaking():
    """Heard over her: an interruption stops her first, or it is her own voice
    from the speakers. Either way it waits, as it always did."""
    from amanda.audio.vad import BargeInDetector

    source, recognizer = voice_input(
        speech(1.0) + silence(1.0), barge_in=BargeInDetector(sustain=60.0)
    )
    await source.start()
    armed = asyncio.create_task(source.wait_for_barge_in())
    # Until the whole utterance is heard, pause included. Not a fixed sleep:
    # replay runs slower than real time on Windows' 15.6ms timer.
    async with asyncio.timeout(10):
        while source._utterances.empty():
            await asyncio.sleep(0.02)
    assert recognizer._index == 0, "nothing transcribed while she spoke"
    armed.cancel()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "first"
    assert not turn.stt_speculative


async def test_too_little_speech_is_not_transcribed_early():
    """A cough: long enough to end an utterance, too little to transcribe."""
    source, recognizer = voice_input(speech(0.36) + silence(1.0) + speech(1.0) + silence(1.0))
    await source.start()
    turn = await source.next_turn()
    await source.stop()

    assert turn.text == "first", "nothing was spent on the cough"
    assert recognizer._index == 1

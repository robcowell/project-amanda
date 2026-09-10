"""Tests for where user turns come from.

The typed source is driven by putting lines on its queue directly rather than
through stdin; the voice source runs against a scripted microphone and a
scripted recogniser, so the whole input path is testable with no device and no
model weights.
"""

from __future__ import annotations

import array
import asyncio
import math

import pytest

from amanda.audio.microphone import CAPTURE_RATE, FRAME_MS, Microphone
from amanda.audio.stt import ScriptedRecognizer
from amanda.audio.vad import SPEECH_THRESHOLD, BargeInDetector, Endpointer
from amanda.runtime.input import ConversationInput, TypedInput, UserTurn, VoiceInput

FRAME_SAMPLES = round(CAPTURE_RATE * FRAME_MS / 1000)
VOICE = SPEECH_THRESHOLD * 4


def tone(level: float) -> bytes:
    amplitude = level * math.sqrt(2) * 32767
    return array.array(
        "h",
        [
            int(max(-32768, min(32767, amplitude * math.sin(2 * math.pi * 200 * i / CAPTURE_RATE))))
            for i in range(FRAME_SAMPLES)
        ],
    ).tobytes()


def frames(level: float, seconds: float) -> list[bytes]:
    return [tone(level) if level else bytes(FRAME_SAMPLES * 2)] * max(
        1, round(seconds * 1000 / FRAME_MS)
    )


async def queue_line(source: TypedInput, line: str | None, at: float | None = None) -> None:
    import time

    await source._lines.put((at if at is not None else time.monotonic(), line))


# --------------------------------------------------------------------------- #
# Both satisfy the interface
# --------------------------------------------------------------------------- #


def test_both_sources_satisfy_the_protocol():
    """The point of the abstraction: phase 2 adds a microphone rather than a
    second turn loop."""
    assert isinstance(TypedInput(), ConversationInput)
    assert isinstance(
        VoiceInput(microphone=Microphone(source=[]), recognizer=ScriptedRecognizer()),
        ConversationInput,
    )


# --------------------------------------------------------------------------- #
# Typed
# --------------------------------------------------------------------------- #


async def test_a_typed_line_becomes_a_turn():
    source = TypedInput()
    await queue_line(source, "morning")

    turn = await source.next_turn()
    assert turn is not None and turn.text == "morning"


async def test_typed_input_has_no_transcription_delay():
    """T0 and T1 are the same instant: no speech to end, no transcript to wait
    for."""
    source = TypedInput()
    await queue_line(source, "morning")
    turn = await source.next_turn()
    assert turn.ready_at == turn.ended_at


@pytest.mark.parametrize("line", [None, "", "   ", "quit", "exit"])
async def test_input_ends(line):
    source = TypedInput()
    await queue_line(source, line)
    assert await source.next_turn() is None


async def test_typing_over_the_avatar_interrupts_and_becomes_the_next_turn():
    """The user should not have to say it twice."""
    source = TypedInput()

    watcher = asyncio.create_task(source.wait_for_barge_in())
    await asyncio.sleep(0)
    await queue_line(source, "sorry, actually")
    await asyncio.wait_for(watcher, timeout=1.0)

    turn = await source.next_turn()
    assert turn is not None and turn.text == "sorry, actually"


async def test_typing_ahead_is_not_interrupting():
    """A line that arrived before the avatar started speaking was typed ahead,
    not over the top. Voice input gets this free -- its detector is only fed
    while armed -- and typed input has to check the clock."""
    import time

    source = TypedInput()
    await queue_line(source, "typed early", at=time.monotonic() - 5)

    watcher = asyncio.create_task(source.wait_for_barge_in())
    await asyncio.sleep(0.05)
    assert not watcher.done(), "a line typed before speaking began should not interrupt"

    watcher.cancel()
    turn = await source.next_turn()
    assert turn is not None and turn.text == "typed early", "and should not be lost"


async def test_an_empty_line_does_not_interrupt():
    source = TypedInput()
    watcher = asyncio.create_task(source.wait_for_barge_in())
    await asyncio.sleep(0)
    await queue_line(source, "   ")
    await asyncio.sleep(0.05)

    assert not watcher.done()
    watcher.cancel()


async def test_end_of_input_does_not_interrupt():
    """It means quit. The utterance is allowed to finish."""
    source = TypedInput()
    watcher = asyncio.create_task(source.wait_for_barge_in())
    await asyncio.sleep(0)
    await queue_line(source, None)
    await asyncio.sleep(0.05)

    assert not watcher.done()
    watcher.cancel()
    assert await source.next_turn() is None


# --------------------------------------------------------------------------- #
# Spoken
# --------------------------------------------------------------------------- #


def voice_input(script: list[bytes], **kwargs) -> VoiceInput:
    return VoiceInput(
        microphone=Microphone(source=script, frame_ms=0),
        recognizer=ScriptedRecognizer(replies=["morning"], latency=0.0),
        **kwargs,
    )


async def test_speech_becomes_a_transcribed_turn():
    source = voice_input(frames(0, 0.1) + frames(VOICE, 1.2) + frames(0, 1.2))
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None
    assert turn.text == "morning"
    assert turn.audio_ms > 1000


async def test_it_records_when_speech_ended_and_when_the_words_were_ready():
    """T0 and T1. Transcription sits between them and is pure added latency."""
    source = VoiceInput(
        microphone=Microphone(source=frames(VOICE, 1.0) + frames(0, 1.2), frame_ms=0),
        recognizer=ScriptedRecognizer(replies=["morning"], latency=0.05),
    )
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn.ready_at > turn.ended_at
    assert (turn.ready_at - turn.ended_at) >= 0.04


async def test_something_too_short_to_be_speech_is_not_transcribed():
    source = voice_input(
        frames(VOICE, 0.4) + frames(0, 1.2) + frames(VOICE, 1.2) + frames(0, 1.2),
        min_voiced_ms=800,
    )
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None and turn.audio_ms > 800
    assert source.discarded >= 1, "the 0.4s burst should not have been transcribed"


async def test_an_empty_transcript_is_not_a_turn():
    """Silence the endpointer let through. Whisper will invent words for it, so
    the recogniser suppresses them and this simply keeps listening."""
    source = VoiceInput(
        microphone=Microphone(
            source=frames(VOICE, 1.0) + frames(0, 1.2) + frames(VOICE, 1.0) + frames(0, 1.2),
            frame_ms=0,
        ),
        recognizer=ScriptedRecognizer(replies=["", "actually here"], latency=0.0),
    )
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn.text == "actually here"
    assert source.discarded == 1


async def test_input_ending_returns_none():
    source = voice_input(frames(0, 0.1))
    await source.start()
    try:
        assert await asyncio.wait_for(source.next_turn(), timeout=5.0) is None
    finally:
        await source.stop()


# --------------------------------------------------------------------------- #
# Barge-in
# --------------------------------------------------------------------------- #


async def test_speech_does_not_interrupt_unless_the_avatar_is_speaking():
    """Otherwise ordinary conversation would count as interrupting an avatar
    that is not saying anything."""
    source = voice_input(frames(VOICE, 1.5))
    await source.start()
    try:
        await asyncio.sleep(0.15)
        assert not source._interrupted.is_set()
    finally:
        await source.stop()


async def test_sustained_speech_interrupts_once_armed():
    source = voice_input(frames(0, 0.1) + frames(VOICE, 2.0))
    await source.start()
    try:
        await asyncio.wait_for(source.wait_for_barge_in(), timeout=5.0)
    finally:
        await source.stop()


async def test_endpointing_keeps_running_during_a_barge_in():
    """The whole reason the microphone fans out: the sentence the user
    interrupts with is also their next turn, so both detectors need the same
    frames at the same time."""
    source = VoiceInput(
        microphone=Microphone(source=frames(VOICE, 1.2) + frames(0, 1.5), frame_ms=0),
        recognizer=ScriptedRecognizer(replies=["the short version"], latency=0.0),
        barge_in=BargeInDetector(sustain=0.10),
        endpointer=Endpointer(),
    )
    await source.start()
    try:
        await asyncio.wait_for(source.wait_for_barge_in(), timeout=5.0)
        # The same speech that interrupted must still arrive as a turn.
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None and turn.text == "the short version"


async def test_the_detector_is_reset_between_arms():
    """A run part-built before the previous utterance ended must not carry over
    and fire instantly on the next one."""
    source = voice_input(frames(VOICE, 3.0))
    await source.start()
    try:
        await asyncio.wait_for(source.wait_for_barge_in(), timeout=5.0)
        assert source.barge_in.progress > 0

        watcher = asyncio.create_task(source.wait_for_barge_in())
        await asyncio.sleep(0)
        assert not watcher.done(), "the previous run should not carry over"
        watcher.cancel()
    finally:
        await source.stop()


# --------------------------------------------------------------------------- #
# UserTurn
# --------------------------------------------------------------------------- #


def test_a_turn_knows_whether_anything_was_said():
    assert UserTurn(text="   ", ended_at=0, ready_at=0).empty
    assert not UserTurn(text="morning", ended_at=0, ready_at=0).empty


async def test_nothing_is_captured_before_the_recogniser_is_ready():
    """Regression: the microphone was opened before the model was warmed, so a
    cold start discarded everything said while it loaded -- silently, because
    the stream was running with nobody subscribed."""

    class SlowRecognizer(ScriptedRecognizer):
        async def warm(self) -> None:
            await asyncio.sleep(0.2)

    source = VoiceInput(
        microphone=Microphone(source=frames(VOICE, 1.0) + frames(0, 1.2), frame_ms=0),
        recognizer=SlowRecognizer(replies=["heard it"], latency=0.0),
    )
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None and turn.text == "heard it"

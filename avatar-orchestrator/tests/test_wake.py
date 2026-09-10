"""Tests for wake word detection and the gate it puts in front of a turn."""

from __future__ import annotations

import array
import asyncio
import math

import pytest

from amanda.audio.microphone import CAPTURE_RATE, FRAME_MS, Microphone
from amanda.audio.stt import ScriptedRecognizer
from amanda.audio.vad import SPEECH_THRESHOLD
from amanda.audio.wake import (
    SESSION_KEYS,
    AlwaysAwake,
    WakeWordDetector,
    WakeWordError,
    build,
)
from amanda.runtime.input import VoiceInput

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


class Triggered:
    """A detector that fires once, after a set number of frames."""

    def __init__(self, after: int = 0) -> None:
        self.after = after
        self.seen = 0
        self.fired = False

    name = "test"
    always_awake = False

    async def warm(self) -> None:
        return None

    def reset(self) -> None:
        self.seen = 0
        self.fired = False

    def feed(self, frame: bytes) -> bool:
        self.seen += 1
        if not self.fired and self.seen >= self.after:
            self.fired = True
            return True
        return False


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_always_awake_satisfies_the_protocol():
    assert isinstance(AlwaysAwake(), WakeWordDetector)


def test_none_is_a_real_answer_not_a_failure():
    """Always-listening is the right shape for a headset. The gate exists for
    rooms with other people in them."""
    for name in ("none", "off", "always"):
        detector = build(name)
        assert detector.always_awake
        assert not detector.feed(tone(VOICE))


def test_an_unknown_backend_lists_the_known_ones():
    with pytest.raises(WakeWordError, match="openwakeword"):
        build("clap-twice")


def test_a_session_setting_is_not_mistaken_for_a_detector_setting():
    """`awake_seconds` configures the conversation, not the detector. It shares
    the config block and must not be warned about or passed on."""
    assert "awake_seconds" in SESSION_KEYS
    build("none", awake_seconds=10)  # must not raise


# --------------------------------------------------------------------------- #
# openWakeWord
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def detector():
    pytest.importorskip("openwakeword", reason="openwakeword is not installed")
    return build("openwakeword")


async def test_it_loads_a_shipped_model(detector):
    await detector.warm()
    assert not detector.always_awake
    assert "hey_" in detector.name or "alexa" in detector.name


async def test_an_unknown_keyword_lists_what_is_available():
    pytest.importorskip("openwakeword", reason="openwakeword is not installed")
    from amanda.audio.wake import OpenWakeWordDetector

    with pytest.raises(WakeWordError, match="Available"):
        await OpenWakeWordDetector(keyword="hey_amanda").warm()


async def test_ordinary_noise_does_not_wake_it(detector):
    await detector.warm()
    detector.reset()
    assert not any(detector.feed(frame) for frame in frames(VOICE, 3.0))


async def test_resetting_leaves_the_models_own_state_alone(detector):
    """Regression, tested precisely rather than by ear.

    openWakeWord's `Model.reset()` clears a 30-entry prediction buffer its
    scoring depends on, which leaves the detector deaf for about 2.4 seconds
    while it refills -- longer than the wake phrase, so the next thing said is
    missed entirely. Ours must clear the framing buffer and the cooldown and
    nothing else.

    Asserted against the model's own state because the audible version is not
    stable: the model has a refractory period of its own, so replaying the same
    phrase immediately does not detect it either way.
    """
    await detector.warm()
    for frame in frames(VOICE, 1.0):
        detector.feed(frame)
    assert detector._model.prediction_buffer, "nothing to preserve"

    detector._buffer += b"\x00\x00"
    detector.reset()

    assert detector._model.prediction_buffer, "reset cleared the model's scoring buffer"
    assert not detector._buffer, "reset should clear our framing buffer"


async def test_the_real_phrase_wakes_it_and_only_once():
    """Piper says it, the detector hears it. One spoken wake word scores above
    threshold on several consecutive chunks, so the cooldown collapses them."""
    pytest.importorskip("openwakeword", reason="openwakeword is not installed")
    pytest.importorskip("piper", reason="piper is not installed")
    from amanda.audio.engines import build as build_engine

    synthesizer, voice = build_engine("piper")
    if not synthesizer.name.startswith("piper"):
        pytest.skip("no piper voice model to synthesise with")

    detector = build("openwakeword")
    await detector.warm()

    pcm = b"".join([chunk async for chunk in synthesizer.synthesize("Hey Marvin.", voice)])
    spoken = frames(0, SETTLE) + _to_frames(pcm, voice.sample_rate)
    hits = sum(1 for frame in spoken if detector.feed(frame))
    assert hits == 1, f"{hits} detections for one wake word"


#: Silence to run through the detector before the phrase under test.
#:
#: openWakeWord's streaming context is process-wide and carries whatever ran
#: before it. A pause lets it settle, which is also what a real room provides:
#: nobody says the wake word straight out of a wall of noise.
SETTLE = 1.5


def _to_frames(pcm: bytes, source_rate: int) -> list[bytes]:
    samples = array.array("h")
    samples.frombytes(pcm)
    ratio = source_rate / CAPTURE_RATE
    out = array.array("h")
    for index in range(int(len(samples) / ratio)):
        start = int(index * ratio)
        stop = max(int((index + 1) * ratio), start + 1)
        window = samples[start : min(stop, len(samples))]
        out.append(int(sum(window) / len(window)) if window else 0)
    raw = out.tobytes()
    size = FRAME_SAMPLES * 2
    return [raw[i : i + size] for i in range(0, len(raw), size) if len(raw[i : i + size]) == size]


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def gated(script: list[bytes], detector, **kwargs) -> VoiceInput:
    return VoiceInput(
        microphone=Microphone(source=script, frame_ms=0),
        recognizer=ScriptedRecognizer(replies=["morning"], latency=0.0),
        wake=detector,
        **kwargs,
    )


async def test_speech_before_the_wake_word_is_not_a_turn():
    """A conversation the avatar is not part of should cost nothing -- not a
    transcription, and certainly not a Claude request."""
    source = gated(frames(VOICE, 1.2) + frames(0, 1.5), Triggered(after=10_000))
    await source.start()
    try:
        # The utterance is heard, discarded unheard, and the source then runs
        # out -- so this returns None rather than a turn.
        assert await asyncio.wait_for(source.next_turn(), timeout=5.0) is None
    finally:
        await source.stop()

    assert source.discarded >= 1


async def test_the_wake_word_opens_the_conversation():
    source = gated(frames(VOICE, 1.2) + frames(0, 1.5), Triggered(after=1))
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None and turn.text == "morning"
    assert source.wakes == 1


async def test_the_wake_word_is_needed_once_not_before_every_sentence():
    """Being made to say it before each turn is what makes an assistant feel
    like a vending machine rather than someone in the room."""
    script = (
        frames(VOICE, 1.0) + frames(0, 1.2) + frames(VOICE, 1.0) + frames(0, 1.2)
    )
    source = gated(script, Triggered(after=1), awake_seconds=60)
    await source.start()
    try:
        first = await asyncio.wait_for(source.next_turn(), timeout=5.0)
        second = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert first is not None and second is not None
    assert source.wakes == 1, "the second turn should not need waking again"


async def test_the_conversation_closes_after_a_silence():
    source = gated(frames(VOICE, 1.0) + frames(0, 1.2), Triggered(after=1), awake_seconds=0.01)
    await source.start()
    try:
        await asyncio.sleep(0.2)
        assert not source.awake
    finally:
        await source.stop()


async def test_without_a_gate_everything_is_a_turn():
    source = gated(frames(VOICE, 1.2) + frames(0, 1.5), AlwaysAwake())
    await source.start()
    try:
        turn = await asyncio.wait_for(source.next_turn(), timeout=5.0)
    finally:
        await source.stop()

    assert turn is not None
    assert source.awake, "always-awake never sleeps"

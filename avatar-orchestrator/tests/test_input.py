"""Tests for microphone capture and voice activity detection.

No sound card: the microphone takes a scripted frame source, which is also how
the input path runs in CI. Frames are synthesised at known levels so the
thresholds can be tested rather than guessed at.
"""

from __future__ import annotations

import array
import asyncio
import math

import pytest

from amanda.audio.microphone import (
    CAPTURE_RATE,
    FRAME_MS,
    Microphone,
    MicrophoneError,
    frame_bytes,
    resolve_device,
)
from amanda.audio.vad import (
    BARGE_IN_THRESHOLD,
    SPEECH_THRESHOLD,
    BargeInDetector,
    Endpointer,
    frame_rms,
)

FRAME_SAMPLES = round(CAPTURE_RATE * FRAME_MS / 1000)


def tone(level: float, samples: int = FRAME_SAMPLES) -> bytes:
    """A frame at roughly the given RMS, 0.0 to 1.0."""
    amplitude = level * math.sqrt(2) * 32767
    return array.array(
        "h",
        [
            int(max(-32768, min(32767, amplitude * math.sin(2 * math.pi * 200 * i / CAPTURE_RATE))))
            for i in range(samples)
        ],
    ).tobytes()


def silence(samples: int = FRAME_SAMPLES) -> bytes:
    return bytes(samples * 2)


def frames(level: float, seconds: float) -> list[bytes]:
    count = max(1, round(seconds * 1000 / FRAME_MS))
    return [tone(level) if level else silence() for _ in range(count)]


VOICE = SPEECH_THRESHOLD * 4
QUIET = SPEECH_THRESHOLD / 4


# --------------------------------------------------------------------------- #
# Level measurement
# --------------------------------------------------------------------------- #


def test_rms_measures_what_it_claims_to():
    assert frame_rms(silence()) == 0.0
    assert frame_rms(tone(0.1)) == pytest.approx(0.1, abs=0.005)
    assert frame_rms(tone(0.5)) == pytest.approx(0.5, abs=0.005)


def test_an_empty_frame_is_not_a_division_by_zero():
    assert frame_rms(b"") == 0.0


def test_frame_size_follows_the_rate():
    assert frame_bytes(16_000, 32) == 512 * 2
    assert frame_bytes(48_000, 10) == 480 * 2


# --------------------------------------------------------------------------- #
# Endpointing
# --------------------------------------------------------------------------- #


def feed_all(endpointer: Endpointer, batch: list[bytes]) -> list:
    return [u for frame in batch if (u := endpointer.feed(frame)) is not None]


def test_silence_alone_produces_no_utterance():
    assert feed_all(Endpointer(), frames(0, 3.0)) == []


def test_speech_followed_by_silence_ends_an_utterance():
    endpointer = Endpointer()
    found = feed_all(endpointer, frames(VOICE, 1.2) + frames(0, 1.2))

    assert len(found) == 1
    assert 1000 < found[0].duration_ms < 2400
    assert not endpointer.speaking


def test_a_cough_is_not_a_turn():
    """Shorter than min_speech, so it never becomes an utterance."""
    assert feed_all(Endpointer(min_speech=0.35), frames(VOICE, 0.15) + frames(0, 1.5)) == []


def test_a_gap_shorter_than_the_endpoint_does_not_split_a_sentence():
    endpointer = Endpointer(silence_to_end=0.75)
    batch = frames(VOICE, 0.8) + frames(0, 0.4) + frames(VOICE, 0.8) + frames(0, 1.0)

    found = feed_all(endpointer, batch)
    assert len(found) == 1, "a pause for breath should not end the turn"


def test_the_pre_roll_keeps_the_start_of_the_first_word():
    """Detection lags onset, so without this the first syllable is clipped."""
    with_roll = Endpointer(pre_roll=0.15)
    without = Endpointer(pre_roll=0.0)

    batch = frames(0, 1.0) + frames(VOICE, 1.0) + frames(0, 1.2)
    kept = feed_all(with_roll, batch)[0]
    clipped = feed_all(without, batch)[0]

    assert kept.duration_ms > clipped.duration_ms
    assert kept.duration_ms - clipped.duration_ms == pytest.approx(150, abs=FRAME_MS * 2)


def test_the_pre_roll_buffer_does_not_grow_without_bound():
    endpointer = Endpointer(pre_roll=0.15)
    for frame in frames(0, 30.0):
        endpointer.feed(frame)
    assert len(endpointer._history) <= round(0.15 * 1000 / FRAME_MS) + 1


def test_a_very_long_utterance_is_cut_at_the_ceiling():
    """A hard cap, not the normal way a turn ends."""
    endpointer = Endpointer(max_utterance=2.0)
    found = feed_all(endpointer, frames(VOICE, 5.0))

    assert found
    assert found[0].duration_ms <= 2200


def test_the_ceiling_is_long_enough_for_a_sentence():
    """Jarvis caps at 6s, which suits commands and truncates conversation."""
    assert Endpointer().max_utterance >= 15


def test_consecutive_utterances_are_separated():
    endpointer = Endpointer()
    batch = (
        frames(VOICE, 1.0) + frames(0, 1.2) + frames(VOICE, 1.0) + frames(0, 1.2)
    )
    assert len(feed_all(endpointer, batch)) == 2


def test_flush_closes_off_speech_in_progress():
    endpointer = Endpointer()
    for frame in frames(VOICE, 1.0):
        endpointer.feed(frame)

    utterance = endpointer.flush()
    assert utterance is not None and utterance.duration_ms > 500
    assert not endpointer.speaking


def test_flush_discards_something_too_short_to_be_speech():
    endpointer = Endpointer(min_speech=0.35)
    for frame in frames(VOICE, 0.1):
        endpointer.feed(frame)
    assert endpointer.flush() is None


# --------------------------------------------------------------------------- #
# Barge-in
# --------------------------------------------------------------------------- #


def fire(detector: BargeInDetector, batch: list[bytes]) -> int:
    return sum(1 for frame in batch if detector.feed(frame))


def test_sustained_speech_interrupts():
    assert fire(BargeInDetector(), frames(VOICE, 0.5)) == 1


def test_a_cough_does_not_interrupt():
    """Cancelling a reply nobody meant to stop is worse than being slow to
    notice a real interruption."""
    assert fire(BargeInDetector(sustain=0.20), frames(VOICE, 0.06) + frames(0, 0.5)) == 0


def test_silence_does_not_interrupt():
    assert fire(BargeInDetector(), frames(0, 2.0)) == 0


def test_it_fires_once_per_run_not_once_per_frame():
    assert fire(BargeInDetector(), frames(VOICE, 3.0)) == 1


def test_gaps_between_syllables_do_not_reset_the_run():
    detector = BargeInDetector(sustain=0.20, tolerance=0.10)
    batch = frames(VOICE, 0.1) + frames(0, 0.05) + frames(VOICE, 0.15)
    assert fire(detector, batch) == 1


def test_a_real_pause_does_reset_the_run():
    detector = BargeInDetector(sustain=0.20, tolerance=0.10)
    batch = frames(VOICE, 0.1) + frames(0, 0.5) + frames(VOICE, 0.1)
    assert fire(detector, batch) == 0


def test_barge_in_is_harder_to_trigger_than_endpointing():
    """The room is noisier while the avatar is speaking, and the microphone may
    be hearing the avatar itself."""
    assert BARGE_IN_THRESHOLD > SPEECH_THRESHOLD

    quiet_speech = frames(SPEECH_THRESHOLD * 1.2, 0.5)
    assert fire(BargeInDetector(), quiet_speech) == 0
    assert feed_all(Endpointer(), quiet_speech + frames(0, 1.2)) != []


def test_progress_reports_how_close_a_run_is():
    detector = BargeInDetector(sustain=0.20)
    assert detector.progress == 0.0
    for frame in frames(VOICE, 0.1):
        detector.feed(frame)
    assert 0.3 < detector.progress < 0.7


# --------------------------------------------------------------------------- #
# Microphone
# --------------------------------------------------------------------------- #


async def test_a_scripted_source_reaches_a_subscriber():
    script = frames(VOICE, 0.3)
    async with Microphone(source=script, frame_ms=1) as mic, mic.listen() as stream:
        received = [frame async for frame in stream]
    assert received == script


async def test_every_subscriber_sees_every_frame():
    """The whole point: endpointing, barge-in and a wake word all read the same
    stream rather than taking turns owning the device."""
    script = frames(VOICE, 0.2)
    # Not mergeable: the inner block needs `mic`, which the outer one binds.
    async with Microphone(source=script, frame_ms=1) as mic:  # noqa: SIM117
        async with mic.listen() as first, mic.listen() as second:
            a, b = await asyncio.gather(
                _collect(first, len(script)), _collect(second, len(script))
            )
    assert a == b == script


async def _collect(stream, count: int) -> list[bytes]:
    out = []
    async for frame in stream:
        out.append(frame)
        if len(out) == count:
            break
    return out


async def test_a_consumer_that_leaves_is_unsubscribed():
    async with Microphone(source=frames(VOICE, 1.0), frame_ms=1) as mic:
        async with mic.listen() as stream:
            await _collect(stream, 2)
        assert mic._subscribers == set()


async def test_a_slow_consumer_loses_audio_rather_than_stalling_capture():
    """The capture callback runs on PortAudio's thread and must never block."""
    script = frames(VOICE, 4.0)
    mic = Microphone(source=script, frame_ms=0)
    async with mic, mic.listen():
        await asyncio.sleep(0.2)
    assert mic.dropped > 0


async def test_stopping_ends_open_subscriptions():
    mic = Microphone(source=frames(VOICE, 10.0), frame_ms=1)
    await mic.start()

    async def consume():
        async with mic.listen() as stream:
            return [frame async for frame in stream]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await mic.stop()

    assert await asyncio.wait_for(task, timeout=1.0) != []
    assert not mic.running


async def test_a_source_running_out_ends_the_stream_rather_than_hanging():
    async with Microphone(source=frames(VOICE, 0.1), frame_ms=0) as mic, mic.listen() as stream:
        received = [frame async for frame in stream]
    assert received


async def test_starting_twice_is_harmless():
    async with Microphone(source=frames(VOICE, 0.1), frame_ms=1) as mic:
        await mic.start()
        assert mic.running


def test_an_unknown_input_device_lists_what_is_available():
    pytest.importorskip("sounddevice")
    from amanda.audio.microphone import list_input_devices

    if not list_input_devices():
        pytest.skip("no input devices on this machine")
    with pytest.raises(MicrophoneError, match="Available"):
        resolve_device("amanda-no-such-microphone")


def test_input_devices_can_be_named_by_a_fragment():
    pytest.importorskip("sounddevice")
    from amanda.audio.microphone import list_input_devices

    devices = list_input_devices()
    if not devices:
        pytest.skip("no input devices on this machine")
    index, name = devices[0]
    assert resolve_device(name[: max(3, len(name) // 2)]) is not None
    assert resolve_device(index) == index
    assert resolve_device(None) is None


# --------------------------------------------------------------------------- #
# Together
# --------------------------------------------------------------------------- #


async def test_the_two_detectors_share_one_stream():
    """A turn: the user speaks, the endpointer produces an utterance, and the
    barge-in detector -- reading the same frames -- also sees the interruption."""
    script = frames(0, 0.2) + frames(VOICE, 1.0) + frames(0, 1.2)
    endpointer = Endpointer()
    detector = BargeInDetector()
    utterances, interruptions = [], 0

    async with Microphone(source=script, frame_ms=0) as mic, mic.listen() as stream:
        async for frame in stream:
            if (utterance := endpointer.feed(frame)) is not None:
                utterances.append(utterance)
            interruptions += int(detector.feed(frame))

    assert len(utterances) == 1
    assert interruptions == 1

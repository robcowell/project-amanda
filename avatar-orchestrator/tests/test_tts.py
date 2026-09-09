"""Tests for synthesis, output and the speech queue.

No sound card and no engine: the providers under test either generate audio
themselves or shell out to a scripted fake, and the sink records instead of
playing.
"""

from __future__ import annotations

import array
import asyncio
import struct
import sys

import pytest

from amanda.audio.providers import CommandSynthesizer, ToneSynthesizer, _speaking_duration_ms
from amanda.audio.sink import (
    AudioSinkError,
    NullSink,
    _ramp_to_silence,
    resolve_device,
)
from amanda.audio.speech import SpeechSession
from amanda.audio.tts import (
    SAMPLE_WIDTH,
    SpeechSynthesizer,
    SynthesisError,
    SynthesisStream,
    VoiceSettings,
    fade_out,
    pcm_duration_ms,
)
from amanda.avatar.protocol import CancelReason, EventType, Preset
from amanda.runtime.metrics import Stage, TurnMetrics

VOICE = VoiceSettings(sample_rate=24_000)


async def collect(stream: SynthesisStream) -> bytes:
    return b"".join([chunk async for chunk in stream])


def samples(pcm: bytes) -> array.array:
    values = array.array("h")
    values.frombytes(pcm)
    return values


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


def test_both_providers_satisfy_the_protocol():
    """The abstraction exists to prevent provider lock-in, so it is worth
    checking that implementations actually conform to it."""
    assert isinstance(ToneSynthesizer(), SpeechSynthesizer)
    assert isinstance(CommandSynthesizer(argv=["true"]), SpeechSynthesizer)


def test_pcm_duration_is_measured_from_frames():
    one_second = b"\0" * (24_000 * SAMPLE_WIDTH)
    assert pcm_duration_ms(one_second, 24_000) == 1000


def test_fade_out_ends_in_silence():
    """A waveform stopped at a non-zero sample is an audible click, which is
    exactly the media-player feeling interruption should avoid."""
    loud = array.array("h", [12_000] * 2400).tobytes()
    faded = samples(fade_out(loud, fade_ms=50, sample_rate=24_000))

    assert faded[-1] == 0
    assert faded[0] == 12_000, "the fade should only touch the tail"
    assert faded[-600] > faded[-300] > faded[-100]


# --------------------------------------------------------------------------- #
# Tone provider
# --------------------------------------------------------------------------- #


async def test_tone_produces_audio_of_a_plausible_length():
    text = "It rained most of the morning, but it cleared up later."
    pcm = await collect(ToneSynthesizer().synthesize(text, VOICE))

    spoken_ms = pcm_duration_ms(pcm, VOICE.sample_rate)
    assert 3000 < spoken_ms < 6000, f"{spoken_ms}ms to say {len(text)} characters"


async def test_tone_is_actually_audible():
    pcm = await collect(ToneSynthesizer().synthesize("Hello there.", VOICE))
    peak = max(abs(value) for value in samples(pcm))
    assert peak > 12_000, "too quiet to hear over a laptop fan"


async def test_tone_puts_its_energy_where_small_speakers_work():
    """Regression: the first version was a 118 Hz buzz plus two harmonics, so
    every bit of its energy sat below 400 Hz -- the band laptop speakers roll
    off. It measured as loud and was inaudible. Peak level does not catch this;
    only the spectrum does."""
    import cmath
    import math

    pcm = await collect(ToneSynthesizer().synthesize("It rained this morning.", VOICE))
    values = samples(pcm)

    size = 4096
    start = len(values) // 2
    window = [
        values[start + i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / size)) for i in range(size)
    ]

    def energy(low: float, high: float) -> float:
        total = 0.0
        for k in range(int(low * size / 24_000), int(high * size / 24_000)):
            omega = 2 * math.pi * k / size
            magnitude = abs(sum(window[i] * cmath.exp(-1j * omega * i) for i in range(size)))
            total += (magnitude / size) ** 2
        return math.sqrt(total)

    speakable = energy(300, 4000)
    unreproducible = energy(20, 300)
    assert speakable > unreproducible * 1.5, (
        f"only {speakable:.0f} above 300 Hz against {unreproducible:.0f} below -- "
        "a small speaker would render this silent"
    )


async def test_tone_starts_and_ends_quietly():
    """Phrases that begin at full amplitude click at the join."""
    values = samples(await collect(ToneSynthesizer().synthesize("Hello there.", VOICE)))
    assert abs(values[0]) < 500
    assert abs(values[-1]) < 500


async def test_pace_changes_duration():
    text = "This is a sentence of a reasonable length to measure."
    quick = await collect(ToneSynthesizer().synthesize(text, VOICE.with_pace(1.4)))
    slow = await collect(ToneSynthesizer().synthesize(text, VOICE.with_pace(0.8)))
    assert len(slow) > len(quick) * 1.4


def test_punctuation_earns_a_pause():
    """Without it, queued phrases butt together and sound like a list."""
    assert _speaking_duration_ms("Yes.", 1.0) > _speaking_duration_ms("Yes", 1.0)


async def test_audio_arrives_in_chunks_not_all_at_once():
    chunks = [c async for c in ToneSynthesizer().synthesize("A fairly long sentence here.", VOICE)]
    assert len(chunks) > 3


async def test_word_timings_cover_the_phrase():
    stream = ToneSynthesizer().synthesize("One two three four.", VOICE)
    await collect(stream)

    assert stream.timings is not None
    assert [timing.text for timing in stream.timings] == ["One", "two", "three", "four."]
    assert stream.timings[0].start_ms == 0
    for earlier, later in zip(stream.timings, stream.timings[1:], strict=False):
        assert earlier.end_ms <= later.start_ms + 1


async def test_synthesis_can_be_cancelled_midway():
    text = "A rather long sentence that keeps going for a while."
    stream = ToneSynthesizer().synthesize(text, VOICE)

    received = []
    async for chunk in stream:
        received.append(chunk)
        if len(received) == 2:
            stream.cancel()

    assert stream.cancelled
    complete = await collect(ToneSynthesizer().synthesize(text, VOICE))
    assert stream.bytes_produced < len(complete)


# --------------------------------------------------------------------------- #
# Command provider
# --------------------------------------------------------------------------- #


def fake_engine(*, wav: bool = True, rate: int = 24_000, frames: int = 2400, exit_code: int = 0):
    """A scripted stand-in for espeak-ng or Piper."""
    script = f"""
import struct, sys
pcm = b"".join(struct.pack("<h", (i % 200) * 40 - 4000) for i in range({frames}))
out = sys.stdout.buffer
if {wav!r}:
    out.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
    out.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, {rate}, {rate} * 2, 2, 16))
    out.write(b"data" + struct.pack("<I", len(pcm)))
out.write(pcm)
out.flush()
sys.exit({exit_code})
"""
    return [sys.executable, "-c", script, "{text}"]


async def test_a_wav_header_is_stripped():
    engine = CommandSynthesizer(argv=fake_engine(wav=True), expects_wav=True)
    pcm = await collect(engine.synthesize("hello", VOICE))

    assert len(pcm) == 2400 * SAMPLE_WIDTH
    assert not pcm.startswith(b"RIFF"), "44 bytes of header would be noise at every phrase start"
    assert struct.unpack_from("<h", pcm)[0] == -4000


async def test_raw_pcm_engines_work_too():
    engine = CommandSynthesizer(argv=fake_engine(wav=False), expects_wav=False)
    assert len(await collect(engine.synthesize("hello", VOICE))) == 2400 * SAMPLE_WIDTH


async def test_a_header_arriving_split_across_reads_is_still_stripped():
    """The header is 44 bytes; a slow engine can deliver it in pieces."""
    engine = CommandSynthesizer(argv=fake_engine(wav=True, frames=30_000), expects_wav=True)
    pcm = await collect(engine.synthesize("hello", VOICE))
    assert len(pcm) == 30_000 * SAMPLE_WIDTH


async def test_a_sample_rate_mismatch_is_refused_not_played():
    """Playing 22050 Hz audio at 24000 makes a chipmunk, and the engine gets
    blamed for what is a config problem."""
    engine = CommandSynthesizer(argv=fake_engine(rate=22_050), expects_wav=True)
    with pytest.raises(SynthesisError, match="22050"):
        await collect(engine.synthesize("hello", VOICE))


async def test_a_missing_engine_is_reported_clearly():
    engine = CommandSynthesizer(argv=["amanda-no-such-engine", "{text}"])
    with pytest.raises(SynthesisError, match="not found"):
        await collect(engine.synthesize("hello", VOICE))


async def test_a_failing_engine_is_reported():
    engine = CommandSynthesizer(argv=fake_engine(exit_code=3), expects_wav=True)
    with pytest.raises(SynthesisError):
        await collect(engine.synthesize("hello", VOICE))


def test_text_is_passed_as_an_argument_not_a_shell_string():
    """No shell, so a phrase containing quotes or a semicolon is data."""
    engine = CommandSynthesizer(argv=["say", "-r", "{rate}", "{text}"])
    stream = engine.synthesize('well; "quoted" & odd', VOICE)
    assert stream._argv[-1] == 'well; "quoted" & odd'


def test_pace_reaches_the_engine_as_a_rate():
    engine = CommandSynthesizer(argv=["say", "-r", "{rate}", "{text}"], base_rate=160)
    assert engine.synthesize("hi", VOICE.with_pace(1.5))._argv[2] == "240"


# --------------------------------------------------------------------------- #
# Sink
# --------------------------------------------------------------------------- #


async def test_null_sink_records_what_it_was_given():
    sink = NullSink()
    await sink.open(24_000)
    await sink.write(b"\1\0" * 100)
    assert sink.played_ms == pcm_duration_ms(b"\1\0" * 100, 24_000)


async def test_stopping_fades_and_then_refuses_writes():
    sink = NullSink()
    await sink.open(24_000)
    await sink.write(array.array("h", [8000] * 240).tobytes())
    await sink.stop(fade_ms=20)
    await sink.write(b"\xff\x7f" * 100)

    assert samples(sink.pcm)[-1] == 0
    assert max(samples(sink.pcm)) == 8000, "no audio should follow the fade"


def test_ramp_starts_at_the_current_amplitude():
    """Ramping from zero is itself the discontinuity we are avoiding."""
    tail = array.array("h", [6000]).tobytes()
    ramp = samples(_ramp_to_silence(tail, fade_ms=10, sample_rate=24_000))
    assert 5000 < ramp[0] <= 6000
    assert ramp[-1] == 0


def test_devices_can_be_found_by_a_fragment_of_their_name():
    """On Windows this is how "CABLE Input" is selected without typing the
    driver decoration exactly."""
    pytest.importorskip("sounddevice")
    from amanda.audio.sink import list_output_devices

    devices = list_output_devices()
    if not devices:
        pytest.skip("no output devices on this machine")

    index, name = devices[0]
    assert resolve_device(name[: max(3, len(name) // 2)]) is not None
    assert resolve_device(index) == index
    assert resolve_device(None) is None


def test_an_unknown_device_lists_what_is_available():
    pytest.importorskip("sounddevice")
    with pytest.raises(AudioSinkError, match="Available"):
        resolve_device("amanda-no-such-device")


# --------------------------------------------------------------------------- #
# Speech session
# --------------------------------------------------------------------------- #


class RecordingSink(NullSink):
    """A NullSink that also records the order of calls against it."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    async def write(self, pcm: bytes) -> None:
        self._log.append("write")
        await super().write(pcm)

    async def stop(self, fade_ms: int = 80) -> None:
        self._log.append("stop")
        await super().stop(fade_ms)


async def run_session(phrases: list[str], **kwargs) -> tuple[SpeechSession, list, NullSink]:
    events: list = []
    sink = kwargs.pop("sink", None) or NullSink()
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=ToneSynthesizer(),
        sink=sink,
        voice=VOICE,
        emit=events.append,
        **kwargs,
    )
    await session.start()
    for phrase in phrases:
        await session.add(phrase)
    session.close_input()
    await session.wait()
    return session, events, sink


async def test_phrases_are_spoken_in_order_and_reach_the_sink():
    session, _, sink = await run_session(["One thing.", "Then another."])
    assert sink.played_ms > 500
    assert session.result.text == "One thing. Then another."
    assert not session.result.cancelled


async def test_the_speech_lifecycle_matches_the_protocol():
    _, events, _ = await run_session(["One thing.", "Then another."])
    assert [event.event for event in events] == [
        EventType.SPEECH_PREPARE,
        EventType.SPEECH_STARTED,
        EventType.SPEECH_COMPLETED,
    ]


async def test_prepare_carries_the_first_phrase_and_the_preset():
    """It fires at the first speakable phrase so the renderer can bring its gaze
    back before the first sample plays."""
    _, events, _ = await run_session(["Morning, Rob."], preset=Preset.WARM)
    prepare = events[0]
    assert prepare.utterance_id == "u_1"
    assert prepare.preset is Preset.WARM
    assert prepare.text == "Morning, Rob."


async def test_started_reports_the_sample_rate_the_renderer_needs():
    _, events, _ = await run_session(["Hello."])
    started = next(event for event in events if event.event == EventType.SPEECH_STARTED)
    assert started.sample_rate == 24_000


async def test_the_last_two_latency_marks_are_recorded():
    """T5 when audio exists, T6 when it is heard -- the two stages this layer
    is responsible for."""
    metrics = TurnMetrics()
    await run_session(["Hello."], metrics=metrics)

    assert Stage.FIRST_AUDIO in metrics.marks
    assert Stage.SPEECH_STARTED in metrics.marks
    assert metrics.marks[Stage.FIRST_AUDIO] <= metrics.marks[Stage.SPEECH_STARTED]


async def test_empty_phrases_are_ignored():
    _, events, _ = await run_session(["   ", "Hello."])
    assert events[0].text == "Hello."


async def test_nothing_is_announced_when_there_is_nothing_to_say():
    _, events, _ = await run_session([])
    assert events == []


# --------------------------------------------------------------------------- #
# Barge-in
# --------------------------------------------------------------------------- #


async def test_cancelling_stops_the_audio_and_drops_the_queue():
    sink = NullSink()
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=ToneSynthesizer(),
        sink=sink,
        voice=VOICE,
    )
    await session.start()
    for phrase in ["The first thing I wanted to say.", "And a second.", "And a third."]:
        await session.add(phrase)

    await asyncio.sleep(0.05)
    await session.cancel()

    assert session.result.cancelled
    assert sink.stopped
    played = sink.played_ms
    assert played > 0, "some audio should have been produced before the interruption"
    assert played < 4000, "the queued phrases should have been discarded"


async def test_the_renderer_is_told_before_the_audio_fades():
    """Build plan 13: the visual transition leads, the audio fade follows. The
    face changing is what makes an interruption feel like being interrupted."""
    order: list[str] = []
    sink = RecordingSink(order)
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=ToneSynthesizer(),
        sink=sink,
        voice=VOICE,
        emit=lambda payload: order.append(f"emit:{payload.event}"),
    )
    await session.start()
    await session.add("Something long enough to be interrupted partway through.")
    await asyncio.sleep(0.05)
    await session.cancel()

    cancelled = order.index(f"emit:{EventType.SPEECH_CANCELLED}")
    assert cancelled < order.index("stop")


async def test_the_cancellation_carries_the_reason_and_fade():
    events: list = []
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=ToneSynthesizer(),
        sink=NullSink(),
        voice=VOICE,
        emit=events.append,
        fade_ms=60,
    )
    await session.start()
    await session.add("Something to interrupt.")
    await asyncio.sleep(0.03)
    await session.cancel(CancelReason.BARGE_IN)

    cancelled = next(e for e in events if e.event == EventType.SPEECH_CANCELLED)
    assert cancelled.reason is CancelReason.BARGE_IN
    assert cancelled.fade_ms == 60


async def test_no_completion_is_announced_after_a_cancellation():
    """The renderer must not be told an utterance finished when it was cut off."""
    events: list = []
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=ToneSynthesizer(),
        sink=NullSink(),
        voice=VOICE,
        emit=events.append,
    )
    await session.start()
    await session.add("Something to interrupt.")
    await asyncio.sleep(0.03)
    await session.cancel()

    assert not any(event.event == EventType.SPEECH_COMPLETED for event in events)


async def test_cancelling_twice_is_harmless():
    session = SpeechSession(
        utterance_id="u_1", synthesizer=ToneSynthesizer(), sink=NullSink(), voice=VOICE
    )
    await session.start()
    await session.add("Hello.")
    await session.cancel()
    await session.cancel()


async def test_cancelling_before_anything_is_said_is_harmless():
    session = SpeechSession(
        utterance_id="u_1", synthesizer=ToneSynthesizer(), sink=NullSink(), voice=VOICE
    )
    await session.start()
    await session.cancel()
    assert session.result.spoken_ms == 0


async def test_phrases_added_after_a_cancellation_are_ignored():
    session = SpeechSession(
        utterance_id="u_1", synthesizer=ToneSynthesizer(), sink=NullSink(), voice=VOICE
    )
    await session.start()
    await session.cancel()
    await session.add("Too late.")
    assert session.result.text == ""


# --------------------------------------------------------------------------- #
# Failure
# --------------------------------------------------------------------------- #


async def test_a_synthesis_failure_surfaces_from_wait():
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=CommandSynthesizer(argv=["amanda-no-such-engine", "{text}"]),
        sink=NullSink(),
        voice=VOICE,
    )
    await session.start()
    await session.add("Hello.")
    session.close_input()

    with pytest.raises(SynthesisError):
        await session.wait()


async def test_the_sink_is_closed_even_when_synthesis_fails():
    sink = NullSink()
    session = SpeechSession(
        utterance_id="u_1",
        synthesizer=CommandSynthesizer(argv=["amanda-no-such-engine", "{text}"]),
        sink=sink,
        voice=VOICE,
    )
    await session.start()
    await session.add("Hello.")
    session.close_input()
    with pytest.raises(SynthesisError):
        await session.wait()

    assert sink.closed


async def test_an_utterance_that_already_finished_cannot_be_cancelled():
    """Regression: with an unpaced sink the worker finishes before a barge-in
    arrives, and the renderer was sent speech.cancelled after speech.completed."""
    session, events, _ = await run_session(["Hello."])
    await session.cancel()

    kinds = [event.event for event in events]
    assert kinds[-1] == EventType.SPEECH_COMPLETED
    assert EventType.SPEECH_CANCELLED not in kinds
    assert not session.result.cancelled


async def test_a_realtime_sink_paces_playback():
    """Without pacing, a memory sink makes any barge-in test meaningless."""
    sink = NullSink(realtime=True)
    session = SpeechSession(
        utterance_id="u_1", synthesizer=ToneSynthesizer(), sink=sink, voice=VOICE
    )
    await session.start()
    await session.add("A sentence long enough to still be playing shortly.")
    session.close_input()

    await asyncio.sleep(0.1)
    assert not sink.stopped
    await session.cancel()

    assert session.result.cancelled
    assert sink.played_ms < 2000, "the rest of the phrase should have been discarded"


# --------------------------------------------------------------------------- #
# Engine registry
# --------------------------------------------------------------------------- #


def test_every_engine_declares_a_plausible_sample_rate():
    from amanda.audio.engines import ENGINES

    for name, engine in ENGINES.items():
        assert 8_000 <= engine.sample_rate <= 48_000, name


def test_the_voice_follows_the_engines_native_rate():
    """A mismatch is refused rather than resampled, so the default has to be
    right -- this is what makes `--engine espeak-ng` work without also
    remembering to pass `--rate 22050`."""
    from amanda.audio.engines import build

    _, voice = build("espeak-ng")
    assert voice.sample_rate == 22_050

    _, tone_voice = build("tone")
    assert tone_voice.sample_rate == 24_000


def test_the_rate_can_still_be_overridden():
    from amanda.audio.engines import build

    _, voice = build("espeak-ng", sample_rate=16_000)
    assert voice.sample_rate == 16_000


def test_an_unknown_engine_lists_the_known_ones():
    from amanda.audio.engines import build

    with pytest.raises(KeyError, match="espeak-ng"):
        build("festival")


def test_the_voice_flag_is_dropped_when_no_voice_is_named():
    """Most engines reject an empty -v argument, so the flag has to go with it."""
    from amanda.audio.engines import build

    synthesizer, _ = build("espeak-ng")
    assert "{voice}" not in synthesizer.argv
    assert "-v" not in synthesizer.argv


def test_the_voice_flag_survives_when_one_is_named():
    from amanda.audio.engines import build

    synthesizer, voice = build("espeak-ng", voice_id="en-gb")
    assert "-v" in synthesizer.argv
    stream = synthesizer.synthesize("hello", voice)
    assert "en-gb" in stream._argv


def test_the_built_in_engine_needs_no_subprocess():
    from amanda.audio.engines import build

    synthesizer, _ = build("tone")
    assert isinstance(synthesizer, ToneSynthesizer)


def test_pace_still_reaches_a_registry_built_engine():
    from amanda.audio.engines import build

    synthesizer, voice = build("espeak-ng", pace=1.5)
    stream = synthesizer.synthesize("hello", voice)
    assert "248" in stream._argv, "165 wpm scaled by 1.5"

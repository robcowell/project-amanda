"""Tests for speech recognition.

The Whisper tests are skipped where faster-whisper is absent, which is most
machines. Everything above the provider is exercised with the scripted
recogniser, so the input path is testable without downloading weights.
"""

from __future__ import annotations

import array
import math

import pytest

from amanda.audio.stt import (
    RecognitionError,
    ScriptedRecognizer,
    SpeechRecognizer,
    Transcript,
    build,
)
from amanda.audio.vad import Utterance

RATE = 16_000


def speech(seconds: float = 1.0, rate: int = RATE) -> Utterance:
    """Not real speech -- enough audio for the plumbing to have something to
    carry. Recognition quality is tested against real synthesis below."""
    samples = array.array(
        "h",
        [
            int(6000 * math.sin(2 * math.pi * 180 * i / rate))
            for i in range(round(seconds * rate))
        ],
    )
    return Utterance(pcm=samples.tobytes(), sample_rate=rate)


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


def test_the_scripted_recogniser_satisfies_the_protocol():
    assert isinstance(ScriptedRecognizer(), SpeechRecognizer)


async def test_it_returns_what_it_was_scripted_to():
    recogniser = ScriptedRecognizer(replies=["morning", "how are you"], latency=0.0)
    assert (await recogniser.transcribe(speech())).text == "morning"
    assert (await recogniser.transcribe(speech())).text == "how are you"


async def test_replies_cycle():
    recogniser = ScriptedRecognizer(replies=["one"], latency=0.0)
    assert (await recogniser.transcribe(speech())).text == "one"
    assert (await recogniser.transcribe(speech())).text == "one"


async def test_it_reports_the_audio_length_it_was_given():
    transcript = await ScriptedRecognizer(latency=0.0).transcribe(speech(2.0))
    assert transcript.duration_ms == pytest.approx(2000, abs=20)


async def test_the_stand_in_does_not_pretend_transcription_is_free():
    """Zero would make the latency budget look better than it is."""
    assert ScriptedRecognizer().latency > 0

    transcript = await ScriptedRecognizer(latency=0.05).transcribe(speech())
    assert transcript.elapsed_ms >= 40


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #


def test_empty_recognises_silence_that_got_past_the_endpointer():
    assert Transcript(text="   ").empty
    assert Transcript(text="").empty
    assert not Transcript(text="hello").empty


def test_the_realtime_factor_says_whether_it_can_keep_up():
    assert Transcript(text="x", duration_ms=2000, elapsed_ms=1000).realtime_factor == 2.0
    assert Transcript(text="x", duration_ms=1000, elapsed_ms=2000).realtime_factor == 0.5


def test_a_zero_elapsed_time_is_not_a_division_by_zero():
    assert Transcript(text="x", duration_ms=1000).realtime_factor == 0.0


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_auto_returns_something_usable():
    """Falls back rather than failing, so a machine without the weights still
    runs the pipeline instead of dying at the first thing anybody says."""
    assert isinstance(build("auto"), SpeechRecognizer)


def test_scripted_can_be_asked_for_by_name():
    assert isinstance(build("scripted"), ScriptedRecognizer)


def test_an_unknown_recogniser_lists_the_known_ones():
    with pytest.raises(RecognitionError, match="whisper"):
        build("dictation-by-hand")


def test_asking_for_whisper_without_it_installed_says_how_to_fix_it():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        with pytest.raises(RecognitionError, match="pip install"):
            build("whisper")
    else:
        pytest.skip("faster-whisper is installed here")


# --------------------------------------------------------------------------- #
# Whisper
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def whisper():
    pytest.importorskip("faster_whisper", reason="faster-whisper is not installed")
    from amanda.audio.whisper_provider import WhisperRecognizer

    return WhisperRecognizer()


async def test_speech_survives_a_round_trip(whisper):
    """Piper says a known sentence and Whisper reads it back.

    Asserts word overlap rather than an exact match. Both ends are
    probabilistic -- Piper's noise_scale makes every synthesis different, and
    `base.en` is marginal on proper nouns, so "Folkestone" comes back as
    "folks and" often enough to make an exact assertion flaky by construction.
    Accuracy is a property of the model, measured in the table in
    whisper_provider.py; this checks the path works.
    """
    pytest.importorskip("piper", reason="piper is not installed")
    from amanda.audio.engines import build as build_engine

    synthesizer, voice = build_engine("piper")
    if not synthesizer.name.startswith("piper"):
        pytest.skip("no piper voice model to synthesise with")

    said = "What is the weather doing in the harbour tomorrow"
    pcm = b"".join([chunk async for chunk in synthesizer.synthesize(said, voice)])

    heard = await whisper.transcribe(_resample(pcm, voice.sample_rate, RATE))
    spoken = set(said.lower().split())
    recognised = set(heard.text.lower().replace("?", "").replace(".", "").split())

    overlap = len(spoken & recognised) / len(spoken)
    assert overlap >= 0.6, f"heard {heard.text!r}, overlap {overlap:.0%}"


async def test_it_keeps_up_with_someone_talking(whisper):
    """Below 1.0 and transcription falls behind the person speaking."""
    await whisper.warm()
    transcript = await whisper.transcribe(speech(3.0))
    assert transcript.realtime_factor > 1.0, f"{transcript.realtime_factor:.2f}x realtime"


async def test_the_model_is_loaded_once(whisper):
    await whisper.warm()
    loaded = await whisper._loaded()
    await whisper.transcribe(speech(0.5))
    assert await whisper._loaded() is loaded


async def test_a_wrong_sample_rate_is_refused_rather_than_misheard(whisper):
    """Capture runs at 16 kHz precisely so this never happens; feeding 22050
    would transcribe a chipmunk and blame the recogniser."""
    with pytest.raises(RecognitionError, match="16000"):
        await whisper.transcribe(speech(0.5, rate=22_050))


async def test_silence_transcribes_to_nothing_rather_than_hallucinating(whisper):
    """A second of digital zero transcribes as "You" unless it is suppressed.
    Left in, a door closing that got past the endpointer becomes a user turn."""
    await whisper.warm()
    quiet = Utterance(pcm=bytes(RATE * 2), sample_rate=RATE)
    heard = await whisper.transcribe(quiet)
    assert heard.empty, f"hallucinated {heard.text!r}"


def _resample(pcm: bytes, source_rate: int, target_rate: int) -> Utterance:
    """Averaging decimation, for the test only.

    Production never needs this: capture runs at 16 kHz because that is what
    the recogniser wants. This exists so Piper's 22050 Hz output can be fed to
    it. Averaging rather than picking the nearest sample, because dropping
    samples without a low-pass aliases.
    """
    samples = array.array("h")
    samples.frombytes(pcm)
    ratio = source_rate / target_rate
    out = array.array("h")
    for index in range(int(len(samples) / ratio)):
        start, stop = int(index * ratio), max(int((index + 1) * ratio), int(index * ratio) + 1)
        window = samples[start : min(stop, len(samples))]
        out.append(int(sum(window) / len(window)) if window else 0)
    return Utterance(pcm=out.tobytes(), sample_rate=target_rate)

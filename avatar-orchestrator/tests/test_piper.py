"""Tests for the in-process Piper provider.

Skipped wholesale where Piper or a voice model is absent, which is most
machines -- the point of the engine registry is that the pipeline does not
depend on any particular engine being present.
"""

from __future__ import annotations

import asyncio

import pytest

from amanda.audio.engines import find_model
from amanda.audio.tts import SynthesisError, VoiceSettings, pcm_duration_ms

pytest.importorskip("piper", reason="piper-tts is not installed")

MODEL = find_model()
if MODEL is None:
    pytest.skip("no piper voice model on this machine", allow_module_level=True)

from amanda.audio.piper_provider import PiperSynthesizer  # noqa: E402

VOICE = VoiceSettings(sample_rate=22_050)


@pytest.fixture(scope="module")
def synthesizer():
    return PiperSynthesizer(model=MODEL)


async def collect(stream) -> bytes:
    return b"".join([chunk async for chunk in stream])


async def test_it_produces_audio_of_a_plausible_length(synthesizer):
    pcm = await collect(
        synthesizer.synthesize("It rained most of the morning, but it cleared up.", VOICE)
    )
    spoken_ms = pcm_duration_ms(pcm, VOICE.sample_rate)
    assert 2000 < spoken_ms < 6000, f"{spoken_ms}ms"


async def test_the_model_is_loaded_once_not_per_phrase(synthesizer):
    """The whole reason this provider exists: each CLI invocation spent about
    3.5 seconds loading before synthesising anything."""
    await synthesizer.warm()
    loaded = await synthesizer.loaded()
    await collect(synthesizer.synthesize("One.", VOICE))
    assert await synthesizer.loaded() is loaded


async def test_synthesis_is_faster_than_realtime_once_loaded(synthesizer):
    """If it is not, phrases cannot be spoken as fast as they are produced and
    the queue underruns between them."""
    await synthesizer.warm()
    text = "It rained most of the morning, but it cleared up around three."

    started = asyncio.get_running_loop().time()
    pcm = await collect(synthesizer.synthesize(text, VOICE))
    elapsed = asyncio.get_running_loop().time() - started

    audio_s = pcm_duration_ms(pcm, VOICE.sample_rate) / 1000
    assert audio_s / elapsed > 1.5, f"only {audio_s / elapsed:.1f}x realtime"


async def test_pace_changes_duration(synthesizer):
    text = "The quick brown fox jumps over the lazy dog."
    quick = await collect(synthesizer.synthesize(text, VOICE.with_pace(1.4)))
    slow = await collect(synthesizer.synthesize(text, VOICE.with_pace(0.8)))
    assert len(slow) > len(quick) * 1.2


async def test_a_sample_rate_mismatch_is_refused(synthesizer):
    """Playing 22050 Hz audio at 24000 is a chipmunk blamed on the engine."""
    with pytest.raises(SynthesisError, match="22050"):
        await collect(synthesizer.synthesize("hello", VoiceSettings(sample_rate=24_000)))


async def test_a_missing_model_is_reported_clearly():
    from pathlib import Path

    absent = PiperSynthesizer(model=Path("voices/amanda-no-such-voice.onnx"))
    with pytest.raises(SynthesisError, match="no such voice model"):
        await collect(absent.synthesize("hello", VOICE))


async def test_cancelling_stops_it(synthesizer):
    await synthesizer.warm()
    stream = synthesizer.synthesize(
        "A long sentence that goes on for a while so there is something to stop.", VOICE
    )
    async for _chunk in stream:
        stream.cancel()
        break
    assert stream.cancelled


async def test_the_registry_builds_it_natively():
    from amanda.audio.engines import build

    synthesizer, voice = build("piper")
    assert isinstance(synthesizer, PiperSynthesizer)
    assert voice.sample_rate == 22_050
    assert synthesizer.name.startswith("piper:")


# --------------------------------------------------------------------------- #
# Choosing a voice
# --------------------------------------------------------------------------- #


def test_a_model_can_be_named_by_a_fragment():
    """Nobody should type "voices/en_GB-cori-medium.onnx" when "cori" is
    unambiguous -- the same reasoning as matching audio devices by name."""
    from amanda.audio.engines import find_model

    model = find_model("cori")
    assert model is not None and "cori" in model.stem


def test_an_unknown_fragment_lists_what_is_there():
    from amanda.audio.engines import find_model

    with pytest.raises(SynthesisError, match="Available"):
        find_model("amanda-no-such-voice")


def test_an_explicit_path_still_wins():
    from amanda.audio.engines import find_model

    assert find_model(str(MODEL)) == MODEL


def test_the_environment_names_the_default_voice(monkeypatch):
    """Otherwise the default falls to whichever model sorts first, which chose
    a male voice for a character named Amanda until somebody noticed."""
    from amanda.audio.engines import VOICE_ENV, find_model

    monkeypatch.setenv(VOICE_ENV, "cori")
    model = find_model()
    assert model is not None and "cori" in model.stem


def test_the_sample_rate_comes_from_the_model_not_a_constant():
    """Medium and high voices are 22050 Hz and low ones 16000, so one registry
    constant is wrong for somebody."""
    from amanda.audio.engines import build, find_model
    from amanda.audio.piper_provider import model_sample_rate

    for model in {find_model("cori-medium"), find_model()}:
        if model is None:
            continue
        declared = model_sample_rate(model)
        assert declared is not None
        _, voice = build("piper", voice_id=str(model))
        assert voice.sample_rate == declared


def test_an_explicit_rate_still_overrides_the_model():
    from amanda.audio.engines import build

    _, voice = build("piper", sample_rate=48_000)
    assert voice.sample_rate == 48_000


def test_config_names_the_default_voice(monkeypatch):
    """config/voices.yaml sat unread from the scaffold until it acquired a
    setting somebody wanted. This is what keeps it honest."""
    from amanda.audio.engines import VOICE_ENV, find_model
    from amanda.config import default_voice, reset

    monkeypatch.delenv(VOICE_ENV, raising=False)
    monkeypatch.delenv("AMANDA_CONFIG_DIR", raising=False)
    reset()
    named = default_voice()
    if named is None:
        pytest.skip("no default voice configured")

    model = find_model()
    assert model is not None and named in model.stem


def test_the_environment_beats_the_config(monkeypatch):
    from amanda.audio.engines import VOICE_ENV, find_model

    monkeypatch.setenv(VOICE_ENV, "alba")
    model = find_model()
    assert model is not None and "alba" in model.stem


def test_an_explicit_choice_beats_both(monkeypatch):
    from amanda.audio.engines import VOICE_ENV, find_model

    monkeypatch.setenv(VOICE_ENV, "alba")
    model = find_model("jenny")
    assert model is not None and "jenny" in model.stem


def test_the_configured_pace_is_applied_by_default():
    from amanda.audio.engines import build
    from amanda.config import default_pace

    configured = default_pace()
    if configured is None:
        pytest.skip("no pace configured")
    _, voice = build("piper")
    assert voice.pace == configured


def test_an_explicit_pace_beats_the_config():
    from amanda.audio.engines import build

    _, voice = build("piper", pace=1.0)
    assert voice.pace == 1.0

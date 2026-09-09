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

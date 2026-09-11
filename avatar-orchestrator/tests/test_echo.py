"""Tests for echo cancellation.

A synthetic room: her voice as the reference, and a microphone that hears it
back 60ms later and quieter, with or without someone else talking.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("livekit", reason="livekit is not installed")
np = pytest.importorskip("numpy")

from amanda.audio.echo import EchoCanceller, build  # noqa: E402
from amanda.audio.microphone import CAPTURE_RATE  # noqa: E402

RATE = CAPTURE_RATE
FRAME = 512  # the microphone's 32ms frame


def voice(seconds: float, seed: int) -> np.ndarray:
    """Speech-like: noise shaped by a syllable-rate envelope."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    envelope = 0.5 + 0.5 * np.sin(2 * math.pi * 4 * t + seed)
    tone = np.sin(2 * math.pi * (140 + 40 * seed) * t) * 0.5
    return ((rng.standard_normal(t.size) * 0.3 + tone) * envelope * 0.3).astype(np.float32)


def pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def level_db(x: bytes) -> float:
    samples = np.frombuffer(x, dtype=np.int16).astype(np.float32) / 32768
    return 20 * math.log10(math.sqrt(float(np.mean(samples**2))) + 1e-9)


def run(canceller: EchoCanceller, reference: np.ndarray, mic: np.ndarray) -> bytes:
    """Feed both sides a frame at a time, as the live loop does."""
    out = bytearray()
    for start in range(0, len(mic) - FRAME + 1, FRAME):
        canceller.played(pcm(reference[start : start + FRAME]), RATE)
        out += canceller.process(pcm(mic[start : start + FRAME]))
    return bytes(out)


def room(reference: np.ndarray, delay_ms: int = 60, gain: float = 0.5) -> np.ndarray:
    shift = RATE * delay_ms // 1000
    echo = np.zeros_like(reference)
    echo[shift:] = reference[:-shift] * gain
    return echo


def test_her_voice_is_taken_out_of_the_microphone():
    her = voice(6.0, seed=1)
    out = run(EchoCanceller(), her, room(her))

    # After a second to converge.
    settled = slice(RATE * 2 * 2, None)
    before = level_db(pcm(room(her))[settled])
    after = level_db(out[settled])
    assert before - after > 15, f"only {before - after:.1f}dB taken out"


def test_someone_else_talking_is_left_in():
    silent = np.zeros(RATE * 4, np.float32)
    them = voice(4.0, seed=2)
    out = run(EchoCanceller(), silent, them)

    settled = slice(RATE * 2, None)
    assert abs(level_db(pcm(them)[settled]) - level_db(out[settled])) < 1.5


def test_every_frame_comes_back_whole():
    """Capture frames are 32ms and the canceller works in 10ms: the remainder
    is carried, so nothing downstream ever sees a short frame."""
    canceller = EchoCanceller()
    for _ in range(50):
        assert len(canceller.process(bytes(FRAME * 2))) == FRAME * 2


def test_a_reference_at_the_voices_own_rate_is_resampled():
    """Piper plays at 22050; the microphone runs at 16000."""
    canceller = EchoCanceller()
    canceller.played(bytes(22050 // 10 * 2), 22050)
    canceller.process(bytes(FRAME * 2))  # must not raise


def test_disabled_means_none():
    assert build(enabled=False) is None

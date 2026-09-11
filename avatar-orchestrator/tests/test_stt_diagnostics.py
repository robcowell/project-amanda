"""Tests for the transcription diagnostics carried into turn telemetry.

Real turns on the renderer PC reported 2-5s of transcription while Whisper
alone measured ~0.5s on the same headset. Whisper's own compute time and the
highest temperature it needed are recorded beside `stt_ms`, so a slow turn says
whether it was transcribing or waiting.
"""

from __future__ import annotations

import pytest

from amanda.audio.vad import Utterance
from amanda.runtime.metrics import Stage, TurnMetrics


def test_whisper_retries_once_rather_than_climbing_to_temperature_one():
    """Every slow turn on 2026-09-11 was the default six-step climb, on noise."""
    pytest.importorskip("faster_whisper")
    from types import SimpleNamespace

    from amanda.audio.whisper_provider import WhisperRecognizer

    asked: dict[str, object] = {}

    class Model:
        def transcribe(self, audio, **options):
            asked.update(options)
            return [], SimpleNamespace(language="en", language_probability=1.0)

    WhisperRecognizer()._run(Model(), Utterance(pcm=bytes(3200), sample_rate=16_000))
    assert asked["temperature"] == [0.0, 0.4]


def test_whisper_compute_and_temperature_are_recorded_beside_stt_ms():
    metrics = TurnMetrics()
    metrics.mark_at(Stage.USER_SPEECH_ENDED, 10.0)
    metrics.mark_at(Stage.TRANSCRIPT_FINAL, 12.5)
    metrics.stt_compute_ms = 510
    metrics.stt_temperature = 0.2

    record = metrics.as_dict()
    assert record["stt_ms"] == 2500
    assert record["stt_compute_ms"] == 510
    assert record["stt_temperature"] == 0.2


def test_a_transcript_started_in_the_pause_says_so():
    """Then stt_compute_ms exceeds stt_ms, and that is the saving, not a bug."""
    metrics = TurnMetrics()
    metrics.mark_at(Stage.USER_SPEECH_ENDED, 10.0)
    metrics.mark_at(Stage.TRANSCRIPT_FINAL, 10.05)
    metrics.stt_compute_ms = 540
    metrics.stt_speculative = True

    record = metrics.as_dict()
    assert record["stt_ms"] == 50
    assert record["stt_speculative"] is True


def test_a_typed_turn_records_no_transcription_detail():
    """Omitted, not zeroed: a typed turn transcribed nothing."""
    record = TurnMetrics().as_dict()
    assert "stt_compute_ms" not in record
    assert "stt_temperature" not in record
    assert "stt_speculative" not in record

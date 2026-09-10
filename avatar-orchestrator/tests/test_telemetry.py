"""Tests for the per-turn record (build plan 24).

Two things carry the weight here. Telemetry must never be able to break a
conversation, and it must never write what was said unless it was asked to --
so most of these are about what happens when it goes wrong, and what it leaves
out when it goes right.
"""

from __future__ import annotations

import json

import pytest

from amanda import config
from amanda.runtime.metrics import SPANS, Stage, TurnMetrics
from amanda.runtime.telemetry import TurnLog


@pytest.fixture(autouse=True)
def _fresh_config():
    config.reset()
    yield
    config.reset()


def log(tmp_path, **kwargs) -> TurnLog:
    return TurnLog(path=tmp_path / "turns.jsonl", enabled=True, clock=lambda: 1000.0, **kwargs)


def a_turn() -> TurnMetrics:
    """A turn whose clock is a list, so the numbers are exact."""
    ticks = iter([0.0, 0.4, 0.4, 1.0, 1.2, 1.3, 1.35, 3.0])
    metrics = TurnMetrics(clock=lambda: next(ticks))
    for stage in Stage:
        metrics.mark(stage)
    return metrics


def lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def test_a_turn_becomes_one_line_of_json(tmp_path):
    with log(tmp_path) as turns:
        turns.record(a_turn())

    written = lines(tmp_path / "turns.jsonl")
    assert len(written) == 1
    assert written[0]["total_response_ms"] == 1350
    assert written[0]["interrupted"] is False


def test_every_turn_is_appended_rather_than_replacing_the_last(tmp_path):
    """The whole point is the shape across turns; one slow reply says nothing."""
    with log(tmp_path) as turns:
        turns.record(a_turn())
        turns.record(a_turn())
    assert len(lines(tmp_path / "turns.jsonl")) == 2


def test_a_second_run_does_not_truncate_the_first(tmp_path):
    with log(tmp_path) as turns:
        turns.record(a_turn())
    with log(tmp_path) as turns:
        turns.record(a_turn())
    assert len(lines(tmp_path / "turns.jsonl")) == 2


def test_each_line_carries_a_wall_clock_time(tmp_path):
    """The turn's own marks are monotonic and mean nothing outside the process.
    Matching a line to a log or a recording needs the wall clock."""
    with log(tmp_path) as turns:
        turns.record(a_turn())
    assert lines(tmp_path / "turns.jsonl")[0]["at"] == 1000.0


def test_the_line_is_flushed_so_it_can_be_watched_live(tmp_path):
    """`tail -f turns.jsonl` while talking to it is how this gets used."""
    turns = log(tmp_path)
    turns.open()
    turns.record(a_turn())
    assert lines(tmp_path / "turns.jsonl"), "the turn should be readable before close"
    turns.close()


def test_extra_fields_are_merged_in(tmp_path):
    with log(tmp_path) as turns:
        turns.record(a_turn(), session="s_abc")
    assert lines(tmp_path / "turns.jsonl")[0]["session"] == "s_abc"


def test_a_missing_directory_is_created(tmp_path):
    turns = TurnLog(path=tmp_path / "deep" / "down" / "turns.jsonl", enabled=True)
    with turns:
        turns.record(a_turn())
    assert turns.written == 1


# --------------------------------------------------------------------------- #
# Privacy (build plan 25)
# --------------------------------------------------------------------------- #


def test_transcripts_are_not_written_by_default(tmp_path):
    """This is the only thing in the orchestrator that could put a conversation
    on disk."""
    with log(tmp_path) as turns:
        turns.record(a_turn(), user_text="my postcode is", reply_text="I'd rather not")

    record = lines(tmp_path / "turns.jsonl")[0]
    assert "user" not in record and "reply" not in record
    assert "postcode" not in (tmp_path / "turns.jsonl").read_text()


def test_transcripts_are_written_when_asked_for(tmp_path):
    with log(tmp_path, transcripts=True) as turns:
        turns.record(a_turn(), user_text="morning", reply_text="morning to you")

    record = lines(tmp_path / "turns.jsonl")[0]
    assert record["user"] == "morning"
    assert record["reply"] == "morning to you"


def test_the_caller_always_passes_the_text_and_the_log_decides(tmp_path):
    """So no caller has to remember the rule, and every caller gets it right."""
    import inspect

    signature = inspect.signature(TurnLog.record)
    assert "user_text" in signature.parameters
    assert "reply_text" in signature.parameters


def test_transcript_logging_is_read_from_the_privacy_block(tmp_path, monkeypatch):
    """It is a privacy decision implemented by the telemetry code. Putting the
    switch in `telemetry:` would hide it from whoever goes looking."""
    (tmp_path / "avatar.yaml").write_text(
        "telemetry:\n  enabled: true\nprivacy:\n  log_transcripts: true\n"
    )
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert TurnLog.from_config().transcripts is True


def test_retaining_microphone_audio_warns_rather_than_pretending(tmp_path, monkeypatch, caplog):
    """Nothing here writes captured audio anywhere, so the setting cannot be
    honoured by doing something -- only by saying it does nothing."""
    (tmp_path / "avatar.yaml").write_text("privacy:\n  retain_microphone_audio: true\n")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))

    with caplog.at_level("WARNING"):
        config.privacy_settings()
    assert "retain" in caplog.text


# --------------------------------------------------------------------------- #
# Failure. None of it may break a conversation.
# --------------------------------------------------------------------------- #


def test_an_unopenable_path_is_not_an_exception(tmp_path):
    blocked = tmp_path / "a_file"
    blocked.write_text("not a directory")
    turns = TurnLog(path=blocked / "turns.jsonl", enabled=True)

    turns.open()
    assert turns.record(a_turn()) is None
    assert turns.enabled is False


def test_a_write_failure_disables_the_log_rather_than_raising(tmp_path):
    turns = log(tmp_path)
    turns.open()
    turns._handle.close()  # as though the filesystem went away mid-run

    assert turns.record(a_turn()) is None
    assert turns.enabled is False


def test_a_failure_is_reported_once_not_every_turn(tmp_path, caplog):
    """Repeating it would bury the conversation in it, and the second occurrence
    tells nobody anything the first did not."""
    turns = log(tmp_path)
    turns.open()
    turns._handle.close()

    with caplog.at_level("WARNING"):
        for _ in range(5):
            turns.record(a_turn())
    assert caplog.text.count("telemetry disabled") == 1


def test_something_unserialisable_does_not_take_the_turn_down(tmp_path):
    with log(tmp_path) as turns:
        assert turns.record(a_turn(), thing=object()) is None


def test_disabled_writes_nothing_and_creates_no_file(tmp_path):
    turns = TurnLog(path=tmp_path / "turns.jsonl", enabled=False)
    with turns:
        assert turns.record(a_turn()) is None
    assert not (tmp_path / "turns.jsonl").exists()


def test_it_is_off_unless_configuration_turns_it_on(tmp_path, monkeypatch):
    """Nothing should start recording conversations because a config file went
    missing."""
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "nowhere"))
    assert TurnLog.from_config().enabled is False


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_a_relative_path_resolves_against_the_orchestrator(tmp_path, monkeypatch):
    (tmp_path / "avatar.yaml").write_text("telemetry:\n  enabled: true\n  path: turns.jsonl\n")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))

    turns = TurnLog.from_config()
    assert turns.path.is_absolute()
    assert turns.path.name == "turns.jsonl"


def test_an_absolute_path_is_left_alone(tmp_path, monkeypatch):
    (tmp_path / "avatar.yaml").write_text(
        f"telemetry:\n  enabled: true\n  path: {tmp_path / 'elsewhere.jsonl'}\n"
    )
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert TurnLog.from_config().path == tmp_path / "elsewhere.jsonl"


def test_the_shipped_config_enables_it(tmp_path):
    """Config nothing reads is dead weight that drifts."""
    assert TurnLog.from_config().enabled is True
    assert TurnLog.from_config().transcripts is False


def test_overrides_beat_configuration():
    assert TurnLog.from_config({"enabled": False}).enabled is False


# --------------------------------------------------------------------------- #
# The record's shape (build plan 24)
# --------------------------------------------------------------------------- #


def test_the_plans_headline_fields_are_all_present(tmp_path):
    metrics = a_turn()
    metrics.preset = "warm"
    with log(tmp_path) as turns:
        record = turns.record(metrics)

    for name in ("stt_ms", "claude_first_token_ms", "tts_first_audio_ms",
                 "total_response_ms", "interrupted", "performance"):
        assert name in record, name


def test_stream_duration_is_recorded_but_kept_out_of_the_latency_budget():
    """Claude keeps writing while the avatar speaks, so generation time is not
    something the listener waits through. Counting it in the budget would make
    the stages stop adding up."""
    metrics = a_turn()
    assert metrics.as_dict()["claude_stream_ms"] > 0
    assert "claude_stream_ms" not in SPANS


def test_a_failed_turn_says_so_in_the_log(tmp_path):
    metrics = a_turn()
    metrics.failed = "RuntimeError: nope"
    with log(tmp_path) as turns:
        record = turns.record(metrics)
    assert record["failed"] == "RuntimeError: nope"


def test_a_stage_that_never_happened_is_omitted_rather_than_zero(tmp_path):
    """A typed turn has no speech to end. Reporting that as 0ms would make the
    numbers lie."""
    metrics = TurnMetrics(clock=lambda: 1.0)
    metrics.mark(Stage.REQUEST_SENT)
    with log(tmp_path) as turns:
        record = turns.record(metrics)
    assert "stt_ms" not in record


def test_counters_that_are_genuinely_zero_are_kept(tmp_path):
    """Zero underruns is a measurement; a missing one is not."""
    metrics = a_turn()
    metrics.underruns = 0
    with log(tmp_path) as turns:
        record = turns.record(metrics)
    assert record["underruns"] == 0

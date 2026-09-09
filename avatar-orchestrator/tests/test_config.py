"""Config loading. Must never be able to stop the application starting."""

from __future__ import annotations

import pytest

from amanda import config


@pytest.fixture(autouse=True)
def _fresh_config():
    """Config is cached, so every test starts and finishes with it cleared."""
    config.reset()
    yield
    config.reset()


def test_a_missing_directory_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "nowhere"))
    assert config.load("voices.yaml") == {}


def test_an_unreadable_file_is_ignored_rather_than_fatal(monkeypatch, tmp_path):
    """A broken config should degrade to defaults, not prevent startup."""
    (tmp_path / "voices.yaml").write_text("this: [is not: valid: yaml")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.load("voices.yaml") == {}


def test_a_file_that_is_not_a_mapping_is_ignored(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text("- just\n- a list\n")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.load("voices.yaml") == {}


def test_the_default_voice_is_read_from_the_named_entry(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text(
        "default: amanda\nvoices:\n  amanda:\n    model: cori-medium\n"
    )
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.default_voice() == "cori-medium"


def test_no_default_voice_is_a_None_not_a_crash(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text("default: amanda\nvoices: {}\n")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.default_voice() is None


def test_the_shipped_config_is_loadable():
    assert config.config_dir() is not None
    assert "voices" in config.load("voices.yaml")


def test_an_explicit_config_dir_does_not_fall_through(monkeypatch, tmp_path, caplog):
    """Pointing at a directory that is not there should load no config, not
    quietly load a different one from the working directory."""
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "nowhere"))

    assert config.config_dir() is None
    assert config.default_voice() is None

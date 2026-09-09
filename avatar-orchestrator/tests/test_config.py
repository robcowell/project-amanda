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


def test_the_base_pace_is_read_from_config(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text(
        "default: amanda\nvoices:\n  amanda:\n    model: cori-medium\n    pace: 1.35\n"
    )
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.default_pace() == 1.35


def test_a_missing_pace_is_None(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text("default: amanda\nvoices:\n  amanda:\n    model: x\n")
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.default_pace() is None


def test_a_nonsense_pace_is_ignored_rather_than_fatal(monkeypatch, tmp_path):
    (tmp_path / "voices.yaml").write_text(
        "default: amanda\nvoices:\n  amanda:\n    pace: quite fast please\n"
    )
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))
    assert config.default_pace() is None


def test_the_shipped_config_sets_a_pace():
    assert config.default_pace() is not None


# --------------------------------------------------------------------------- #
# dotenv
# --------------------------------------------------------------------------- #


def test_values_are_read_into_the_environment(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('export ANTHROPIC_API_KEY="sk-ant-example"\nAMANDA_BRIDGE_PORT=8765\n')
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AMANDA_BRIDGE_PORT", raising=False)

    assert set(config.load_env(env)) == {"ANTHROPIC_API_KEY", "AMANDA_BRIDGE_PORT"}
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-example", "quotes should be stripped"


def test_the_real_environment_wins(monkeypatch, tmp_path):
    """Otherwise `KEY=... python -m amanda.main` would be silently overridden by
    a file, which is not what that command looks like it does."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")

    assert config.load_env(env) == []
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_comments_blanks_and_junk_are_skipped(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nnot a pair\n1INVALID=x\nGOOD=yes\n")
    monkeypatch.delenv("GOOD", raising=False)
    assert config.load_env(env) == ["GOOD"]


def test_an_empty_value_is_not_set(monkeypatch, tmp_path):
    """`.env.example` ships keys with empty values; loading those would mask a
    real credential set in the shell."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert config.load_env(env) == []
    import os

    assert "ANTHROPIC_API_KEY" not in os.environ


def test_a_missing_file_is_not_an_error(tmp_path):
    assert config.load_env(tmp_path / "nothing-here") == []


def test_names_are_returned_but_never_values(monkeypatch, tmp_path):
    """A loader that reports what it loaded is a loader that leaks the key."""
    env = tmp_path / ".env"
    env.write_text("SECRET_THING=hunter2\n")
    monkeypatch.delenv("SECRET_THING", raising=False)

    assert config.load_env(env) == ["SECRET_THING"]

"""Finding and loading the project's configuration.

Small on purpose. The build plan puts engine and voice choice in config rather
than in code (section 4), and this is what makes that true rather than
aspirational -- `config/voices.yaml` sat unread from the scaffold until it
acquired a setting somebody actually wanted.

Nothing here is required: every caller has a working default, so a missing or
unparseable config file degrades to the built-in behaviour rather than
preventing startup.
"""

from __future__ import annotations

import logging
import os
from functools import cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Overrides the search below, for running from somewhere unusual.
CONFIG_ENV = "AMANDA_CONFIG_DIR"


@cache
def config_dir() -> Path | None:
    """The config directory, if one can be found.

    Checked in order: the environment, the working directory, and the project
    root inferred from this file -- which covers an editable install, where the
    package is imported from `src/` beside `config/`.
    """
    if override := os.environ.get(CONFIG_ENV):
        # Authoritative. Falling through to a different directory because the
        # named one is missing would silently load a config nobody asked for,
        # which is worse than loading none.
        directory = Path(override)
        if directory.is_dir():
            return directory
        log.warning("%s points at %s, which is not a directory", CONFIG_ENV, directory)
        return None

    for candidate in (
        Path.cwd() / "config",
        # Covers an editable install, where the package is imported from src/
        # beside config/.
        Path(__file__).resolve().parents[2] / "config",
    ):
        if candidate.is_dir():
            return candidate
    return None


@cache
def load(name: str) -> dict[str, Any]:
    """One config file as a dict, or empty if it is missing or unreadable."""
    directory = config_dir()
    if directory is None:
        return {}

    path = directory / name
    if not path.is_file():
        return {}

    try:
        import yaml

        loaded = yaml.safe_load(path.read_text())
    except Exception as exc:  # noqa: BLE001 - config must never block startup
        log.warning("ignoring %s: %s", path, exc)
        return {}

    return loaded if isinstance(loaded, dict) else {}


def project_root() -> Path:
    """The orchestrator directory, whether run from it or from elsewhere."""
    if (Path.cwd() / "pyproject.toml").is_file():
        return Path.cwd()
    return Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> list[str]:
    """Read KEY=VALUE lines from a dotenv file into the environment.

    Returns the names that were set, never the values -- this file holds an API
    key, and a loader that logs what it loaded is a loader that leaks it.

    **The real environment wins.** A variable already exported is left alone, so
    running with an explicit `ANTHROPIC_API_KEY=... python -m amanda.main` does
    what it looks like it does rather than being silently overridden by a file.

    Deliberately hand-rolled rather than pulling in a dependency: the format
    here is a handful of KEY=VALUE lines, and the parsing below is the whole of
    what this project needs from it.
    """
    path = path or project_root() / ".env"
    if not path.is_file():
        return []

    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)
        return []

    # Parsed into a dict first so that a later definition beats an earlier one,
    # which is what someone means when they paste a new key below the old one
    # rather than replacing it. Applying line by line would silently keep the
    # stale value.
    parsed: dict[str, str] = {}
    duplicated: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        name = name.strip()
        if not name.isidentifier():
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            continue

        if name in parsed:
            duplicated.add(name)
        parsed[name] = value

    for name in sorted(duplicated):
        log.warning("%s is defined more than once in %s; using the last", name, path.name)

    applied = [name for name in parsed if name not in os.environ]
    for name in applied:
        os.environ[name] = parsed[name]
    return applied


def reset() -> None:
    """Forget what was loaded.

    Config is read once and cached, which is right for an application that
    reads it at startup and wrong for anything that changes the environment
    afterwards -- tests, mainly. Without this they leak state into each other
    in whatever order they happen to run.
    """
    config_dir.cache_clear()
    load.cache_clear()


def _default_entry() -> dict[str, Any]:
    voices = load("voices.yaml")
    return (voices.get("voices") or {}).get(voices.get("default")) or {}


def default_voice() -> str | None:
    """The voice model named in config/voices.yaml, if any."""
    return _default_entry().get("model") or None


def claude_settings() -> dict[str, Any]:
    """The `claude:` block from config/avatar.yaml.

    Returned raw so the caller decides what to do with unknown keys -- config
    that names a setting the code has dropped should not stop startup.
    """
    settings = load("avatar.yaml").get("claude")
    return dict(settings) if isinstance(settings, dict) else {}


def wake_settings() -> dict[str, Any]:
    """The `wake:` block from config/avatar.yaml."""
    settings = load("avatar.yaml").get("wake")
    return dict(settings) if isinstance(settings, dict) else {}


def stt_settings() -> dict[str, Any]:
    """The `stt:` block from config/avatar.yaml."""
    settings = load("avatar.yaml").get("stt")
    return dict(settings) if isinstance(settings, dict) else {}


def default_pace() -> float | None:
    """Base delivery speed for the configured voice, if set."""
    pace = _default_entry().get("pace")
    return float(pace) if isinstance(pace, (int, float)) else None

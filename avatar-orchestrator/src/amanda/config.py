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


def reset() -> None:
    """Forget what was loaded.

    Config is read once and cached, which is right for an application that
    reads it at startup and wrong for anything that changes the environment
    afterwards -- tests, mainly. Without this they leak state into each other
    in whatever order they happen to run.
    """
    config_dir.cache_clear()
    load.cache_clear()


def default_voice() -> str | None:
    """The voice model named in config/voices.yaml, if any."""
    voices = load("voices.yaml")
    default = voices.get("default")
    entry = (voices.get("voices") or {}).get(default) or {}
    return entry.get("model") or None

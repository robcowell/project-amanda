"""Persisting the per-turn record (build plan 24).

`metrics.py` measures a turn; this writes it down. The point of the split is
that "it feels sluggish" is only diagnosable across turns -- one slow reply
tells you nothing, thirty of them tell you which stage moved. So the record
goes to a file you can `tail -f` while talking to the avatar, one JSON object
per line, and the shape is the build plan's.

Three rules, in the order they matter:

  * **Telemetry never breaks a conversation.** Any failure disables the log for
    the rest of the run and says so once. A turn that worked must not be
    reported as failed because a disk filled up.
  * **It never writes what was said unless asked.** `privacy.log_transcripts`
    is false by default (build plan 25), and this is the only thing in the
    orchestrator that could put a transcript on disk.
  * **The file is opened once.** Same discipline as the models: per-turn open,
    write, close would make the cheapest thing in a turn one of the slowest.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)

#: Where turns are recorded, relative to the orchestrator directory unless the
#: configured path is absolute. Gitignored: it is a record of conversations.
DEFAULT_PATH = "turns.jsonl"


@dataclass
class TurnLog:
    """Appends one JSON object per turn.

    Disabled is the default, so nothing starts writing conversation records to
    disk because a config file went missing.
    """

    path: Path | None = None
    enabled: bool = False
    #: Include the user's words and the reply. Off unless privacy config says
    #: otherwise -- see build plan 25.
    transcripts: bool = False
    #: Wall-clock, so a line can be matched against a log or a recording. The
    #: turn's own numbers are monotonic and only meaningful relative to itself.
    clock: Any = time.time

    _handle: TextIO | None = field(default=None, init=False, repr=False)
    _failed: bool = field(default=False, init=False, repr=False)
    written: int = field(default=0, init=False)

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None) -> TurnLog:
        """Built from the `telemetry:` and `privacy:` blocks.

        Transcript logging is deliberately read from `privacy:` rather than
        `telemetry:`. It is a privacy decision that happens to be implemented by
        the telemetry code, and putting the switch anywhere else would hide it
        from the person looking for it.
        """
        from amanda.config import privacy_settings, project_root, telemetry_settings

        values = {**telemetry_settings(), **(overrides or {})}
        path = Path(str(values.get("path") or DEFAULT_PATH)).expanduser()
        if not path.is_absolute():
            path = project_root() / path

        return cls(
            path=path,
            enabled=bool(values.get("enabled", False)),
            transcripts=bool(privacy_settings().get("log_transcripts", False)),
        )

    # ----------------------------------------------------------------- #

    def open(self) -> None:
        """Open the log, or quietly stay closed if it cannot be opened.

        Called at startup so a bad path is reported before the first turn rather
        than in the middle of one.
        """
        if not self.enabled or self.path is None or self._handle is not None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - see _disable
            self._disable(exc)

    def record(
        self,
        metrics: Any,
        *,
        user_text: str | None = None,
        reply_text: str | None = None,
        **extra: Any,
    ) -> dict[str, Any] | None:
        """Write one turn. Returns what was written, or None if nothing was.

        The transcripts are passed in either way and dropped here, so no caller
        has to remember the privacy rule and every caller gets it right.
        """
        if self._handle is None:
            return None

        record: dict[str, Any] = {"at": round(self.clock(), 3)}
        record.update(metrics.as_dict())
        record.update({key: value for key, value in extra.items() if value is not None})
        if self.transcripts:
            for name, text in (("user", user_text), ("reply", reply_text)):
                if text is not None:
                    record[name] = text

        try:
            self._handle.write(json.dumps(record) + "\n")
            # Flushed per line so `tail -f` shows a turn as it finishes, which
            # is the whole way this gets used. One line is cheap and the write
            # happens after the utterance, not during it.
            self._handle.flush()
        # Deliberately every exception, not a chosen list. The rule is that a
        # turn that worked is never reported as failed because the log could
        # not be written, and a list is a bet on which failures exist -- a
        # closed handle raises ValueError, not OSError, and that bet was
        # already lost once.
        except Exception as exc:  # noqa: BLE001 - see above
            self._disable(exc)
            return None

        self.written += 1
        return record

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                handle.close()
            except OSError:
                log.debug("turn log did not close cleanly", exc_info=True)

    def _disable(self, exc: BaseException) -> None:
        """Say it once, then stop trying.

        Repeating the same failure on every turn would bury the conversation in
        it, and the second occurrence tells nobody anything the first did not.
        """
        if not self._failed:
            self._failed = True
            log.warning("turn telemetry disabled: %s (%s)", exc, self.path)
        self.close()
        self.enabled = False

    def __enter__(self) -> TurnLog:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

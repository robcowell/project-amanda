"""Multi-turn conversation state (epic 2).

Mostly bookkeeping, with one genuinely awkward case: what to record when the
user interrupts. The assistant generated one thing and the user heard another,
and the history has to hold the second. Getting this wrong makes Claude act as
though it said sentences nobody heard, which is the fastest way to make a
conversation feel broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Sent as a mid-conversation system message after a barge-in. This is the
#: operator channel -- it carries operator authority and, unlike editing the
#: top-level system prompt, does not invalidate the cached prefix.
INTERRUPTION_NOTE = (
    "You were interrupted while speaking. The user heard only the part of your "
    "previous reply shown above, and nothing after it. Do not repeat what they "
    "already heard, and do not refer to anything they did not."
)


@dataclass(slots=True)
class Conversation:
    """The message history sent to Claude each turn."""

    #: Trim to the most recent N messages, or None to keep everything.
    #:
    #: Trimming from the front does not hurt prompt caching here: the render
    #: order is tools, system, messages, and only the system block carries a
    #: cache breakpoint, so it stays intact. Server-side compaction is the
    #: answer when conversations get long enough to matter.
    max_messages: int | None = 200

    #: Append an operator note after an interruption. Needs a model that
    #: supports mid-conversation system messages. Tested 2026-09-10: Opus 5 and
    #: Sonnet 5 both accept one, contrary to the documentation, which lists
    #: Sonnet 5 as unsupported. Older models do reject it with a 400, so the
    #: switch stays.
    interruption_notes: bool = True

    _messages: list[dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self._messages)

    def messages(self) -> list[dict[str, Any]]:
        """A copy, so a caller cannot mutate history by editing the request."""
        return [dict(message) for message in self._messages]

    def user(self, text: str) -> None:
        self._append({"role": "user", "content": text})

    def assistant(self, text: str) -> None:
        """Record a reply the user heard in full."""
        if text.strip():
            self._append({"role": "assistant", "content": text})

    def assistant_interrupted(self, spoken: str) -> None:
        """Record a reply the user heard only part of.

        `spoken` is what actually reached the speakers, not what Claude
        generated -- the tail was cancelled before it was synthesised, so as far
        as the conversation is concerned it was never said.
        """
        if spoken.strip():
            self._append({"role": "assistant", "content": spoken})

    def note_interruption(self) -> None:
        """Flag the interruption to Claude, if the model supports it.

        Must come after the user's interrupting message and be last in the list,
        which is exactly where a barge-in leaves it: assistant partial, user
        interruption, note.
        """
        if not self.interruption_notes:
            return
        if not self._messages or self._messages[-1]["role"] != "user":
            # The placement rules are strict and a violation is a 400, so skip
            # rather than send something the API will reject.
            return
        self._messages.append({"role": "system", "content": INTERRUPTION_NOTE})

    def clear(self) -> None:
        self._messages.clear()

    def _append(self, message: dict[str, Any]) -> None:
        # An operator note is scoped to the turn it was written for; once the
        # conversation moves on it is stale, and leaving it would have Claude
        # apologising for an interruption several turns old.
        while self._messages and self._messages[-1]["role"] == "system":
            self._messages.pop()

        self._messages.append(message)

        if self.max_messages is not None and len(self._messages) > self.max_messages:
            del self._messages[: len(self._messages) - self.max_messages]
            # Never open on an assistant turn: the API expects the first message
            # to be from the user.
            while self._messages and self._messages[0]["role"] != "user":
                self._messages.pop(0)

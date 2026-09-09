"""Phrase segmentation for streaming into TTS (build plan 12).

Claude's response arrives a token at a time; TTS wants whole phrases. Waiting
for the full response before speaking adds the entire generation time to the
latency budget, so the synthesiser should start on phrase one while Claude is
still writing phrase two.

The whole difficulty is deciding where a phrase ends without cutting somewhere
that makes the synthesiser mispronounce it. A full stop is usually a boundary,
except after "Dr", inside "3.14", or in the middle of an ellipsis.

Provider-neutral: this knows nothing about Claude or about any TTS engine. It
lives here because its producer does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Trailing tokens after which a full stop is an abbreviation, not a sentence
#: end. Not exhaustive, and does not need to be -- a missed abbreviation splits
#: a phrase slightly early, which is a much smaller problem than a split that
#: never comes.
ABBREVIATIONS = frozenset(
    {
        # titles
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "hon", "capt", "sgt", "lt",
        "col", "gen",
        # latin and editorial
        "e.g", "i.e", "etc", "vs", "cf", "al", "approx", "dept", "est", "fig", "no", "vol",
        # months
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        # days
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
        # times and places
        "a.m", "p.m", "u.s", "u.k",
    }
)

_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)")
_CLAUSE_END = re.compile(r"[,;:](?=\s)")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")


@dataclass(slots=True)
class PhraseSegmenter:
    """Accumulates streamed text and emits speakable phrases.

    A phrase is emitted at a sentence boundary, or at a clause boundary once
    there is enough text to be worth speaking, or when the buffer grows long
    enough that waiting further would cost more than an awkward split.
    """

    #: Below this, a clause boundary is not worth splitting on -- "Well," on its
    #: own is a worse thing to synthesise than a slightly longer wait.
    min_phrase_chars: int = 40

    #: The first phrase is allowed to be shorter, because it alone decides when
    #: speech *starts*. Everything after it is spoken while earlier audio is
    #: still playing, so its synthesis is hidden; the first one's is not, and it
    #: sits on the critical path twice -- once waiting for the boundary and
    #: again waiting for the engine. Still high enough that a phrase is a
    #: clause and not an interjection.
    first_phrase_chars: int = 24

    #: The backstop. Claude has been told not to produce markdown or code, but a
    #: phrase this long without punctuation means something has gone wrong and
    #: speaking late is worse than speaking imperfectly.
    max_phrase_chars: int = 240

    _buffer: str = ""
    _emitted: list[str] = field(default_factory=list)

    def feed(self, text: str) -> list[str]:
        """Add streamed text and return any phrases that are now complete."""
        self._buffer += text
        phrases = []
        while (phrase := self._take()) is not None:
            phrases.append(phrase)
            self._emitted.append(phrase)
        return phrases

    def flush(self) -> str | None:
        """Emit whatever is left. Call once the stream has ended."""
        remainder = self._buffer.strip()
        self._buffer = ""
        if not remainder:
            return None
        self._emitted.append(remainder)
        return remainder

    @property
    def spoken(self) -> str:
        """Everything emitted so far, which is what the user actually heard."""
        return " ".join(self._emitted)

    def reset(self) -> None:
        self._buffer = ""
        self._emitted.clear()

    # ----------------------------------------------------------------- #

    def _take(self) -> str | None:
        cut = self._boundary()
        if cut is None:
            return None
        phrase = self._buffer[:cut].strip()
        self._buffer = self._buffer[cut:].lstrip()
        return phrase or None

    def _boundary(self) -> int | None:
        for match in _SENTENCE_END.finditer(self._buffer):
            if self._is_real_sentence_end(match):
                return match.end()

        minimum = self.first_phrase_chars if not self._emitted else self.min_phrase_chars
        if len(self._buffer) >= minimum:
            for match in _CLAUSE_END.finditer(self._buffer):
                if match.end() >= minimum:
                    return match.end()

        if len(self._buffer) >= self.max_phrase_chars:
            # Split at the last word boundary rather than mid-word.
            space = self._buffer.rfind(" ", 0, self.max_phrase_chars)
            return space if space > 0 else self.max_phrase_chars

        return None

    def _is_real_sentence_end(self, match: re.Match[str]) -> bool:
        preceding = self._buffer[: match.start()]

        # A decimal point: "3.14", "v1.2". The digit on both sides is the tell.
        if (
            match.group().startswith(".")
            and preceding[-1:].isdigit()
            and self._buffer[match.end() : match.end() + 1].isdigit()
        ):
            return False

        word = _WORD_BEFORE.search(preceding)
        if word and word.group(1).lower().rstrip(".") in ABBREVIATIONS:
            return False

        # A single initial: "J. R. R." -- one letter before the stop.
        return not (word and len(word.group(1)) == 1 and word.group(1).isupper())

"""An offline stand-in for the Claude client.

Same surface as `ClaudeClient`, canned replies instead of an API call. It exists
because the machine this is developed on has no API key, and because the rest of
the pipeline -- segmentation, synthesis, playback, the protocol's speech
lifecycle, the latency marks -- is worth exercising without spending money or
network round trips on every run.

It streams word by word with a small delay, so phrase segmentation and the T3
mark behave as they do against the real thing rather than arriving all at once.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from amanda.runtime.metrics import Stage, TurnMetrics

#: Replies chosen to exercise the segmenter: several sentences, an abbreviation,
#: a decimal, a long clause that has to split at a comma.
DEFAULT_REPLIES: tuple[str, ...] = (
    "It rained most of the morning, but it cleared up around three. "
    "Dr. Patel said the walk did him good.",
    "I'm not sure, honestly. The reading was 3.14 exactly, which surprised "
    "everyone, and nobody has managed to reproduce it since.",
    "Well, I suppose the honest answer is that nobody really knows, and that "
    "is probably the interesting part of it.",
    "Yes. That one I'm confident about.",
    "That's a longer story than it sounds. The short version is that the first "
    "attempt failed, the second worked by accident, and the third is the one "
    "everybody remembers.",
)


class ScriptedTurn:
    """Mimics `StreamedTurn`: iterate for text, cancel to abandon."""

    def __init__(self, text: str, metrics: TurnMetrics, word_delay: float, latency: float) -> None:
        self._text = text
        self._word_delay = word_delay
        self._latency = latency
        self.metrics = metrics

        self.cancelled = False
        self.stop_reason: str | None = None
        self.refusal = None
        self.usage = None

        self._chunks: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    def cancel(self) -> None:
        self.cancelled = True
        self.metrics.interrupted = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def __aiter__(self) -> AsyncIterator[str]:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="scripted-turn")
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            self._chunks.append(chunk)
            yield chunk

    async def _pump(self) -> None:
        try:
            self.metrics.mark(Stage.REQUEST_SENT)
            # Stands in for time to first token, which is most of the gap the
            # thinking animation has to cover.
            await asyncio.sleep(self._latency)
            for index, word in enumerate(self._text.split(" ")):
                self.metrics.mark(Stage.FIRST_TOKEN)
                self._queue.put_nowait(word if index == 0 else f" {word}")
                await asyncio.sleep(self._word_delay)
            self.metrics.mark(Stage.LAST_TOKEN)
            self.stop_reason = "end_turn"
            self.metrics.model = "scripted"
        except asyncio.CancelledError:
            self.cancelled = True
        finally:
            self._queue.put_nowait(None)


@dataclass
class ScriptedClient:
    """Drop-in replacement for `ClaudeClient` with no network."""

    replies: Sequence[str] = DEFAULT_REPLIES
    #: Time to first token. The real thing is several hundred milliseconds, and
    #: pretending otherwise would make the thinking animation look unnecessary.
    latency: float = 0.6
    word_delay: float = 0.045

    _cycle: itertools.cycle = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._cycle = itertools.cycle(self.replies)

    def start_turn(self, messages: list[dict]) -> ScriptedTurn:
        return ScriptedTurn(next(self._cycle), TurnMetrics(), self.word_delay, self.latency)

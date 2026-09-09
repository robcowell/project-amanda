"""Streaming Messages API client (epic 2).

Three things this has to get right, in order of how badly they break the
illusion when wrong:

  * **Stream, don't wait.** TTS starts on the first phrase, not the last token
    (build plan 12). A turn that waits for the full response adds its entire
    generation time to the latency budget.
  * **Cancel promptly.** Barge-in means abandoning a turn mid-sentence, and the
    cancellation has to be immediate rather than cooperative-at-the-next-token
    (build plan 13).
  * **Stay behind configuration.** The model is a config value, never a
    hard-coded constant, so the application is not pinned to one model
    generation (build plan 4).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from amanda.runtime.metrics import Stage, TurnMetrics

log = logging.getLogger(__name__)

#: Sent alongside `fallbacks="default"`. On a policy decline the API re-runs the
#: same request on a fallback model inside the same call, routed by refusal
#: category, so there is no model list to maintain here.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Sent alongside `speed="fast"`. Research preview, Opus 5 and 4.8 only, and
#: priced above standard -- off unless the config asks for it.
FAST_MODE_BETA = "fast-mode-2026-02-01"


@dataclass(frozen=True, slots=True)
class ClaudeSettings:
    """Everything about the request that belongs in config/avatar.yaml."""

    model: str = "claude-opus-5"

    #: A spoken conversational turn is short. This is a deliberate cap, not a
    #: cost measure: a reply long enough to hit it has already stopped being
    #: conversation.
    max_tokens: int = 1024

    #: The latency lever. Opus 5 thinks by default, and effort governs how much.
    #: "low" suits chat; raise it for turns that genuinely need reasoning and
    #: measure what it costs at T3.
    effort: str = "low"

    #: Opt in to server-side refusal fallbacks. A decline before any output is
    #: not billed; the rescue bills at the fallback model's own rates.
    fallbacks: bool = True

    #: Research preview: the same model at up to 2.5x output tokens per second,
    #: at premium pricing. Directly relevant to the T6 - T0 budget, so it is
    #: exposed here -- but it is a spending decision, so it defaults off.
    fast: bool = False

    #: Cache the system prompt across turns. Below the model's minimum cacheable
    #: prefix (512-4096 tokens) this silently does nothing, which is the case
    #: for a short conversational prompt -- harmless, and correct once the
    #: prompt grows.
    cache_system: bool = True


@dataclass(frozen=True, slots=True)
class Refusal:
    """A `stop_reason: "refusal"` that survived the fallback chain."""

    category: str | None
    explanation: str | None


class StreamedTurn:
    """One in-flight assistant turn.

    Iterate it for text as it arrives; read `text`, `stop_reason` and `refusal`
    afterwards. `cancel()` abandons it immediately.

        turn = client.start_turn(messages)
        async for chunk in turn:
            segmenter.feed(chunk)
        if turn.refusal:
            ...
    """

    def __init__(
        self,
        request: dict[str, Any],
        opener: Callable[..., Any],
        metrics: TurnMetrics,
    ) -> None:
        self._request = request
        self._opener = opener
        self.metrics = metrics

        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._chunks: list[str] = []
        self._error: BaseException | None = None

        self.cancelled = False
        self.stop_reason: str | None = None
        self.refusal: Refusal | None = None
        self.usage: Any | None = None

    # ----------------------------------------------------------------- #

    @property
    def text(self) -> str:
        """Everything received so far. After a cancellation this is the partial
        response -- which is what the user actually heard, and therefore what
        belongs in the conversation history."""
        return "".join(self._chunks)

    def cancel(self) -> None:
        """Abandon the turn now.

        Cancels the reading task rather than setting a flag the loop checks,
        because a flag only takes effect at the next token and there may not be
        one for a while. Barge-in cannot wait for that.
        """
        self.cancelled = True
        self.metrics.interrupted = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def __aiter__(self) -> AsyncIterator[str]:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="claude-turn")

        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            self._chunks.append(chunk)
            yield chunk

        if self._error is not None:
            raise self._error

    # ----------------------------------------------------------------- #

    async def _pump(self) -> None:
        """Read the SSE stream into the queue.

        Runs as its own task so `cancel()` can interrupt a wait between tokens,
        not just the gaps the consumer happens to look at.
        """
        try:
            self.metrics.mark(Stage.REQUEST_SENT)
            async with self._opener(**self._request) as stream:
                async for text in stream.text_stream:
                    self.metrics.mark(Stage.FIRST_TOKEN)
                    self._queue.put_nowait(text)
                self._finish(await stream.get_final_message())
        except asyncio.CancelledError:
            # Swallowed deliberately: the consumer's loop should end quietly on a
            # barge-in rather than seeing an exception it would only suppress.
            self.cancelled = True
        except BaseException as exc:  # noqa: BLE001 - re-raised to the consumer
            self._error = exc
        finally:
            self._queue.put_nowait(None)

    def _finish(self, message: Any) -> None:
        self.stop_reason = getattr(message, "stop_reason", None)
        self.usage = getattr(message, "usage", None)

        if self.stop_reason == "refusal":
            # Reaching here means the whole fallback chain declined. Guard
            # before reading stop_details: it is None for every other reason.
            details = getattr(message, "stop_details", None)
            self.refusal = Refusal(
                category=getattr(details, "category", None),
                explanation=getattr(details, "explanation", None),
            )
            self.metrics.refused = True
            log.warning("turn refused (%s)", self.refusal.category)

        self.metrics.model = getattr(message, "model", None)
        if self.usage is not None:
            self.metrics.input_tokens = getattr(self.usage, "input_tokens", None)
            self.metrics.output_tokens = getattr(self.usage, "output_tokens", None)
            self.metrics.cached_tokens = getattr(self.usage, "cache_read_input_tokens", None)


@dataclass
class ClaudeClient:
    """Builds and runs streaming turns.

    The SDK client is injected so tests can drive the whole path -- streaming,
    cancellation, refusals, errors -- without an API key or a network call.
    """

    settings: ClaudeSettings = field(default_factory=ClaudeSettings)
    system: str = ""
    client: Any = None
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if self.client is None:
            import anthropic

            # No api_key argument: the SDK resolves ANTHROPIC_API_KEY, then
            # ANTHROPIC_AUTH_TOKEN, then an `ant auth login` profile. Passing a
            # key here would mean reading it into the process ourselves for no
            # benefit (build plan 25).
            self.client = anthropic.AsyncAnthropic()

    @property
    def has_credentials(self) -> bool:
        """Whether the SDK resolved a credential.

        Asked of the client rather than of the environment, because the SDK
        looks in several places -- ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, an
        `ant auth login` profile -- and reimplementing that here would drift.
        Construction succeeds regardless; only a request fails, which is too
        late to tell somebody they have no key.
        """
        return bool(
            getattr(self.client, "api_key", None)
            or getattr(self.client, "auth_token", None)
        )

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None, **kwargs: Any) -> ClaudeClient:
        """Build from config/avatar.yaml, with explicit overrides winning.

        Unknown config keys are dropped with a warning rather than raising: a
        config file naming a setting the code no longer has should not stop the
        application starting.
        """
        from amanda.config import claude_settings

        fields = {field.name for field in dataclasses.fields(ClaudeSettings)}
        values = {**claude_settings(), **(overrides or {})}
        unknown = set(values) - fields
        for key in unknown:
            log.warning("ignoring unknown claude setting %r in config", key)
        known = {key: value for key, value in values.items() if key in fields}
        return cls(settings=ClaudeSettings(**known), **kwargs)

    def start_turn(self, messages: list[dict[str, Any]]) -> StreamedTurn:
        """Begin a turn. Nothing is sent until the result is iterated."""
        metrics = TurnMetrics(clock=self.clock)
        return StreamedTurn(self.build_request(messages), self.client.beta.messages.stream, metrics)

    def build_request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """The request body, as its own method so tests can assert on it."""
        request: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "messages": messages,
            # Thinking is deliberately omitted. On Opus 5 that means adaptive
            # thinking, governed by effort -- which is the right lever here.
            # Explicitly disabling it is a documented footgun on this model:
            # it can write tool calls into visible text and leak thinking tags.
            "output_config": {"effort": self.settings.effort},
        }

        if self.system:
            block: dict[str, Any] = {"type": "text", "text": self.system}
            if self.settings.cache_system:
                block["cache_control"] = {"type": "ephemeral"}
            request["system"] = [block]

        betas: list[str] = []
        if self.settings.fallbacks:
            betas.append(FALLBACK_BETA)
            request["fallbacks"] = "default"
        if self.settings.fast:
            betas.append(FAST_MODE_BETA)
            request["speed"] = "fast"
        if betas:
            request["betas"] = betas

        return request

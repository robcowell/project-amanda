"""Tests for the Claude client, conversation state and phrase segmenter.

The SDK client is injected, so every path here -- streaming, cancellation,
refusals, transport errors -- runs without an API key or a network call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from amanda.claude.client import (
    FALLBACK_BETA,
    FAST_MODE_BETA,
    ClaudeClient,
    ClaudeSettings,
)
from amanda.claude.conversation import INTERRUPTION_NOTE, Conversation
from amanda.claude.prompts import CONVERSATION_SYSTEM, PERFORMANCE_SYSTEM
from amanda.claude.segmenter import PhraseSegmenter
from amanda.runtime.metrics import Stage

# --------------------------------------------------------------------------- #
# A stand-in for the SDK
# --------------------------------------------------------------------------- #


@dataclass
class FakeUsage:
    input_tokens: int = 120
    output_tokens: int = 44
    cache_read_input_tokens: int = 0


@dataclass
class FakeStopDetails:
    category: str | None = None
    explanation: str | None = None


@dataclass
class FakeMessage:
    stop_reason: str = "end_turn"
    stop_details: FakeStopDetails | None = None
    model: str = "claude-opus-5"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeStream:
    def __init__(
        self,
        chunks: list[str],
        final: FakeMessage,
        *,
        stall_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._final = final
        self._stall_after = stall_after
        self._error = error
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True

    @property
    def text_stream(self):
        async def generate():
            for index, chunk in enumerate(self._chunks):
                if self._stall_after is not None and index == self._stall_after:
                    # Stands in for the gap between tokens, which is where a
                    # barge-in usually lands.
                    await asyncio.sleep(3600)
                if self._error is not None and index == 1:
                    raise self._error
                await asyncio.sleep(0)
                yield chunk

        return generate()

    async def get_final_message(self) -> FakeMessage:
        return self._final


class FakeAnthropic:
    """Records the request it was handed and replays a scripted stream."""

    def __init__(self, stream: FakeStream) -> None:
        self._stream = stream
        self.requests: list[dict[str, Any]] = []
        self.beta = self

    @property
    def messages(self):
        return self

    def stream(self, **request: Any) -> FakeStream:
        self.requests.append(request)
        return self._stream


def make_client(
    chunks: list[str] | None = None,
    final: FakeMessage | None = None,
    settings: ClaudeSettings | None = None,
    **stream_kwargs: Any,
) -> tuple[ClaudeClient, FakeAnthropic]:
    stream = FakeStream(
        chunks if chunks is not None else ["Hello", " there."],
        final or FakeMessage(),
        **stream_kwargs,
    )
    sdk = FakeAnthropic(stream)
    client = ClaudeClient(
        settings=settings or ClaudeSettings(),
        system=CONVERSATION_SYSTEM,
        client=sdk,
    )
    return client, sdk


async def drain(turn) -> list[str]:
    return [chunk async for chunk in turn]


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #


def test_the_model_comes_from_configuration():
    """Never hard-coded to one model generation (build plan 4)."""
    client, _ = make_client(settings=ClaudeSettings(model="claude-sonnet-5"))
    assert client.build_request([])["model"] == "claude-sonnet-5"


def test_effort_is_the_latency_lever_and_thinking_is_left_alone():
    """Explicitly disabling thinking on Opus 5 is a documented footgun -- it can
    put tool calls in visible text and leak thinking tags. Lower effort instead."""
    client, _ = make_client()
    request = client.build_request([])
    assert request["output_config"] == {"effort": "low"}
    assert "thinking" not in request


def test_refusal_fallbacks_are_on_by_default():
    client, _ = make_client()
    request = client.build_request([])
    assert request["fallbacks"] == "default"
    assert FALLBACK_BETA in request["betas"]


def test_fallbacks_can_be_turned_off():
    client, _ = make_client(settings=ClaudeSettings(fallbacks=False))
    request = client.build_request([])
    assert "fallbacks" not in request
    assert "betas" not in request


def test_fast_mode_is_off_unless_asked_for():
    """It is a real latency lever for this project, and a spending decision."""
    client, _ = make_client()
    assert "speed" not in client.build_request([])

    client, _ = make_client(settings=ClaudeSettings(fast=True))
    request = client.build_request([])
    assert request["speed"] == "fast"
    assert FAST_MODE_BETA in request["betas"]


def test_the_system_prompt_is_cached():
    client, _ = make_client()
    system = client.build_request([])["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_caching_can_be_turned_off():
    client, _ = make_client(settings=ClaudeSettings(cache_system=False))
    assert "cache_control" not in client.build_request([])["system"][0]


def test_no_system_block_when_there_is_no_system_prompt():
    client = ClaudeClient(client=object(), system="")
    assert "system" not in client.build_request([])


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


async def test_text_arrives_in_chunks():
    client, sdk = make_client(["It rained ", "most of ", "the morning."])
    turn = client.start_turn([{"role": "user", "content": "how was it?"}])

    assert await drain(turn) == ["It rained ", "most of ", "the morning."]
    assert turn.text == "It rained most of the morning."
    assert turn.stop_reason == "end_turn"
    assert not turn.cancelled
    assert sdk.requests[0]["messages"] == [{"role": "user", "content": "how was it?"}]


async def test_nothing_is_sent_until_the_turn_is_iterated():
    client, sdk = make_client()
    client.start_turn([{"role": "user", "content": "hi"}])
    await asyncio.sleep(0)
    assert sdk.requests == []


async def test_the_stream_is_closed_when_the_turn_ends():
    client, sdk = make_client()
    await drain(client.start_turn([]))
    assert sdk._stream.closed


async def test_usage_and_model_land_on_the_metrics():
    client, _ = make_client(final=FakeMessage(usage=FakeUsage(200, 60, 150)))
    turn = client.start_turn([])
    await drain(turn)

    assert turn.metrics.model == "claude-opus-5"
    assert turn.metrics.input_tokens == 200
    assert turn.metrics.output_tokens == 60
    assert turn.metrics.cached_tokens == 150


async def test_the_latency_marks_are_recorded():
    """T2 when the request goes out, T3 on the first token -- the two stages
    this client is responsible for."""
    client, _ = make_client()
    turn = client.start_turn([])
    await drain(turn)

    assert Stage.REQUEST_SENT in turn.metrics.marks
    assert Stage.FIRST_TOKEN in turn.metrics.marks
    assert turn.metrics.elapsed_ms(Stage.REQUEST_SENT, Stage.FIRST_TOKEN) is not None


async def test_first_token_is_the_first_one():
    """A naive mark() that overwrites would report the last token instead."""
    client, _ = make_client(["a", "b", "c", "d"])
    turn = client.start_turn([])

    marks = []
    async for _ in turn:
        marks.append(turn.metrics.marks[Stage.FIRST_TOKEN])
    assert len(set(marks)) == 1


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


async def test_a_refusal_is_surfaced_not_treated_as_a_reply():
    """stop_reason "refusal" on the final response means the whole fallback
    chain declined."""
    client, _ = make_client(
        chunks=[],
        final=FakeMessage(
            stop_reason="refusal",
            stop_details=FakeStopDetails(category="cyber", explanation="declined"),
        ),
    )
    turn = client.start_turn([])
    await drain(turn)

    assert turn.refusal is not None
    assert turn.refusal.category == "cyber"
    assert turn.metrics.refused is True
    assert turn.metrics.as_dict()["refused"] is True


async def test_an_ordinary_reply_has_no_refusal():
    """stop_details is None for every stop reason but refusal, so reading it
    unguarded would break on the common path."""
    client, _ = make_client()
    turn = client.start_turn([])
    await drain(turn)

    assert turn.refusal is None
    assert "refused" not in turn.metrics.as_dict()


# --------------------------------------------------------------------------- #
# Cancellation -- barge-in
# --------------------------------------------------------------------------- #


async def test_cancelling_mid_stream_keeps_what_was_already_said():
    client, _ = make_client(["It rained ", "most of ", "the morning."])
    turn = client.start_turn([])

    received = []
    async for chunk in turn:
        received.append(chunk)
        if len(received) == 1:
            turn.cancel()

    assert turn.cancelled
    assert turn.metrics.interrupted is True
    assert turn.metrics.as_dict()["interrupted"] is True
    assert turn.text.startswith("It rained ")
    assert "the morning." not in turn.text


async def test_cancellation_interrupts_a_stalled_stream():
    """The barge-in case. A flag the loop checks would never fire here, because
    the loop is waiting on a token that is not coming."""
    client, _ = make_client(["Well, "], stall_after=1)
    turn = client.start_turn([])

    async def consume() -> list[str]:
        return await drain(turn)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    turn.cancel()

    received = await asyncio.wait_for(task, timeout=1.0)
    assert received == ["Well, "]
    assert turn.cancelled
    assert turn.text == "Well, "


async def test_cancelling_before_iteration_is_harmless():
    client, _ = make_client()
    turn = client.start_turn([])
    turn.cancel()
    assert turn.cancelled


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


async def test_a_transport_error_reaches_the_consumer():
    client, _ = make_client(["one", "two"], error=ConnectionError("stream died"))
    turn = client.start_turn([])

    with pytest.raises(ConnectionError, match="stream died"):
        await drain(turn)


async def test_text_before_an_error_is_still_available():
    """It was spoken, so it belongs in the history whatever happened next."""
    client, _ = make_client(["one ", "two"], error=ConnectionError("stream died"))
    turn = client.start_turn([])

    with pytest.raises(ConnectionError):
        await drain(turn)
    assert turn.text == "one "


# --------------------------------------------------------------------------- #
# Conversation
# --------------------------------------------------------------------------- #


def test_history_round_trips():
    conversation = Conversation()
    conversation.user("morning")
    conversation.assistant("Morning, Rob.")

    assert conversation.messages() == [
        {"role": "user", "content": "morning"},
        {"role": "assistant", "content": "Morning, Rob."},
    ]


def test_messages_are_copied_not_shared():
    conversation = Conversation()
    conversation.user("morning")
    conversation.messages()[0]["content"] = "tampered"
    assert conversation.messages()[0]["content"] == "morning"


def test_empty_replies_are_not_recorded():
    conversation = Conversation()
    conversation.assistant("   ")
    assert len(conversation) == 0


def test_an_interruption_records_what_was_heard_not_what_was_generated():
    """Claude must not act as though it said sentences nobody heard."""
    conversation = Conversation()
    conversation.user("tell me about the weather")
    conversation.assistant_interrupted("It rained most of the")
    conversation.user("sorry, actually —")

    assert conversation.messages()[1] == {
        "role": "assistant",
        "content": "It rained most of the",
    }


def test_the_interruption_note_lands_where_the_api_allows_it():
    """Must follow a user message and be last, which is exactly where a
    barge-in leaves it."""
    conversation = Conversation()
    conversation.user("tell me about the weather")
    conversation.assistant_interrupted("It rained most of the")
    conversation.user("sorry, actually —")
    conversation.note_interruption()

    messages = conversation.messages()
    assert messages[-1] == {"role": "system", "content": INTERRUPTION_NOTE}
    assert messages[-2]["role"] == "user"


def test_a_note_is_skipped_where_it_would_be_rejected():
    """Placement violations are a 400, so skip rather than send one."""
    conversation = Conversation()
    conversation.user("hello")
    conversation.assistant("Hello.")
    conversation.note_interruption()

    assert all(message["role"] != "system" for message in conversation.messages())


def test_the_note_can_be_turned_off_for_models_that_reject_it():
    """Opus 5 supports mid-conversation system messages; Sonnet 5 returns 400."""
    conversation = Conversation(interruption_notes=False)
    conversation.user("hello")
    conversation.note_interruption()
    assert all(message["role"] != "system" for message in conversation.messages())


def test_a_stale_note_is_dropped_on_the_next_turn():
    """Otherwise Claude apologises for an interruption several turns old."""
    conversation = Conversation()
    conversation.user("first")
    conversation.note_interruption()
    conversation.user("second")

    roles = [message["role"] for message in conversation.messages()]
    assert "system" not in roles


def test_history_is_trimmed_and_still_opens_on_a_user_turn():
    conversation = Conversation(max_messages=4)
    for index in range(6):
        conversation.user(f"q{index}")
        conversation.assistant(f"a{index}")

    messages = conversation.messages()
    assert len(messages) <= 4
    assert messages[0]["role"] == "user"


def test_history_can_be_kept_whole():
    conversation = Conversation(max_messages=None)
    for index in range(50):
        conversation.user(f"q{index}")
    assert len(conversation) == 50


# --------------------------------------------------------------------------- #
# Segmenter
# --------------------------------------------------------------------------- #


def feed_all(segmenter: PhraseSegmenter, text: str, size: int = 5) -> list[str]:
    phrases = []
    for index in range(0, len(text), size):
        phrases += segmenter.feed(text[index : index + size])
    if (tail := segmenter.flush()) is not None:
        phrases.append(tail)
    return phrases


def test_sentences_become_phrases():
    assert feed_all(PhraseSegmenter(), "One thing. Then another. And a third.") == [
        "One thing.",
        "Then another.",
        "And a third.",
    ]


def test_chunk_size_does_not_change_the_result():
    """Token boundaries are arbitrary, so the output must not depend on them."""
    text = "It rained this morning. It cleared up later, which was a relief."
    results = {tuple(feed_all(PhraseSegmenter(), text, size)) for size in (1, 3, 7, 40)}
    assert len(results) == 1


def test_abbreviations_do_not_end_a_sentence():
    assert feed_all(PhraseSegmenter(), "Dr. Patel said so. He was right.") == [
        "Dr. Patel said so.",
        "He was right.",
    ]


def test_decimals_do_not_end_a_sentence():
    assert feed_all(PhraseSegmenter(), "It was 3.14 exactly. Nobody expected that.") == [
        "It was 3.14 exactly.",
        "Nobody expected that.",
    ]


def test_initials_do_not_end_a_sentence():
    phrases = feed_all(PhraseSegmenter(), "That was J. R. R. Tolkien. Obviously.")
    assert phrases[0] == "That was J. R. R. Tolkien."


def test_a_long_clause_splits_at_a_comma():
    """So the synthesiser can start on a long sentence rather than waiting for
    the full stop."""
    text = "Well I suppose the honest answer is that nobody really knows, and "
    text += "that is the interesting part of it."
    phrases = feed_all(PhraseSegmenter(min_phrase_chars=40), text)
    assert len(phrases) == 2
    assert phrases[0].endswith(",")


def test_a_short_clause_is_not_worth_splitting():
    """"Well," on its own is a worse thing to synthesise than a slight wait."""
    assert feed_all(PhraseSegmenter(), "Well, yes.") == ["Well, yes."]


def test_a_runaway_phrase_is_broken_at_a_word():
    text = "and " * 200
    phrases = feed_all(PhraseSegmenter(max_phrase_chars=100), text)
    assert len(phrases) > 1
    assert all(len(phrase) <= 100 for phrase in phrases)
    assert not any(phrase.endswith("an") for phrase in phrases)


def test_flush_is_empty_when_nothing_is_pending():
    segmenter = PhraseSegmenter()
    segmenter.feed("Done.")
    assert segmenter.flush() is None


def test_spoken_is_what_the_user_heard():
    segmenter = PhraseSegmenter()
    feed_all(segmenter, "One thing. Then another.")
    assert segmenter.spoken == "One thing. Then another."


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #


def test_the_conversational_prompt_forbids_what_tts_cannot_speak():
    lowered = CONVERSATION_SYSTEM.lower()
    for forbidden in ("markdown", "bullet", "emoji", "stage direction"):
        assert forbidden in lowered


def test_the_conversational_prompt_asks_for_no_stage_directions():
    """Build plan 6: performance metadata must not contaminate spoken text."""
    assert "square brackets" in CONVERSATION_SYSTEM.lower()


def test_the_classifier_prompt_never_asks_for_dialogue():
    assert "never write dialogue" in PERFORMANCE_SYSTEM.lower()


def test_the_classifier_prompt_biases_toward_restraint():
    assert "neutral_attentive is the correct answer most of the time" in PERFORMANCE_SYSTEM


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_the_shipped_config_builds_valid_settings():
    """config/avatar.yaml keys map straight onto ClaudeSettings, so a typo or a
    drifted field name fails here rather than at startup."""
    from pathlib import Path

    import yaml

    config = yaml.safe_load((Path(__file__).parent.parent / "config" / "avatar.yaml").read_text())
    settings = ClaudeSettings(**config["claude"])

    assert settings.model == "claude-opus-5"
    assert settings.effort in {"low", "medium", "high", "xhigh", "max"}
    assert settings.max_tokens > 0


def test_configured_model_ids_carry_no_date_suffix():
    """Current model IDs are complete as written; an appended date is a stale
    pattern and names a model that does not exist."""
    import re
    from pathlib import Path

    import yaml

    config = yaml.safe_load((Path(__file__).parent.parent / "config" / "avatar.yaml").read_text())
    ids = [config["claude"]["model"], config["performance"]["classifier_model"]]
    for model_id in ids:
        assert not re.search(r"-\d{8}$", model_id), f"{model_id} has a date suffix"

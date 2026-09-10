"""Tests for the performance director (phase 4).

The SDK client is injected, so the whole path -- the schema it sends, the
clamping, every way a classification can fail -- runs with no API key and no
network call.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from amanda import config
from amanda.avatar.protocol import Preset, encode
from amanda.claude.prompts import PERFORMANCE_SYSTEM
from amanda.performance.director import (
    DEFAULT_PRESETS,
    MIN_REPLY_CHARS,
    REPLY_CHARS,
    Director,
    DirectorSettings,
    NullDirector,
    PerformanceDirector,
    ScriptedDirector,
    build,
)
from amanda.runtime.metrics import TurnMetrics

# --------------------------------------------------------------------------- #
# A stand-in for the SDK
# --------------------------------------------------------------------------- #


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeThinking:
    type: str = "thinking"


@dataclass
class FakeResponse:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"


class FakeAnthropic:
    """Records the request and replays one scripted response."""

    api_key = "test"

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.requests: list[dict[str, Any]] = []

    @property
    def messages(self):
        return self

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._response


def answering(preset: str, intensity: float, **kwargs: Any) -> FakeAnthropic:
    body = json.dumps({"preset": preset, "intensity": intensity})
    return FakeAnthropic(FakeResponse(content=[FakeBlock(body)]), **kwargs)


def make_director(sdk: FakeAnthropic, **settings: Any) -> PerformanceDirector:
    return PerformanceDirector(settings=DirectorSettings(**settings), client=sdk)


# --------------------------------------------------------------------------- #
# Everything satisfies the interface
# --------------------------------------------------------------------------- #


def test_every_director_satisfies_the_protocol():
    """So switching the classifier off is a config change, not a code path."""
    assert isinstance(PerformanceDirector(client=FakeAnthropic()), Director)
    assert isinstance(ScriptedDirector(), Director)
    assert isinstance(NullDirector(), Director)


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #


def test_the_model_comes_from_configuration():
    director = make_director(FakeAnthropic(), model="claude-haiku-4-5")
    assert director.build_request("hi", "hello")["model"] == "claude-haiku-4-5"


def test_it_uses_the_performance_prompt_not_the_conversational_one():
    """The two must not be merged: asking one call to do both contaminates the
    spoken text with stage directions (build plan 6)."""
    director = make_director(FakeAnthropic())
    assert director.build_request("hi", "hello")["system"] == PERFORMANCE_SYSTEM


def test_the_schema_constrains_the_preset_vocabulary():
    """The enabled subset is enforced by the schema, not merely requested in the
    prompt."""
    director = make_director(FakeAnthropic(), presets=(Preset.WARM, Preset.CONCERNED))
    schema = director.build_request("hi", "hello")["output_config"]["format"]["schema"]

    assert schema["properties"]["preset"]["enum"] == ["warm", "concerned"]
    assert schema["required"] == ["preset", "intensity"]
    assert schema["additionalProperties"] is False


def test_it_does_not_ask_the_classifier_to_think():
    """One right answer and no reasoning to do. A classifier that deliberates
    talks itself into `surprised`."""
    request = make_director(FakeAnthropic()).build_request("hi", "hello")
    assert "thinking" not in request
    assert "effort" not in request.get("output_config", {})


def test_both_sides_of_the_exchange_are_sent():
    request = make_director(FakeAnthropic()).build_request("Is he all right?", "He's fine.")
    content = request["messages"][0]["content"]
    assert "Is he all right?" in content
    assert "He's fine." in content


def test_a_long_reply_is_truncated():
    """It is judging delivery, not reading the answer, and a turn that ran long
    should not cost more to classify."""
    request = make_director(FakeAnthropic()).build_request("hi", "word " * 500)
    assert len(request["messages"][0]["content"]) < REPLY_CHARS + 200


# --------------------------------------------------------------------------- #
# The direction
# --------------------------------------------------------------------------- #


async def test_a_classification_becomes_a_performance_update():
    director = make_director(answering("warm", 0.22))
    update = await director.direct("morning", "Morning. How did you sleep?")

    assert update is not None
    assert update.preset is Preset.WARM
    assert update.intensity == pytest.approx(0.22)
    assert director.directions == 1


async def test_the_transition_comes_from_configuration_not_the_model():
    """How fast the face moves is an animation decision, not one the classifier
    gets an opinion about."""
    director = make_director(answering("warm", 0.2), transition_ms=800)
    update = await director.direct("morning", "Morning.")
    assert update.transition_ms == 800


async def test_a_direction_is_a_valid_protocol_payload():
    """The producer's job: what leaves here has to encode."""
    director = make_director(answering("concerned", 0.30))
    update = await director.direct("he's in hospital", "I'm sorry to hear that.")
    assert json.loads(encode(update))["payload"]["preset"] == "concerned"


async def test_nothing_is_classified_when_there_is_no_reply():
    sdk = FakeAnthropic()
    director = make_director(sdk)
    assert await director.direct("morning", "   ") is None
    assert sdk.requests == [], "and no call is made"


# --------------------------------------------------------------------------- #
# Restraint
# --------------------------------------------------------------------------- #


async def test_intensity_is_capped_at_the_configured_ceiling():
    """Build plan 18. The prompt asks for restraint; this is what enforces it,
    because structured outputs support `enum` but not `maximum`."""
    director = make_director(answering("surprised", 0.95), max_intensity=0.45)
    update = await director.direct("guess what", "No! Really?")

    assert update.intensity == pytest.approx(0.45)
    assert director.clamped == 1


async def test_a_negative_intensity_is_floored():
    director = make_director(answering("warm", -0.5))
    update = await director.direct("hi", "hello there")
    assert update.intensity == 0.0


async def test_a_direction_within_the_ceiling_is_left_alone():
    director = make_director(answering("warm", 0.2), max_intensity=0.45)
    await director.direct("hi", "hello there")
    assert director.clamped == 0


async def test_a_preset_outside_the_enabled_set_is_refused():
    """The schema's enum should have prevented it. If it happens the vocabulary
    gate has failed, which is worth saying rather than quietly animating a
    preset phase 4 has not authored."""
    director = make_director(answering("enthusiastic", 0.3), presets=(Preset.WARM,))
    assert await director.direct("hi", "hello there") is None
    assert director.failures == 1


# --------------------------------------------------------------------------- #
# Failure. None of it may end a turn.
# --------------------------------------------------------------------------- #


async def test_a_transport_error_is_no_direction_rather_than_an_exception():
    director = make_director(FakeAnthropic(error=RuntimeError("connection reset")))
    assert await director.direct("hi", "hello there") is None
    assert director.failures == 1


async def test_unparseable_json_is_no_direction():
    sdk = FakeAnthropic(FakeResponse(content=[FakeBlock("not json at all")]))
    director = make_director(sdk)
    assert await director.direct("hi", "hello there") is None
    assert director.failures == 1


async def test_an_unknown_preset_name_is_no_direction():
    sdk = FakeAnthropic(FakeResponse(content=[FakeBlock('{"preset": "smug", "intensity": 0.2}')]))
    assert await make_director(sdk).direct("hi", "hello there") is None


async def test_a_response_with_no_text_is_no_direction():
    """A refusal, or max_tokens reached before any text. The format guarantee
    survives neither."""
    sdk = FakeAnthropic(FakeResponse(content=[], stop_reason="refusal"))
    director = make_director(sdk)
    assert await director.direct("hi", "hello there") is None
    assert director.failures == 1


async def test_a_thinking_block_before_the_json_is_skipped():
    body = json.dumps({"preset": "warm", "intensity": 0.2})
    sdk = FakeAnthropic(FakeResponse(content=[FakeThinking(), FakeBlock(body)]))
    update = await make_director(sdk).direct("hi", "hello there")
    assert update is not None and update.preset is Preset.WARM


async def test_a_slow_classifier_is_abandoned():
    """A direction arriving after the utterance is worse than none."""

    class Slow(FakeAnthropic):
        async def create(self, **request: Any) -> Any:
            await asyncio.sleep(3600)

    director = make_director(Slow(), timeout=0.05)
    assert await director.direct("hi", "hello there") is None
    assert director.failures == 1


async def test_cancellation_propagates_rather_than_counting_as_a_failure():
    """A barge-in cancels the classification on purpose. That is not the
    director failing, and must not be reported as one."""

    class Slow(FakeAnthropic):
        async def create(self, **request: Any) -> Any:
            await asyncio.sleep(3600)

    director = make_director(Slow())
    task = asyncio.create_task(director.direct("hi", "hello there"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert director.failures == 0


async def test_warming_never_raises():
    """It is an optimisation. A key that does not work should be reported by the
    first real turn, not by a failure to start."""
    director = make_director(FakeAnthropic(error=RuntimeError("no")))
    await director.warm()


async def test_warming_happens_once():
    director = make_director(answering("warm", 0.2))
    await director.warm()
    await director.warm()
    assert len(director.client.requests) == 1


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@pytest.fixture
def _fresh_config():
    config.reset()
    yield
    config.reset()


def write_config(tmp_path, body: str, monkeypatch) -> None:
    (tmp_path / "avatar.yaml").write_text(body)
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path))


def test_settings_are_read_from_the_performance_block(tmp_path, monkeypatch, _fresh_config):
    write_config(
        tmp_path,
        "performance:\n"
        "  classifier_model: claude-haiku-4-5\n"
        "  classifier_max_tokens: 64\n"
        "  max_intensity: 0.3\n"
        "  default_transition_ms: 600\n"
        "  enabled_presets: [warm, concerned]\n",
        monkeypatch,
    )
    settings = DirectorSettings.from_config()

    assert settings.model == "claude-haiku-4-5"
    assert settings.max_tokens == 64
    assert settings.max_intensity == 0.3
    assert settings.transition_ms == 600
    assert settings.presets == (Preset.WARM, Preset.CONCERNED)


def test_an_unknown_preset_narrows_the_vocabulary_rather_than_failing(
    tmp_path, monkeypatch, _fresh_config
):
    """Config naming a preset the code has dropped should not stop startup."""
    write_config(
        tmp_path, "performance:\n  enabled_presets: [warm, sardonic]\n", monkeypatch
    )
    assert DirectorSettings.from_config().presets == (Preset.WARM,)


def test_a_missing_block_is_defaults_not_a_crash(tmp_path, monkeypatch, _fresh_config):
    write_config(tmp_path, "bridge:\n  port: 8765\n", monkeypatch)
    assert DirectorSettings.from_config().presets == DEFAULT_PRESETS


def test_overrides_beat_configuration(tmp_path, monkeypatch, _fresh_config):
    write_config(tmp_path, "performance:\n  classifier_model: from-config\n", monkeypatch)
    settings = DirectorSettings.from_config({"classifier_model": "from-the-flag"})
    assert settings.model == "from-the-flag"


def test_the_shipped_config_produces_a_usable_director(_fresh_config):
    """Config that nothing reads is dead weight that drifts. This is the check
    that the shipped `performance:` block is actually wired up."""
    settings = DirectorSettings.from_config()
    assert settings.model
    assert 0.0 < settings.max_intensity <= 1.0
    assert settings.presets
    assert Preset.NEUTRAL_ATTENTIVE in settings.presets


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_none_is_a_real_answer():
    """Presets driven by conversation state alone still look like participation.
    Being able to switch the classifier off is how that gets compared."""
    assert isinstance(build("none"), NullDirector)


def test_scripted_needs_no_credentials():
    assert isinstance(build("scripted"), ScriptedDirector)


def test_auto_falls_back_to_the_stand_in_without_credentials():
    class Keyless:
        api_key = None
        auth_token = None

    assert isinstance(build(client=Keyless()), ScriptedDirector)


def test_auto_uses_the_real_director_when_there_are_credentials(_fresh_config):
    assert isinstance(build(client=FakeAnthropic()), PerformanceDirector)


def test_an_explicit_choice_that_cannot_be_built_says_so(_fresh_config):
    """`auto` degrades quietly; naming the classifier and not getting it should
    not be silent."""

    class Keyless:
        api_key = None
        auth_token = None

    with pytest.raises(ValueError):
        build("claude", client=Keyless())


def test_an_unknown_name_is_an_error():
    with pytest.raises(ValueError):
        build("vibes")


# --------------------------------------------------------------------------- #
# The offline stand-in
# --------------------------------------------------------------------------- #


async def test_the_stand_in_produces_valid_directions():
    director = ScriptedDirector(latency=0.0)
    update = await director.direct("morning", "Morning to you.")
    assert update is not None
    assert update.preset in set(Preset)
    assert 0.0 <= update.intensity <= 1.0


async def test_the_stand_in_varies():
    """So the previz shows the face changing rather than sitting still."""
    director = ScriptedDirector(latency=0.0)
    seen = {
        (await director.direct("hi", "a reply")).preset
        for _ in range(len(director.directions))
    }
    assert len(seen) > 1


async def test_the_stand_in_has_latency():
    """Its whole premise is that the direction lands part-way through an
    utterance. One that answered instantly would hide the case where it does
    not."""
    director = ScriptedDirector(latency=0.05)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await director.direct("hi", "a reply")
    assert loop.time() - started >= 0.04


async def test_the_null_director_directs_nothing():
    assert await NullDirector().direct("hi", "a reply") is None


def test_the_reply_threshold_is_longer_than_the_first_phrase():
    """The first phrase is deliberately short because it decides when speech
    starts. It is not enough to tell warm from concerned."""
    from amanda.claude.segmenter import PhraseSegmenter

    assert PhraseSegmenter().first_phrase_chars < MIN_REPLY_CHARS


# --------------------------------------------------------------------------- #
# When it runs. This is the design, so it is worth pinning.
# --------------------------------------------------------------------------- #


class FakeStream:
    def __init__(self, text: str) -> None:
        self.text = text
        self.metrics = TurnMetrics()


def gate(reply: str, speaking: bool = True, final: bool = False):
    """Run the turn loop's gate against a stub session.

    Called unbound: the method touches only `self.direction` and `self.direct`,
    and standing a whole Session up would drag in a bridge, a synthesiser and a
    microphone to test four lines of scheduling.
    """
    from types import SimpleNamespace

    from amanda.main import Session

    started = asyncio.Event()
    if speaking:
        started.set()

    fired: list[str] = []

    async def direct(user_text: str, text: str, metrics=None) -> None:
        fired.append(text)

    stub = SimpleNamespace(direction=None, direct=direct)
    Session.consider_direction(
        stub, SimpleNamespace(text="hi"), FakeStream(reply), started, final=final
    )
    return stub, fired


async def consider(reply: str, speaking: bool = True, final: bool = False) -> list[str]:
    stub, fired = gate(reply, speaking, final)
    await asyncio.sleep(0)
    if stub.direction is not None:
        stub.direction.cancel()
    return fired


async def test_nothing_is_classified_before_the_avatar_speaks():
    """A direction landing during THINKING would override the envelope while the
    avatar is still visibly considering."""
    assert await consider("x" * 500, speaking=False) == []


async def test_a_short_opening_waits_for_more_of_the_reply():
    assert await consider("It rained most of the morning,") == []


async def test_it_fires_once_there_is_enough_reply_to_judge():
    assert await consider("x" * (MIN_REPLY_CHARS + 1)) != []


async def test_a_reply_that_ends_short_is_still_classified():
    """Nothing more is coming, so judge what there is however little it is."""
    assert await consider("Yes. That one I'm confident about.", final=True) != []


async def test_it_fires_only_once_per_turn():
    from types import SimpleNamespace

    from amanda.main import Session

    started = asyncio.Event()
    started.set()
    fired: list[str] = []

    async def direct(user_text: str, text: str, metrics=None) -> None:
        fired.append(text)

    stub = SimpleNamespace(direction=None, direct=direct)
    for _ in range(3):
        Session.consider_direction(
            stub, SimpleNamespace(text="hi"), FakeStream("x" * 500), started
        )

    await asyncio.sleep(0)
    stub.direction.cancel()
    assert len(fired) == 1

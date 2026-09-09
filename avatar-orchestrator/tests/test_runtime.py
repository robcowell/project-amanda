"""Tests for the conversation state machine and the offline Claude stand-in."""

from __future__ import annotations

import asyncio

from amanda.avatar.protocol import EventType, GazeTarget, Preset
from amanda.claude.scripted import ScriptedClient, ScriptedTurn
from amanda.runtime.metrics import Stage
from amanda.runtime.state_machine import (
    ENVELOPES,
    ConversationState,
    ConversationStateMachine,
)


def events(payloads) -> list[str]:
    return [payload.event for payload in payloads]


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #


def test_every_state_has_an_animation_envelope():
    assert set(ENVELOPES) == set(ConversationState)


def test_entering_a_state_emits_its_envelope():
    machine = ConversationStateMachine()
    payloads = machine.enter(ConversationState.ATTENTIVE)

    assert EventType.PERFORMANCE_UPDATE in events(payloads)
    assert machine.state is ConversationState.ATTENTIVE


def test_re_entering_a_state_emits_nothing():
    """So a caller can be careless and the renderer still sees one transition."""
    machine = ConversationStateMachine()
    machine.enter(ConversationState.LISTENING)
    assert machine.enter(ConversationState.LISTENING) == []


def test_thinking_looks_away_rather_than_puzzled():
    """Build plan 7: eye contact drops and the gaze moves off-axis. THINKING is
    not a facial expression."""
    envelope = ENVELOPES[ConversationState.THINKING]
    assert envelope.preset is Preset.CONSIDERING
    assert envelope.gaze is not GazeTarget.USER
    assert envelope.intensity < 0.35


def test_attention_returns_when_speech_begins():
    assert ENVELOPES[ConversationState.SPEAKING].gaze is GazeTarget.USER


def test_the_avatar_does_not_track_the_user_while_idle():
    assert ENVELOPES[ConversationState.IDLE].gaze is GazeTarget.DISTANT


def test_thinking_is_bracketed_by_its_lifecycle_events():
    """So the renderer can tell the THINKING state from the considering mood."""
    machine = ConversationStateMachine()
    assert EventType.ASSISTANT_THINKING_STARTED in events(
        machine.enter(ConversationState.THINKING)
    )
    assert EventType.ASSISTANT_THINKING_ENDED in events(
        machine.enter(ConversationState.SPEAKING)
    )


def test_listening_is_bracketed_too():
    machine = ConversationStateMachine()
    assert EventType.USER_SPEECH_STARTED in events(machine.enter(ConversationState.LISTENING))
    assert EventType.USER_SPEECH_ENDED in events(machine.enter(ConversationState.THINKING))


def test_a_full_turn_produces_a_coherent_sequence():
    machine = ConversationStateMachine()
    emitted: list[str] = []
    for state in (
        ConversationState.LISTENING,
        ConversationState.THINKING,
        ConversationState.SPEAKING,
        ConversationState.SETTLING,
        ConversationState.ATTENTIVE,
    ):
        emitted += events(machine.enter(state))

    assert emitted.index(EventType.USER_SPEECH_STARTED) < emitted.index(
        EventType.USER_SPEECH_ENDED
    )
    assert emitted.index(EventType.ASSISTANT_THINKING_STARTED) < emitted.index(
        EventType.ASSISTANT_THINKING_ENDED
    )
    assert machine.history[0] is ConversationState.IDLE


# --------------------------------------------------------------------------- #
# Scripted client
# --------------------------------------------------------------------------- #


async def test_the_scripted_client_streams_word_by_word():
    """All at once would hide whether segmentation and T3 behave."""
    client = ScriptedClient(latency=0.0, word_delay=0.0)
    turn = client.start_turn([])
    chunks = [chunk async for chunk in turn]

    assert len(chunks) > 5
    assert turn.text == "".join(chunks)
    assert turn.stop_reason == "end_turn"


async def test_it_marks_the_same_latency_stages_as_the_real_client():
    client = ScriptedClient(latency=0.0, word_delay=0.0)
    turn = client.start_turn([])
    async for _ in turn:
        pass

    assert Stage.REQUEST_SENT in turn.metrics.marks
    assert Stage.FIRST_TOKEN in turn.metrics.marks


async def test_it_can_be_cancelled_like_the_real_one():
    client = ScriptedClient(latency=0.0, word_delay=0.01)
    turn = client.start_turn([])

    received = []
    async for chunk in turn:
        received.append(chunk)
        if len(received) == 2:
            turn.cancel()

    assert turn.cancelled
    assert turn.metrics.interrupted is True
    assert len(turn.text) < len(client.replies[0])


async def test_replies_cycle_so_a_session_does_not_repeat_immediately():
    client = ScriptedClient(latency=0.0, word_delay=0.0)
    seen = []
    for _ in range(3):
        turn = client.start_turn([])
        async for _chunk in turn:
            pass
        seen.append(turn.text)
    assert len(set(seen)) == 3


async def test_the_stand_in_matches_the_real_turn_interface():
    """It is only useful if it can be swapped in without the caller noticing,
    so compare real instances rather than the classes -- most of the surface is
    set in __init__ and would pass a class-level check vacuously."""
    from amanda.claude.client import StreamedTurn
    from amanda.runtime.metrics import TurnMetrics

    real = StreamedTurn(request={}, opener=lambda **_: None, metrics=TurnMetrics())
    stand_in = ScriptedClient(latency=0.0).start_turn([])

    for attribute in ("text", "cancel", "metrics", "cancelled", "stop_reason", "refusal"):
        assert hasattr(real, attribute), f"StreamedTurn lost {attribute}"
        assert hasattr(stand_in, attribute), f"ScriptedTurn is missing {attribute}"
    assert isinstance(stand_in, ScriptedTurn)


async def test_it_reports_time_to_first_token():
    """The gap the thinking animation has to cover -- pretending it is zero
    would make that animation look unnecessary."""
    client = ScriptedClient(latency=0.05, word_delay=0.0)
    turn = client.start_turn([])
    async for _ in turn:
        pass

    elapsed = turn.metrics.elapsed_ms(Stage.REQUEST_SENT, Stage.FIRST_TOKEN)
    assert elapsed is not None and elapsed >= 40


async def test_a_turn_not_iterated_sends_nothing():
    client = ScriptedClient()
    client.start_turn([])
    await asyncio.sleep(0)

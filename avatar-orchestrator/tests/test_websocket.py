"""Bridge behaviour tests.

Mostly integration: a real server, a real client over loopback. The policy
decisions worth pinning down are resync-on-connect, never replaying speech, and
what happens when the renderer stops keeping up.
"""

from __future__ import annotations

import asyncio

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from amanda.avatar import protocol as p
from amanda.avatar.websocket import AvatarBridge, _Client

RECV_TIMEOUT = 2.0


@pytest.fixture
async def bridge():
    """A bridge on an ephemeral port, cleanly shut down afterwards."""
    instance = AvatarBridge(port=0)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


async def renderer(bridge: AvatarBridge):
    """A client connection standing in for Unreal."""
    return connect(f"ws://{bridge.host}:{bridge.port}")


async def recv(connection, count: int = 1) -> list[p.Envelope]:
    envelopes = []
    for _ in range(count):
        raw = await asyncio.wait_for(connection.recv(), RECV_TIMEOUT)
        envelopes.append(p.decode(raw))
    return envelopes


async def settle() -> None:
    """Let the server's connection handler run before asserting on state."""
    for _ in range(5):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# Connecting
# --------------------------------------------------------------------------- #


async def test_bridge_binds_an_ephemeral_port(bridge):
    assert bridge.port > 0
    assert bridge.host == "127.0.0.1", "the bridge must not leave loopback by default"


async def test_a_fresh_renderer_is_reset_before_anything_else(bridge):
    async with await renderer(bridge) as connection:
        (envelope,) = await recv(connection)
        assert envelope.event == p.EventType.AVATAR_RESET


async def test_events_reach_every_connected_renderer(bridge):
    async with await renderer(bridge) as first, await renderer(bridge) as second:
        await recv(first)  # avatar.reset
        await recv(second)
        await settle()
        assert bridge.client_count == 2

        bridge.send(p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.2))

        for connection in (first, second):
            (envelope,) = await recv(connection)
            assert envelope.parse() == p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.2)


async def test_wait_for_client_returns_when_a_renderer_arrives(bridge):
    waiting = asyncio.create_task(bridge.wait_for_client(timeout=RECV_TIMEOUT))
    async with await renderer(bridge):
        assert await waiting is True


async def test_wait_for_client_times_out_without_one(bridge):
    assert await bridge.wait_for_client(timeout=0.05) is False


async def test_disconnecting_removes_the_client(bridge):
    async with await renderer(bridge) as connection:
        await recv(connection)
        await settle()
        assert bridge.client_count == 1
    for _ in range(50):
        await settle()
        if bridge.client_count == 0:
            break
    assert bridge.client_count == 0


# --------------------------------------------------------------------------- #
# Sending without a renderer
# --------------------------------------------------------------------------- #


async def test_sending_with_no_renderer_is_silent(bridge):
    """The conversation runs whether or not anything is drawing it."""
    bridge.send(p.AssistantThinkingStarted())
    assert bridge.stats.messages_dropped == 1
    assert bridge.stats.messages_sent == 0


# --------------------------------------------------------------------------- #
# Resync
# --------------------------------------------------------------------------- #


async def test_a_reconnecting_renderer_is_resynced_to_current_state(bridge):
    """Unreal restarts mid-conversation and must not sit in neutral while the
    conversation has moved on."""
    performance = p.PerformanceUpdate(preset=p.Preset.CONSIDERING, intensity=0.28)
    gaze = p.GazeSetTarget(target=p.GazeTarget.SLIGHTLY_RIGHT, hold_ms=900)

    bridge.send(p.SessionStarted(session_id="s_1"))
    bridge.send(performance)
    bridge.send(gaze)

    async with await renderer(bridge) as connection:
        envelopes = await recv(connection, 4)

    assert [envelope.event for envelope in envelopes] == [
        p.EventType.AVATAR_RESET,
        p.EventType.SESSION_STARTED,
        p.EventType.PERFORMANCE_UPDATE,
        p.EventType.GAZE_SET_TARGET,
    ]
    assert envelopes[2].parse() == performance
    assert envelopes[3].parse() == gaze


async def test_resync_carries_only_the_latest_state(bridge):
    bridge.send(p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.2))
    bridge.send(p.PerformanceUpdate(preset=p.Preset.SERIOUS, intensity=0.4))

    async with await renderer(bridge) as connection:
        envelopes = await recv(connection, 2)

    assert envelopes[1].parse() == p.PerformanceUpdate(preset=p.Preset.SERIOUS, intensity=0.4)


async def test_speech_is_never_replayed_to_a_reconnecting_renderer(bridge):
    """Resuming lip-sync for audio that has already played would be worse than
    missing the utterance entirely."""
    bridge.send(p.SpeechPrepare(utterance_id="u_1"))
    bridge.send(p.SpeechStarted(utterance_id="u_1"))
    bridge.send(p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.2))

    async with await renderer(bridge) as connection:
        envelopes = await recv(connection, 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(connection.recv(), 0.1)

    assert [envelope.event for envelope in envelopes] == [
        p.EventType.AVATAR_RESET,
        p.EventType.PERFORMANCE_UPDATE,
    ]


async def test_one_shot_events_are_not_replayed(bridge):
    for payload in (
        p.UserSpeechStarted(),
        p.AssistantThinkingStarted(),
        p.GestureTrigger(gesture="small_nod"),
        p.UserDetected(present=True),
    ):
        bridge.send(payload)

    async with await renderer(bridge) as connection:
        (envelope,) = await recv(connection)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(connection.recv(), 0.1)

    assert envelope.event == p.EventType.AVATAR_RESET


# --------------------------------------------------------------------------- #
# Inbound traffic
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        '{"version": 99, "event": "renderer.stats"}',
        '{"version": 1, "event": "renderer.stats", "payload": {"fps": 61.2}}',
    ],
)
async def test_renderer_chatter_is_ignored_not_fatal(bridge, message):
    """Protocol v1 is one-directional. A renderer sending anything -- telemetry,
    or a newer event this build predates -- must not lose its connection."""
    async with await renderer(bridge) as connection:
        await recv(connection)
        await connection.send(message)
        await settle()

        bridge.send(p.AvatarReset())
        (envelope,) = await recv(connection)
        assert envelope.event == p.EventType.AVATAR_RESET
    assert bridge.stats.inbound_ignored == 1


# --------------------------------------------------------------------------- #
# Backpressure
# --------------------------------------------------------------------------- #


class _StalledConnection:
    """Stands in for a renderer that has stopped reading."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def send(self, message: str) -> None:  # pragma: no cover - never drained
        await asyncio.sleep(3600)


async def test_a_renderer_that_stops_keeping_up_is_dropped(bridge):
    """Dropping individual messages would silently desynchronise the renderer --
    a lost speech.completed leaves the avatar stuck talking. Drop the client and
    let its reconnect resync it instead."""
    connection = _StalledConnection()
    client = _Client(id=99, connection=connection, queue=asyncio.Queue(1))
    bridge._clients[client.id] = client

    bridge.send(p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.1))  # fills the queue
    bridge.send(p.PerformanceUpdate(preset=p.Preset.WARM, intensity=0.2))  # overflows

    assert bridge.stats.clients_dropped_for_backpressure == 1
    assert client.dropping is True
    await settle()
    assert connection.closed is True


async def test_a_client_already_being_dropped_is_skipped(bridge):
    connection = _StalledConnection()
    client = _Client(id=99, connection=connection, queue=asyncio.Queue(1), dropping=True)
    bridge._clients[client.id] = client

    bridge.send(p.AvatarReset())

    assert client.queue.empty()
    assert bridge.stats.clients_dropped_for_backpressure == 0


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #


async def test_stop_is_idempotent(bridge):
    await bridge.stop()
    await bridge.stop()


async def test_starting_twice_is_an_error(bridge):
    with pytest.raises(RuntimeError):
        await bridge.start()


async def test_stop_closes_connected_renderers():
    instance = AvatarBridge(port=0)
    await instance.start()
    async with await renderer(instance) as connection:
        await recv(connection)
        await instance.stop()
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(connection.recv(), RECV_TIMEOUT)
    assert instance.client_count == 0


async def test_context_manager_starts_and_stops():
    async with AvatarBridge(port=0) as instance:
        assert instance.port > 0
    assert instance.client_count == 0

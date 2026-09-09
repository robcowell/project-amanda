"""Local WebSocket bridge carrying protocol v1 to the renderer.

The orchestrator is the server and the renderer is the client. That way round
because the conversation outlives the renderer: Unreal can be restarted, crash
or be attached to a debugger mid-conversation without taking Claude, the
microphone or the conversation state down with it.

Bound to loopback by default (build plan 25). Nothing here should ever be
reachable from the network.

Three properties matter more than throughput:

  * **Sending never blocks the conversation.** `send` is a plain method, not a
    coroutine, and it only enqueues. A wedged renderer must not be able to
    stall the turn.
  * **A reconnecting renderer is resynchronised.** It is sent `avatar.reset`
    and then the current performance and gaze state, so it neither inherits a
    mood from a dead connection nor sits in neutral while the conversation has
    moved on.
  * **Speech is never replayed.** A renderer that reconnects mid-utterance must
    not resume lip-syncing audio that has already played.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from amanda.avatar.protocol import (
    AvatarReset,
    Envelope,
    GazeSetTarget,
    Payload,
    PerformanceUpdate,
    ProtocolError,
    SessionStarted,
    decode,
    encode,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Deep enough to absorb a burst of gaze and performance updates, shallow enough
# that a wedged renderer is noticed in well under a second rather than
# accumulating minutes of stale animation.
DEFAULT_QUEUE_SIZE = 64


@dataclass(slots=True)
class _Client:
    id: int
    connection: ServerConnection
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(DEFAULT_QUEUE_SIZE))
    dropping: bool = False

    @property
    def label(self) -> str:
        return f"renderer#{self.id}"


@dataclass(slots=True)
class BridgeStats:
    clients_connected: int = 0
    messages_sent: int = 0
    messages_dropped: int = 0
    clients_dropped_for_backpressure: int = 0
    inbound_ignored: int = 0


class AvatarBridge:
    """Serves protocol v1 to any connected renderer.

    Usage:

        async with AvatarBridge() as bridge:
            bridge.send(PerformanceUpdate(preset=Preset.WARM, intensity=0.2))

    `send` must be called from the thread running the bridge's event loop.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        self.host = host
        self._requested_port = port
        self._queue_size = queue_size

        self._server = None
        self._clients: dict[int, _Client] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._next_client_id = 1
        self._client_arrived = asyncio.Event()

        # The resync snapshot. Deliberately only the *standing* state: what the
        # avatar should look like right now with no conversation in flight.
        self._session: SessionStarted | None = None
        self._performance: PerformanceUpdate | None = None
        self._gaze: GazeSetTarget | None = None

        self.stats = BridgeStats()

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    @property
    def port(self) -> int:
        """The bound port, which differs from the requested one when 0 was
        asked for. Reads back as the requested port before `start`."""
        if self._server is None or not self._server.sockets:
            return self._requested_port
        return self._server.sockets[0].getsockname()[1]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("bridge already started")
        self._server = await serve(self._handle, self.host, self._requested_port)
        log.info("avatar bridge listening on ws://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Close the listener and every client, and wait for the writers."""
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

        for client in list(self._clients.values()):
            with contextlib.suppress(Exception):
                await client.connection.close()

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._clients.clear()
        log.info("avatar bridge stopped")

    async def __aenter__(self) -> "AvatarBridge":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def wait_for_client(self, timeout: float | None = None) -> bool:
        """Block until at least one renderer is connected.

        Useful at startup so the first `session.started` is not sent into the
        void, but the orchestrator should not *require* a renderer to run.
        """
        if self._clients:
            return True
        try:
            await asyncio.wait_for(self._client_arrived.wait(), timeout)
        except TimeoutError:
            return False
        return True

    # ----------------------------------------------------------------- #
    # Sending
    # ----------------------------------------------------------------- #

    def send(self, payload: Payload) -> None:
        """Queue one event for every connected renderer.

        Never blocks and never raises. With no renderer attached the event is
        counted and discarded -- the conversation carries on regardless of
        whether anything is drawing it.
        """
        self._remember(payload)
        if not self._clients:
            self.stats.messages_dropped += 1
            return
        self._enqueue(self._clients.values(), encode(payload))

    def send_all(self, payloads: Iterable[Payload]) -> None:
        for payload in payloads:
            self.send(payload)

    def _enqueue(self, clients: Iterable[_Client], message: str) -> None:
        for client in clients:
            if client.dropping:
                continue
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Dropping individual messages would silently desynchronise the
                # renderer -- losing a speech.completed leaves the avatar stuck
                # talking. Drop the *client* instead and let its reconnect
                # resynchronise it from a known state.
                log.warning("%s is not keeping up; dropping it to force a resync", client.label)
                self.stats.clients_dropped_for_backpressure += 1
                self._drop(client)
            else:
                self.stats.messages_sent += 1

    def _remember(self, payload: Payload) -> None:
        """Update the resync snapshot.

        Speech and one-shot events are excluded on purpose: they describe
        something happening, not a state to be restored.
        """
        if isinstance(payload, PerformanceUpdate):
            self._performance = payload
        elif isinstance(payload, GazeSetTarget):
            self._gaze = payload
        elif isinstance(payload, SessionStarted):
            self._session = payload

    def _resync(self) -> list[Payload]:
        """What a newly connected renderer needs to be current.

        `avatar.reset` leads so the renderer starts from neutral rather than
        whatever it was left showing.
        """
        payloads: list[Payload] = [AvatarReset()]
        if self._session is not None:
            payloads.append(self._session)
        if self._performance is not None:
            payloads.append(self._performance)
        if self._gaze is not None:
            payloads.append(self._gaze)
        return payloads

    # ----------------------------------------------------------------- #
    # Connection handling
    # ----------------------------------------------------------------- #

    async def _handle(self, connection: ServerConnection) -> None:
        client = _Client(
            id=self._next_client_id,
            connection=connection,
            queue=asyncio.Queue(self._queue_size),
        )
        self._next_client_id += 1
        self._clients[client.id] = client
        self.stats.clients_connected += 1
        log.info("%s connected", client.label)

        for payload in self._resync():
            client.queue.put_nowait(encode(payload))

        writer = asyncio.create_task(self._writer(client), name=f"bridge-writer-{client.id}")
        self._tasks.add(writer)
        writer.add_done_callback(self._tasks.discard)

        self._client_arrived.set()

        try:
            async for message in connection:
                self._on_inbound(client, message)
        except ConnectionClosed:
            pass
        finally:
            self._clients.pop(client.id, None)
            if not self._clients:
                self._client_arrived.clear()
            writer.cancel()
            log.info("%s disconnected", client.label)

    async def _writer(self, client: _Client) -> None:
        try:
            while True:
                message = await client.queue.get()
                await client.connection.send(message)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("%s writer failed", client.label)

    def _on_inbound(self, client: _Client, message: str | bytes) -> None:
        """Protocol v1 is one-directional, so anything inbound is logged and
        ignored -- never fatal. A renderer sending its own telemetry should not
        be disconnected for it, and adding a renderer-to-orchestrator event
        later is a protocol change, not something to guess at here."""
        self.stats.inbound_ignored += 1
        try:
            envelope: Envelope = decode(message)
        except ProtocolError as exc:
            log.debug("%s sent an unreadable message: %s", client.label, exc)
            return
        log.debug("%s sent %r, ignored", client.label, envelope.event)

    def _drop(self, client: _Client) -> None:
        client.dropping = True
        task = asyncio.create_task(self._close(client), name=f"bridge-drop-{client.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _close(self, client: _Client) -> None:
        with contextlib.suppress(Exception):
            await client.connection.close()

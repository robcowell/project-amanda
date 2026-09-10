"""The conversation loop.

Phase 1 was typed input to a spoken, animated reply (§26). Phase 2 adds the
microphone, and the only thing that changes is where turns come from — the
state machine, the speech pipeline and the bridge are the same either way. See
`runtime/input.py` for why that abstraction exists.

    python3 tools/previz.py               # in another terminal
    python3 -m amanda.main                # typed
    python3 -m amanda.main --voice        # spoken

Everything downstream of the model degrades rather than failing: no credentials
means canned replies, no voice model means a stand-in, no microphone means
typed input. A machine with none of them still runs the whole pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import uuid

from amanda.audio.engines import ENGINES, build, describe
from amanda.audio.microphone import Microphone
from amanda.audio.sink import DeviceSink, NullSink
from amanda.audio.speech import SpeechSession
from amanda.audio.stt import build as build_recognizer
from amanda.audio.wake import build as build_wake
from amanda.avatar.protocol import CancelReason, SessionEnded, SessionStarted, UserDetected
from amanda.avatar.websocket import DEFAULT_HOST, DEFAULT_PORT, AvatarBridge
from amanda.claude.conversation import Conversation
from amanda.claude.scripted import ScriptedClient
from amanda.claude.segmenter import PhraseSegmenter
from amanda.config import load_env
from amanda.performance.director import MIN_REPLY_CHARS, Director
from amanda.performance.director import build as build_director
from amanda.runtime.input import ConversationInput, TypedInput, UserTurn, VoiceInput
from amanda.runtime.metrics import Stage
from amanda.runtime.state_machine import ConversationState, ConversationStateMachine

log = logging.getLogger("amanda")


def build_client(args: argparse.Namespace):
    """Claude when there are credentials, canned replies otherwise."""
    if args.scripted:
        return ScriptedClient(), "scripted"

    try:
        from amanda.claude.client import ClaudeClient
        from amanda.claude.prompts import CONVERSATION_SYSTEM

        overrides = {
            key: value
            for key, value in (("model", args.model), ("effort", args.effort))
            if value is not None
        }
        client = ClaudeClient.from_config(overrides, system=CONVERSATION_SYSTEM)
    except Exception as exc:  # noqa: BLE001 - config or import problem
        print(f"  (could not build a Claude client: {exc}; using scripted replies)")
        return ScriptedClient(), "scripted"

    # Checked here rather than caught later: the SDK constructs happily without
    # a credential and only fails when a request is made, which is after the
    # user has spoken and waited.
    if not client.has_credentials:
        print(
            "  no Anthropic credentials found. Set ANTHROPIC_API_KEY, or run\n"
            "  `ant auth login`. Using scripted replies for now.\n"
        )
        return ScriptedClient(), "scripted"

    return client, client.settings.model


class Session:
    """One run: a bridge, an input source, and a turn loop."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bridge = AvatarBridge(args.host, args.port)
        self.states = ConversationStateMachine()
        self.conversation = Conversation()
        self.client, self.model = build_client(args)
        self.synthesizer, self.voice = build(
            args.engine, voice_id=args.voice_id, sample_rate=args.rate
        )
        self.director: Director = self._build_director()
        self.input: ConversationInput = self._build_input()
        self.session_id = f"s_{uuid.uuid4().hex[:8]}"
        #: The in-flight classification, if any. One turn at a time, so one task.
        self.direction: asyncio.Task[None] | None = None

    def _build_director(self) -> Director:
        # The SDK client is shared with the conversational one so the classifier
        # reuses an open connection rather than paying a handshake in the middle
        # of an utterance. Scripted replies get a scripted director: a run with
        # no key should still show the avatar changing expression.
        name = self.args.director or ("scripted" if self.args.scripted else None)
        return build_director(name, client=getattr(self.client, "client", None))

    def _build_input(self) -> ConversationInput:
        if not self.args.voice:
            return TypedInput()
        from amanda.config import wake_settings

        return VoiceInput(
            microphone=Microphone(device=self.args.input_device),
            recognizer=build_recognizer(self.args.stt),
            wake=build_wake(self.args.wake),
            awake_seconds=float(wake_settings().get("awake_seconds", 45.0)),
        )

    # ----------------------------------------------------------------- #

    def enter(self, state: ConversationState) -> None:
        for payload in self.states.enter(state):
            self.bridge.send(payload)

    def make_sink(self):
        if self.args.no_audio:
            # Paced, so an interruption still lands partway through rather than
            # after an utterance that "finished" the moment it was synthesised.
            return NullSink(realtime=True)
        return DeviceSink(device=self.args.device)

    async def run(self) -> int:
        async with self.bridge:
            print(f"bridge on ws://{self.bridge.host}:{self.bridge.port}")
            print(
                f"model {self.model}, voice via {self.synthesizer.name} "
                f"at {self.voice.sample_rate} Hz"
            )
            print(f"performance director: {self.director.name}")

            warm = getattr(self.synthesizer, "warm", None)
            if warm is not None:
                print("warming the voice model...", end="", flush=True)
                await warm()
                print(" ready")

            # Pays the classifier's one-time schema compilation before the first
            # turn rather than during it.
            await self.director.warm()

            try:
                await self.input.start()
            except Exception as exc:  # noqa: BLE001 - fall back rather than fail
                print(f"  (could not open the microphone: {exc}; falling back to typing)")
                self.args.voice = False
                self.input = TypedInput()
                await self.input.start()

            if self.args.voice:
                detector = getattr(self.input, "wake", None)
                if detector is not None and not detector.always_awake:
                    print(f"say the wake word ({detector.name}), then speak")
                else:
                    print("speak, then pause -- listening to everything")
            else:
                print("type a message; type again while it speaks to interrupt")
            print("ctrl-d to quit\n")

            self.bridge.send(SessionStarted(session_id=self.session_id))
            self.bridge.send(UserDetected(present=True))
            self.enter(ConversationState.ATTENTIVE)

            try:
                await self.loop()
            finally:
                await self.input.stop()
                self.bridge.send(SessionEnded(session_id=self.session_id, reason="quit"))
                await asyncio.sleep(0.05)
        return 0

    async def loop(self) -> None:
        while True:
            if not self.args.voice:
                print("> ", end="", flush=True)

            # Asleep is a state the renderer should show: gaze off, settled,
            # not tracking anybody. Waking is what ATTENTIVE is for.
            awake = getattr(self.input, "awake", True)
            self.enter(ConversationState.LISTENING if awake else ConversationState.IDLE)
            turn = await self.input.next_turn()
            if turn is None:
                print()
                return

            if self.args.voice:
                print(f"> {turn.text}")
            await self.turn(turn)

    async def turn(self, user: UserTurn) -> None:
        """One exchange, from what the user said to the avatar settling."""
        self.conversation.user(user.text)
        stream = self.client.start_turn(self.conversation.messages())
        metrics = stream.metrics
        metrics.mark_at(Stage.USER_SPEECH_ENDED, user.ended_at)
        metrics.mark_at(Stage.TRANSCRIPT_FINAL, user.ready_at)

        self.enter(ConversationState.THINKING)

        segmenter = PhraseSegmenter()
        session = SpeechSession(
            utterance_id=f"u_{uuid.uuid4().hex[:6]}",
            synthesizer=self.synthesizer,
            sink=self.make_sink(),
            voice=self.voice,
            metrics=metrics,
            emit=self.bridge.send,
            fade_ms=self.args.fade,
        )
        await session.start()

        # Barge-in is only barge-in once there is something to barge into.
        # Arming before the first phrase would let a sound during the thinking
        # pause cancel an utterance that had not started.
        started = asyncio.Event()
        self.direction = None
        speaking = asyncio.create_task(
            self.speak(stream, segmenter, session, started, user), name="turn"
        )
        interrupt = asyncio.create_task(self.watch_interrupt(started), name="interrupt")

        done, _ = await asyncio.wait({speaking, interrupt}, return_when=asyncio.FIRST_COMPLETED)

        if interrupt in done:
            await self.interrupted(stream, segmenter, session, speaking, metrics)
            return

        interrupt.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await interrupt

        # The utterance is over, so a direction arriving now would land on a
        # face that is already settling back to rest.
        await self.stop_direction()

        try:
            # asyncio.wait does not raise, so without this a failed turn shows
            # up only as "Task exception was never retrieved" and the loop
            # carries on as though nothing had happened.
            speaking.result()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            await self.report_failure(session, exc)
            return

        self.conversation.assistant(stream.text)
        if stream.refusal is not None:
            print(f"\n  [declined: {stream.refusal.category}]")

        # SETTLING is a state, not a formality: entering ATTENTIVE in the same
        # breath overrides the settle transition before any of it is drawn.
        self.enter(ConversationState.SETTLING)
        self.report(metrics)
        await asyncio.sleep(self.args.settle / 1000)
        self.enter(ConversationState.ATTENTIVE)

    async def interrupted(self, stream, segmenter, session, speaking, metrics) -> None:
        """Barge-in. Ordering matters and is the build plan's (§13).

        The renderer has already been told by `SpeechSession.cancel`, which
        sends speech.cancelled before it fades the audio -- the visual
        transition leads, because that is what makes an interruption feel like
        being interrupted.
        """
        stream.cancel()
        await session.cancel(CancelReason.BARGE_IN)
        await self.stop_direction()
        speaking.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await speaking

        print(f"\n  [interrupted after {session.result.spoken_ms}ms]")
        # What was spoken, not what was generated: the tail was cancelled before
        # synthesis, so as far as the conversation is concerned it was never said.
        self.conversation.assistant_interrupted(segmenter.spoken)
        self.enter(ConversationState.LISTENING)
        self.report(metrics)

    async def watch_interrupt(self, started: asyncio.Event) -> None:
        await started.wait()
        await self.input.wait_for_barge_in()

    async def speak(self, stream, segmenter, session, started: asyncio.Event, user) -> None:
        def begin() -> None:
            if started.is_set():
                return
            if session.metrics is not None:
                session.metrics.mark(Stage.FIRST_PHRASE)
            self.enter(ConversationState.SPEAKING)
            started.set()
            print()

        async for chunk in stream:
            for phrase in segmenter.feed(chunk):
                begin()
                print(f"  {phrase}")
                await session.add(phrase)
            self.consider_direction(user, stream, started)

        if tail := segmenter.flush():
            begin()
            print(f"  {tail}")
            await session.add(tail)

        # Nothing more is coming, so classify what there is however short it is.
        self.consider_direction(user, stream, started, final=True)

        session.close_input()
        await session.wait()

    # ----------------------------------------------------------------- #

    def consider_direction(self, user, stream, started, final: bool = False) -> None:
        """Start the classification once, as soon as it is worth starting.

        Two conditions, and both are about not fighting something else. Speech
        must have begun, or the direction would override the THINKING envelope
        while the avatar is still visibly considering. And enough of the reply
        must exist to tell one delivery from another -- see MIN_REPLY_CHARS.
        """
        if self.direction is not None or not started.is_set():
            return
        if not final and len(stream.text) < MIN_REPLY_CHARS:
            return
        self.direction = asyncio.create_task(self.direct(user.text, stream.text), name="director")

    async def direct(self, user_text: str, reply: str) -> None:
        """Classify the delivery while the avatar is already speaking.

        Sends straight to the bridge rather than returning a value, because the
        point is that it lands the moment it arrives -- part-way through the
        utterance, where the renderer's transition makes it read as an
        expression settling in rather than a mask being swapped.
        """
        try:
            update = await self.director.direct(user_text, reply)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a classifier must never end a turn
            log.debug("performance director failed", exc_info=True)
            return
        if update is None:
            return
        print(f"  [{update.preset.value} {update.intensity:.2f}]")
        self.bridge.send(update)

    async def stop_direction(self) -> None:
        if self.direction is not None:
            self.direction.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.direction
            self.direction = None

    async def report_failure(self, session: SpeechSession, exc: BaseException) -> None:
        """One bad turn should not end the conversation, but the renderer must
        not be left mid-utterance either."""
        if session.announced:
            await session.cancel(CancelReason.ERROR)
        print(f"\n  [turn failed: {type(exc).__name__}: {exc}]")
        log.debug("turn failed", exc_info=exc)
        self.enter(ConversationState.ATTENTIVE)

    def report(self, metrics) -> None:
        record = metrics.as_dict()
        parts = [f"{key}={value}" for key, value in record.items() if key.endswith("_ms")]
        print(f"  [{'  '.join(parts)}]\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", action="store_true", help="listen on the microphone")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scripted", action="store_true", help="canned replies, no API call")
    parser.add_argument("--model", help="overrides claude.model in config/avatar.yaml")
    parser.add_argument("--effort", help="overrides claude.effort in config/avatar.yaml")
    parser.add_argument("--stt", help="whisper, scripted, or auto (the default)")
    parser.add_argument(
        "--wake", help="openwakeword, porcupine, or none to listen to everything"
    )
    parser.add_argument(
        "--director",
        help="performance classifier: claude, scripted, none, or auto (the default)",
    )
    parser.add_argument("--input-device", help="microphone, by index or name fragment")
    parser.add_argument(
        "--engine", default="auto", choices=["auto", *sorted(ENGINES)],
        help="; ".join(describe()),
    )
    parser.add_argument("--voice-id", help="engine-specific voice id or model path")
    parser.add_argument("--device", help="output device, by index or name fragment")
    parser.add_argument("--no-audio", action="store_true", help="run silently")
    parser.add_argument("--rate", type=int, help="override the engine's native sample rate")
    parser.add_argument("--fade", type=int, default=80)
    parser.add_argument(
        "--settle", type=int, default=700, metavar="MS",
        help="how long the avatar takes to come to rest after speaking",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    # Before anything reads credentials. Names only in the message -- this file
    # holds an API key.
    if loaded := load_env():
        print(f"loaded {', '.join(loaded)} from .env")

    with contextlib.suppress(KeyboardInterrupt, EOFError):
        return asyncio.run(Session(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

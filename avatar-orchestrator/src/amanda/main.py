"""Phase 1: type a message, hear a spoken reply, watch the avatar deliver it.

This is the build plan's shortest useful path (section 26) -- typed input rather
than a microphone, so the first experiment answers the question that matters
without STT and voice activity detection in the way:

    text input -> Claude -> TTS -> bridge -> avatar speech

Run it alongside the previsualiser and the face is driven by the same protocol
Unreal will eventually receive:

    python3 tools/previz.py            # then open http://127.0.0.1:8766/previz.html
    python3 -m amanda.main             # this, in another terminal

Typing while the avatar is speaking interrupts it, which is the barge-in path
from build plan 13 exercised by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import uuid

from amanda.audio.engines import ENGINES, build, describe
from amanda.audio.sink import DeviceSink, NullSink
from amanda.audio.speech import SpeechSession
from amanda.avatar.protocol import CancelReason, SessionEnded, SessionStarted, UserDetected
from amanda.avatar.websocket import DEFAULT_HOST, DEFAULT_PORT, AvatarBridge
from amanda.claude.conversation import Conversation
from amanda.claude.scripted import ScriptedClient
from amanda.claude.segmenter import PhraseSegmenter
from amanda.runtime.metrics import Stage
from amanda.runtime.state_machine import ConversationState, ConversationStateMachine

log = logging.getLogger("amanda")

def build_client(args: argparse.Namespace):
    """Claude when there are credentials, canned replies otherwise.

    Falling back rather than failing is deliberate: everything downstream of the
    model -- segmentation, synthesis, playback, the protocol, the latency marks
    -- is worth exercising on a machine that has no key.
    """
    if args.scripted:
        return ScriptedClient(), "scripted"

    try:
        from amanda.claude.client import ClaudeClient, ClaudeSettings
        from amanda.claude.prompts import CONVERSATION_SYSTEM

        settings = ClaudeSettings(model=args.model, effort=args.effort)
        return ClaudeClient(settings=settings, system=CONVERSATION_SYSTEM), args.model
    except Exception as exc:  # noqa: BLE001 - any credential or import problem
        print(f"  (no Claude client: {exc}; falling back to scripted replies)")
        return ScriptedClient(), "scripted"


class Session:
    """One run of the demo: a bridge, a conversation, and a turn loop."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bridge = AvatarBridge(args.host, args.port)
        self.states = ConversationStateMachine()
        self.conversation = Conversation()
        self.client, self.model = build_client(args)
        # The sample rate follows the engine unless overridden: a mismatch is
        # refused rather than resampled, so the default has to be right.
        self.synthesizer, self.voice = build(
            args.engine, voice_id=args.voice, sample_rate=args.rate
        )
        self.typed: asyncio.Queue[str] = asyncio.Queue()
        self.session_id = f"s_{uuid.uuid4().hex[:8]}"
        self.eof = False

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

    async def read_stdin(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                await self.typed.put("")
                return
            await self.typed.put(line.strip())

    # ----------------------------------------------------------------- #

    async def run(self) -> int:
        async with self.bridge:
            print(f"bridge on ws://{self.bridge.host}:{self.bridge.port}")
            print(
            f"model {self.model}, voice via {self.synthesizer.name} "
            f"at {self.voice.sample_rate} Hz"
        )
            print("type a message; type again while it speaks to interrupt; ctrl-d to quit\n")

            # Neural engines load a model that takes seconds. Paying that on
            # the first phrase of the first turn would put it straight into the
            # latency budget; paying it here costs nobody anything.
            warm = getattr(self.synthesizer, "warm", None)
            if warm is not None:
                print("warming the voice model...", end="", flush=True)
                await warm()
                print(" ready\n")

            self.bridge.send(SessionStarted(session_id=self.session_id))
            self.bridge.send(UserDetected(present=True))
            self.enter(ConversationState.ATTENTIVE)

            reader = asyncio.create_task(self.read_stdin(), name="stdin")
            try:
                await self.loop()
            finally:
                reader.cancel()
                self.bridge.send(SessionEnded(session_id=self.session_id, reason="quit"))
                await asyncio.sleep(0.05)
        return 0

    async def loop(self) -> None:
        pending: str | None = None
        while True:
            if self.eof:
                print()
                return
            print("> ", end="", flush=True)
            message = pending if pending is not None else await self.typed.get()
            pending = None

            if not message:
                print()
                return
            if message.lower() in {"quit", "exit"}:
                return

            self.enter(ConversationState.LISTENING)
            pending = await self.turn(message)

    async def turn(self, message: str) -> str | None:
        """One exchange. Returns the interrupting message, if there was one."""
        self.conversation.user(message)
        turn = self.client.start_turn(self.conversation.messages())
        metrics = turn.metrics

        # Typed input, so T0 and T1 are the same instant -- there is no speech to
        # end and no transcript to finalise.
        metrics.mark(Stage.USER_SPEECH_ENDED)
        metrics.mark(Stage.TRANSCRIPT_FINAL)

        self.enter(ConversationState.THINKING)

        utterance_id = f"u_{uuid.uuid4().hex[:6]}"
        segmenter = PhraseSegmenter()
        session = SpeechSession(
            utterance_id=utterance_id,
            synthesizer=self.synthesizer,
            sink=self.make_sink(),
            voice=self.voice,
            metrics=metrics,
            emit=self.bridge.send,
            fade_ms=self.args.fade,
        )
        await session.start()

        # Barge-in is only barge-in once there is something to barge into. Arming
        # the watcher before the first phrase would let a line typed during the
        # thinking pause cancel an utterance that had not started.
        started = asyncio.Event()
        speaking = asyncio.create_task(
            self.speak(turn, segmenter, session, started), name="turn"
        )
        interrupt = asyncio.create_task(self.watch_interrupt(started), name="interrupt")

        done, _ = await asyncio.wait({speaking, interrupt}, return_when=asyncio.FIRST_COMPLETED)

        if interrupt in done:
            barge_in = interrupt.result()
            turn.cancel()
            await session.cancel(CancelReason.BARGE_IN)
            speaking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await speaking

            spoken = segmenter.spoken
            print(f"\n  [interrupted after {session.result.spoken_ms}ms]")
            self.conversation.assistant_interrupted(spoken)
            self.enter(ConversationState.LISTENING)
            self.conversation.user(barge_in)
            self.conversation.note_interruption()
            self.report(metrics)
            # The interrupting text is the next turn's message, so the user does
            # not have to say it twice.
            return barge_in or None

        interrupt.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await interrupt

        self.conversation.assistant(turn.text)
        if turn.refusal is not None:
            print(f"\n  [declined: {turn.refusal.category}]")
        # SETTLING is a state, not a formality. Entering ATTENTIVE in the same
        # breath overrides the settle transition before any of it is drawn, and
        # the renderer sees two near-identical performance updates instead of a
        # character coming to rest.
        self.enter(ConversationState.SETTLING)
        self.report(metrics)
        await asyncio.sleep(self.args.settle / 1000)
        self.enter(ConversationState.ATTENTIVE)
        return None

    async def watch_interrupt(self, started: asyncio.Event) -> str:
        """Wait for the user to say something over the top of the avatar.

        An empty line is not an interruption -- pressing Enter with nothing
        typed is not speech, and end of input means quit rather than barge in.
        Either way the utterance is allowed to finish.
        """
        await started.wait()
        while True:
            line = await self.typed.get()
            if line:
                return line
            self.eof = True
            await asyncio.Event().wait()  # never fires; the speech task wins

    async def speak(
        self,
        turn,
        segmenter: PhraseSegmenter,
        session: SpeechSession,
        started: asyncio.Event,
    ) -> None:
        def begin() -> None:
            if started.is_set():
                return
            if session.metrics is not None:
                session.metrics.mark(Stage.FIRST_PHRASE)
            self.enter(ConversationState.SPEAKING)
            started.set()
            print()

        async for chunk in turn:
            for phrase in segmenter.feed(chunk):
                begin()
                print(f"  {phrase}")
                await session.add(phrase)

        if tail := segmenter.flush():
            begin()
            print(f"  {tail}")
            await session.add(tail)

        session.close_input()
        await session.wait()

    def report(self, metrics) -> None:
        record = metrics.as_dict()
        parts = [f"{key}={value}" for key, value in record.items() if key.endswith("_ms")]
        print(f"  [{'  '.join(parts)}]\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scripted", action="store_true", help="canned replies, no API call")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort", default="low")
    parser.add_argument(
        "--engine", default="auto", choices=["auto", *sorted(ENGINES)],
        help="; ".join(describe()),
    )
    parser.add_argument("--voice", help="engine-specific voice id or model path")
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
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        return asyncio.run(Session(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

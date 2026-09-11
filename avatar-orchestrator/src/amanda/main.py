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

from amanda.audio import devices
from amanda.audio.engines import ENGINES, build, describe
from amanda.audio.microphone import Microphone, MicrophoneError
from amanda.audio.sink import DeviceSink, MonitorSink, NullSink
from amanda.audio.speech import SpeechSession
from amanda.audio.stt import build as build_recognizer
from amanda.audio.tts import SynthesisError
from amanda.audio.wake import build as build_wake
from amanda.avatar.protocol import CancelReason, SessionEnded, SessionStarted, UserDetected
from amanda.avatar.websocket import DEFAULT_HOST, DEFAULT_PORT, AvatarBridge
from amanda.claude.conversation import Conversation
from amanda.claude.scripted import ScriptedClient
from amanda.claude.segmenter import PhraseSegmenter
from amanda.config import cable_device, load_env
from amanda.performance.director import MIN_REPLY_CHARS, Director
from amanda.performance.director import build as build_director
from amanda.runtime.input import ConversationInput, TypedInput, UserTurn, VoiceInput
from amanda.runtime.metrics import SPANS, Stage
from amanda.runtime.state_machine import ConversationState, ConversationStateMachine
from amanda.runtime.telemetry import TurnLog

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
        #: Which microphone, which output to hear her on, and the cable --
        #: chosen now and re-chosen when devices come and go. See
        #: audio/devices.py.
        self.route = self._choose_route()
        self.watcher = devices.DeviceWatcher()
        #: Held for a whole turn and for a device switch, so a switch never
        #: restarts PortAudio under an open stream.
        self.audio_lock = asyncio.Lock()
        self.input: ConversationInput = self._build_input()
        self.session_id = f"s_{uuid.uuid4().hex[:8]}"
        self.telemetry = TurnLog.from_config(
            {"enabled": False} if args.no_telemetry else None
        )
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
            microphone=Microphone(device=self._microphone_device()),
            recognizer=build_recognizer(self.args.stt),
            wake=build_wake(self.args.wake),
            awake_seconds=float(wake_settings().get("awake_seconds", 45.0)),
        )

    def _choose_route(self, need_microphone: bool | None = None) -> devices.AudioRoute | None:
        """The devices for this moment. None when there is nothing to choose."""
        if self.args.no_audio and not self.args.voice:
            return None
        try:
            found, default_in, default_out = devices.current_devices()
        except Exception as exc:  # noqa: BLE001 - no PortAudio: typed and silent still run
            log.warning("could not list audio devices: %s", exc)
            return None
        return devices.choose_route(
            found,
            default_in,
            default_out,
            cable=self.args.device or cable_device(),
            require_cable=self.args.device is not None,
            microphone=self.args.input_device,
            listen=self.args.monitor,
            need_microphone=self.args.voice if need_microphone is None else need_microphone,
        )

    def _microphone_device(self) -> int | str | None:
        if self.route is not None and self.route.microphone is not None:
            return self.route.microphone.index
        return self.args.input_device

    def describe_route(self) -> str:
        route = self.route
        if route is None:
            return "  audio: default devices"
        lines = []
        if route.microphone is not None:
            lines.append(f"listening on {route.microphone.name}")
        if route.cable is None:
            lines.append(f"voice on {route.listen.name} (no virtual cable, so no Unreal lip sync)")
        elif self.args.no_monitor:
            lines.append(f"voice into {route.cable.name} for the face, not played aloud")
        else:
            lines.append(
                f"voice into {route.cable.name} for the face; heard on "
                f"{route.listen.name}, {self.args.monitor_delay}ms later"
            )
        return "\n".join(f"  {line}" for line in lines)

    # ----------------------------------------------------------------- #

    def enter(self, state: ConversationState) -> None:
        for payload in self.states.enter(state):
            self.bridge.send(payload)

    def make_sink(self):
        if self.args.no_audio:
            # Paced, so an interruption still lands partway through rather than
            # after an utterance that "finished" the moment it was synthesised.
            return NullSink(realtime=True)
        route = self.route
        if route is None:
            return DeviceSink(device=self.args.device)
        if route.cable is None:
            return DeviceSink(device=route.listen.index)
        sink = DeviceSink(device=route.cable.index)
        if self.args.no_monitor:
            return sink
        # The cable drives the face; the monitor is what a person hears, held
        # back to land with the mouth. See MonitorSink.
        return MonitorSink(
            primary=sink,
            monitor=DeviceSink(device=route.listen.index),
            delay_ms=self.args.monitor_delay,
        )

    async def run(self) -> int:
        async with self.bridge:
            print(f"bridge on ws://{self.bridge.host}:{self.bridge.port}")
            print(
                f"model {self.model}, voice via {self.synthesizer.name} "
                f"at {self.voice.sample_rate} Hz"
            )
            print(f"performance director: {self.director.name}")
            print(self.describe_route())

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

            self.telemetry.open()
            if self.telemetry.enabled:
                print(f"recording turns to {self.telemetry.path}")

            self.bridge.send(SessionStarted(session_id=self.session_id))
            self.bridge.send(UserDetected(present=True))
            self.enter(ConversationState.ATTENTIVE)

            follow = None
            if self.route is not None and await self.watcher.start():
                follow = asyncio.create_task(self.follow_devices(), name="devices")
            try:
                await self.loop()
            finally:
                if follow is not None:
                    follow.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await follow
                await self.watcher.stop()
                await self.input.stop()
                self.telemetry.close()
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
            async with self.audio_lock:
                await self.turn(turn)

    async def turn(self, user: UserTurn) -> None:
        """One exchange, from what the user said to the avatar settling."""
        self.conversation.user(user.text)
        stream = self.client.start_turn(self.conversation.messages())
        metrics = stream.metrics
        metrics.mark_at(Stage.USER_SPEECH_ENDED, user.ended_at)
        metrics.mark_at(Stage.TRANSCRIPT_FINAL, user.ready_at)
        # Here rather than in finish(): report() prints before finish() runs.
        metrics.stt_compute_ms = user.stt_compute_ms
        metrics.stt_temperature = user.stt_temperature
        if user.stt_compute_ms is not None:
            metrics.stt_speculative = user.stt_speculative

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
            self.finish(user, stream, session, metrics)
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
            metrics.failed = f"{type(exc).__name__}: {exc}"
            self.finish(user, stream, session, metrics)
            return

        self.conversation.assistant(stream.text)
        if stream.refusal is not None:
            print(f"\n  [declined: {stream.refusal.category}]")

        # SETTLING is a state, not a formality: entering ATTENTIVE in the same
        # breath overrides the settle transition before any of it is drawn.
        self.enter(ConversationState.SETTLING)
        self.report(metrics)
        self.finish(user, stream, session, metrics)
        await asyncio.sleep(self.args.settle / 1000)
        self.enter(ConversationState.ATTENTIVE)

    def finish(self, user: UserTurn, stream, session, metrics) -> None:
        """Complete the turn record and write it.

        Build plan 24 asks for more than the latency budget, and the pieces are
        scattered across the objects that own them -- the sink knows about
        underruns, the speech session about queue depth, the bridge about
        whether anyone was listening. Gathered here, once, so all three exit
        paths from a turn record the same thing.
        """
        spoken = session.result
        metrics.heard_ms = user.audio_ms or None
        metrics.spoken_ms = spoken.spoken_ms
        metrics.phrases = session.phrases or None
        metrics.peak_queue_depth = session.peak_queue_depth
        metrics.underruns = getattr(session.sink, "underruns", None)
        metrics.renderer_connected = self.bridge.client_count > 0
        metrics.renderer_backpressure_drops = self.bridge.stats.clients_dropped_for_backpressure

        self.telemetry.record(metrics, user_text=user.text, reply_text=spoken.text)

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
        self.direction = asyncio.create_task(
            self.direct(user.text, stream.text, stream.metrics), name="director"
        )

    async def direct(self, user_text: str, reply: str, metrics=None) -> None:
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
        if metrics is not None:
            metrics.preset = update.preset.value
            metrics.intensity = round(update.intensity, 3)
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
        """Print the latency budget: the stages the user waited through.

        Named explicitly rather than filtered on an `_ms` suffix, because the
        telemetry record also carries durations that run *alongside* speech --
        printing those here would make the budget look like it did not add up.
        """
        record = metrics.as_dict()
        names = [*SPANS, "total_response_ms"]
        parts = [f"{name}={record[name]}" for name in names if name in record]
        print(f"  [{'  '.join(parts)}]")
        if "stt_compute_ms" in record:
            # Beside stt_ms rather than in the budget. Without speculation it is
            # part of that span, and a gap between the two is waiting. With it,
            # most of it ran in the pause before T0, and stt_ms is the rest.
            compute = record["stt_compute_ms"]
            temperature = record.get("stt_temperature", 0.0)
            if record.get("stt_speculative"):
                hidden = max(0, compute - record.get("stt_ms", 0))
                print(
                    f"  [whisper computed {compute}ms, started in the pause -- "
                    f"{hidden}ms of it hidden; temperature {temperature}]"
                )
            else:
                print(f"  [whisper computed {compute}ms of that, temperature {temperature}]")
        print()

    async def follow_devices(self) -> None:
        """Re-choose devices when headphones come or go, between turns."""
        while True:
            await self.watcher.changed.wait()
            # Headphones become the default output a moment after they
            # connect; switching on the first sign would pick the old one.
            await asyncio.sleep(devices.SETTLE_SECONDS)
            self.watcher.changed.clear()
            async with self.audio_lock:
                await self.switch_devices()

    async def switch_devices(self) -> None:
        """Restart PortAudio so it sees the change, and move to the new route.

        Only called holding `audio_lock`, so no turn has a stream open. The
        microphone is closed around the restart by VoiceInput, which keeps the
        conversation going across it.
        """
        before = self.route

        def reopen() -> int | str | None:
            devices.reinitialise()
            try:
                self.route = self._choose_route()
            except devices.DeviceError:
                # No microphone right now. Her voice still has to follow the
                # outputs -- the old indices died with the restart -- and she
                # waits, deaf, for the next change to bring a microphone.
                self.route = self._choose_route(need_microphone=False)
                raise
            return self._microphone_device()

        try:
            if isinstance(self.input, VoiceInput):
                await self.input.switch_microphone(reopen)
            else:
                reopen()
        except (devices.DeviceError, MicrophoneError) as exc:
            print(f"\n  [audio: {exc} -- waiting for a device]")
            return
        if self.route != before:
            print(f"\n  [audio devices changed]\n{self.describe_route()}")


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
    parser.add_argument(
        "--input-device",
        help="microphone, by name fragment (default: the one on the device you are "
        "listening through; never a virtual cable)",
    )
    parser.add_argument(
        "--engine", default="auto", choices=["auto", *sorted(ENGINES)],
        help="; ".join(describe()),
    )
    parser.add_argument("--voice-id", help="engine-specific voice id or model path")
    parser.add_argument(
        "--device",
        help="the virtual cable her face is driven from, by name fragment "
        "(default: output_device in config/voices.yaml, used if present)",
    )
    parser.add_argument(
        "--monitor",
        help="where you hear her, by name fragment (default: Windows' default output)",
    )
    parser.add_argument(
        "--no-monitor", action="store_true",
        help="send her voice to the cable only, without playing it aloud",
    )
    parser.add_argument(
        # Measured by eye on 2026-09-11 at a solver lookahead of 240ms: 650 while
        # the solver ran on the GPU and dropped frames; 615 on the CPU; then 650
        # again after the CPU's power limits were lowered in the BIOS (640 mouth
        # late, 680 early, 660 close, 650 in sync). It tracks the face's whole
        # lag, so it moves with anything that changes how fast the solver runs.
        "--monitor-delay", type=int, default=650, metavar="MS",
        help="how far the monitor lags the face's audio device, so the two land "
        "together (default: 650, measured at a solver lookahead of 240ms)",
    )
    parser.add_argument("--no-audio", action="store_true", help="run silently")
    parser.add_argument(
        "--no-telemetry", action="store_true", help="do not record turns to disk"
    )
    parser.add_argument(
        "--rate",
        type=int,
        metavar="HZ",
        help="declare what the engine emits, when it is not what the registry expects; "
        "this cannot ask an engine for a different rate, because nothing here resamples",
    )
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

    try:
        with contextlib.suppress(KeyboardInterrupt, EOFError):
            return asyncio.run(Session(args).run())
    except SynthesisError as exc:
        # A voice that cannot be built is a startup problem with a fix in it,
        # not a crash. Say the sentence, not the traceback.
        print(f"{exc}")
        return 2
    except devices.DeviceError as exc:
        # Same: no microphone, or a choice that would have her hear herself.
        print(f"{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

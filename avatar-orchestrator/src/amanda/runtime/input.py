"""Where the user's turns come from.

Typed input and spoken input differ in two places and are otherwise the same
conversation: what produces a turn, and what signals an interruption. Putting
those two behind an interface means Phase 2 adds a microphone rather than a
second turn loop, and the typed path stays runnable — which matters, because it
is how the pipeline is exercised on a machine with no microphone and no model
weights (§26).

The semantics of interruption differ between them, and the interface reflects
that:

  * Typed: the interrupting line *is* the next turn, complete the moment it
    arrives.
  * Spoken: barge-in fires part-way through a sentence, long before the
    endpointer knows where it ends. Cancel first; the utterance arrives later
    and becomes the next turn on its own.

So `wait_for_barge_in` is a bare signal and `next_turn` is the only thing that
produces text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from amanda.audio.microphone import Microphone
from amanda.audio.stt import SpeechRecognizer
from amanda.audio.vad import BargeInDetector, Endpointer, Utterance
from amanda.audio.wake import AlwaysAwake, WakeWordDetector

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserTurn:
    """Something the user said, and when.

    The timestamps are the latency budget's T0 and T1. They are carried rather
    than marked directly so this module does not need to know about metrics.
    """

    text: str
    #: Monotonic time the user stopped speaking. T0, the start of everything.
    ended_at: float
    #: Monotonic time the text was ready. T1.
    ready_at: float
    audio_ms: int = 0

    @property
    def empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class ConversationInput(Protocol):
    """A source of user turns."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def next_turn(self) -> UserTurn | None:
        """The next thing the user said, or None when input has ended."""
        ...

    async def wait_for_barge_in(self) -> None:
        """Block until the user interrupts. Only awaited while speaking."""
        ...


# --------------------------------------------------------------------------- #
# Typed
# --------------------------------------------------------------------------- #


@dataclass
class TypedInput:
    """Lines from stdin. The build plan's first experiment (§26).

    Typing while the avatar speaks counts as barge-in, which is how the
    interruption path was exercised by hand before there was a microphone.
    """

    #: (arrival time, line). The timestamp is what lets a line typed *before*
    #: the avatar started speaking be told apart from one typed over it.
    _lines: asyncio.Queue[tuple[float, str | None]] = field(
        default_factory=asyncio.Queue, init=False
    )
    _reader: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _pending: str | None = field(default=None, init=False)
    _ended: bool = field(default=False, init=False)

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read(), name="stdin")

    async def stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None

    async def next_turn(self) -> UserTurn | None:
        if self._pending is not None:
            line, self._pending = self._pending, None
        else:
            if self._ended:
                return None
            _, line = await self._lines.get()

        if line is None or not line.strip() or line.lower() in {"quit", "exit"}:
            self._ended = True
            return None

        # Typed, so there is no speech to end and no transcript to wait for:
        # T0 and T1 are the same instant.
        now = time.monotonic()
        return UserTurn(text=line, ended_at=now, ready_at=now)

    async def wait_for_barge_in(self) -> None:
        """Wait for the user to type over the top of the avatar.

        An empty line is not speech and end of input means quit, so neither
        counts as an interruption -- the utterance is allowed to finish. Both
        cases block rather than returning, so the speaking task wins the race.

        Nor does a line that arrived *before* the avatar began speaking: typing
        ahead is not interrupting. It is held as the next turn instead. Voice
        input gets this free, because the detector is only fed while armed;
        typed input has to check the clock.
        """
        armed_at = time.monotonic()
        while True:
            arrived, line = await self._lines.get()
            if line is None:
                self._ended = True
                await asyncio.Event().wait()  # never fires
            if not line.strip():
                continue
            # Held back either way, so the line becomes their next turn rather
            # than being swallowed.
            self._pending = line
            if arrived >= armed_at:
                return

    async def _read(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                await self._lines.put((time.monotonic(), None))
                return
            await self._lines.put((time.monotonic(), line.strip()))


# --------------------------------------------------------------------------- #
# Spoken
# --------------------------------------------------------------------------- #


@dataclass
class VoiceInput:
    """Turns from a microphone (phase 2, epic 5).

    One capture stream feeds both detectors at once. That is the whole reason
    `Microphone` fans out: endpointing has to keep running *while* the avatar
    speaks, because the sentence the user interrupts with is also their next
    turn, and a design that took the microphone in turns could not do both.
    """

    microphone: Microphone
    recognizer: SpeechRecognizer
    endpointer: Endpointer = field(default_factory=Endpointer)
    barge_in: BargeInDetector = field(default_factory=BargeInDetector)

    #: The word that opens a conversation. `AlwaysAwake` means there is no gate
    #: and every utterance is a turn.
    wake: WakeWordDetector = field(default_factory=AlwaysAwake)

    #: How long the conversation stays open after the last thing anybody said.
    #: Long enough that the wake word is not needed between turns -- being made
    #: to say it before every sentence is what makes an assistant feel like a
    #: vending machine rather than someone in the room.
    awake_seconds: float = 45.0

    #: Utterances with less voiced audio than this are discarded rather than
    #: transcribed. Measured against `voiced_ms`, not the buffer length: an
    #: utterance always carries its pre-roll and the silence that ended it, so
    #: a 0.4s cough arrives as a 1.3s buffer and any threshold on total length
    #: passes it.
    #:
    #: The endpointer's `min_speech` is the primary gate. This is a second line
    #: with a higher bar, for a room noisy enough that the first one is not
    #: enough.
    min_voiced_ms: int = 400

    _utterances: asyncio.Queue[Utterance | None] = field(
        default_factory=asyncio.Queue, init=False
    )
    _reader: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _interrupted: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _armed: bool = field(default=False, init=False)

    #: Utterances dropped for being too short, too quiet, or unheard because
    #: the wake word had not been said.
    discarded: int = field(default=0, init=False)
    #: How many times the wake word has opened a conversation.
    wakes: int = field(default=0, init=False)

    _awake_until: float = field(default=0.0, init=False)

    @property
    def awake(self) -> bool:
        """Whether the avatar is currently listening for turns."""
        return self.wake.always_awake or time.monotonic() < self._awake_until

    async def start(self) -> None:
        # Warm first, then open the microphone. Loading a model takes seconds,
        # and a stream running before anything is subscribed to it captures
        # audio that goes nowhere -- a cold start would be deaf for exactly as
        # long as the model took to load, without saying so.
        await self.recognizer.warm()
        await self.wake.warm()
        await self.microphone.start()
        self._reader = asyncio.create_task(self._read(), name="microphone")

    async def stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        await self.microphone.stop()

    async def next_turn(self) -> UserTurn | None:
        """Wait for a complete utterance and transcribe it."""
        while True:
            utterance = await self._utterances.get()
            if utterance is None:
                return None

            # T0: the user has stopped speaking. Everything after this is
            # latency they can feel.
            ended_at = time.monotonic()

            if utterance.voiced_ms < self.min_voiced_ms:
                self.discarded += 1
                continue

            if not self.awake:
                # Heard, but not addressed to the avatar. Not transcribed
                # either: the point of the gate is that a conversation the
                # avatar is not part of costs nothing.
                self.discarded += 1
                continue

            transcript = await self.recognizer.transcribe(utterance)
            if transcript.empty:
                # Silence the endpointer let through. Whisper will happily
                # invent words for it, which is why the recogniser suppresses
                # them -- here we simply keep listening.
                self.discarded += 1
                continue

            # Each turn holds the conversation open, so the wake word is
            # needed once rather than before every sentence.
            self._awake_until = time.monotonic() + self.awake_seconds
            return UserTurn(
                text=transcript.text,
                ended_at=ended_at,
                ready_at=time.monotonic(),
                audio_ms=utterance.duration_ms,
            )

    async def wait_for_barge_in(self) -> None:
        """Block until sustained speech is heard over the avatar."""
        self.barge_in.reset()
        self._interrupted.clear()
        self._armed = True
        try:
            await self._interrupted.wait()
        finally:
            self._armed = False

    async def _read(self) -> None:
        """Feed every frame to both detectors.

        Endpointing runs unconditionally; barge-in only while armed, because
        otherwise the user's ordinary speech would count as interrupting an
        avatar that is not saying anything.
        """
        try:
            async with self.microphone.listen() as frames:
                async for frame in frames:
                    if (utterance := self.endpointer.feed(frame)) is not None:
                        self._utterances.put_nowait(utterance)
                    if self._armed and self.barge_in.feed(frame):
                        self._interrupted.set()
                    if self.wake.feed(frame):
                        self.wakes += 1
                        self._awake_until = time.monotonic() + self.awake_seconds
                        log.info("woken by %s", self.wake.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("microphone reader failed")
        finally:
            self._utterances.put_nowait(None)

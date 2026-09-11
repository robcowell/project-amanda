"""Voice activity detection, and the two different questions it answers.

Both jobs measure the same thing and want opposite tuning, which is why they
are separate classes rather than one with a threshold argument:

  * **Endpointing** decides when the user has finished a sentence, so the turn
    can be sent to Claude. It should trigger readily -- a missed onset clips the
    first word -- and its cost when wrong is a slightly early or late cut.
  * **Barge-in** decides whether the user is talking over the avatar, and must
    confirm *sustained* voice rather than a cough (§13.1). It should be
    reluctant: its cost when wrong is cancelling a reply nobody meant to stop.

The constants come from `~/code/jarvis`, where they were arrived at by
listening rather than derived. See `docs/jarvis-overlap.md`. The one deliberate
change is the maximum utterance length: Jarvis caps at 6 seconds, which suits
"turn on the kitchen light" and truncates a sentence.

Energy-based, not a neural VAD. That is a real limitation in a noisy room, and
the interface is narrow enough to swap later.
"""

from __future__ import annotations

import array
import math
from collections import deque
from dataclasses import dataclass, field

from amanda.audio.microphone import CAPTURE_RATE
from amanda.audio.tts import CHANNELS, SAMPLE_WIDTH, pcm_duration_ms

#: A frame louder than this counts as voiced. From jarvis.
SPEECH_THRESHOLD = 0.012

#: Barge-in wants a higher bar. While the avatar is speaking the room is
#: noisier, and on a machine playing through speakers rather than into a
#: virtual cable the microphone may hear the avatar itself.
BARGE_IN_THRESHOLD = 0.018


def frame_rms(frame: bytes) -> float:
    """Root-mean-square level of a frame, 0.0 to 1.0."""
    samples = array.array("h")
    samples.frombytes(frame)
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples)) / 32768.0


@dataclass(frozen=True, slots=True)
class Utterance:
    """A complete stretch of speech, ready to transcribe."""

    pcm: bytes
    sample_rate: int

    #: How much of it was voiced, as against how long the buffer is.
    #:
    #: The two differ by about a second and always in the same direction: an
    #: utterance carries its pre-roll at the front and the silence that ended it
    #: at the back. Filtering noise on `duration_ms` therefore does nothing --
    #: a 0.4s cough arrives as a 1.3s buffer. This is the number to threshold.
    voiced_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return pcm_duration_ms(self.pcm, self.sample_rate)


@dataclass
class Endpointer:
    """Turns a stream of frames into utterances.

    Feed it every frame; it returns an `Utterance` on the frame that completes
    one, and None otherwise.
    """

    sample_rate: int = CAPTURE_RATE
    threshold: float = SPEECH_THRESHOLD

    #: Quiet for this long ends the utterance.
    silence_to_end: float = 0.75
    #: Shorter than this is a cough, not a turn.
    min_speech: float = 0.35
    #: Audio kept from *before* detection triggers.
    #:
    #: Detection necessarily lags onset, so without this the first syllable is
    #: clipped -- "urn the lights on". Four lines, and the difference between a
    #: transcript that parses and one that does not.
    pre_roll: float = 0.15
    #: A hard ceiling, not the normal way an utterance ends. Jarvis caps at 6s,
    #: which is right for commands and truncates conversation.
    max_utterance: float = 20.0

    _speaking: bool = field(default=False, init=False)
    _captured: list[bytes] = field(default_factory=list, init=False)
    _history: deque[bytes] = field(default_factory=deque, init=False)
    _voiced_ms: float = field(default=0.0, init=False)
    _silent_ms: float = field(default=0.0, init=False)
    _captured_ms: float = field(default=0.0, init=False)
    _pre_roll_ms: float = field(default=0.0, init=False)

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def silent_ms(self) -> float:
        """Silence since the last voiced frame of the utterance in progress."""
        return self._silent_ms

    @property
    def voiced_ms(self) -> float:
        """Voiced audio in the utterance in progress."""
        return self._voiced_ms

    def snapshot(self) -> Utterance:
        """The utterance so far, without ending it.

        For transcribing during the silence that may or may not end it: if it
        does end, the finished utterance is this plus nothing but silence.
        """
        return Utterance(
            pcm=b"".join(self._captured),
            sample_rate=self.sample_rate,
            voiced_ms=round(self._voiced_ms),
        )

    def reset(self) -> None:
        self._speaking = False
        self._captured.clear()
        self._history.clear()
        self._voiced_ms = self._silent_ms = self._captured_ms = self._pre_roll_ms = 0.0

    def feed(self, frame: bytes) -> Utterance | None:
        duration = _frame_ms(frame, self.sample_rate)
        voiced = frame_rms(frame) >= self.threshold

        if not self._speaking:
            self._remember(frame, duration)
            if not voiced:
                return None
            # Onset. The ring buffer goes in ahead of this frame.
            self._speaking = True
            self._captured = list(self._history)
            self._captured_ms = self._pre_roll_ms
            self._history.clear()
            self._pre_roll_ms = 0.0

        self._captured.append(frame)
        self._captured_ms += duration

        if voiced:
            self._voiced_ms += duration
            self._silent_ms = 0.0
        else:
            self._silent_ms += duration

        ended = (
            self._voiced_ms >= self.min_speech * 1000
            and self._silent_ms >= self.silence_to_end * 1000
        )
        if ended or self._captured_ms >= self.max_utterance * 1000:
            return self._finish()
        return None

    def flush(self) -> Utterance | None:
        """Close off whatever is in progress. Call when capture stops."""
        if self._speaking and self._voiced_ms >= self.min_speech * 1000:
            return self._finish()
        self.reset()
        return None

    def _remember(self, frame: bytes, duration: float) -> None:
        self._history.append(frame)
        self._pre_roll_ms += duration
        while self._pre_roll_ms > self.pre_roll * 1000 and len(self._history) > 1:
            self._pre_roll_ms -= _frame_ms(self._history.popleft(), self.sample_rate)

    def _finish(self) -> Utterance:
        utterance = Utterance(
            pcm=b"".join(self._captured),
            sample_rate=self.sample_rate,
            voiced_ms=round(self._voiced_ms),
        )
        self.reset()
        return utterance


@dataclass
class BargeInDetector:
    """Decides whether the user is talking over the avatar.

    Deliberately reluctant. Cancelling a reply nobody meant to stop is worse
    than being slow to notice a real interruption, so voice has to be sustained
    rather than merely present -- a cough, a chair, a door should not stop the
    avatar mid-sentence (§13.1).

    Not echo cancellation. If the microphone can hear the avatar's own voice
    through speakers this will fire on every reply. On the target machine that
    does not happen, because output goes to a virtual audio cable rather than
    to speakers -- but that is an accident of the architecture, not a solution,
    and it stops being true the moment anyone develops with headphones off.
    """

    sample_rate: int = CAPTURE_RATE
    threshold: float = BARGE_IN_THRESHOLD
    #: Voice must persist this long before it counts as an interruption.
    sustain: float = 0.20
    #: A gap shorter than this does not reset the run, so ordinary gaps between
    #: syllables do not stop it accumulating.
    tolerance: float = 0.10

    _voiced_ms: float = field(default=0.0, init=False)
    _gap_ms: float = field(default=0.0, init=False)
    _fired: bool = field(default=False, init=False)

    @property
    def progress(self) -> float:
        """How close the current run is to triggering, 0.0 to 1.0."""
        return min(1.0, self._voiced_ms / max(1e-6, self.sustain * 1000))

    def reset(self) -> None:
        self._voiced_ms = self._gap_ms = 0.0
        self._fired = False

    def feed(self, frame: bytes) -> bool:
        """True on the frame that confirms an interruption, once per run."""
        duration = _frame_ms(frame, self.sample_rate)

        if frame_rms(frame) >= self.threshold:
            self._voiced_ms += duration
            self._gap_ms = 0.0
        else:
            self._gap_ms += duration
            if self._gap_ms > self.tolerance * 1000:
                self.reset()
                return False

        if self._fired or self._voiced_ms < self.sustain * 1000:
            return False

        self._fired = True
        return True


def _frame_ms(frame: bytes, sample_rate: int) -> float:
    return len(frame) / (SAMPLE_WIDTH * CHANNELS) * 1000 / sample_rate

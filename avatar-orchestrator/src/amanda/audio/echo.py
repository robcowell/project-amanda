"""Echo cancellation: taking her own voice back out of the microphone.

On speakers with a desk microphone she hears herself. Measured on the renderer
PC, 2026-09-11: her echo barged in on nearly every reply, and what it said
became her next turn -- "Well, I suppose the other", from "Well, I suppose the
honest answer". A wake word does not stop that: once awake, her echo is a turn
like any other and keeps the conversation open.

This is the textbook case for an acoustic echo canceller, and an unusually easy
one, because the orchestrator has the exact far-end signal: every sample she
plays passes through `DeviceSink`. WebRTC's AEC3 (via `livekit`, the one
packaging of it with Windows wheels for this Python) models the path from the
speakers to the microphone and subtracts it.

Timing is the part that matters. The reference is tapped a 20ms block at a
time, from the sink's writer thread, as each block goes to the device -- not
when a whole phrase is handed to the sink, which would put it seconds ahead of
the room. The canceller estimates what is left (device buffers, air).

Offline, on a synthetic room with Piper speech (2026-09-11): echo down 20-40dB,
to below the endpointer's threshold; the user alone transcribed word for word;
the user talking over her went from "Hey, no, I can I ask you something about
that to Tim Gu" to "You know, can I ask you something about it?", about 4dB
quieter. A real room adds loudspeaker distortion and two devices' clocks.
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from typing import Any

from amanda.audio.microphone import CAPTURE_RATE
from amanda.audio.tts import SAMPLE_WIDTH

log = logging.getLogger(__name__)

#: AEC3 takes exactly 10ms at a time.
FRAME_MS = 10


@dataclass
class EchoCanceller:
    """Removes what she is playing from what the microphone hears.

    `played` is called from the output's writer thread; `process` from the
    event loop, with every capture frame. The two sides meet in a thread-safe
    queue, and only `process` touches the canceller itself.
    """

    sample_rate: int = CAPTURE_RATE

    _apm: Any = field(default=None, init=False, repr=False)
    _played: queue.SimpleQueue[tuple[int, bytes]] = field(
        default_factory=queue.SimpleQueue, init=False, repr=False
    )
    _resamplers: dict[int, Any] = field(default_factory=dict, init=False, repr=False)
    _reference: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _capture: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _cleaned: bytearray = field(default_factory=bytearray, init=False, repr=False)

    def __post_init__(self) -> None:
        from livekit import rtc

        self._apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)
        # Capture frames are 32ms and the canceller takes 10ms, so there is
        # always a remainder waiting for the next frame. Starting 10ms in hand
        # means every frame can be answered in full: a constant 10ms of delay
        # rather than frames that come back short.
        self._cleaned += bytes(self._frame_bytes)

    @property
    def _frame_bytes(self) -> int:
        return self.sample_rate * FRAME_MS // 1000 * SAMPLE_WIDTH

    @property
    def name(self) -> str:
        return "webrtc-aec3"

    def played(self, pcm: bytes, sample_rate: int) -> None:
        """A block that has just gone to the listening device. Any thread."""
        if pcm:
            self._played.put((sample_rate, pcm))

    def process(self, frame: bytes) -> bytes:
        """One capture frame, with her voice taken out. Same length back."""
        from livekit import rtc

        self._take_reference()

        size = self._frame_bytes
        samples = size // SAMPLE_WIDTH
        self._capture += frame
        while len(self._capture) >= size:
            block = rtc.AudioFrame(bytes(self._capture[:size]), self.sample_rate, 1, samples)
            del self._capture[:size]
            # Delay beyond what the reference tap already accounts for is
            # AEC3's to estimate; zero says there is no known extra.
            self._apm.set_stream_delay_ms(0)
            self._apm.process_stream(block)
            self._cleaned += bytes(block.data)

        out = bytes(self._cleaned[: len(frame)])
        del self._cleaned[: len(frame)]
        return out

    def _take_reference(self) -> None:
        from livekit import rtc

        size = self._frame_bytes
        samples = size // SAMPLE_WIDTH
        while True:
            try:
                rate, pcm = self._played.get_nowait()
            except queue.Empty:
                break
            if rate == self.sample_rate:
                self._reference += pcm
                continue
            resampler = self._resamplers.get(rate)
            if resampler is None:
                # Stateful, so blocks join without a click at every boundary.
                resampler = rtc.AudioResampler(rate, self.sample_rate, num_channels=1)
                self._resamplers[rate] = resampler
            for frame in resampler.push(bytearray(pcm)):
                self._reference += bytes(frame.data)

        while len(self._reference) >= size:
            block = rtc.AudioFrame(bytes(self._reference[:size]), self.sample_rate, 1, samples)
            del self._reference[:size]
            self._apm.process_reverse_stream(block)


def build(enabled: bool = True) -> EchoCanceller | None:
    """A canceller, or None -- disabled, or `livekit` not installed.

    Not having one is not an error: on headphones there is no echo to cancel.
    """
    if not enabled:
        return None
    try:
        return EchoCanceller()
    except ImportError:
        log.warning("livekit is not installed; no echo cancellation")
        return None

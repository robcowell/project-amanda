"""The conversation state machine (build plan 7).

IDLE -> ATTENTIVE -> LISTENING -> THINKING -> SPEAKING -> SETTLING -> ATTENTIVE

Each state owns an animation envelope, and the envelopes are the point. THINKING
does not mean "look theatrically puzzled": eye contact drops, the gaze moves
off-axis, the head goes still. That is what turns a wait into someone
considering, and it is why the latency between T0 and T6 does not need a verbal
filler to cover it.

These presets are driven by conversation *state*, not by classifying what was
said. The performance director (phase 4) layers on top of this and can override
any of it; until then, the states alone are enough for the avatar to look like
it is participating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from amanda.avatar.protocol import (
    AssistantThinkingEnded,
    AssistantThinkingStarted,
    GazeSetTarget,
    GazeTarget,
    Payload,
    PerformanceUpdate,
    Preset,
    UserSpeechEnded,
    UserSpeechStarted,
)


class ConversationState(StrEnum):
    IDLE = "idle"
    ATTENTIVE = "attentive"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SETTLING = "settling"


@dataclass(frozen=True, slots=True)
class Envelope:
    """What the avatar should look like in a given state."""

    preset: Preset
    intensity: float
    transition_ms: int
    gaze: GazeTarget | None = None
    gaze_hold_ms: int = 0

    def payloads(self) -> list[Payload]:
        events: list[Payload] = [
            PerformanceUpdate(
                preset=self.preset, intensity=self.intensity, transition_ms=self.transition_ms
            )
        ]
        if self.gaze is not None:
            events.append(GazeSetTarget(target=self.gaze, hold_ms=self.gaze_hold_ms))
        return events


ENVELOPES: dict[ConversationState, Envelope] = {
    # Present, not attentive. Do not track the user while idle.
    ConversationState.IDLE: Envelope(Preset.NEUTRAL_ATTENTIVE, 0.06, 2000, GazeTarget.DISTANT),
    ConversationState.ATTENTIVE: Envelope(Preset.NEUTRAL_ATTENTIVE, 0.12, 700, GazeTarget.USER),
    ConversationState.LISTENING: Envelope(Preset.LISTENING, 0.18, 400, GazeTarget.USER),
    # Gaze off-axis and the head still. Not a puzzled face.
    ConversationState.THINKING: Envelope(
        Preset.CONSIDERING, 0.28, 520, GazeTarget.SLIGHTLY_RIGHT, gaze_hold_ms=900
    ),
    # Attention returns as speech begins.
    ConversationState.SPEAKING: Envelope(Preset.WARM, 0.22, 400, GazeTarget.USER),
    ConversationState.SETTLING: Envelope(Preset.NEUTRAL_ATTENTIVE, 0.12, 900),
}


@dataclass
class ConversationStateMachine:
    """Tracks the state and emits the events each transition implies."""

    state: ConversationState = ConversationState.IDLE
    history: list[ConversationState] = field(default_factory=list)

    def enter(self, state: ConversationState) -> list[Payload]:
        """Move to a state and return the protocol events it produces.

        Re-entering the current state produces nothing, so a caller can be
        careless about calling this and the renderer still sees one transition.
        """
        if state is self.state:
            return []

        events: list[Payload] = []

        # Lifecycle events bracket the states they belong to, so the renderer
        # can distinguish "considering" the mood from THINKING the state.
        if self.state is ConversationState.LISTENING:
            events.append(UserSpeechEnded())
        if self.state is ConversationState.THINKING:
            events.append(AssistantThinkingEnded())

        self.history.append(self.state)
        self.state = state

        if state is ConversationState.LISTENING:
            events.append(UserSpeechStarted())
        if state is ConversationState.THINKING:
            events.append(AssistantThinkingStarted())

        events.extend(ENVELOPES[state].payloads())
        return events

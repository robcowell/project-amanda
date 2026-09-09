"""The continuous performance state (build plan 5.1) and the preset table.

`Preset` and the wire vocabulary live in `amanda.avatar.protocol` because they
cross the boundary to the renderer. This module holds what each preset *means*
in animation terms, and the continuous state that smoothing operates on.

These are animation controls, not claims that Claude experiences emotions.
"""

from __future__ import annotations

from dataclasses import dataclass

from amanda.avatar.protocol import Preset


@dataclass(frozen=True, slots=True)
class PresetShape:
    """What a preset looks like at the reference intensity.

    Two kinds of channel, and the difference matters when intensity changes:

      * **Expressive** -- brow and smile. These scale with intensity, because
        that is what intensity means.
      * **Postural** -- eye contact, head motion, blink rate. These describe the
        state the character is in rather than how strongly they feel it, so they
        are taken as-is. Scaling eye contact to zero as intensity falls would
        make a calm character stare into the middle distance.
    """

    eye_contact: float
    head_motion: float
    brow_activity: float
    smile: float
    blink_rate_scale: float = 1.0


#: The intensity these shapes are authored at. Values scale linearly from here.
REFERENCE_INTENSITY = 0.30

#: Deliberately low across the board. Build plan 18: start every coefficient
#: lower than feels necessary. Nothing here should look like acting.
PRESET_SHAPES: dict[Preset, PresetShape] = {
    Preset.NEUTRAL_ATTENTIVE: PresetShape(0.55, 0.10, 0.05, 0.02, 1.00),
    Preset.LISTENING: PresetShape(0.80, 0.12, 0.08, 0.04, 1.00),
    # Blinking slows while concentrating -- a real effect, and one of the few
    # cues that reads as thought without looking theatrically puzzled.
    Preset.CONSIDERING: PresetShape(0.25, 0.06, 0.10, 0.01, 1.35),
    Preset.MILDLY_AMUSED: PresetShape(0.62, 0.14, 0.10, 0.18, 0.95),
    Preset.WARM: PresetShape(0.62, 0.12, 0.07, 0.12, 1.00),
    Preset.CONCERNED: PresetShape(0.66, 0.08, 0.20, 0.00, 1.10),
    Preset.UNCERTAIN: PresetShape(0.40, 0.10, 0.14, 0.03, 0.95),
    Preset.CONFUSED: PresetShape(0.48, 0.16, 0.24, 0.02, 0.90),
    Preset.SURPRISED: PresetShape(0.75, 0.10, 0.34, 0.05, 0.70),
    Preset.EXPLAINING: PresetShape(0.58, 0.20, 0.10, 0.05, 1.05),
    Preset.ENTHUSIASTIC: PresetShape(0.68, 0.26, 0.16, 0.24, 0.90),
    # Read from a stiller head and slower blinks, not from a frowning face.
    Preset.SERIOUS: PresetShape(0.70, 0.07, 0.06, 0.00, 1.25),
}

assert set(PRESET_SHAPES) == set(Preset), "every preset needs a shape"


@dataclass(slots=True)
class PerformanceState:
    """The continuous state from build plan 5.1.

    Not yet used by the director -- it is the vocabulary the classifier will
    eventually emit, which presets are then derived from. Kept here so the two
    representations stay in one place.
    """

    attention: float = 0.9
    engagement: float = 0.7
    valence: float = 0.0
    arousal: float = 0.2
    confidence: float = 0.8
    thoughtfulness: float = 0.3
    amusement: float = 0.0
    surprise: float = 0.0
    confusion: float = 0.0

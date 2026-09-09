"""The conversation state machine (build plan 7).

IDLE -> ATTENTIVE -> LISTENING -> ACKNOWLEDGING -> THINKING ->
PREPARING_SPEECH -> SPEAKING -> (INTERRUPTED) -> SETTLING -> ATTENTIVE/IDLE

Each state owns an animation envelope. THINKING does not mean "look
theatrically puzzled": eye contact drops slightly, gaze moves off-axis, the
head goes still.

TODO: implement.
"""

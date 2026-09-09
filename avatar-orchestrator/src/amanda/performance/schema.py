"""The continuous performance state (build plan 5.1) and its mapping to presets.

The state object -- attention, engagement, valence, arousal, confidence,
thoughtfulness, amusement, surprise, confusion -- holds continuous values that
smoothing operates on. `Preset` and the wire-level vocabulary live in
`amanda.avatar.protocol` because they cross the boundary to the renderer; this
module maps continuous state onto them.

These are animation controls, not claims that Claude experiences emotions.

TODO: implement.
"""

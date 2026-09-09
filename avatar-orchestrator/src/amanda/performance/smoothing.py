"""Emotional inertia (build plan 18).

Raw per-utterance classification flickers; the avatar must not snap from
concerned to happy because the next sentence contains a joke. Applies decay,
hysteresis and rate limiting to the continuous state before it reaches the
wire, and keeps intensities low -- every coefficient should start lower than
feels necessary.

TODO: implement.
"""

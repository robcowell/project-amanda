"""Turns conversational context into performance directions (phase 4).

A second, fast, heavily-constrained model call receives the user's message and
Claude's response and emits a small structured description (build plan 6,
option A). Cheap and boring by design.

Start with the phase 4 subset only: neutral_attentive, warm, considering,
mildly_amused, concerned, uncertain, surprised.

TODO: implement.
"""

"""Multi-turn conversation state (epic 2).

Owns message history, and the awkward case from build plan 13: when the user
interrupts, the partially-spoken assistant response has to be recorded as what
was actually *heard*, not what was generated.

TODO: implement.
"""

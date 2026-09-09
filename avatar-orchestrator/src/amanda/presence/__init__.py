"""Renderer-side presence behaviour, written in Python so it can be tested.

The build plan puts this logic in Unreal (BP_IdleController, BP_GazeController,
section 14) and that is still where it belongs at runtime. It lives here as a
*reference implementation*: blink timing, gaze scheduling, breathing and drift
are stochastic processes whose constants have to be tuned by watching them, and
that is miserable to do in a Blueprint graph and ordinary in Python.

So: tune here, port the tuned algorithm and constants to Blueprints later. The
previz tool (`tools/previz.py`) runs this module as a real protocol v1 renderer,
which is exactly the role Unreal will play.

Nothing in here talks to the network or reads a clock. Every scheduler is driven
by an explicit `now` and an injected `random.Random`, so a test can run an hour
of behaviour in milliseconds and get the same answer twice.
"""

from amanda.presence.engine import FaceState, PresenceEngine
from amanda.presence.schedulers import (
    BlinkScheduler,
    BreathScheduler,
    DriftScheduler,
    GazeScheduler,
)

__all__ = [
    "BlinkScheduler",
    "BreathScheduler",
    "DriftScheduler",
    "FaceState",
    "GazeScheduler",
    "PresenceEngine",
]

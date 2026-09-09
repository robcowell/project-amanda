"""Project Amanda -- conversation orchestrator.

Owns everything except rendering: microphone, STT, Claude, TTS, the performance
director, and the bridge that drives the avatar. The renderer is downstream of
this process and never calls Claude itself (build plan 4).
"""

__version__ = "0.1.0"

"""Barge-in (phase 5, build plan 13).

Ordering matters. On confirmed sustained voice activity: cancel the Claude
stream, cancel queued TTS, fade current audio, and transition the face from
speaking to listening -- the *visual* transition should happen almost
immediately, ahead of the slower cancellations.

TODO: implement.
"""

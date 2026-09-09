"""Microphone capture, voice activity detection, STT and TTS.

Every provider sits behind an interface so engines can be swapped without
touching the rest of the system (build plan 4). Phase 1 uses typed input and
needs only `tts`.
"""

"""Latency telemetry (build plan 11 and 24).

Stages: T0 user stops speaking, T1 transcript final, T2 request sent, T3 first
Claude token, T4 first speakable phrase, T5 first TTS audio, T6 avatar begins
speaking. The headline metric is T6 - T0.

Also FPS, GPU frame time, audio underruns, bridge latency, TTS queue depth,
stream duration and utterance length -- enough to turn "it feels sluggish" into
something diagnosable.

TODO: implement.
"""

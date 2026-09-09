"""Text-to-speech behind a provider-neutral interface (epic 3).

The build plan fixes the shape (section 4):

    TTS.speak(text, performance_state) -> audio_stream + timing_metadata

Required of any backend: streaming or low-latency synthesis, timestamps or
viseme data where available, a consistent voice identity, controllable pace,
and cancellation -- cancellation is not optional, because barge-in depends on
it (build plan 13).

TODO: define the Protocol and implement the first backend.
"""

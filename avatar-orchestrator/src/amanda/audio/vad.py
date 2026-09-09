"""Voice activity detection and endpoint detection (phase 2, epic 5).

Two jobs, and they want different tuning:

  * end-of-utterance, which decides when to send to Claude -- optimise for
    latency and reliable endpointing over transcription perfection;
  * barge-in, which must confirm *sustained* voice activity before cancelling
    the avatar mid-sentence, so a cough does not stop it (build plan 13).

TODO: implement.
"""

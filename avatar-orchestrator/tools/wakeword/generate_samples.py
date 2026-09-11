"""Synthetic speech for wake word training, on Windows, with Piper.

openWakeWord's `train.py` imports `generate_samples` from the folder its config
names as `piper_sample_generator_path`, and calls it with a fixed signature.
The real one (rhasspy/piper-sample-generator) is Linux-only: it phonemises with
`piper-phonemize`, which has no Windows wheels, and its current version no
longer matches that signature at all. This is a stand-in with the same
signature, built on the Piper this project already runs on Windows.

The voice is `en_US-libritts_r-medium`: the same LibriTTS-R model the real
generator uses, as a Piper ONNX voice with 904 speakers. Every clip picks a
speaker at random and varies speed and the two noise scales, which is where
the variety a wake word model generalises from comes from.

Nothing here is specific to "hey Amanda".
"""

from __future__ import annotations

import multiprocessing
import os
import random
import uuid
from pathlib import Path

import numpy as np

#: The multi-speaker voice, downloaded by prepare_data.py.
VOICE = os.environ.get(
    "AMANDA_WAKE_VOICE", r"D:\amanda-wakeword\voices\en_US-libritts_r-medium.onnx"
)
SPEAKERS = 904
RATE = 16_000

#: Endings that change a phrase's prosody -- a question, a call, a statement.
ENDINGS = (".", "!", "?", ",", "")

_voice = None


def _load(path: str) -> None:
    global _voice
    from piper import PiperVoice

    _voice = PiperVoice.load(path)


def _synthesise(job: tuple[str, int, float, float, float, str]) -> bool:
    import soundfile
    from piper import SynthesisConfig
    from scipy.signal import resample_poly

    text, speaker, length_scale, noise_scale, noise_w, path = job
    config = SynthesisConfig(
        speaker_id=speaker,
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w_scale=noise_w,
    )
    chunks = list(_voice.synthesize(text, config))
    if not chunks:
        return False
    audio = np.concatenate([chunk.audio_float_array for chunk in chunks])
    rate = chunks[0].sample_rate
    if rate != RATE:
        audio = resample_poly(audio, RATE, rate)
    audio = _trim(audio)
    if audio.size < RATE // 10:
        return False
    # Piper normalises to full scale, and resampling overshoots it: every
    # clip in the first test peaked at exactly 1.00, which is clipping.
    peak = float(np.abs(audio).max())
    if peak > 0.9:
        audio = audio * (0.9 / peak)
    soundfile.write(path, audio, RATE, subtype="PCM_16")
    return True


def _trim(audio: np.ndarray, floor_db: float = -40.0, margin_ms: int = 50) -> np.ndarray:
    """Cut leading and trailing silence, keeping a little either side.

    The real generator trims with webrtcvad, which needs a compiler on Windows.
    Piper's output is clean, so an energy floor does the same job.
    """
    window = RATE // 100
    frames = audio[: audio.size // window * window].reshape(-1, window)
    if not len(frames):
        return audio
    level = 20 * np.log10(np.sqrt(np.mean(frames**2, axis=1)) + 1e-9)
    voiced = np.flatnonzero(level > level.max() + floor_db)
    if not voiced.size:
        return audio
    margin = margin_ms * RATE // 1000
    start = max(0, voiced[0] * window - margin)
    end = min(audio.size, (voiced[-1] + 1) * window + margin)
    return audio[start:end]


def generate_samples(
    text,
    output_dir,
    max_samples,
    batch_size=1,
    noise_scales=(0.667,),
    noise_scale_ws=(0.8,),
    length_scales=(1.0,),
    auto_reduce_batch_size=False,
    file_names=None,
    **_ignored,
):
    """Write `max_samples` clips of `text` to `output_dir`, 16 kHz mono WAV.

    `text` is a phrase or a list to draw from. `batch_size` and
    `auto_reduce_batch_size` are the GPU generator's and mean nothing here.
    """
    phrases = [text] if isinstance(text, str) else list(text)
    if not phrases or max_samples <= 0:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    names = list(file_names or [])[:max_samples]
    names += [uuid.uuid4().hex + ".wav" for _ in range(max_samples - len(names))]

    jobs = [
        (
            random.choice(phrases).rstrip(".!?,") + random.choice(ENDINGS),
            random.randrange(SPEAKERS),
            random.choice(list(length_scales)) * random.uniform(0.9, 1.1),
            random.choice(list(noise_scales)),
            random.choice(list(noise_scale_ws)),
            str(Path(output_dir) / name),
        )
        for name in names
    ]

    workers = max(1, min(8, (os.cpu_count() or 2) // 4))
    with multiprocessing.get_context("spawn").Pool(workers, _load, (VOICE,)) as pool:
        written = 0
        for index, ok in enumerate(pool.imap_unordered(_synthesise, jobs, chunksize=16), 1):
            written += ok
            if index % 500 == 0 or index == len(jobs):
                print(f"  {index}/{len(jobs)} clips, {written} written -> {output_dir}", flush=True)

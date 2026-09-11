"""Fetch what wake word training needs, into D:\\amanda-wakeword\\data.

Run once, in the training venv:

    D:\\amanda-wakeword\\.venv\\Scripts\\python prepare_data.py

  * openWakeWord's precomputed negative features: 2,000 hours of ACAV100M
    (17.3 GB) and its false-positive validation set (185 MB). The model learns
    what is *not* the wake word mostly from these.
  * MIT's room impulse responses (270 files), for reverb.
  * One shard of AudioSet (about 690 MB of FLAC), converted to 16 kHz WAV, for
    background noise. The notebook's .tar link is dead; the repository is
    Parquet now.
  * The 904-speaker Piper voice generate_samples.py speaks with.
  * openWakeWord's two feature models, which the pip package leaves out.

Everything already present is skipped, so it is safe to run again.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(r"D:\amanda-wakeword")
DATA = ROOT / "data"

FEATURES = "davidscripka/openwakeword_features"
FEATURE_FILES = (
    "validation_set_features.npy",
    "openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
)
RIRS = "davidscripka/MIT_environmental_impulse_responses"
AUDIOSET = "agkphysics/AudioSet"
AUDIOSET_SHARD = "data/bal_train/09.parquet"
VOICE = "rhasspy/piper-voices"
VOICE_FILES = (
    "en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx",
    "en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json",
)


def features() -> None:
    from huggingface_hub import hf_hub_download

    for name in FEATURE_FILES:
        if (DATA / name).is_file():
            print(f"have {name}")
            continue
        print(f"downloading {name}")
        hf_hub_download(FEATURES, name, repo_type="dataset", local_dir=DATA)


def rirs() -> None:
    from huggingface_hub import snapshot_download

    target = DATA / "mit_rirs"
    if (target / "16khz").is_dir() and any((target / "16khz").iterdir()):
        print("have room impulse responses")
        return
    print("downloading room impulse responses")
    snapshot_download(RIRS, repo_type="dataset", local_dir=target, allow_patterns=["16khz/*"])


def audioset() -> None:
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile
    from huggingface_hub import hf_hub_download
    from scipy.signal import resample_poly

    target = DATA / "audioset_16k"
    if target.is_dir() and any(target.iterdir()):
        print("have AudioSet background clips")
        return
    target.mkdir(parents=True, exist_ok=True)
    print(f"downloading AudioSet {AUDIOSET_SHARD}")
    shard = hf_hub_download(
        AUDIOSET, AUDIOSET_SHARD, repo_type="dataset", local_dir=DATA / "audioset"
    )

    table = pq.read_table(shard)
    audio_column = next(name for name in table.column_names if "audio" in name.lower())
    written = 0
    for index, cell in enumerate(table.column(audio_column).to_pylist()):
        raw = cell.get("bytes") if isinstance(cell, dict) else cell
        if not raw:
            continue
        try:
            samples, rate = soundfile.read(io.BytesIO(raw), dtype="float32", always_2d=True)
        except Exception as exc:  # noqa: BLE001 - one bad clip is not a reason to stop
            print(f"  skipping clip {index}: {exc}")
            continue
        mono = samples.mean(axis=1)
        if rate != 16_000:
            mono = resample_poly(mono, 16_000, rate)
        soundfile.write(target / f"{index:05d}.wav", np.clip(mono, -1, 1), 16_000, subtype="PCM_16")
        written += 1
    print(f"wrote {written} background clips")


def voice() -> None:
    from huggingface_hub import hf_hub_download

    target = ROOT / "voices"
    for name in VOICE_FILES:
        if (target / Path(name).name).is_file():
            print(f"have {Path(name).name}")
            continue
        print(f"downloading {Path(name).name}")
        path = hf_hub_download(VOICE, name, local_dir=target / "_hub")
        Path(path).replace(target / Path(name).name)


def feature_models() -> None:
    """openWakeWord's melspectrogram and embedding models, which turn audio
    into the features every keyword is trained on. Not in the pip package;
    the first run of --augment_clips failed for want of them."""
    from openwakeword.utils import download_models

    # Always fetches the feature models; a name it does not know adds nothing.
    download_models(["__features_only__"])
    print("have openWakeWord's feature models")


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    feature_models()
    voice()
    rirs()
    audioset()
    features()
    return 0


if __name__ == "__main__":
    sys.exit(main())

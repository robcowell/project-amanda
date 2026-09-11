# Training the "hey Amanda" wake word, on Windows

openWakeWord ships models for a handful of phrases; "hey Amanda" is not one of
them, so it is trained here. The official route (a Colab notebook, and every
maintained third-party trainer) is Linux-only. This folder is the Windows
route, and uses openWakeWord's own `train.py` for everything it can:

| Linux-only piece | Here |
|---|---|
| `piper-sample-generator` (no Windows `piper-phonemize` wheels, and its current version no longer matches `train.py`) | `generate_samples.py`: same signature, built on Windows Piper and the same 904-speaker LibriTTS-R voice as ONNX |
| `webrtcvad` silence trimming (needs a compiler) | an energy floor -- Piper's output is clean |
| training `DataLoader` worker processes (Windows cannot pickle the live generator they are handed) | `train_hey_amanda.py` loads in-process; batches come from a memory-mapped file anyway |
| `acoustics` asking for `scipy.special.sph_harm`, removed in SciPy 1.15 | the old name restored, under `sph_harm_y`'s argument order, by `shim/sitecustomize.py` -- in every process, because Windows workers re-import `train.py` before any of our code runs |
| trimming a feature file by deleting it while it is still memory-mapped (Windows refuses: WinError 32) | the trim is deferred until the file is released, and swapped in with `os.replace` |
| training draws random batches from 17 GB of memory-mapped features, and Windows keeps every touched page in the process's working set: 20.4 GB of it measured, 6.3 GB its own, and the first run was stopped for low memory at 88% | the process's working set is emptied every 30 seconds -- the pages are clean, so at worst re-read -- and its memory logged each minute |
| every validation collates the whole 481,329-window false positive set into a fresh 3 GB copy, and on Windows the process never gives it back: from step 37,500, where validation starts, to 104 GB and a kill in the second pass | validation sets are handed over as the tensors themselves, moved to the GPU once and reused. Measured on 3,000-step runs: 9.3 GB peak and finished, against 20.3 GB and climbing after 30 seconds of validation with the copy (`AMANDA_COLLATE=1` puts it back, to compare) |
| ONNX export needs the `onnx` package, which nothing else pulls in | installed in setup |
| TFLite export (TensorFlow) | skipped; Amanda loads the ONNX model, written first |

Nothing in openWakeWord's installed files is edited.

## Setup (once)

The training environment lives outside the repo, with about 20 GB of data:

```powershell
py -3.13 -m venv D:\amanda-wakeword\.venv
D:\amanda-wakeword\.venv\Scripts\python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
D:\amanda-wakeword\.venv\Scripts\python -m pip install openwakeword==0.6.0 piper-tts soundfile scipy pyyaml tqdm huggingface_hub "datasets<4" audiomentations torch-audiomentations speechbrain acoustics mutagen torchinfo torchmetrics pronouncing onnxruntime onnx
cd avatar-orchestrator\tools\wakeword
D:\amanda-wakeword\.venv\Scripts\python prepare_data.py
```

torch 2.8 rather than the newest: it is the last with the torchaudio loading
openWakeWord's data code calls.

## Train

From this folder, three stages, each skippable once done:

```powershell
D:\amanda-wakeword\.venv\Scripts\python train_hey_amanda.py --generate_clips
D:\amanda-wakeword\.venv\Scripts\python train_hey_amanda.py --augment_clips
D:\amanda-wakeword\.venv\Scripts\python train_hey_amanda.py --train_model
```

The model is written to `D:\amanda-wakeword\model\hey_amanda.onnx`. Copy it to
`avatar-orchestrator\models\` and set `wake.keyword` in `config/avatar.yaml` to
`models/hey_amanda.onnx`.

Close the Unreal editor first: training is sustained GPU load, and this PC has
shut down under GPU and CPU load together once already.

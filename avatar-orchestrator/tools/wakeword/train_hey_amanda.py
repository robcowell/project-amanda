"""Train the "hey Amanda" wake word on Windows, with openWakeWord's own train.py.

Run from this folder, in the training venv (see README.md here):

    D:\\amanda-wakeword\\.venv\\Scripts\\python train_hey_amanda.py --generate_clips
    D:\\amanda-wakeword\\.venv\\Scripts\\python train_hey_amanda.py --augment_clips
    D:\\amanda-wakeword\\.venv\\Scripts\\python train_hey_amanda.py --train_model

Two things stand between train.py and Windows, and both are handled here
rather than by editing the installed package:

  * It imports `generate_samples` from `piper_sample_generator_path`, which is
    this folder: generate_samples.py is a Windows stand-in built on Piper.
  * Its training DataLoader uses worker processes over a live generator.
    Windows starts workers by spawning, which has to pickle the dataset, and a
    generator cannot be pickled. Loading in-process instead costs little: the
    batches come from a memory-mapped file.

The last step converts the model to TFLite, which needs TensorFlow. The ONNX
model is written first, which is the one Amanda loads, so that failure is
reported and otherwise ignored.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "hey_amanda.yaml"


def _in_process_loading() -> None:
    """Load batches in this process, and validation sets straight from memory.

    Validation data comes as a TensorDataset with a batch size of the whole
    set -- 481,329 windows, 3 GB, for the false positive set -- so every
    validation fetches 481,329 items one by one and collates them into a fresh
    3 GB copy. On the renderer PC the process's own memory grew by about that
    much per validation and never came back: from step 37,500, where
    validation starts, to 104 GB and a kill for low memory in the second
    pass. Handing training the tensors themselves, moved to the GPU once and
    reused, does the same arithmetic without the copy. AMANDA_COLLATE=1 turns
    this off, to compare.
    """
    import torch
    import torch.utils.data

    original = torch.utils.data.DataLoader
    resident = os.environ.get("AMANDA_COLLATE") != "1"

    class InProcess(original):
        def __init__(self, dataset, *args, **kwargs):
            kwargs["num_workers"] = 0
            kwargs.pop("prefetch_factor", None)
            super().__init__(dataset, *args, **kwargs)
            self._whole = None
            if (
                resident
                and isinstance(dataset, torch.utils.data.TensorDataset)
                and self.batch_size is not None
                and self.batch_size >= len(dataset)
            ):
                self._whole = dataset.tensors

        def __iter__(self):
            if self._whole is None:
                return super().__iter__()
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if self._whole[0].device.type != device:
                self._whole = tuple(tensor.to(device) for tensor in self._whole)
            return iter([self._whole])

    torch.utils.data.DataLoader = InProcess


def _old_scipy_name() -> None:
    """Put back scipy.special.sph_harm, in this process and every one it starts.

    See shim/sitecustomize.py: `acoustics` still imports the name SciPy 1.15
    removed. This process has already started, so it is patched directly;
    worker processes inherit PYTHONPATH and load the shim before anything else.
    Restoring the name rather than pinning an old SciPy into a Python it may not
    build for."""
    shim = HERE / "shim"
    os.environ["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(shim), os.environ.get("PYTHONPATH")])
    )
    sys.path.insert(0, str(shim))
    import sitecustomize  # noqa: F401 - applied on import


def _trim_after_closing() -> None:
    """Trim each feature file once nothing has it mapped.

    openWakeWord computes features into a memory-mapped file, then trims its
    unused rows by writing a copy and deleting the original -- while its own
    caller still has the original mapped. Linux allows that; Windows refuses
    (WinError 32), and the first full run lost its positive features to it.
    So the trim is deferred until compute_features_from_generator has returned
    and released its map, and done with every map closed before the swap.
    """
    import gc

    import numpy as np
    import openwakeword.data
    import openwakeword.utils
    from numpy.lib.format import open_memmap

    pending: list[str] = []
    openwakeword.data.trim_mmap = pending.append  # utils imports it at call time

    def trim(path: str) -> None:
        data = np.load(path, mmap_mode="r")
        last = -1
        while last > -data.shape[0] and np.all(data[last] == 0):
            last -= 1
        rows = data.shape[0] + last + 1
        tmp = path[: -len(".npy")] + ".trimmed.npy"
        out = open_memmap(tmp, mode="w+", dtype=np.float32, shape=(rows, *data.shape[1:]))
        for start in range(0, rows, 1024):
            out[start : min(start + 1024, rows)] = data[start : min(start + 1024, rows)]
        out.flush()
        del out, data
        gc.collect()
        os.replace(tmp, path)

    compute = openwakeword.utils.compute_features_from_generator

    def compute_then_trim(*args, **kwargs):
        result = compute(*args, **kwargs)
        gc.collect()  # the caller's map of the file goes with its frame
        while pending:
            trim(pending.pop())
        return result

    openwakeword.utils.compute_features_from_generator = compute_then_trim


def _release_mapped_pages(interval: float = 30.0) -> None:
    """Keep the process's working set from filling RAM with the features file.

    Training draws random batches from 17 GB of memory-mapped negative
    features. On Windows every page touched stays in the process's working
    set, counted as memory in use, so the first full run grew until it was
    stopped for low memory 88% of the way through. Those pages are clean -- a
    re-read from disk if wanted again -- so emptying the working set every
    `interval` seconds costs little. It also prints the process's memory each
    minute, so the next run cannot creep up unseen.
    """
    if os.name != "nt":
        return
    import ctypes
    import threading
    import time
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # Declared, not left to ctypes' defaults: the current-process handle is -1,
    # and passed as a default 32-bit int it arrives as an invalid handle, so
    # both calls fail silently -- the first run of this read 0.0 GB and
    # released nothing.
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()

    def memory() -> str:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return (
            f"working set {counters.WorkingSetSize / 2**30:.1f} GB, "
            f"private {counters.PagefileUsage / 2**30:.1f} GB"
        )

    def run() -> None:
        ticks = 0
        while True:
            time.sleep(interval)
            before = memory()
            psapi.EmptyWorkingSet(process)
            ticks += 1
            if ticks % max(1, round(60 / interval)) == 0:
                print(f"\n[memory] {before}; emptied to {memory()}", flush=True)

    threading.Thread(target=run, name="release-mapped-pages", daemon=True).start()


def main() -> int:
    import openwakeword

    os.chdir(HERE)
    _old_scipy_name()
    _in_process_loading()
    _trim_after_closing()
    if "--train_model" in sys.argv[1:]:
        _release_mapped_pages()
    train = Path(openwakeword.__file__).parent / "train.py"
    args = sys.argv[1:]
    config = CONFIG
    if "--steps" in args:
        # A short run through the same code, for checking memory: validation
        # runs 20 times per pass whatever the step count.
        at = args.index("--steps")
        steps = int(args[at + 1])
        del args[at : at + 2]
        import yaml

        settings = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        settings["steps"] = steps
        # Beside the data, not in the repo. The config's relative paths are
        # relative to the working directory, which is still this folder.
        config = Path(r"D:\amanda-wakeword") / f"_steps_{steps}.yaml"
        config.write_text(yaml.safe_dump(settings), encoding="utf-8")
    sys.argv = [str(train), "--training_config", str(config), *args]
    try:
        runpy.run_path(str(train), run_name="__main__")
    except ImportError as exc:
        model = Path("D:/amanda-wakeword/model/hey_amanda.onnx")
        if "--train_model" in sys.argv and model.exists():
            print(f"ONNX model written to {model}; TFLite conversion skipped ({exc})")
            return 0
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

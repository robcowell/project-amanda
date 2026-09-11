"""Loaded at startup by every Python process train_hey_amanda.py starts.

`acoustics` 0.2.6, imported by openwakeword.data, still asks for
scipy.special.sph_harm, which SciPy 1.15 removed in favour of sph_harm_y
(arguments reordered). Nothing train.py runs calls it, but the import alone
fails.

It has to be here rather than in train_hey_amanda.py: clip generation runs in
worker processes, and on Windows each worker starts by re-importing the main
script -- train.py -- whose first imports reach `acoustics` before any code of
ours could run. `sitecustomize` runs before all of it, in every process whose
PYTHONPATH includes this folder.
"""

try:
    import scipy.special

    if not hasattr(scipy.special, "sph_harm"):
        scipy.special.sph_harm = lambda m, n, theta, phi: scipy.special.sph_harm_y(
            n, m, phi, theta
        )
except ImportError:
    pass

#!/usr/bin/env bash
# Cratered regolith base for the Lava Tube scene: a FLAT plane (no real DEM, no slope) + procedural
# impact craters + regolith roughness for a natural pitted look, with the central corridor + tube
# footprint flattened so the rover drives a clean path and the tube sits flush. Pure numpy.
# Separate dems_path (Terrains/LavaTube) so terrain_id=0 is unambiguous.
#   docker exec isaac-sim-omnilrs-container bash /workspace/omnilrs/tmp/make_lava_dem.sh
set -euo pipefail
OUT=/workspace/omnilrs/assets/Terrains/LavaTube/lava_base
mkdir -p "$OUT"
python3 - "$OUT" <<'PY'
import sys, numpy as np
out = sys.argv[1]
H = W = 800
RES = 50.0 / W                      # 0.0625 m/vertex (lab 50 / 800)
a = np.zeros((H, W), dtype='float32')
rng = np.random.default_rng(7)

# procedural impact craters (bowl + raised rim), lunaryard-like size bins
yy, xx = np.mgrid[0:H, 0:W].astype('float32')
for count, rmin, rmax in [(6, 1.0, 2.0), (16, 0.5, 1.0), (40, 0.25, 0.5)]:
    for _ in range(count):
        cx, cy = rng.uniform(0, W), rng.uniform(0, H)
        Rpx = rng.uniform(rmin, rmax) / RES
        depth = rng.uniform(0.12, 0.20) * (2 * Rpx * RES)
        r = np.hypot(xx - cx, yy - cy) / Rpx
        a += np.where(r < 1.0, -depth * (1.0 - r * r), 0.0).astype('float32')   # bowl
        a += (0.18 * depth) * np.exp(-((r - 1.0) / 0.18) ** 2).astype('float32')  # rim

# regolith roughness
def _blur(x, k=8):
    for _ in range(k):
        x = (x + np.roll(x, 1, 0) + np.roll(x, -1, 0) + np.roll(x, 1, 1) + np.roll(x, -1, 1)) / 5.0
    return x
n = _blur(rng.standard_normal((H, W)).astype('float32'))
a += (0.04 / (n.std() + 1e-6)) * n
a = (a - a.min()).astype('float32')          # surface min -> 0 (rover/tube reference)
# fully cratered (no flat path): the rover drives the pitted surface as in PSR; the tube walls
# extend below the surface (lava_tube.usda) so the cratered ground does not leave base gaps.

np.save(out + '/dem.npy', a)
np.save(out + '/mask.npy', np.ones_like(a, dtype=bool))
print('wrote', out + '/dem.npy', a.shape, 'relief_m=%.2f' % float(a.max() - a.min()))
PY

#!/usr/bin/env bash
# Build the PSR premade-DEM (OmniLRS Lunaryard TerrainManager format) from a real
# Shackleton DEM slice. Run INSIDE the isaac-sim-omnilrs container (has gdal + numpy).
#
#   docker exec isaac-sim-omnilrs-container bash /workspace/omnilrs/tmp/make_psr_dem.sh
#
# Source: LDEM_83S_10MPP_ADJ.tiff (Moon S-polar stereographic, 10 m/px, pole at px 21640,21640).
# Slice : Shackleton S-rim inner wall, srcwin 22660 23150 20 20 = a 200 m x 200 m real patch.
# Output: assets/Terrains/Lunaryard/shackleton_psr/{dem.npy,mask.npy}  (800x800, float32 metres).
#
# Lunaryard maps 1 dem pixel -> 1 mesh vertex spaced `resolution` m; vertex z = dem value (m),
# re-zeroed here so the patch floor sits at z=0. VERTICAL SCALE Z_K (<1) gentles the real ~35 deg
# wall to a rover-drivable grade -- horizontal geometry + feature layout stay the real DEM; the
# vertical scaling is DISCLOSED (notes + DL). Tune Z_K / srcwin and re-run to iterate.
set -euo pipefail

T=${1:-/workspace/omnilrs/tmp/LDEM_83S_10MPP_ADJ.tiff}
SRCWIN="22660 23150 20 20"      # xoff yoff xsize ysize (px) -- 200 m Shackleton inner-wall slice
MESH=800                        # output dem side (= lab_length / resolution = 200 / 0.25)
Z_K=0.20                        # vertical scale for drivability (real ~35deg -> ~8deg, climbable); DISCLOSED
OUT=/workspace/omnilrs/assets/Terrains/Lunaryard/shackleton_psr
TIF=/workspace/omnilrs/tmp/psr_${MESH}.tif
RAW=/workspace/omnilrs/tmp/psr_${MESH}.raw

# windowed crop + resample to MESHxMESH (CLI gdal; keep .tif for hillshade verification),
# then flatten to a raw float32 binary (ENVI) so python reads it with PURE numpy --
# importing osgeo.gdal alongside numpy segfaults in this container.
gdal_translate -q -srcwin $SRCWIN -outsize $MESH $MESH -r cubic "$T" "$TIF"
gdal_translate -q -of ENVI -ot Float32 "$TIF" "$RAW"
mkdir -p "$OUT"

python3 - "$RAW" "$OUT" "$Z_K" "$MESH" <<'PY'
import sys, numpy as np
raw, out, zk, mesh = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
res = 200.0 / mesh                              # m per vertex (= env resolution)
a = np.fromfile(raw, dtype='<f4').reshape(mesh, mesh)  # ENVI = row-major little-endian float32
a = a - a.min()            # re-zero: patch floor -> 0 m
a = a * zk                 # real Shackleton macro slope, vertical-scaled (drivability; disclosed)
H, W = a.shape
rng = np.random.default_rng(42)

# --- (2) procedural impact craters: bowl (parabola) + raised rim ring, lunaryard-like size bins.
#     The 10 m/px source carries only the macro slope; sub-10 m relief (the small craters the
#     earlier lunaryard take showed) is synthesized here -- same idea as OmniLRS LargeScale adding
#     procedural craters onto a low-res DEM. (count, Rmin_m, Rmax_m) ; depth ~ 0.12-0.20 * diameter.
yy, xx = np.mgrid[0:H, 0:W].astype('float32')
for count, rmin, rmax in [(10, 1.5, 3.0), (30, 0.9, 1.5), (90, 0.5, 0.9)]:
    for _ in range(count):
        cx, cy = rng.uniform(0, W), rng.uniform(0, H)
        Rpx = rng.uniform(rmin, rmax) / res
        depth = rng.uniform(0.12, 0.20) * (2 * Rpx * res)
        r = np.hypot(xx - cx, yy - cy) / Rpx
        a += np.where(r < 1.0, -depth * (1.0 - r * r), 0.0).astype('float32')   # bowl
        a += (0.18 * depth) * np.exp(-((r - 1.0) / 0.18) ** 2).astype('float32')  # rim

# --- (3) gentle regolith undulation: box-blurred white noise, ~8 cm RMS (no scipy dependency).
def _blur(x, k=8):
    for _ in range(k):
        x = (x + np.roll(x, 1, 0) + np.roll(x, -1, 0) + np.roll(x, 1, 1) + np.roll(x, -1, 1)) / 5.0
    return x
n = _blur(rng.standard_normal((H, W)).astype('float32'))
a += (0.05 / (n.std() + 1e-6)) * n

# --- flat spawn pad: level a small disc at the husky spawn so it starts level (not tilted on a
#     crater/bump). World spawn (100,25); vertex z = flip(DEM,0) so the DEM-array row is H-1-row_v.
sx, sy, pad_r = 100.0, 25.0, 3.0
col_s, row_v = int(sx / res), int(sy / res)
drow = H - 1 - row_v
rr = int(pad_r / res)
ys2, xs2 = np.mgrid[0:H, 0:W]
disc = (xs2 - col_s) ** 2 + (ys2 - drow) ** 2 <= rr * rr
a[disc] = float(np.median(a[disc]))

np.save(out + '/dem.npy', a.astype('float32'))
np.save(out + '/mask.npy', np.ones_like(a, dtype=bool))
a.astype('<f4').tofile(out + '/final.dat')   # flat binary + ENVI hdr for a gdaldem hillshade self-check
open(out + '/final.hdr', 'w').write(
    "ENVI\nsamples = %d\nlines = %d\nbands = 1\nheader offset = 0\ndata type = 4\n"
    "interleave = bsq\nbyte order = 0\n"
    "map info = {Arbitrary, 1.0, 1.0, 0.0, 0.0, %g, %g, units=Meters}\n" % (W, H, res, res))
print('wrote', out + '/dem.npy', a.shape, a.dtype,
      'relief_m=%.2f' % float(a.max() - a.min()),
      'craters=130  regolith_rms~0.08m')
PY

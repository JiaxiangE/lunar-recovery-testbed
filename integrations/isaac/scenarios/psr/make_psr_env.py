"""Derive cfg/environment/lunaryard_psr.yaml from lunaryard_20m.yaml with a minimal diff.

Round-trips through PyYAML (OmegaConf re-resolves the ${...} relative interpolations on load,
since no nodes move). Run INSIDE the isaac-sim container (PyYAML + numpy present; the dem.npy
load is numpy-only, do NOT import osgeo -- it segfaults alongside numpy here):

  docker exec isaac-sim-omnilrs-container bash -lc     "cd /workspace/omnilrs && python3 tmp/make_psr_env.py        cfg/environment/lunaryard_20m.yaml cfg/environment/lunaryard_psr.yaml"

Adds, over lunaryard_20m: the Shackleton premade-DEM terrain (200 m @ 0.25), a no-fall level
husky spawn, a low fixed PSR sun, COLLIDABLE static_assets (a base lander + boulders flanking the
descent corridor -- the PointInstancer rocks render without colliders so the rover clips through
them; these replace them with real colliders), and a monitoring camera (side-3/4 view of the
corridor, published on a ROS2 topic) so the scene can be inspected/figured without an X grab.
"""
import sys, yaml, numpy as np
src, dst = (sys.argv[1], sys.argv[2])
c = yaml.safe_load(open(src))
c['lunaryard_settings']['lab_length'] = 200.0
c['lunaryard_settings']['lab_width'] = 200.0
c['lunaryard_settings']['resolution'] = 0.25
c['lunaryard_settings']['terrain_id'] = 0
c['lunaryard_settings']['coordinates'] = {'latitude': -89.9, 'longitude': 0.0}
c['rocks_settings']['enable'] = False
c['robots_settings']['parameters']['pose']['position'] = [100.0, 25.0, 22.8]
c['robots_settings']['parameters']['pose']['orientation'] = [1.0, 0.0, 0.0, 0.0]
c['stellar_engine_settings'] = None
c['sun_settings']['elevation'] = 13.0
c['sun_settings']['azimuth'] = 200.0
c['sun_settings']['intensity'] = 2200.0
d = np.load('/workspace/omnilrs/assets/Terrains/Lunaryard/shackleton_psr/dem.npy')
df = np.flip(d, 0)
H, W = d.shape
RES = 200.0 / W

def h(x, y):
    r = min(H - 1, max(0, int(y / RES)))
    cc = min(W - 1, max(0, int(x / RES)))
    return float(df[r, cc])

def asset(name, usd, x, y, zoff=0.0, orient=(1.0, 0.0, 0.0, 0.0), collision=True):
    return {'asset_name': name, 'usd_path': usd, 'pose': {'position': [float(x), float(y), round(h(x, y) + zoff, 2)], 'orientation': list(orient)}, 'collision': collision}
ROCK = 'USD_Assets/rocks/lunalab_rocks/rock_%d/rock%d.usd'
boulders = [(95, 30), (105, 33), (96, 39), (104, 28), (94, 44), (106, 41), (93, 35), (107, 37)]
ice = [(97, 32), (103, 36), (98, 41), (102, 44), (96, 38)]
params = [asset('base_lander', 'USD_Assets/common/lander/Vikram/Vikram.usd', 100, 17, 0.0, (0.5, 0.5, 0.5, 0.5))]
for i, (x, y) in enumerate(boulders):
    rk = i % 4 + 1
    params.append(asset('boulder_%d' % i, ROCK % (rk, rk), x, y))
for i, (x, y) in enumerate(ice):
    params.append(asset('ice_%d' % i, 'USD_Assets/psr/ice_deposit.usda', x, y, 0.0, collision=False))
params.append(asset('relay_station', 'USD_Assets/psr/relay_station.usda', 107, 33, 0.0))
c['static_assets_settings'] = {'root_path': '/StaticAssets', 'parameters': params}

def lookat_xyzw(C, T, up=(0.0, 0.0, 1.0)):
    C, T, up = (np.array(C, float), np.array(T, float), np.array(up, float))
    f = T - C
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    M = np.array([[r[0], u[0], -f[0]], [r[1], u[1], -f[1]], [r[2], u[2], -f[2]]])
    tr = M.trace()
    if tr > 0:
        s = 0.5 / np.sqrt(tr + 1.0)
        w, x, y, z = (0.25 / s, (M[2, 1] - M[1, 2]) * s, (M[0, 2] - M[2, 0]) * s, (M[1, 0] - M[0, 1]) * s)
    else:
        i = int(np.argmax(np.diag(M)))
        if i == 0:
            s = 2 * np.sqrt(1 + M[0, 0] - M[1, 1] - M[2, 2])
            w, x, y, z = ((M[2, 1] - M[1, 2]) / s, 0.25 * s, (M[0, 1] + M[1, 0]) / s, (M[0, 2] + M[2, 0]) / s)
        elif i == 1:
            s = 2 * np.sqrt(1 + M[1, 1] - M[0, 0] - M[2, 2])
            w, x, y, z = ((M[0, 2] - M[2, 0]) / s, (M[0, 1] + M[1, 0]) / s, 0.25 * s, (M[1, 2] + M[2, 1]) / s)
        else:
            s = 2 * np.sqrt(1 + M[2, 2] - M[0, 0] - M[1, 1])
            w, x, y, z = ((M[1, 0] - M[0, 1]) / s, (M[0, 2] + M[2, 0]) / s, (M[1, 2] + M[2, 1]) / s, 0.25 * s)
    return [round(float(x), 6), round(float(y), 6), round(float(z), 6), round(float(w), 6)]
cam = [116.0, 33.0, round(h(116, 33) + 12.0, 2)]
tgt = [100.0, 33.0, round(h(100, 33) + 0.5, 2)]
c['monitoring_cameras_settings'] = {'enabled': True, 'root_path': '/MonitoringCameras', 'camera_definitions': [{'name': 'psr_chase', 'pose': {'position': cam, 'orientation': lookat_xyzw(cam, tgt)}, 'camera_params': {'focal_length': 1.93, 'horizontal_aperture': 2.4, 'vertical_aperture': 1.8, 'fstop': 5.0, 'focus_distance': 10.0, 'clipping_range': [0.01, 1000000.0]}, 'ros2': {'topic': '/monitoring/psr_chase', 'frame_id': 'psr_chase'}, 'type': 'Rgb', 'resolution': [640, 480]}]}
yaml.safe_dump(c, open(dst, 'w'), sort_keys=False, default_flow_style=False)
print('wrote', dst, '| static_assets=%d (1 lander + %d boulders) | cam=%s look %s' % (len(params), len(boulders), cam, lookat_xyzw(cam, tgt)))

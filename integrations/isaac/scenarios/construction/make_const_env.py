"""Derive cfg/environment/lunaryard_const.yaml from lunaryard_20m.yaml (PyYAML round-trip; run in
the isaac-sim container, numpy-only, NO osgeo).

  docker exec isaac-sim-omnilrs-container bash -lc     "cd /workspace/omnilrs && python3 tmp/make_const_env.py        cfg/environment/lunaryard_20m.yaml cfg/environment/lunaryard_const.yaml"

Construction scene (design 8.2): the flat cratered lunaryard base (reuses Terrains/LavaTube/lava_base)
+ a base lander (comm anchor) + simple construction modules / solar panel / comm tower placed via
static_assets. comm stays ONLINE (run the comm_evaluator with a large range and NO occlusion) so the
broad sigma=S re-decomposition may use the base -- the contrast to PSR/Lava's comm-down forced edge T.
"""
import sys, yaml, numpy as np
src, dst = (sys.argv[1], sys.argv[2])
c = yaml.safe_load(open(src))
LAB = 50.0
c['lunaryard_settings']['lab_length'] = LAB
c['lunaryard_settings']['lab_width'] = LAB
c['lunaryard_settings']['resolution'] = round(LAB / 800.0, 6)
c['lunaryard_settings']['terrain_id'] = 0
c['lunaryard_settings']['coordinates'] = {'latitude': -85.0, 'longitude': 0.0}
c['terrain_manager']['dems_path'] = 'Terrains/LavaTube'
c['rocks_settings']['enable'] = False
c['stellar_engine_settings'] = None
c['sun_settings']['elevation'] = 30.0
c['sun_settings']['azimuth'] = 135.0
c['sun_settings']['intensity'] = 2600.0
d = np.load('/workspace/omnilrs/assets/Terrains/LavaTube/lava_base/dem.npy')
df = np.flip(d, 0)
H, W = d.shape
RES = LAB / W

def h(x, y):
    return float(df[min(H - 1, max(0, int(y / RES))), min(W - 1, max(0, int(x / RES)))])
c['robots_settings']['parameters']['pose']['position'] = [25.0, 10.0, round(h(25, 10) + 0.4, 2)]
c['robots_settings']['parameters']['pose']['orientation'] = [1.0, 0.0, 0.0, 0.0]

def asset(name, usd, x, y, zoff=0.0, orient=(0.0, 0.0, 0.0, 1.0), collision=True):
    return {'asset_name': name, 'usd_path': usd, 'pose': {'position': [float(x), float(y), round(h(x, y) + zoff, 2)], 'orientation': list(orient)}, 'collision': collision}
BLOCK = 'USD_Assets/construction/construction_block.usda'
params = [asset('base_lander', 'USD_Assets/common/lander/Vikram/Vikram.usd', 25, 41, 0.0, (0.5, 0.5, 0.5, 0.5)), asset('assembler_R2', 'USD_Assets/robots/nograph_husky.usd', 25, 24, 0.0), asset('foundation_A', BLOCK, 25, 27, 0.0), asset('module_staged', BLOCK, 22, 12, 0.0), asset('solar_panel', 'USD_Assets/construction/solar_panel.usda', 17, 32, 0.0), asset('comm_tower', 'USD_Assets/psr/relay_station.usda', 33, 38, 0.0)]
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
            w = (M[2, 1] - M[1, 2]) / s
            x = 0.25 * s
            y = (M[0, 1] + M[1, 0]) / s
            z = (M[0, 2] + M[2, 0]) / s
        elif i == 1:
            s = 2 * np.sqrt(1 + M[1, 1] - M[0, 0] - M[2, 2])
            w = (M[0, 2] - M[2, 0]) / s
            x = (M[0, 1] + M[1, 0]) / s
            y = 0.25 * s
            z = (M[1, 2] + M[2, 1]) / s
        else:
            s = 2 * np.sqrt(1 + M[2, 2] - M[0, 0] - M[1, 1])
            w = (M[1, 0] - M[0, 1]) / s
            x = (M[0, 2] + M[2, 0]) / s
            y = (M[1, 2] + M[2, 1]) / s
            z = 0.25 * s
    return [round(float(x), 6), round(float(y), 6), round(float(z), 6), round(float(w), 6)]
cam = [43.0, 14.0, round(h(43, 14) + 14.0, 2)]
tgt = [26.0, 26.0, round(h(26, 26) + 1.0, 2)]
c['monitoring_cameras_settings'] = {'enabled': True, 'root_path': '/MonitoringCameras', 'camera_definitions': [{'name': 'psr_chase', 'pose': {'position': cam, 'orientation': lookat_xyzw(cam, tgt)}, 'camera_params': {'focal_length': 1.93, 'horizontal_aperture': 2.4, 'vertical_aperture': 1.8, 'fstop': 5.0, 'focus_distance': 10.0, 'clipping_range': [0.01, 1000000.0]}, 'ros2': {'topic': '/monitoring/psr_chase', 'frame_id': 'psr_chase'}, 'type': 'Rgb', 'resolution': [640, 480]}]}
yaml.safe_dump(c, open(dst, 'w'), sort_keys=False, default_flow_style=False)
print('wrote', dst, '| static_assets=%d | cam=%s' % (len(params), cam))

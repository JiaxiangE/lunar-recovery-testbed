"""Correct known relocated references in a new external-asset overlay only."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def relocated_reference(ref):
    if ref.startswith('file:') and ref.endswith('velodyne-vlp16.usd'):
        return 'velodyne-vlp16.usd'
    if ref.startswith('omniverse://localhost/') and ref.endswith('/rsd455.usd'):
        return '../common/rsd455.usd'
    return None

def main():
    from pxr import Sdf
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--external-assets', type=Path, required=True)
    a = p.parse_args()
    root = a.root.resolve()
    assets = root / 'assets'
    rows = []
    names = ['robots/ros2_husky_PhysX_vlp16.usd', 'robots/ros2_husky_PhysX_vlp16_mono_depth_imu.usd', 'robots/nograph_husky.usd', 'common/rsd455.usd', 'common/Earth.usd', 'common/lander/Vikram/Vikram.usd']
    names += [f'rocks/lunalab_rocks/rock_{i}/rock{i}.usd' for i in range(1, 5)]
    for name in names:
        dest = assets / 'USD_Assets' / name
        source = a.external_assets / 'USD_Assets' / name
        if not source.is_file():
            rows.append({'path': name, 'missing': True})
            continue
        original = source.read_bytes()
        layer = Sdf.Layer.FindOrOpen(str(source))
        text = layer.ExportToString()
        changes = []
        for ref in sorted(set(re.findall('@([^@\\r\\n]*)@', text))):
            new = relocated_reference(ref)
            if new:
                target = source.parent / new
                if not target.is_file():
                    raise FileNotFoundError(target)
                text = text.replace('@' + ref + '@', '@' + new + '@')
                changes.append({'old': ref, 'new': new})
        if changes:
            if not dest.parent.resolve().is_relative_to(assets.resolve()):
                raise ValueError('overlay directory escaped')
            if not dest.is_symlink():
                raise ValueError('reference patch requires a fresh overlay link')
            dest.unlink()
            dest.write_text(text, encoding='utf-8')
            if Sdf.Layer.FindOrOpen(str(dest)) is None:
                raise ValueError('prepared USD is not parseable')
        rows.append({'path': name, 'source_sha256': hashlib.sha256(original).hexdigest(), 'changes': changes, 'prepared_sha256': hashlib.sha256(dest.read_bytes() if changes else original).hexdigest()})
    a.output.write_text(json.dumps({'version': 'relocated-reference-overlay-v1', 'rows': rows, 'material_policy': 'bare OmniPBR/OmniGlass references retained for actual Isaac 5 resolver validation; no substitute shader', 'scope': 'new installation configuration; original external files and historical provenance unchanged'}, indent=2) + '\n')
if __name__ == '__main__':
    main()

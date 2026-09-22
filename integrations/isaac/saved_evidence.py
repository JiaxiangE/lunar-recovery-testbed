"""Check saved simulator observations against their bound candidate content."""
import gzip
import json
from pathlib import Path
from integrations.isaac.motion_record import bind_record_content

def verify():
    root = Path(__file__).resolve().parents[2] / 'data/isaac'

    def read(p):
        if p.suffix == '.gz':
            with gzip.open(p, 'rt', encoding='utf-8') as f:
                return json.load(f)
        return json.loads(p.read_text(encoding='utf-8'))
    rows = []
    paper = root / 'paper'
    bound = bind_record_content(read(paper / 'positive_rgb5s_input.json'), read(paper / 'positive_rgb5s_clock_bound.json.gz'))
    if not bound['content_bound']:
        raise AssertionError('paper motion content binding failed')
    for name in ('psr-07', 'lava-01', 'construction-01'):
        folder = root / 'installation' / name
        result = bind_record_content(read(folder / 'input.json'), read(folder / 'motion.json.gz'))
        if not result['content_bound'] or result['physical_parent_goal_met'] is not None:
            raise AssertionError('saved installation binding or goal scope differs')
        rows.append({'scene_record': name, 'content_bound': True, 'physical_parent_goal_met': None})
    return {'paper_motion_bound': True, 'installation_records': rows, 'measurement': 'saved simulator observations; CPU content-binding check'}

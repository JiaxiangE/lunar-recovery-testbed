"""Small offline examples using explicitly declared deterministic responses."""
import argparse
import json
from pathlib import Path
from controller.api import recover
from examples.recovery_inputs import build_case
CASES = {'psr-return': 'B05_psr_strict_disconnected', 'psr-position': 'R_T_psr_collection_position_drift', 'lava-return': 'E05_lava_communication_return', 'construction': 'R_S_construction_prefix_source_inj005'}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case', choices=CASES, default='psr-position')
    p.add_argument('--method', choices=('ours', 'flat', 'htn', 'bt'), default='ours')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError('select a new output directory')
    a.output.mkdir(parents=True)
    case = build_case(CASES[a.case], a.seed)
    trial, calls = recover(case, a.output, method=a.method, generator='fixture')
    (a.output / 'trial.json').write_text(json.dumps(trial, indent=2) + '\n', encoding='utf-8')
    if calls is not None:
        (a.output / 'calls.json').write_text(json.dumps(calls, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': trial['status'], 'original_goal': trial['original_goal_met'], 'alternative_goal': trial['degraded_goal_met'], 'dispatches': trial['dispatch_n'], 'generator': 'deterministic example responses; no model inference'}))
    return 0 if trial['original_goal_met'] or trial['degraded_goal_met'] else 1
if __name__ == '__main__':
    raise SystemExit(main())

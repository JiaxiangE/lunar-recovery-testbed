"""Read-only reanalysis of saved trials/calls; never imports a model client."""
from collections import Counter
import json
from pathlib import Path
from copy import deepcopy

def request_parity(folder):
    from reproduction.context import payload
    checks = []
    allowed = {'remaining_model_logical_including_this_request', 'remaining_physical_capacity', 'remaining_token_reservation', 'remaining_trial_model_logical'}
    for path in sorted(Path(folder).glob('d*/R0/s*/calls.jsonl')):
        other = path.parents[2] / 'R1' / path.parent.name / 'calls.jsonl'
        if not other.exists():
            continue
        qs = [next((q for q in json.loads(p.read_text(encoding='utf-8'))['requests'] if q['operation'] == 'D_T'), None) for p in (path, other)]
        if not all(qs):
            checks.append({'control': path.as_posix(), 'treated': other.as_posix(), 'both_generated_Task': False})
            continue
        a, b = [deepcopy(payload(q)) for q in qs]
        changed = [k for k in set(a['generation_constraints']) | set(b['generation_constraints']) if a['generation_constraints'].get(k) != b['generation_constraints'].get(k)]
        for item in (a, b):
            for k in allowed:
                item['generation_constraints'].pop(k, None)
        checks.append({'control': path.as_posix(), 'treated': other.as_posix(), 'both_generated_Task': True, 'same_system': qs[0]['system'] == qs[1]['system'], 'changed_constraint_keys': sorted(changed), 'same_complete_payload_except_remaining_budget': a == b, 'only_declared_budget_differences': set(changed) <= allowed})
    return checks

def analyze(folder):
    folder = Path(folder)
    units = []
    for path in sorted(folder.glob('d*/R*/s*/trial.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        calls = json.loads(path.with_name('calls.jsonl').read_text(encoding='utf-8'))
        native = row.get('native') or {}
        by_id = {q.get('logical_call_id'): q for q in calls['requests']}
        costs = {}
        for physical in calls['physical_attempts']:
            if physical['measurement_provenance'] != 'actual':
                continue
            operation = by_id.get(physical['logical_call_id'], {}).get('operation', 'unknown')
            cost = costs.setdefault(operation, {'physical_n': 0, 'known_tokens': 0, 'unknown_usage_n': 0, 'transport_wall_s': 0.0})
            cost['physical_n'] += 1
            tokens = physical['usage']['total_tokens']
            if type(tokens) is int:
                cost['known_tokens'] += tokens
            else:
                cost['unknown_usage_n'] += 1
            cost['transport_wall_s'] += physical.get('elapsed_s', 0.0)
        events = native.get('generated_hierarchy', [])
        rejections = Counter((e['rejection'] for e in events if e.get('rejection')))
        units.append({k: row[k] for k in ('cell', 'arm', 'seed', 'status', 'original_goal_met', 'logical_n', 'actual_api_calls', 'actual_physical_posts', 'actual_provider_tokens', 'known_provider_tokens', 'unknown_usage_physical_n', 'dispatch_n', 'total_wall_s', 'source_category')} | {'trial': path.as_posix(), 'calls': path.with_name('calls.jsonl').as_posix(), 'selected_scope': native.get('selected_scope'), 'adopted_scope': native.get('final_scope'), 'posterior': native.get('posterior'), 'diagnosis': native.get('diagnosis_audit'), 'costs_by_operation': costs, 'candidate_rejections': dict(rejections), 'generation_attempts': len(events), 'generation_retries': sum((e.get('attempt', 0) > 1 for e in events)), 'scope_attempts': len(native.get('attempts', [])), 'first_executable_repair_scope': native.get('final_scope') if (row.get('execution') or {}).get('execution_success') else None, 'local_work': row['local_work']['metrics'], 'scope_infeasibility_not_inferred_from_candidate_rejection': True})
    arms = {}
    for arm in ('R0', 'R1', 'R2'):
        rows = [u for u in units if u['arm'] == arm]
        if not rows:
            continue
        arms[arm] = {'units': len(rows), 'parent_met_n': sum((u['original_goal_met'] for u in rows)), 'logical_n': sum((u['logical_n'] for u in rows)), 'physical_n': sum((u['actual_physical_posts'] for u in rows)), 'known_tokens': sum((u['known_provider_tokens'] for u in rows)), 'unknown_usage_physical_n': sum((u['unknown_usage_physical_n'] for u in rows)), 'inclusive_wall_s': sum((u['total_wall_s'] for u in rows)), 'selected_scopes': dict(Counter((u['selected_scope'] for u in rows))), 'candidate_rejections': dict(sum((Counter(u['candidate_rejections']) for u in rows), Counter()))}
    return {'units': units, 'arms': arms, 'R0_R1_first_Task_request_parity': request_parity(folder), 'interpretation': {'denominator': 'one fixed task layout, two current-state observation conditions plus an explicit T-only operation negative control; two response seeds are not independent tasks', 'first_executable': 'actual successful dispatch and complete parent, not a scope name or accepted diagnostic answer', 'scope_failures': 'candidate rejection is not proof the entire scope is infeasible; P audit exhausts only the actual deterministic original-action retry mechanism', 'time': 'inclusive unit wall; diagnosis/transport/resource components overlap and are not added to it', 'latency_comparison': 'descriptive only; sequential order/cache/host variations uncontrolled', 'reference': 'state precondition policy through frozen selector, not calibrated cause truth'}}

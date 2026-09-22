"""All six declared units, including unexecuted capacity-limited comparisons."""
from copy import deepcopy
import json
from reproduction.task_view_inputs import CELLS
from reproduction.task_view_inputs import ARMS
from reproduction.task_view_inputs import OUTPUT
from reproduction.task_view_inputs import case_input
from reproduction.task_view_inputs import check_views
from reproduction.costs import cost
from reproduction.costs import plus
from reproduction.context import payload
from execution.binding import execute_plan

def summarize(root):
    CELLS = ('R_T_psr_collection_position_drift',)
    folder = root / OUTPUT
    prep = json.loads((folder / 'offline/preparation.json').read_text(encoding='utf-8'))
    rows = []
    total = cost({})
    requests = {}
    for cell in CELLS:
        for arm in ARMS:
            trial_path = folder / cell / arm / 'trial.json'
            t = json.loads(trial_path.read_text(encoding='utf-8'))
            cp = trial_path.parent / 'calls.jsonl'
            calls = json.loads(cp.read_text(encoding='utf-8')) if cp.exists() else {}
            amount = cost(calls)
            total = plus(total, amount)
            qs = calls.get('requests', [])
            requests[cell, arm] = qs
            ex = t.get('execution') or {}
            c = case_input(cell)
            if ex:
                if ex['executed_plan'] != ex['verification']['normalized_plan'][:len(ex['executed_plan'])]:
                    raise AssertionError('unbound execution')
                independent = execute_plan(deepcopy(c.world), c.nominal, ex['verification']['normalized_plan'])
                if independent['goal_met'] != t['original_goal_met']:
                    raise AssertionError('parent/world mismatch')
            for q in qs:
                if q['route']['role'] != 'edge' or q['route']['communication'] != 'Disconnected':
                    raise AssertionError('non-edge or wrong communication')
                if q['route']['model'] != 'qwen-7b-local':
                    raise AssertionError('model substitution')
                measured = (q.get('tokenizer_measurement') or {}).get('logical_input_tokens')
                provider = ((q.get('provider_response') or {}).get('usage') or {}).get('prompt_tokens')
                if measured is not None and provider is not None and (measured != provider):
                    raise AssertionError('meter differs from provider')
            if any((l['seed'] != 1 for l in calls.get('logical_calls', []))):
                raise AssertionError('response seed changed')
            if any((p.get('measurement_provenance') != 'actual' for p in calls.get('physical_attempts', []))):
                raise AssertionError('stub presented as real generation')
            events = [e for a in t.get('attempts', []) for e in a.get('native', {}).get('generated_hierarchy', [])]
            first = qs[0] if qs else None
            row = {'cell': cell, 'arm': arm, 'source_trial': trial_path.relative_to(root).as_posix(), 'source_calls': cp.relative_to(root).as_posix() if cp.exists() else None, 'status': t['status'], 'parent_goal_met': t['original_goal_met'], 'input_fits': next((q['fits'] for q in prep['requests'] if q['cell'] == cell and q['arm'] == arm)), 'full_cost': amount, 'first_request_cost': cost(calls, [first]) if first else None, 'new_request_retry_n': max(0, len(qs) - 1) if qs else None, 'child_retry_n': sum((e['operation'] == 'D_T' and e.get('attempt', 1) > 1 for e in events)) if qs else None, 'selected_scope': next((a.get('native', {}).get('selected_scope') for a in t.get('attempts', []) if 'native' in a), None), 'dispatch_n': t['dispatch_n'], 'actors': sorted({s['agent_id'] for s in ex.get('executed_plan', [])}), 'actual_plan': ex.get('executed_plan'), 'world_s': ex.get('elapsed_s'), 'energy_wh': ex.get('energy_consumed_wh'), 'unit_wall_s': t.get('wall_s'), 'local_work': t.get('local_work'), 'first_request_action_rows': len(payload(first)['context']['domain']['actions']) if first else None, 'physical_history_prefix_n': len(c.executed_prefix_ids), 'original_suffix_rejected': not next((x for x in prep['cells'] if x['cell'] == cell))['audit']['suffix_symbolic']['accepted'], 'base_requests': sum((q['route']['role'] == 'base' for q in qs)), 'goal_policy': t.get('selected_policy'), 'extra_sample_outcomes': sorted((f for f in ex.get('final_facts', []) if f.startswith(('stored(', 'in_storage(', 'in_base_storage(')) and f not in c.nominal.initial_state and (f not in c.nominal.goal_facts))), 'not_run_reason': t.get('reason') if not qs else None}
            rows.append(row)
    comparisons = []
    for cell in CELLS:
        selected = {r['arm']: r for r in rows if r['cell'] == cell}
        if all((requests[cell, a] for a in ARMS)):
            check_views(payload(requests[cell, 'ours_full'][0]), payload(requests[cell, 'ours_view'][0]))
        for comparator in ('ours_full', 'flat_full'):
            a = selected['ours_view']
            b = selected[comparator]
            matched = bool(a['parent_goal_met'] and b['parent_goal_met'] and (a['extra_sample_outcomes'] == b['extra_sample_outcomes']))
            known = not any((sum(r['full_cost']['unknown_usage_attempts'].values()) for r in (a, b)))
            av = a['full_cost']['known_tokens_lower_bound']['total_tokens']
            bv = b['full_cost']['known_tokens_lower_bound']['total_tokens']
            comparisons.append({'cell': cell, 'comparator': comparator, 'same_parent_success_and_sample_output': matched, 'view_minus_comparator_tokens': av - bv if matched and known else None, 'fraction_reduction': 1 - av / bv if matched and known and bv else None, 'same_executed_plan': a['actual_plan'] == b['actual_plan'] if matched else None, 'scope': 'failed or unexecuted arm is not equal-success efficiency evidence; clocks and actors remain separate'})
    total['accounting'] = 'unique actual requests and physical attempts across the three paper units; no reused model work or discounted cache tokens'
    return {'version': 'edge_repair_view_applicability_v1', 'rows': rows, 'comparisons': comparisons, 'unique_model_cost': total, 'planned_units': len(CELLS) * len(ARMS), 'executed_units': sum((r['actual_plan'] is not None for r in rows)), 'unexecuted_units': sum((r['source_calls'] is None for r in rows)), 'fixed_source_inputs': len(CELLS), 'model_response_seed': 1, 'completed_equal_success_input_n': sum((all((r['parent_goal_met'] for r in rows if r['cell'] == c)) for c in CELLS)), 'preparation_wall_s': None, 'interpretation': 'new treatment of two existing source cases, not holdout or natural error frequency; no general robustness/CL-023 claim; no isolated hardware speedup'}

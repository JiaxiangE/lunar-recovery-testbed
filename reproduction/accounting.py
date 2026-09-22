"""Shared scientific accounting; excludes historical batch orchestration."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from reproduction.records import ROOT
RANK = {'P': 0, 'T': 1, 'M': 2, 'S': 3}

def read(path):
    from reproduction.records import read_record
    return read_record(path)

def merge_unique(target, values, key):
    for value in values:
        identity = value.get(key)
        if not identity:
            continue
        if identity in target and target[identity] != value:
            raise ValueError('conflicting reused ' + key + ': ' + identity)
        target[identity] = value

def accounting(paths):
    requests = {}
    logical = {}
    physical = {}
    for p in sorted(set(paths)):
        c = read(p)
        merge_unique(requests, c.get('requests', []), 'logical_call_id')
        merge_unique(logical, c.get('logical_calls', []), 'logical_call_id')
        merge_unique(physical, c.get('physical_attempts', []), 'physical_attempt_id')
    tokens = Counter()
    unknown = Counter()
    roles = Counter()
    posts = Counter()
    for q in requests.values():
        roles[q['route']['role']] += 1
    for a in physical.values():
        role = requests.get(a['logical_call_id'], {}).get('route', {}).get('role', 'unknown')
        posts[role] += 1
        value = (a.get('usage') or {}).get('total_tokens')
        if type(value) is int:
            tokens[role] += value
        else:
            unknown[role] += 1
    return ({'source_files': sorted(set(paths)), 'logical_records': len(logical), 'physical_attempts': len(physical), 'known_tokens': sum(tokens.values()), 'unknown_usage_attempts': sum(unknown.values()), 'known_tokens_by_role': dict(tokens), 'unknown_usage_by_role': dict(unknown), 'requests_by_role': dict(roles), 'physical_by_role': dict(posts), 'transport_retries': sum((max(0, n - 1) for n in Counter((p['logical_call_id'] for p in physical.values())).values())), 'request_operations': dict(Counter((q['operation'] for q in requests.values())))}, list(requests.values()))

def scopes(trial, method, requests, continued=False):
    if method != 'ours':
        return {'applicable': False, 'initial_scope': None, 'attempt_sequence': None, 'broader_scope_attempted': None, 'original_success_without_broader_attempt': None, 'scope_trace_complete': None, 'semantic_retries': None, 'parent_trace_complete': None}
    sequence = []
    initial = None
    events = []
    complete = not continued
    for a in trial.get('attempts', []):
        n = a.get('native') or {}
        selected = n.get('selected_scope')
        if initial is None:
            initial = selected
        attempts = n.get('attempts')
        if attempts is None or selected not in RANK:
            complete = False
        for t in attempts or []:
            if t.get('scope') not in RANK:
                complete = False
            sequence.append({'policy': a['policy'], 'scope': t.get('scope'), 'reason': t.get('reason'), 'transition': t.get('scope_transition')})
        events.extend(n.get('generated_hierarchy') or [])
    observed_broad = initial in RANK and any((RANK.get(x['scope'], -1) > RANK[initial] for x in sequence))
    broad = True if observed_broad else False if complete else None
    success_without_broader = False if trial.get('original_goal_met') is False or broad is True else True if trial.get('original_goal_met') is True and broad is False else None
    generation_n = sum((q['operation'] in ('D_T', 'D_M', 'D_S') for q in requests))
    trace_complete = complete and len(events) == generation_n and all((type(e.get('attempt')) is int for e in events))
    retries = sum((e['attempt'] > 1 for e in events)) if trace_complete else None
    return {'applicable': True, 'initial_scope': initial, 'attempt_sequence': sequence, 'scope_trace_complete': complete, 'broader_scope_attempted': broad, 'original_success_without_broader_attempt': success_without_broader, 'semantic_retries': retries, 'parent_trace_complete': trace_complete, 'recorded_hierarchy_events': len(events), 'model_generation_requests': generation_n}

def process(row, trial, requests):
    method = row['method']
    continued = row.get('operational_recovery', False)
    scope = scopes(trial, method, requests, continued)
    attempts = trial.get('attempts', [])
    policy = [a.get('policy') for a in attempts]
    if method == 'ours':
        retries = scope['semantic_retries']
    elif method == 'flat':
        values = [a.get('native', {}).get('attempts') for a in attempts]
        complete = not continued and all((isinstance(v, list) for v in values)) and (sum((len(v) for v in values)) == len(requests))
        retries = sum((max(0, len(v) - 1) for v in values)) if complete else None
    else:
        retries = 0
    native = []
    for a in attempts:
        n = a.get('native') or {}
        artifact = n.get('native_artifact') or {}
        if method == 'llmp':
            native.append(artifact.get('planner_invocations', 0))
        elif method in ('bt', 'htn'):
            native.append(1 if n else 0)
    work = trial.get('local_work', {})
    execution = trial.get('execution') or {}
    wall = work.get('total_wall_s', trial.get('wall_s'))
    translations = []
    if method == 'llmp':
        qs = [q for q in requests if q['operation'] == 'translation']
        for i, a in enumerate(attempts):
            n = a.get('native') or {}
            artifact = n.get('native_artifact') or {}
            translations.append({'policy': a['policy'], 'source_request_ordinal': qs[i]['ordinal'] if i < len(qs) else None, 'original_response_equal': a.get('raw_translation_response') == qs[i].get('response') if i < len(qs) else None, 'format': a.get('translation_format'), 'parsed_goal': a.get('parsed_translation'), 'native_goal': {k: artifact.get(k) for k in ('supplied_goal_facts', 'supplied_negative_goal_facts', 'supplied_goal_cardinality')}, 'native_status': n.get('native_status'), 'native_plan': n.get('canonical_plan'), 'selected_native_plan_equals_execution': n.get('canonical_plan') == execution.get('executed_plan') if a['policy'] == trial.get('selected_policy') and execution else None})
    return {'original_goal_met': trial.get('original_goal_met'), 'approved_degraded_goal_met': trial.get('degraded_goal_met'), 'goal_policies': [{'policy': a.get('policy'), 'goal': a.get('goal')} for a in attempts], 'selected_policy': trial.get('selected_policy'), 'parent_goal_results': trial.get('parent_goal_results'), 'original_suffix_still_valid': trial.get('original_suffix_still_valid'), 'suffix_validity_scope': trial.get('original_suffix_validity_scope'), 'repair_required_by_observed_network': trial.get('repair_required_by_observed_network'), 'semantic_retries': retries, 'scope': scope, 'goal_policy_changes': max(0, len(policy) - 1), 'communication_reentries': 0 if not continued else None, 'communication_scope': 'static-link main runner; interrupted continuation is not a link reentry', 'native_solver_calls': sum(native), 'native_calls_scope': 'FD planner_invocations or explicit BT/HTN solve invocations; resource search separate', 'controller_wall_s': None if continued else wall, 'recorded_segment_wall_s': wall, 'wall_boundary': 'execute_integrated inclusive arm: model/solver, verification, forecast/search and dispatch; excludes model service startup and case construction; continuation/replay has no uninterrupted wall', 'simulated_action_s': execution.get('elapsed_s'), 'energy_wh': execution.get('energy_consumed_wh'), 'dispatch_n': execution.get('dispatch_n', 0), 'executed_plan': execution.get('executed_plan', []), 'local_work': work, 'local_work_scope': 'recorded segment only' if continued else 'whole recorded unit', 'model_answer_binding': {k: trial.get('repair_process', {}).get(k) for k in ('adoption', 'adopted_matches_execution_validation', 'adopted_syntax_equals_grounded_plan', 'actual_is_validated_prefix')}, 'translation_native_binding': translations, 'status': trial.get('status')}

def count(rows):
    return [{'method': m, 'n': len(g), 'original': sum((x['original_goal_met'] is True for x in g)), 'adjusted_only': sum((x['approved_degraded_goal_met'] is True and (not x['original_goal_met']) for x in g)), 'failed': sum((not (x['original_goal_met'] or x['approved_degraded_goal_met']) for x in g)), 'logical': sum((x['cost']['logical_records'] for x in g)), 'physical': sum((x['cost']['physical_attempts'] for x in g)), 'tokens': sum((x['cost']['known_tokens'] for x in g)), 'unknown_usage': sum((x['cost']['unknown_usage_attempts'] for x in g))} for m in sorted({r['method'] for r in rows}) for g in [[r for r in rows if r['method'] == m]]]

def mechanism_processes():
    """Separate denominators: existing feedback pair and six continuous-link units."""
    rows = []
    summary = read('data/local_repair/child_retry/summary.json')
    for old in summary['rows']:
        paths = [old['source_calls'], old['prefix_reuse']['source_calls']]
        cost, qs = accounting(paths)
        trial = read(old['source_trial'])
        row = {'study': 'feedback_state_clarity_v1', 'cell': 'PSR N2', 'method': old['label'], 'source_trial': old['source_trial'], 'operational_recovery': True}
        row.update(process(row, trial, qs), cost=cost)
        branch_retries = old['child_retry_n'] if old['label'] == 'ours' else process({'method': 'flat', 'operational_recovery': False}, trial, qs)['semantic_retries']
        row.update(additional_cost=old['new_cost'], retained_child_n=old['retained_child_n'], retained_action_n=old['retained_action_n'], semantic_retries=branch_retries, semantic_retry_scope='explicit branch child retry or flat candidate retry; initial child requests excluded', parent_regeneration_n=old['parent_regeneration_n'])
        row['scope'] = scopes(trial, old['label'], qs)
        rows.append(row)
    for p in sorted((ROOT / 'data/communication').glob('*/*/trial.json')):
        t = read(p)
        cost, qs = accounting([p.with_name('calls.jsonl').as_posix()])
        attempts = []
        for entry in t['entries']:
            for a in entry['attempts']:
                v = deepcopy(a)
                if not v.get('native') and entry.get('interrupted_native_trace'):
                    v['native'] = entry['interrupted_native_trace']
                if v.get('native'):
                    attempts.append(v)
        fused = {'attempts': attempts, 'original_goal_met': t['parent_goal_met']}
        scope = scopes(fused, 'ours', qs)
        rows.append({'study': 'e13_continuous_link_v1', 'cell': t['cell'], 'condition': t['condition'], 'method': 'ours', 'source_trial': p.as_posix(), 'cost': cost, 'scope': scope, 'original_goal_met': t['parent_goal_met'], 'approved_degraded_goal_met': None, 'original_suffix_still_valid': t['entries'][0].get('original_suffix_still_valid'), 'goal_policies': [{'policy': a['policy'], 'goal': a.get('goal')} for a in attempts], 'goal_policy_changes': len(set((a['policy'] for a in attempts))) - 1, 'semantic_retries': scope['semantic_retries'], 'native_solver_calls': 0, 'communication_reentries': max(0, t['entry_n'] - 1), 'controller_wall_s': t['software_wall_s'], 'wall_boundary': 'continuous controller from start through terminal; includes event waits and all reentries, excludes service loading; separate from simulated action clock', 'simulated_action_s': t['world_elapsed_s'], 'dispatch_n': t['dispatch_n'], 'energy_wh': t['energy_wh'], 'local_work': t['resource_work'], 'events_consumed': t['events_consumed']})
    return rows

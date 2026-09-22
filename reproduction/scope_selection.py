"""Recompute scope choices and utility terms from the saved paper inputs."""
from reproduction.records import record_path
import ast
from collections import Counter
from dataclasses import asdict
from functools import lru_cache
import json
from pathlib import Path
import subprocess
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order
from controller.selector import ScopeSelector
from controller.settings import frozen_weights
from controller.settings import frozen_p_fix_params
from reproduction.context import payload
from reproduction.records import ROOT
BASELINE = '59b09261'

def read(path):
    from reproduction.records import read_record
    return read_record(path)

def introduced(path):
    return read(ROOT / 'data/scope_selection/implementation.json')['source_record_revisions'].get(path)

def version_check(revision):
    config = read(ROOT / 'data/scope_selection/implementation.json')
    return config['implementation_comparisons'].get(revision, {'matched': False, 'reason': 'implementation correspondence unavailable'})

def inventory(root):
    data = read(record_path('data/indexes/scope_subset.json'))
    return (data['entries'], data['mentions'])

def locate_calls(root, path, hint):
    candidates = []
    if hint.get('source_calls'):
        candidates.append(hint['source_calls'])
    candidates.extend(hint.get('whole_recorded_path_cost', {}).get('source_files', []))
    candidates.extend((p.relative_to(root).as_posix() for p in record_path(path).parent.rglob('calls.jsonl')))
    for p in dict.fromkeys(candidates):
        if record_path(p).is_file():
            record = read(record_path(p))
            if record.get('requests'):
                return (p, record)
    return (None, {})

def actor_edge(root, revision, scenario, actor, information):
    if not actor:
        return (None, 'failure actor not saved')
    descriptions = [information.get('shared_resource_information', {}).get('actors', {}).get(actor, {})]
    for key in ('post_deviation_observation', 'pre_deviation_observation', 'original_observation'):
        descriptions.append(information.get(key, {}).get('agents', {}).get(actor, {}))
    kind = next((x.get('agent_type', x.get('type')) for x in descriptions if x.get('agent_type', x.get('type'))), None)
    table = read(record_path('data/scope_selection/implementation.json'))['actor_capabilities'].get(scenario, {})
    if kind not in table:
        return (None, 'saved actor type has no declared capability mapping')
    return ([Scope(s) for s in table[kind]], {'actor': actor, 'saved_actor_type': kind, 'capability_source': 'data/scope_selection/implementation.json'})

def references(scores, allowed, actual):
    ordered = sorted(allowed, key=scope_order)
    return {'allowed': [s.value for s in ordered], 'allowed_n': len(ordered), 'actual_initial_choice': actual, 'narrowest': ordered[0].value, 'widest': ordered[-1].value, 'uniform_distribution': {s.value: 1 / len(ordered) for s in ordered}, 'actual_matches_narrowest': actual == ordered[0].value, 'actual_matches_widest': actual == ordered[-1].value, 'uniform_mass_on_actual': 1 / len(ordered), 'singleton': len(ordered) == 1, 'not_run_alternative_policies': True}

def build(root=ROOT):
    entries, mentions = inventory(root)
    rows = []
    exclusions = []
    versions = {}
    seen = {}
    selector = ScopeSelector(frozen_weights(), frozen_p_fix_params())
    parameters = read(record_path('data/scope_selection/implementation.json'))['parameters']
    if asdict(selector.weights) != parameters['weights'] or asdict(selector.p_fix_params) != parameters['p_fix']:
        raise ValueError('paper scope parameters differ from installed selector')
    for path, entry in entries.items():
        t = read(record_path(path))
        hint = entry['hint']
        source = {'study': entry['study'], 'index_source': entry['index_source'], 'source_trial': path, 'also_indexed_by': entry['aliases']}
        method = t.get('method_label') or hint.get('method') or ('ours' if 'hierarchy' in t.get('method', '') else t.get('method', ''))
        if entry['study'] == 'task_view_microstudy_v7':
            exclusions.append({**source, 'reason': 'direct_task_microstudy_not_recovery_selector', 'arm': hint.get('arm')})
            continue
        if method != 'ours' and 'hierarchy' not in str(method) and (not str(hint.get('arm', '')).startswith('ours')):
            exclusions.append({**source, 'reason': 'flat_or_other_method_no_Ours_recovery_selector', 'method': method})
            continue
        cp, calls = locate_calls(root, path, hint)
        if entry['study'] == 'communication_closure_v1' and (t.get('communication_protocol') or {}).get('segments_n', 1) > 1:
            segment_path = record_path(path).parent / 'segments.json'
            for si, segment in enumerate(read(segment_path)[:-1]):
                for pi, part in enumerate(segment.get('attempts', [])):
                    if (part.get('native') or {}).get('selected_scope') is not None:
                        raise ValueError('additional saved dynamic decision needs explicit inventory support')
                    exclusions.append({**source, 'source_segment': segment_path.relative_to(root).as_posix(), 'segment_index': si, 'policy_index': pi, 'reason': 'dynamic_pre_reconnect_initial_choice_not_recorded', 'saved_failure': part.get('failure'), 'note': 'a later request route is not substituted for the missing initial choice'})
        if not t.get('attempts'):
            exclusions.append({**source, 'reason': 'not_run_or_no_native_decision', 'status': t.get('status')})
            continue
        revision = introduced(path)
        version = version_check(revision)
        versions[revision or 'missing'] = version
        qs = list(calls.get('requests', []))
        reuse = calls.get('experimental_prefix_reuse') or calls.get('replayed_prefix')
        if reuse and record_path(reuse['source_calls']).is_file():
            qs = read(record_path(reuse['source_calls']))['requests'][:reuse['used_n']] + qs
        for i, attempt in enumerate(t['attempts']):
            n = attempt.get('native', {})
            policy = attempt.get('policy')
            selection = n.get('selected_scope')
            if selection not in [s.value for s in SCOPES]:
                exclusions.append({**source, 'policy_index': i, 'reason': 'historical_initial_choice_not_recorded', 'saved_failure': attempt.get('failure'), 'new_native_record': attempt.get('scope_decision')})
                continue
            info = n.get('original_information', t.get('original_information', {}))
            hist = {'diagnosis_source': n.get('diagnosis_source'), 'posterior': n.get('posterior'), 'initial_selected_scope': selection, 'before_operation_filter_scope': n.get('unconstrained_selector_scope'), 'allowed_escalation_sequence': n.get('allowed_scope_sequence'), 'attempted_scopes': [a.get('scope') for a in n.get('attempts', []) if a.get('scope')], 'terminal_scope_field': n.get('final_scope'), 'successful_scope': n.get('final_scope') if (n.get('execution') or {}).get('execution_success') and (n.get('execution') or {}).get('goal_met') else None, 'trial_scope_field_raw': t.get('scope'), 'preset_task_scope': None, 'preset_scope_reason': 'case preset scope not serialized as a scope value; never inferred from cell name or initial selection', 'historical_scores_recorded': n.get('scope_decision') is not None}
            row = {**source, 'policy_index': i, 'policy': policy, 'historical_fact': hist, 'historical_version_ref': revision, 'offline_recomputation': None, 'missing': []}
            rows.append(row)
            if not version['matched']:
                row['missing'].append('historical implementation not matched')
                continue
            if not isinstance(n.get('posterior'), dict):
                row['missing'].append('posterior missing')
                continue
            comm = info.get('current_experiment_communication')
            matching = []
            for q in qs:
                if q.get('operation') == 'diagnosis' or not isinstance(q.get('user'), str):
                    continue
                try:
                    b = payload(q)
                except (ValueError, KeyError):
                    continue
                if b.get('original_information', {}).get('public_policy') != info.get('public_policy'):
                    continue
                if policy != 'nominal' and b.get('goal') != attempt.get('goal'):
                    continue
                matching.append((q, b))
            if comm is None:
                policies = [b['context']['domain'].get('communication_policy') for q, b in matching]
                policies = [p for p in policies if p and p.get('state')]
                unique = {json.dumps(p, sort_keys=True) for p in policies}
                if len(unique) != 1:
                    row['missing'].append('decision-time communication missing or ambiguous')
                    continue
                comm = policies[0]
            state = comm.get('state')
            connected = state == 'Connected'
            if state not in ('Connected', 'Disconnected', 'Degraded'):
                row['missing'].append('unknown communication state')
                continue
            actor = (n.get('diagnosis_audit') or {}).get('failure', {}).get('agent_id') or info.get('observed_deviation', {}).get('agent')
            if actor is None and matching:
                actor = matching[0][0].get('route', {}).get('actor')
            scenario = next((b['context']['domain'].get('scenario') for q, b in matching), None)
            edge, edge_source = (None, 'not used by Connected selector') if connected else actor_edge(root, revision, scenario, actor, info)
            if not connected and edge is None:
                row['missing'].append(edge_source)
                continue
            operation = set(SCOPES) if policy == 'nominal' else {Scope.S}
            comm_allowed = set(SCOPES) if connected else set(edge)
            allowed = operation & comm_allowed
            if not allowed:
                row['missing'].append('empty permitted set in historical input')
                continue
            posterior = {Scope(k): float(v) for k, v in n['posterior'].items()}
            d = selector.select(None, posterior, {}, allowed, base_reachable=connected)
            selected = max(allowed, key=lambda s: (d.scores[s], -scope_order(s)))
            panels = {s.value: {'score': d.scores[s], 'terms': selector._terms(s, posterior, {})} for s in d.scores}
            unrestricted_scores = {s: selector._utility(s, posterior, {}) for s in operation}
            no_comm = max(operation, key=lambda s: (unrestricted_scores[s], -scope_order(s)))
            if selected.value != selection or (hist['before_operation_filter_scope'] and d.sigma.value != hist['before_operation_filter_scope']):
                row['missing'].append('recorded selection does not match archived-input calculation')
                continue
            identity_origin = (reuse['source_calls'] if reuse and reuse.get('used_n', 0) > 0 else cp) or path
            identity_origin = record_path(identity_origin).resolve().relative_to(root.resolve()).as_posix()
            identity = json.dumps([identity_origin, policy, n['posterior'], comm, sorted((s.value for s in operation)), info], sort_keys=True)
            prior = seen.get(identity)
            row['duplicate_decision_of'] = prior
            if prior is None:
                seen[identity] = f'{path}#/attempts/{i}/native'
            row['offline_recomputation'] = {'provenance': 'later arithmetic from saved inputs and matched archived repository implementation, not original native scores', 'ctx': {}, 'ctx_source': 'literal third argument in matched historical run_hierarchical call', 'communication_policy': comm, 'base_reachable_used': connected, 'actor_edge_source': edge_source, 'communication_actor_scopes': [s.value for s in sorted(comm_allowed, key=scope_order)], 'operation_scopes': [s.value for s in sorted(operation, key=scope_order)], 'evaluated_scores_and_terms': panels, 'raw_choice': d.sigma.value, 'final_choice': selected.value, 'final_selected_terms': panels[selected.value]['terms'], 'matches_recorded_choice': True, 'operation_changed_choice': selected != d.sigma, 'policy_references': references(d.scores, allowed, selection), 'communication_arithmetic': {'with_constraint': selected.value, 'without_communication_constraint': no_comm.value, 'without_constraint_scores': {s.value: {'score': v, 'terms': selector._terms(s, posterior, {})} for s, v in unrestricted_scores.items()}, 'changed': selected != no_comm, 'same_operation_set': True, 'no_Connected_observation_fabricated': True, 'interpretation': 'remove only feasible-set restriction in offline arithmetic; no candidate execution, success, cost or violation counterfactual'}, 'decision_identity_basis': identity_origin}
    unique = [r for r in rows if r['offline_recomputation'] is not None and (not r.get('duplicate_decision_of'))]
    by_study = {}
    for study in sorted({e['study'] for e in entries.values()}):
        group = [r for r in unique if r['study'] == study]
        by_study[study] = {'unique_recomputed_decisions': len(group), 'allowed_sizes': dict(Counter((r['offline_recomputation']['policy_references']['allowed_n'] for r in group))), 'matches_narrowest': sum((r['offline_recomputation']['policy_references']['actual_matches_narrowest'] for r in group)), 'matches_widest': sum((r['offline_recomputation']['policy_references']['actual_matches_widest'] for r in group)), 'communication_changes_choice': sum((r['offline_recomputation']['communication_arithmetic']['changed'] for r in group))}
    from planning.repair.scope_decision_record import choose
    validation = {}
    choose(selector, None, {s: 0.25 for s in SCOPES}, {}, connected=True, actor_edge_scopes=None, operation_scopes={Scope.S}, audit=validation)
    return {'version': 'scope_decision_closure_v1', 'source_catalog': 'data/indexes/scope_subset.json', 'selected_catalog_studies': sorted({e['study'] for e in entries.values()}), 'source_mentions': mentions, 'historical_versions': versions, 'parameters_for_matched_snapshots': {'weights': asdict(selector.weights), 'p_fix': asdict(selector.p_fix_params), 'undershoot_mult': selector.undershoot_mult, 'subtree_size': {s.value: v for s, v in selector.subtree_size.items()}, 'llm_cost': {s.value: v for s, v in selector.llm_cost.items()}}, 'decisions': rows, 'excluded_entries': exclusions, 'counts': {'indexed_mentions': len(mentions), 'unique_trial_paths': len(entries), 'native_initial_decision_records': len(rows), 'unique_recomputed_decisions': len(unique), 'duplicate_reused_decisions': sum((bool(r.get('duplicate_decision_of')) for r in rows)), 'not_recomputable': sum((r['offline_recomputation'] is None for r in rows)), 'exclusions_by_reason': dict(Counter((r['reason'] for r in exclusions)))}, 'by_study': by_study, 'new_model_calls': 0, 'new_candidate_dispatches': 0, 'new_record_capability_validation': {'source': 'synthetic offline operation={S}, uniform posterior; not an original historical log', 'record': validation}, 'limits': 'descriptive choice comparison only; uniform is an exact distribution, not sampled. A two-element set necessarily chooses an endpoint. No alternative-policy recovery outcomes, diagnosis accuracy or violation rates are inferred. Preset scope and escalation are not additional argmax decisions.'}

"""Read-only process measurements. References existing requests; no causal score."""
from difflib import SequenceMatcher
import json

def action_key(step):
    """Only executable content; hierarchy ids/labels never constitute changes."""
    return json.dumps({k: step.get(k) for k in ('primitive', 'agent_id', 'params')}, sort_keys=True, separators=(',', ':'))

def compare_actions(original, actual, fault_indices):
    old = [action_key(s) for s in original]
    new = [action_key(s) for s in actual]
    pairs = [(i + k, j + k) for i, j, n in SequenceMatcher(a=old, b=new, autojunk=False).get_matching_blocks() for k in range(n)]
    retained = {i for i, j in pairs}
    fault = set(fault_indices)
    return {'original_remaining_n': len(old), 'actual_executed_n': len(new), 'actual_preserved_action_n': len(pairs), 'actual_preserved_outside_fault_n': sum((i not in fault for i, j in pairs)), 'removed_or_replaced_outside_fault_n': sum((i not in fault and i not in retained for i in range(len(old)))), 'unmatched_actual_action_n': len(new) - len(pairs), 'matched_original_actual_indices': pairs, 'fault_original_indices': sorted(fault), 'alignment': 'ordered exact executable-content matching, IDs ignored; unmatched insertions have no unique source scope', 'interpretation': 'observed preservation/change, not counterfactual savings; no execution means zero physically preserved actions'}

def token_sum(requests):
    result = {}
    unknown = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [((q.get('provider_response') or {}).get('usage') or {}).get(key) for q in requests]
        result[key] = sum((v for v in values if type(v) is int))
        unknown[key] = sum((type(v) is not int for v in values))
    return {'known_lower_bound': result, 'unknown_request_n': unknown, 'request_n': len(requests), 'scope': 'completed response usage; transport retries/unknown posts remain separately in the existing ledger'}

def measure(case, row, calls=None, *, replay_requests=()):
    """Common interface for Ours/flat and historical derived analysis.

    replay_requests are caller-resolved references to original calls, never new
    usage; ambiguous old event timing stays unknown. Generated segments are not
    dispatched before parent acceptance.
    """
    from planning.repair.domain_hierarchy import leaves
    from planning.repair.domain_hierarchy import ancestor
    from controller.scope import Scope
    remaining = [(i, s) for i, s in leaves(case.original_tree) if i not in set(case.executed_prefix_ids)]
    fault = ancestor(case.original_tree, case.failed_node_id, Scope.T)
    fault_ids = {i for i, s in leaves(case.original_tree, fault)}
    actual = (row.get('execution') or {}).get('executed_plan', [])
    actions = compare_actions([s for i, s in remaining], actual, [j for j, (i, s) in enumerate(remaining) if i in fault_ids])
    actions['fault_scope_definition'] = 'original Task containing observed failed primitive; fixed across methods, not selected repair scope'
    adopted = (row.get('execution') or {}).get('verification', {}).get('normalized_plan')
    plan_change = compare_actions([s for i, s in remaining], adopted, [j for j, (i, s) in enumerate(remaining) if i in fault_ids]) if adopted is not None else None
    if not actual:
        actions['removed_or_replaced_outside_fault_n'] = None
        actions['interpretation'] = 'no physical recovery actions executed; do not classify non-execution as a whole-plan rewrite'
    calls = calls or {}
    requests = list(replay_requests) + list(calls.get('requests', []))
    cursor = 0
    event_rows = []
    invalidated = []
    scope_transitions = []
    earliest_error = None
    ancestry_complete = True
    for pi, policy in enumerate(row.get('attempts', [])):
        native = policy.get('native') or {}
        events = native.get('generated_hierarchy', [])
        hierarchical = bool(events)
        if not hierarchical:
            events = [e for e in native.get('attempts', []) if 'raw_response' in e]
        lineage_known = not hierarchical or all(('parent_event_index' in e for e in events))
        ancestry_complete = ancestry_complete and lineage_known
        local = []
        first_scope = None
        for ei, e in enumerate(events):
            op = e.get('operation', 'flat')
            raw = e.get('raw_response')
            request = None
            qi = None
            for index in range(cursor, len(requests)):
                q = requests[index]
                if raw is not None and q.get('operation') == op and (q.get('response') == raw):
                    qi = index
                    request = q
                    cursor = index + 1
                    break
            parent = e.get('parent_event_index')
            ancestors = []
            seen = set()
            while parent is not None and parent not in seen and (parent < len(events)):
                seen.add(parent)
                ancestors.append(parent)
                parent = events[parent].get('parent_event_index')
            retry = e.get('attempt', ei + 1 if not hierarchical else 1) > 1
            regenerated = any((events[p].get('attempt', 1) > 1 for p in ancestors))
            kinds = []
            if retry:
                kinds.append('current_child_retry' if op == 'D_T' else 'parent_group_regeneration' if hierarchical else 'whole_plan_retry')
            if regenerated:
                kinds.append('descendant_of_regenerated_parent')
            if not retry and (not regenerated):
                kinds.append('first_generation' if lineage_known else 'generation_parent_context_unknown')
            scope = (request.get('route') or {}).get('repair_scope') if request else None
            if scope is None and hierarchical:
                root = events[ancestors[-1]] if ancestors else e
                scope = next((a.get('scope') for a in native.get('attempts', []) if a.get('selected_node') == root.get('node', {}).get('id')), None)
            if first_scope is None:
                first_scope = scope
            if scope is not None and first_scope is not None and (scope != first_scope):
                kinds.append('scope_escalation_generation')
            experimental = bool(e.get('experimental_prefix_reuse')) or (qi is not None and qi < len(replay_requests) and ('experimental_prefix_reuse' in calls))
            replay = (bool(e.get('operational_replay')) or (qi is not None and qi < len(replay_requests))) and (not experimental)
            if replay:
                kinds.append('operational_replay')
            if experimental:
                kinds.append('experimental_prefix_reuse')
            check = e.get('child_verification', e.get('composed_parent_verification', e.get('parent_verification', {})))
            failed = e.get('accepted') is False or check.get('accepted') is False or e.get('reason') == 'MODEL_DECLINED'
            item = {'record': f'/attempts/{pi}/native/' + ('generated_hierarchy' if hierarchical else 'attempts') + f'/{ei}', 'operation': op, 'kinds': kinds, 'policy': policy.get('policy'), 'event_index': ei, 'request_index': qi, 'request_logical_id': request.get('logical_call_id') if request else None, 'tokens': token_sum([request]) if request else None, 'failed': failed, 'accepted_speculative_action_n': len(e.get('candidate') or []) if e.get('accepted') and op == 'D_T' else 0, 'physical_execution': False, 'ancestors': ancestors, 'ancestor_context_known': lineage_known}
            local.append(item)
            event_rows.append(item)
        rejected = [(ei, e) for ei, e in enumerate(events) if e.get('accepted') is False or (e.get('parent_verification') or {}).get('accepted') is False or e.get('reason') == 'MODEL_DECLINED']
        if earliest_error is None and rejected:
            if hierarchical and (not all(('completion_order' in e for _, e in rejected))):
                earliest_error = {'policy': policy.get('policy'), 'tokens': None, 'reason': 'historical recursive unwind timing unavailable'}
            else:
                ei, e = min(rejected, key=lambda t: t[1].get('completion_order', t[0]))
                prior = [r['request_index'] for r in local if r['request_index'] is not None and (r['event_index'] == ei or ei in r['ancestors'] or r['event_index'] < ei)]
                end = max(prior) if prior else None
                earliest_error = {'record': local[ei]['record'], 'tokens': token_sum(requests[:end + 1]) if end is not None else None}
        for ei, e in rejected:
            if e.get('operation') not in {'D_S', 'D_M'}:
                continue
            if not lineage_known:
                invalidated.append({'parent_record': local[ei]['record'], 'accepted_leaf_records': None, 'speculative_action_n': None, 'tokens': None, 'not_physical_rollback': True, 'overlaps_ancestor_invalidations': True, 'unavailable_reason': 'historical parent-event association absent; zero loss cannot be inferred'})
                continue
            lost = [r for r in local if ei in r['ancestors'] and r['accepted_speculative_action_n']]
            invalidated.append({'parent_record': local[ei]['record'], 'accepted_leaf_records': [r['record'] for r in lost], 'speculative_action_n': sum((r['accepted_speculative_action_n'] for r in lost)), 'tokens': token_sum([requests[r['request_index']] for r in lost if r['request_index'] is not None]), 'not_physical_rollback': True, 'overlaps_ancestor_invalidations': True})
        for attempt in native.get('attempts', []):
            trans = attempt.get('scope_transition')
            if trans and trans.get('from') is not None:
                scope_transitions.append({'policy': policy.get('policy'), **trans})
    regen = [r for r in event_rows if any((k in r['kinds'] for k in ('current_child_retry', 'parent_group_regeneration', 'descendant_of_regenerated_parent', 'whole_plan_retry', 'scope_escalation_generation')))]
    unique = sorted({r['request_index'] for r in regen if r['request_index'] is not None and (not {'operational_replay', 'experimental_prefix_reuse'} & set(r['kinds']))})
    return {'version': 'mechanism_observation_v1', 'actions': actions, 'adopted_plan_changes': plan_change, 'events': event_rows, 'regeneration_tokens_new_calls': token_sum([requests[i] for i in unique]), 'regeneration_ancestry_complete': ancestry_complete, 'regeneration_token_interpretation': 'observed linked calls only; missing ancestry makes this a partial lower bound, not total regeneration cost', 'regeneration_unmatched_request_n': sum((r['request_index'] is None for r in regen)), 'scope_escalations': scope_transitions, 'parent_invalidated_work': invalidated, 'first_discovered_error_cost': earliest_error, 'operational_replay_n': (calls.get('replayed_prefix') or {}).get('used_n', 0), **({'experimental_prefix_reuse_n': calls['experimental_prefix_reuse']['used_n']} if 'experimental_prefix_reuse' in calls else {}), 'physical_prefix_n': len(case.executed_prefix_ids), 'physical_prefix_source': 'original_information.executed_prefix; distinct from generated verification', 'source_obligations': {'original_goal_met': row.get('original_goal_met'), 'selected_policy': row.get('selected_policy'), 'approved_policy_changed': row.get('selected_policy') not in (None, 'nominal'), 'implementation_action_rewrite_is_goal_change': False}, 'causal_benefit_established': False}

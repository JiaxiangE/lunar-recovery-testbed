"""References to existing trial candidates, not a causal attribution score."""
from domains.offload_audit import remaining_goal

def prediction_reference(value, record):
    return None if value is None else {'resource_feasible': value.get('resource_feasible'), 'status': value.get('status'), 'record': record}

def describe_process(row, problem):
    stages, retries, escalations = ([], [], [])
    adopted = None
    searches = (row.get('resource_assistance') or {}).get('events', [])
    for i, policy in enumerate(row['attempts']):
        native = policy.get('native', {})
        root = f'/attempts/{i}/native'
        events = native.get('generated_hierarchy', [])
        for j, event in enumerate(events):
            check = event.get('child_verification', event.get('composed_parent_verification', {}))
            stages.append({'candidate_record': f'{root}/generated_hierarchy/{j}', 'format': event.get('format'), 'grounding': check.get('grounding'), 'symbolic_contract': check.get('symbolic_contract'), 'resource_prediction': None, 'resource_scope': 'resource forecast is recorded for the composed complete candidate'})
            if event.get('attempt', 1) > 1:
                prior = next((e for e in reversed(events[:j]) if e['node']['id'] == event['node']['id']), None)
                retries.append({'candidate_record': f'{root}/generated_hierarchy/{j}', 'layer': event['node']['layer'], 'candidate_changed': event.get('parsed_response', event.get('raw_response')) != prior.get('parsed_response', prior.get('raw_response')) if prior else None})
        for j, candidate in enumerate(native.get('attempts', [])):
            check = candidate.get('parent_verification', {})
            stages.append({'candidate_record': f'{root}/attempts/{j}', 'format': candidate.get('format'), 'grounding': check.get('grounding'), 'symbolic_contract': check.get('symbolic_contract'), 'resource_prediction': prediction_reference(candidate.get('resource_prediction'), f'{root}/attempts/{j}/resource_prediction')})
            transition = candidate.get('scope_transition')
            if transition and transition['from'] is not None:
                escalations.append(transition)
        if native.get('adoption') is not None and policy.get('policy') == row.get('selected_policy') and (policy.get('execution') is not None):
            adopted = {**native['adoption'], 'candidate_record': root + '/adopted_candidate'}
        if policy.get('prefix_recomposition', {}).get('execution') is not None and policy.get('policy') == row.get('selected_policy'):
            adopted = {'origin': 'prefix_recomposition', 'candidate_record': f'/attempts/{i}/adopted_candidate'}
        if 'raw_translation_response' in policy:
            check = policy.get('common_symbolic_validation', {})
            stages.append({'candidate_record': f'/attempts/{i}', 'format': policy.get('translation_format'), 'grounding': check.get('grounding'), 'symbolic_contract': check.get('symbolic_contract'), 'resource_prediction': prediction_reference(policy.get('common_resource_prediction'), f'/attempts/{i}/common_resource_prediction')})
        elif 'native_process' in policy:
            check = policy.get('common_symbolic_validation', {})
            stages.append({'candidate_record': f'/attempts/{i}/native_process/result', 'format': None, 'grounding': check.get('grounding'), 'symbolic_contract': check.get('symbolic_contract'), 'resource_prediction': prediction_reference(policy.get('common_resource_prediction'), f'/attempts/{i}/common_resource_prediction'), 'origin': 'native kernel; not model output'})
    execution = row.get('execution') or {}
    validated = (execution.get('verification') or {}).get('normalized_plan')
    actual = execution.get('executed_plan', [])
    source_plan = None
    expected_grounded = None
    if adopted:
        index = int(adopted['candidate_record'].split('/')[2])
        source = row['attempts'][index]['native']
        if adopted['origin'] == 'prefix_recomposition':
            policy = row['attempts'][index]
            source_plan = policy['adopted_candidate']
            prepared = policy['prefix_recomposition'].get('preparation')
            check = prepared['child_parent_check']['parent'] if prepared else execution.get('verification', {})
        elif adopted['origin'] == 'resource_search':
            source_plan = source['adopted_candidate']
            check = searches[adopted['search_event_index']].get('full_parent_validation', {})
        else:
            source_plan = source['adopted_candidate']
            check = source['attempts'][adopted.get('candidate_attempt_index', adopted.get('scope_attempt_index'))].get('parent_verification', {})
        expected_grounded = check.get('normalized_plan')
    elif row.get('adoption_origin') == 'public_source_plan' and validated is not None:
        index = next((i for i, p in enumerate(row['attempts']) if p.get('source_preflight', {}).get('hit') and p.get('policy') == row.get('selected_policy')))
        adopted = {'origin': 'public_source_plan', 'candidate_record': f'/attempts/{index}/source_preflight/candidate'}
        source_plan = row['attempts'][index]['source_preflight']['candidate']
        expected_grounded = row['attempts'][index]['source_preflight']['verification'].get('normalized_plan')
    elif validated is not None:
        native_origin = any(('native_process' in p or 'raw_translation_response' in p for p in row['attempts']))
        adopted = {'origin': 'native_kernel' if native_origin else 'unknown', 'candidate_record': '/execution/verification/normalized_plan'}
        source_plan = validated
        expected_grounded = validated
    changed_search = False
    if adopted and adopted['origin'] == 'resource_search':
        changed_search = searches[adopted['search_event_index']].get('candidate_changed') is True
    return {'version': 'repair_process_observation_v1', 'candidate_stages': stages, 'child_or_parent_retries': retries, 'scope_escalations': escalations, 'resource_search_records': [f'/resource_assistance/events/{i}' for i, e in enumerate(searches) if e['kind'] == 'resource_search'], 'adoption': adopted, 'adopted_matches_execution_validation': expected_grounded == validated if expected_grounded is not None and validated is not None else None, 'adopted_syntax_equals_grounded_plan': source_plan == validated if source_plan is not None and validated is not None else None, 'actual_is_validated_prefix': actual == validated[:len(actual)] if validated is not None else None, 'observed_mechanisms_nonexclusive': {'model_candidate_used_without_resource_change': adopted is not None and adopted['origin'] in {'model', 'composed_model', 'resource_search'} and (not changed_search), 'hierarchical_retry_occurred': bool(retries), 'scope_escalation_occurred': bool(escalations), 'adopted_resource_search_changed_candidate': changed_search}, 'prefix_recomposition_adopted': bool(adopted and adopted['origin'] == 'prefix_recomposition'), 'parent_result': remaining_goal(problem, frozenset(row['final_facts'])), 'interpretation': 'Process observations can overlap. Module invocation, candidate change and success do not establish causal benefit; CL-023 requires comparison arms.'}

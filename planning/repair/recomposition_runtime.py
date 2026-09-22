"""Select a method's own complete candidate and invoke the existing controller."""
from copy import copy
from copy import deepcopy
import time
from planning.repair.domain_hierarchy import IMPLEMENTATION_ID as OURS
from planning.repair.domain_hierarchy import FLAT_IMPLEMENTATION_ID as FLAT
from controller.diagnosis import DiagnosisModule
from controller.selector import ScopeSelector
from controller.settings import frozen_weights
from controller.settings import frozen_p_fix_params

def own_candidate(native, method, initial_state):
    if method == FLAT:
        for index, event in reversed(list(enumerate(native.get('attempts', [])))):
            if event.get('candidate') is not None and event.get('format', {}).get('accepted'):
                return (deepcopy(event['candidate']), [{'method': method, 'operation': 'flat', 'raw_response': event['raw_response'], 'native_event': f'attempts/{index}'}])
        return (None, [])
    events = native.get('generated_hierarchy', [])
    starts = [i for i, e in enumerate(events) if e.get('operation') == 'D_S']
    if not starts:
        return (None, [])
    segments = []
    states = [frozenset(initial_state)]
    for index in range(starts[-1] + 1, len(events)):
        event = events[index]
        if event.get('operation') != 'D_T' or not event.get('accepted'):
            continue
        state = frozenset(event['start_state'])
        matches = [i for i, s in enumerate(states) if s == state]
        if not matches:
            continue
        boundary = matches[-1]
        segments = segments[:boundary]
        states = states[:boundary + 1]
        segments.append((deepcopy(event['candidate']), {'method': method, 'operation': 'D_T', 'raw_response': event['raw_response'], 'actor': event['node']['assigned_agent_id'], 'native_event': f'generated_hierarchy/{index}'}))
        states.append(frozenset(event['child_verification']['final_state']))
    return ([s for plan, _ in segments for s in plan], [part for _, part in segments]) if segments else (None, [])

def try_recomposition(case, problem, world, native, *, method, policy_name, controller):
    started = time.perf_counter()
    result = {'enabled': True, 'original_native_status': native.get('status'), 'execution': None, 'dispatch_n': 0}
    if native.get('execution') is not None:
        return {**result, 'status': 'ORIGINAL_EXECUTION_ALREADY_ATTEMPTED', 'wall_s': time.perf_counter() - started}
    if policy_name != 'nominal':
        scope = 'S'
    elif method == OURS:
        attempts = native.get('attempts', [])
        scope = attempts[-1].get('scope') if attempts else None
    else:
        posterior = DiagnosisModule().diagnose(case.failure, base_llm_client=None)
        scope = ScopeSelector(frozen_weights(), frozen_p_fix_params()).select(case.failure, posterior, {}, world.c_edge_for(case.failure.agent_id), base_reachable=case.communication == 'Connected').sigma.value
    if scope != 'S':
        return {**result, 'status': 'SCOPE_NOT_SUPPORTED', 'scope': scope, 'wall_s': time.perf_counter() - started}
    live = copy(case)
    live.world = world
    if problem.goal_met(problem.initial_state):
        from planning.repair.prefix_recomposition import propose
        from execution.binding import execute_plan
        maintenance = propose(live, problem, [], scope='S', source_reference={'policy': policy_name, 'kind': 'observed initial maintenance'})
        if maintenance['status'] == 'MAINTENANCE_ONLY':
            execution = execute_plan(world, problem, [], capture_transitions=True)
            return {**result, 'status': 'MAINTENANCE_ONLY', 'execution': execution, 'adopted_candidate': [], 'maintenance': maintenance, 'wall_s': time.perf_counter() - started}
    candidate, parts = own_candidate(native, method, problem.initial_state)
    if candidate is None:
        return {**result, 'status': 'NO_COMPLETE_OWN_CANDIDATE', 'wall_s': time.perf_counter() - started}
    prepared = controller.prepare(live, problem, method=method, scope=scope, candidate=candidate, source_reference={'policy': policy_name, 'native_events': [p['native_event'] for p in parts]}, raw_parts=parts)
    result['preparation'] = prepared
    if prepared['status'] == 'PREPARED_NOT_DISPATCHED':
        adopted = controller.adopt(prepared, case=live, problem=problem)
        result.update(adoption=adopted, execution=adopted.get('execution'), dispatch_n=adopted.get('dispatch_n', 0), adopted_candidate=deepcopy(adopted.get('adopted_candidate')))
    result.update(status=(result.get('adoption') or prepared)['status'], wall_s=time.perf_counter() - started)
    return result

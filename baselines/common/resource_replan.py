"""One independent native parent replan after a public resource forecast fails.

This adapter never calls the resource optimizer. It frees explicitly incidental
Task grouping only at an allowed Mission/Scene boundary, retaining the actual
executed prefix observation and every parent goal. The HTN/BT kernel remains
responsible for generating a new plan; a second resource failure is retained.
"""
from copy import deepcopy
from dataclasses import asdict
from baselines.common.limited_worker import run_limited
from baselines.htn_repair_holler.domain_repair import EnsureGoals
from baselines.htn_repair_holler.domain_repair import AchieveLiteral
from baselines.htn_repair_holler.domain_repair import TaskNetwork
IMPLEMENTATION_ID = 'paper4_native_public_resource_parent_replan_v1'

def _delivery_atom(fact):
    return fact.startswith('in_base_storage(') or fact.startswith('at_base(')

def _other_network_obligations(context, parent):
    """Conservative structural check of explicit current network conditions."""
    extra = set()

    def inspect(term):
        if isinstance(term, EnsureGoals):
            extra.update((f for f in term.positive if not _delivery_atom(f) or (f.startswith('in_base_storage(') and f not in parent.goal_facts)))
            extra.update(('not ' + f for f in term.negative))
        elif isinstance(term, AchieveLiteral):
            if term.negative or not _delivery_atom(term.fact):
                extra.add(('not ' if term.negative else '') + term.fact)
        elif isinstance(term, TaskNetwork):
            for _, child in term.nodes:
                inspect(child)
    for method in context.methods:
        extra.update((f for f in method.preconditions if not _delivery_atom(f) or (f.startswith('in_base_storage(') and f not in parent.goal_facts)))
        extra.update(('not ' + f for f in method.negative_preconditions))
        for term in method.subtasks:
            inspect(term)
    network = context.original_network
    if isinstance(network, TaskNetwork):
        inspect(network)
    else:
        for term in network:
            inspect(term)
    return sorted(extra)

def replan_native_parent(method, problem, *, context, scope, prediction, allow_mission_regrouping=False, mandatory_network_obligations=(), timeout_s=30.0, memory_mb=2048):
    """Call each method's own whole-parent kernel once, under a real hard cap.

    The caller exposes the same Mission regrouping permission to every method.
    That permission concerns incidental grouping, never goal degradation. An
    audit/ordering obligation not encoded as a goal must be supplied through
    mandatory_network_obligations; any such obligation conservatively preserves
    the old network instead of silently dropping it. Eligibility is not cell-ID
    or expected-outcome dependent.
    """
    if method not in {'htn', 'bt'}:
        raise ValueError('independent parent replan supports htn or bt')
    result = {'implementation': IMPLEMENTATION_ID, 'method': method, 'status': 'NATIVE_PARENT_REGROUPING_INELIGIBLE', 'plan': None, 'resource_feedback': deepcopy(prediction), 'scope': scope, 'goal_changed': False, 'optimizer_calls': 0, 'model_calls': 0, 'native_replan_calls': 0, 'world_dispatch_n': 0, 'native_search_limit': {'wall_s': timeout_s, 'memory_mb': memory_mb}, 'parent_goal': {'positive': sorted(problem.goal_facts), 'negative': sorted(problem.negative_goal_facts), 'cardinality': [[k, sorted(g)] for k, g in problem.goal_cardinality]}}
    reason = None
    if scope not in {'M', 'S'} or allow_mission_regrouping is not True:
        reason = 'no explicit Mission/Scene regrouping permission'
    elif prediction is None or prediction.get('resource_feasible') is not False:
        reason = 'no observed candidate resource-infeasibility feedback'
    elif problem.scenario_id != 'psr' or not problem.goal_facts or problem.goal_cardinality or problem.negative_goal_facts:
        reason = 'outside declared pure PSR named-delivery parent family'
    elif not all((_delivery_atom(f) for f in problem.goal_facts)):
        reason = 'parent contains other source obligations'
    elif mandatory_network_obligations:
        reason = 'mandatory source network obligations forbid discarding the original arrangement'
    elif context is None:
        reason = 'original prefix context missing'
    if reason:
        return {**result, 'reason': reason}
    extra = _other_network_obligations(context, problem)
    if extra:
        return {**result, 'reason': 'non-delivery Task/network obligations must remain', 'other_obligations': extra}
    original_actions = {a.action_id: a for a in context.original_actions or problem.actions}
    state = frozenset(context.original_state)
    if context.original_plan is not None and tuple(context.original_plan[:len(context.executed_prefix)]) != context.executed_prefix:
        return {**result, 'status': 'NATIVE_PREFIX_VIOLATION', 'reason': 'executed prefix is not original-plan prefix'}
    for aid in context.executed_prefix:
        action = original_actions.get(aid)
        if action is None or not action.applicable(state):
            return {**result, 'status': 'NATIVE_PREFIX_VIOLATION', 'reason': 'invalid action in executed original prefix'}
        state = frozenset(action.apply(state))
    observed = frozenset(state - context.observed_deviation_delete | context.observed_deviation_add)
    result['prefix_preservation'] = {'executed_prefix_action_ids': list(context.executed_prefix), 'prefix_replayed_state': sorted(state), 'observed_deviation_add': sorted(context.observed_deviation_add), 'observed_deviation_delete': sorted(context.observed_deviation_delete), 'replan_initial_state': sorted(observed), 'executed_prefix_reissued': False}
    if observed != problem.initial_state:
        return {**result, 'status': 'NATIVE_PREFIX_VIOLATION', 'reason': 'prefix plus observed deviation differs from common initial state'}
    module, function = ('baselines.htn_repair_holler.domain_repair', 'solve_htn') if method == 'htn' else ('baselines.bt.paper4_task_tree', 'solve_bt')
    kwargs = {'timeout_s': min(24.0, timeout_s * 0.8)} if method == 'bt' else {}
    limited = run_limited(module, function, (problem,), kwargs, timeout_s=timeout_s, memory_mb=memory_mb)
    result.update(native_process=limited.to_dict(), native_replan_calls=1, status=limited.status)
    if limited.status != 'COMPLETED':
        return result
    native = limited.result
    solved = native.solved if method == 'htn' else native.native_success
    result['status'] = native.status
    result['native_result'] = asdict(native) if method == 'htn' else native.to_dict()
    if solved:
        result['plan'] = deepcopy(list(native.plan))
    result['interpretation'] = 'independently regenerated native whole-parent candidate; symbolic/resource checks and actual execution remain separate'
    return result

"""Structural call bounds, independent of outcomes, costs or a desired winner."""
from dataclasses import asdict
from planning.repair.domain_hierarchy import HierarchyLimits

def derive_call_bounds(*, max_children=6, node_attempts=2, policy_goals=1):
    if any((type(x) is not int or x < 1 for x in (max_children, node_attempts, policy_goals))):
        raise ValueError('positive integer structure bounds required')
    task = node_attempts
    mission = node_attempts * (1 + max_children * task)
    scene = node_attempts * (1 + max_children * mission)
    per_goal = task + mission + scene
    return {'max_children': max_children, 'node_attempts': node_attempts, 'scope_model_upper': {'P': 0, 'T': task, 'M': mission, 'S': scene}, 'per_goal_all_scope_upper': per_goal, 'policy_goals': policy_goals, 'trial_model_upper': policy_goals * per_goal, 'derivation': 'B(T)=a; B(M)=a*(1+b*B(T)); B(S)=a*(1+b*B(M)); sum permitted scope attempts, then sum policy goals. Includes regeneration of every accepted prefix after a parent failure.'}

def case_call_bounds(case, *, max_children=6, node_attempts=2):
    from controller.scope import SCOPES
    from controller.scope import Scope
    from controller.scope import scope_order
    from controller.diagnosis import DiagnosisModule
    from controller.selector import ScopeSelector
    from controller.settings import frozen_weights
    from controller.settings import frozen_p_fix_params
    structural = derive_call_bounds(max_children=max_children, node_attempts=node_attempts)
    connected = case.communication == 'Connected'
    allowed = set(SCOPES) if connected else set(case.world.c_edge_for(case.failure.agent_id))
    posterior = DiagnosisModule().diagnose(case.failure, base_llm_client=None)
    selected = ScopeSelector(frozen_weights(), frozen_p_fix_params()).select(case.failure, posterior, {}, allowed, base_reachable=connected)
    nominal = [s.value for s in SCOPES if s in allowed and scope_order(s) >= scope_order(selected.sigma)]
    goals = [{'policy': 'nominal', 'scopes': nominal, 'logical_upper': sum((structural['scope_model_upper'][s] for s in nominal))}]
    if case.fallback is not None:
        if not connected:
            raise ValueError('the supplied Scene policy lacks a connected supervisory channel')
        goals.append({'policy': case.policy_id, 'scopes': ['S'], 'logical_upper': structural['scope_model_upper']['S']})
    return {**structural, 'actual_goal_scope_bounds': goals, 'trial_model_upper': sum((g['logical_upper'] for g in goals)), 'nominal_policy_upper': goals[0]['logical_upper'], 'source': 'same frozen rule/selector, actor communication scopes and public Scene rewrite policy as production; independent of model outcomes'}

def limits_for_case(case):
    bound = case_call_bounds(case)
    return HierarchyLimits(bound['trial_model_upper'], bound['node_attempts'], bound['max_children'], bound['nominal_policy_upper'])

def request_constraints(limits, session, call_ceiling, *, node=None, node_ids=()):
    ceiling = min(limits.max_model_logical, session.logical_cap, limits.max_model_logical if call_ceiling is None else call_ceiling)
    return {'limits': asdict(limits), 'remaining_model_logical_including_this_request': max(0, ceiling - session.logical_n), 'prompt_cap_candidate': session.prompt_cap, 'completion_reservation': session.completion_cap, 'transport_retry_upper': 1, 'remaining_physical_capacity': max(0, ceiling - session.logical_n) * 2, 'remaining_token_reservation': max(0, ceiling - session.logical_n) * 2 * (session.prompt_cap + session.completion_cap), 'remaining_trial_model_logical': max(0, min(limits.max_model_logical, session.logical_cap) - session.logical_n), 'max_immediate_children': limits.max_children, 'assigned_actor': getattr(node, 'assigned_agent_id', None), 'actor_rule': 'D_T keeps the assigned actor; flat and generated Tasks must name roster actors with the required capability', 'dependency_rule': 'acyclic dependencies on siblings only; ids globally unique in the retained hierarchy', 'reserved_node_ids': sorted(node_ids), 'child_contract_rule': 'textual postconditions and domain_goal positives agree; explicit negative/cardinality conditions remain obligations; complete parent checked after all deletes', 'leaf_schema': {'chain': 'array of objects; strings and mixed non-object elements are invalid', 'required': ['primitive', 'params'], 'params': 'object using actual domain schema', 'additional_step_fields_allowed': False, 'optional_D_T': ['agent_id (must equal assigned actor)', 'agent_type (must match roster)'], 'flat_required_additional': ['agent_id']}, 'may_decline': True, 'decline_shape': {'decline': True, 'reason': 'explain inability without fabricating a plan'}, 'budget_use': 'remaining allowance is available, not a requirement to spend it'}

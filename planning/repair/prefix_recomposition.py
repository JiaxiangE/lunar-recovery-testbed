"""Offline method prototype: a parsed generated prefix becomes a NEW candidate.

Used by the optional integrated Scene controller; default behavior stays off.
Both methods use the same candidate-local operator. Original model responses
and the original failed trial remain unchanged. This initial implementation has
one-actor child mapping; unsupported process duties are reported, not erased.
"""
from copy import deepcopy
import json
import time
from domains.transition import binding_observation
from domains.transition import observe
from execution.binding import verify_plan
from planning.schema.decomposition import Task
from planning.repair.domain_hierarchy import set_node_goal
from planning.repair.domain_hierarchy import problem_for_node
from planning.repair.output_contract import parse_generation
from planning.repair.resource_support import validate_resource_children

def propose(case, parent_problem, generated_sequence, *, scope, source_reference):
    started = time.perf_counter()
    scans = []
    output = {'version': 'parsed_prefix_recomposition_prototype_v1', 'source_reference': source_reference, 'original_generated_sequence': deepcopy(generated_sequence), 'scope': scope, 'obligation_origin': {'parent': 'supplied source/policy parent contract', 'explicit_source_process': deepcopy(case.original_information.get('mandatory_network_obligations', [])), 'generated': 'candidate sequence/child objectives added by model; not automatically immutable source requirements'}, 'candidate': None, 'prefix_checks': scans, 'automatic_runtime_adoption': False, 'model_calls': 0}

    def finish(status, **kwargs):
        return {**output, **kwargs, 'status': status, 'wall_s': time.perf_counter() - started}
    if scope not in {'M', 'S'}:
        return finish('TASK_OR_PRIMITIVE_OBLIGATIONS_RETAINED')
    if case.communication != 'Connected':
        return finish('SUPERVISORY_SCOPE_UNAVAILABLE')
    if observe(case.world, parent_problem.scenario_id) != parent_problem.initial_state or binding_observation(case.world, parent_problem.scenario_id) != parent_problem.observation.get('world'):
        return finish('SOURCE_STATE_CHANGED')
    from planning.repair.domain_hierarchy import goal_record
    known = [case.nominal]
    if case.policy_eligible and case.fallback is not None:
        known.append(case.fallback)
    if goal_record(parent_problem) not in [goal_record(p) for p in known]:
        return finish('PARENT_POLICY_NOT_AUTHORIZED')
    rewritten = goal_record(parent_problem) != goal_record(case.nominal)
    if rewritten and scope != 'S':
        return finish('GOAL_REWRITE_REQUIRES_SCENE')
    if scope == 'M':
        return finish('MISSION_BOUNDARY_MAPPING_NOT_IMPLEMENTED')
    if case.original_information.get('mandatory_network_obligations'):
        return finish('EXPLICIT_PROCESS_MAPPING_REQUIRED')
    remaining_tasks = [n for n in case.original_tree.nodes.values() if isinstance(n, Task) and (not n.children_ids or not set(n.children_ids) <= set(case.executed_prefix_ids))]
    if any((n.precondition or n.required_primitives for n in remaining_tasks)):
        return finish('EXPLICIT_PROCESS_MAPPING_REQUIRED')
    parent_members = set(parent_problem.goal_facts)
    parent_members.update((f for _, group in parent_problem.goal_cardinality for f in group))
    source_goals = []
    for node in remaining_tasks:
        contract = problem_for_node(parent_problem, node)
        source_goals.append({'task': node.id, 'goal': goal_record(contract), 'origin': 'original source tree', 'dependencies': list(node.dependencies), 'named_cardinality_refinement_replaced_by_policy': rewritten})
        if not contract.goal_facts <= parent_members or not contract.negative_goal_facts <= parent_problem.negative_goal_facts or contract.goal_cardinality:
            output['obligation_origin']['source_tasks'] = source_goals
            return finish('EXPLICIT_PROCESS_MAPPING_REQUIRED')
    output['obligation_origin']['source_tasks'] = source_goals
    output['obligation_origin']['structural_scope'] = {'replaced_root': case.original_tree.root_id, 'rule': 'Scene replaces its internal original decomposition, as in the existing S controller; explicit source process duties are not thereby waived', 'external_dependencies': list(case.original_tree.nodes[case.original_tree.root_id].dependencies)}
    if output['obligation_origin']['structural_scope']['external_dependencies']:
        return finish('EXPLICIT_PROCESS_MAPPING_REQUIRED')
    if parent_problem.goal_met(parent_problem.initial_state):
        return finish('MAINTENANCE_ONLY', new_recovery=False)
    parse_generation(json.dumps({'chain': generated_sequence}), 'flat')
    actors = {s['agent_id'] for s in generated_sequence}
    if len(actors) != 1:
        return finish('MULTI_ACTOR_CHILD_MAPPING_NOT_IMPLEMENTED')
    actor = next(iter(actors))
    for end in range(1, len(generated_sequence) + 1):
        plan = deepcopy(generated_sequence[:end])
        verdict = verify_plan(parent_problem, plan)
        scans.append({'prefix_length': end, 'accepted': verdict['accepted'], 'reason': verdict['reason']})
        if not verdict['accepted']:
            continue
        child = set_node_goal(Task(id='recomposed_task', assigned_agent_id=actor), parent_problem)
        item = {**child.model_dump(mode='json'), 'chain': plan}
        composition = validate_resource_children(parent_problem, [item], plan)
        if not composition['accepted']:
            continue
        return finish('NEW_COMPLETE_CANDIDATE_VERIFIED', candidate=plan, changed=plan != generated_sequence, new_children=[item], child_parent_verification=composition, new_recovery=True, resource_prediction='not part of the formal guarantee; runtime resource check and current binding still required before adoption', removed_generated_suffix=deepcopy(generated_sequence[end:]))
    return finish('NO_VALID_COMPLETE_PREFIX')

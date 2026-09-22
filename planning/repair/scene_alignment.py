"""Current request views and one budgeted clarification, never a tree mutation."""
from copy import deepcopy
import json
VERSION = 'current_goal_scene_view_v5'

def request_alignment(problem, node, information, scope):
    from planning.repair.domain_hierarchy import goal_record
    goal = goal_record(problem)
    policy = information.get('public_policy') or {}
    tree = information.get('tree') or {}
    root = tree.get('root_id')
    nodes = tree.get('nodes') or {}
    old = nodes.get(root, {})
    at_root = node is None or (node.layer == 'S' and node.id == root)
    nominal = goal == policy.get('nominal_goal')
    replacement = policy.get('eligible') is True and goal == policy.get('fallback_goal')
    connected = problem.observation.get('communication_policy', {}).get('state') == 'Connected'
    authorized = connected and scope == 'S' and at_root and bool(root) and (nominal or replacement)
    children = list(old.get('children_ids', []))
    retained = [] if authorized else children
    return {'authorized_scene_replacement': authorized, 'policy_replacement': authorized and replacement, 'policy_id': policy.get('id'), 'goal': goal, 'root_id': root, 'replaced_children_ids': [i for i in children if i not in retained] if authorized else [], 'retained_children': [deepcopy(nodes[i]) for i in retained], 'external_dependencies': deepcopy(old.get('dependencies', [])), 'source_precondition': old.get('precondition', ''), 'explicit_process_obligations': deepcopy(information.get('mandatory_network_obligations', []))}

def scoped_history(information):
    """Keep historical values and exact available provenance, without current assertions."""
    policy = information.get('public_policy') or {}
    observation_fields = {'original_observation', 'pre_deviation_observation', 'post_deviation_observation', 'observed_deviation'}

    def walk(value, path=()):
        if isinstance(value, list):
            return [walk(v, path + (str(i),)) for i, v in enumerate(value)]
        if not isinstance(value, dict):
            return deepcopy(value)
        out = {}
        for key, item in value.items():
            label = key == 'scene_unsatisfiable' or (key == 'symptom' and isinstance(item, str) and ('scene_unsatisfiable' in item))
            if label:
                known = bool(path and path[0] in observation_fields)
                out['historical_' + key] = {'original_value': deepcopy(item), 'source_path': 'original_information.' + '.'.join(path + (key,)), 'then_goal': deepcopy(policy.get('nominal_goal')) if known else None, 'then_state_reference': '.'.join(path) if known else None, 'observed_sim_time_s': value.get('sim_time_s') if known else None, 'meaning': 'historical observation/diagnosis, not an unsolvability proof for the current requested goal; absent provenance is unknown'}
            else:
                out[key] = walk(item, path + (key,))
        return out
    return walk(information)

def clarification(session, problem, node, information, *, scope, attempt, limits, call_ceiling):
    if not getattr(session, 'goal_alignment_enabled', False):
        return None
    alignment = request_alignment(problem, node, information, scope)
    if not alignment['policy_replacement']:
        return None
    ceiling = min(limits.max_model_logical, session.logical_cap, limits.max_model_logical if call_ceiling is None else call_ceiling)
    if attempt >= limits.node_attempts or session.logical_n >= ceiling:
        return None
    key = json.dumps([alignment['policy_id'], alignment['goal']], sort_keys=True)
    used = getattr(session, 'goal_clarifications_used', set())
    if key in used:
        return None
    used.add(key)
    session.goal_clarifications_used = used
    return {'kind': 'current_goal_clarification', 'current_goal': alignment['goal'], 'retained_children': alignment['retained_children'], 'external_dependencies': alignment['external_dependencies'], 'source_precondition': alignment['source_precondition'], 'explicit_process_obligations': alignment['explicit_process_obligations'], 'clarification': 'The approved current parent replaces the historical goal. Replaced internal children are history; retained children and external/process obligations remain required. Historical infeasibility labels apply only to their recorded goal/state. Reconsider this current request once; another decline is allowed. No feasibility guarantee or candidate is supplied.'}

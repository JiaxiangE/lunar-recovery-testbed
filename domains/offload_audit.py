"""Observed offload state changes, independent of earlier store call counts."""
from copy import deepcopy

def custody_state(world):
    return {'cargo': {a: sorted(v.get('cargo', ())) for a, v in world.agents.items()}, 'statuses': {s: v['status'] for s, v in world.samples.items()}, 'base_storage': sorted(world.base_storage), 'sim_time_s': world.sim_time_s, 'actors': {a: {'position': list(v['position']), 'energy_wh': v['energy_wh'], 'facts': sorted(v.get('facts', ()))} for a, v in world.agents.items()}}

def offload_transition(before, after, actor, *, success, step_index, prior_store_calls=0):
    cargo = set(before['cargo'][actor])
    expected = {s for s in cargo if before['statuses'][s] == 'stored'}
    removed = cargo - set(after['cargo'][actor])
    added = set(after['base_storage']) - set(before['base_storage'])
    moved = {s for s in added if after['statuses'].get(s) == 'in_base'}
    nonstored = cargo - expected
    other = {a: before['cargo'][a] == after['cargo'].get(a) and all((before['statuses'][s] == after['statuses'].get(s) for s in before['cargo'][a])) for a in before['cargo'] if a != actor}
    checks = {'moved_set_matches': moved == expected and removed == expected and (added == expected), 'nonstored_retained': nonstored <= set(after['cargo'][actor]) and all((before['statuses'][s] == after['statuses'].get(s) for s in nonstored)), 'other_custody_unchanged': all(other.values())}
    return {'step_index': step_index, 'actor': actor, 'execution': 'succeeded' if success is True else 'failed' if success is False else 'not_dispatched', 'before': deepcopy(before), 'after': deepcopy(after), 'expected_stored_cargo': sorted(expected), 'actual_moved_to_base': sorted(moved), 'cargo_removed': sorted(removed), 'base_added': sorted(added), 'state_changed': before != after, 'observed_relations': checks, 'required_success_checks': checks if success is True else None, 'successful_offload_matches': all(checks.values()) if success is True else None, 'prior_store_calls_descriptive_only': prior_store_calls, 'expectation_scope': 'immediate pre-dispatch state' if success is not None else 'observed stop state; no future prefix simulated'}

def remaining_goal(problem, state):
    return {'parent_goal_met': problem.goal_met(state), 'missing_positive': sorted(problem.goal_facts - state), 'forbidden_present': sorted(problem.negative_goal_facts & state), 'cardinality': [{'required': k, 'achieved': len(g & state), 'shortfall': max(0, k - len(g & state)), 'missing_members': sorted(g - state)} for k, g in problem.goal_cardinality]}

def execution_offload_report(world, problem, plan, records, dispatch_n, state):
    rows = deepcopy(records)
    stopped = custody_state(world)
    for i, raw in enumerate(plan):
        step = raw if isinstance(raw, dict) else problem.action_by_id().get(raw) if isinstance(raw, str) else None
        if hasattr(step, 'to_step'):
            step = step.to_step()
        if i >= dispatch_n and isinstance(step, dict) and (step.get('primitive') == 'sample_offload') and (step.get('agent_id') in stopped['cargo']):
            rows.append(offload_transition(stopped, stopped, step['agent_id'], success=None, step_index=i))
    return {'version': 'psr_offload_step_state_v1', 'evidence_source': 'actual execution only; not original candidate simulation', 'offloads': rows, **remaining_goal(problem, state)}

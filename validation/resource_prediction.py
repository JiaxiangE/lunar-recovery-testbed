"""Offline simulator resource diagnostics; deliberately outside the formal gate.

The predictor uses an independent world copy and the existing world transition
function. It predicts this deterministic simulator, not an independent hardware
energy model. No acceptance label or expected-success answer is an input.
"""
from copy import deepcopy
from dataclasses import asdict
from itertools import permutations
import json
from pathlib import Path
import time
from domains.common import observe
from execution.binding import verify_plan
from execution.binding import execute_plan

def remaining_obligations(problem, state):
    return {'positive_facts': sorted(problem.goal_facts - state), 'forbidden_facts_present': sorted(problem.negative_goal_facts & state), 'cardinality': [{'required': k, 'achieved': len(group & state), 'shortfall': max(0, k - len(group & state)), 'not_yet_achieved': sorted(group - state)} for k, group in problem.goal_cardinality]}

def resource_information(world, scenario):
    """Same source-backed observation packet available to every method.

This is data for requests/planners, not a new contract. Unknown domains retain
unknown numerical costs even when a placeholder energy field exists in world.
"""
    if scenario != 'psr':
        return {'scenario': scenario, 'numeric_resource_model': 'unknown', 'reason': 'No approved numeric movement/task energy source for this execution world.', 'formal_energy_guarantee': False}
    names = sorted(world.agents)
    from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
    return {'scenario': scenario, 'numeric_resource_model': 'PSRWorld deterministic simulator', 'formal_energy_guarantee': False, 'source': ['domains/physics/energy_model.py', 'domains/actions/psr/primitives.py'], 'config': asdict(world.energy_model.cfg), 'travel_rule': 'distance_3d * (travel_cost_per_m + abs(target_slope_deg) * slope_factor)', 'runtime_failure_rule': 'An action fails before effects if actor.energy_wh < action_cost_wh.', 'low_energy_threshold_note': 'Config threshold is not an extra return obligation in PSRWorld.step.', 'actors': {a: {'position': list(world.agents[a]['position']), 'energy_wh': world.agents[a]['energy_wh'], 'cargo': sorted(world.agents[a]['cargo']), 'agent_type': world.agents[a]['agent_type'], 'available': world.agents[a].get('available', True), 'capabilities': sorted(world.agents[a].get('capabilities', [n for n, s in STRICT_TOKEN_SPECS.items() if world.agents[a]['agent_type'] in s.agent_types]))} for a in names}, 'samples': {s: {'position': list(v['location']), 'status': v['status'], 'target_slope_deg': world.terrain.get_slope(*v['location'][:2]), 'traversable': world.terrain.is_traversable(*v['location'][:2]), 'unreachable': s in world.injected_unreachable} for s, v in world.samples.items()}, 'base': {'position': list(world.base_pos), 'target_slope_deg': world.terrain.get_slope(*world.base_pos[:2])}, 'nonmovement_cost_wh': {n: world.energy_model.compute_task_cost(n) for n in STRICT_TOKEN_SPECS if n not in {'move_to', 'return_to_base', 'navigate_to_relay'}}}

def _cost(world, step):
    if step['primitive'] in {'move_to', 'return_to_base', 'navigate_to_relay'}:
        target = world.resolve_target(step['primitive'], step['params'], step['agent_id'])
        if target is None:
            return None
        return world.energy_model.compute_travel_cost(world.energy_model.euclidean_distance(world.agents[step['agent_id']]['position'], target), world.terrain.get_slope(*target[:2]))
    return world.energy_model.compute_task_cost(step['primitive'])

def predict_resources(world, problem, plan):
    """Roll out supplied steps, stop at first actual simulator failure, no gate."""
    started = time.perf_counter()
    if problem.scenario_id != 'psr':
        return {'resource_feasible': None, 'numeric_resource_model': 'unknown', 'steps': [], 'first_failure': None, 'first_resource_failure': None, 'prediction_dispatch_n': 0}
    copied = deepcopy(world)
    initial = {a: v['energy_wh'] for a, v in copied.agents.items()}
    trace, first_failure = ([], None)
    from domains.offload_audit import custody_state
    from domains.offload_audit import offload_transition
    from domains.offload_audit import execution_offload_report
    offloads = []
    for index, step in enumerate(deepcopy(plan)):
        actor = copied.agents[step['agent_id']]
        before = actor['energy_wh']
        position = list(actor['position'])
        required = _cost(copied, step)
        custody = custody_state(copied) if step['primitive'] == 'sample_offload' else None
        result = copied.step(step['primitive'], step['agent_id'], step['params'])
        if custody is not None:
            offloads.append(offload_transition(custody, custody_state(copied), step['agent_id'], success=result.success, step_index=index, prior_store_calls=sum((r['action']['primitive'] == 'sample_store' and r['actor'] == step['agent_id'] for r in trace))))
        row = {'step_index': index, 'step_number': index + 1, 'action': step, 'actor': step['agent_id'], 'position_before': position, 'position_after': list(actor['position']), 'energy_before_wh': before, 'required_energy_wh': required, 'energy_after_wh': actor['energy_wh'], 'consumed_wh': before - actor['energy_wh'], 'result': asdict(result), 'remaining_obligations': remaining_obligations(problem, observe(copied, 'psr'))}
        trace.append(row)
        if not result.success:
            first_failure = row
            break
    resource_failure = first_failure if first_failure and first_failure['result']['failure_mode'] == 'R-02_low_energy' else None
    offload_report = execution_offload_report(copied, problem, plan, offloads, len(trace), observe(copied, 'psr'))
    offload_report['evidence_source'] = 'candidate simulation in an independent copy; zero actual dispatch'
    return {'resource_feasible': False if resource_failure else None if first_failure else True, 'simulation_offload_audit': offload_report, 'numeric_resource_model': 'PSRWorld deterministic simulator clone', 'prediction_scope': 'same-source simulator forecast; no hardware validation; no formal guarantee', 'steps': trace, 'first_failure': first_failure, 'first_resource_failure': resource_failure, 'prediction_dispatch_n': len(trace), 'goal_met_after_prediction': problem.goal_met(observe(copied, 'psr')), 'remaining_obligations': remaining_obligations(problem, observe(copied, 'psr')), 'energy_used_by_actor_wh': {a: initial[a] - copied.agents[a]['energy_wh'] for a in initial}, 'total_energy_used_wh': sum((initial[a] - copied.agents[a]['energy_wh'] for a in initial)), 'prediction_wall_s': time.perf_counter() - started}

def diagnose_candidate(world, problem, plan):
    prediction = predict_resources(world, problem, plan)
    symbolic = verify_plan(problem, deepcopy(plan))
    actual = execute_plan(deepcopy(world), problem, deepcopy(plan), capture_transitions=True)
    return {'symbolic': symbolic, 'resource_prediction': prediction, 'actual_execution': actual}

def _batch_plan(groups, *, store_at_base=True, dock=False):
    plan = []

    def add(name, actor, **params):
        plan.append({'primitive': name, 'agent_id': actor, 'params': params})
    for actor, ids in groups:
        if not ids:
            continue
        for sid in ids:
            add('move_to', actor, target=sid)
            add('sample_collect', actor, sample_id=sid)
            if not store_at_base:
                add('sample_store', actor, sample_id=sid)
        add('return_to_base', actor)
        if store_at_base:
            for sid in ids:
                add('sample_store', actor, sample_id=sid)
        if dock:
            add('dock_with_base', actor)
        add('sample_offload', actor, base='base')
    return plan

def resource_aware_delivery_prototype(world, problem, *, max_route_orders=120):
    """Bounded Mission-level development planner for named PSR delivery goals.

Reassigns/batches obligations; caller must rebuild and validate Task/parent
decomposition. It is a method improvement candidate, never a shared-gate repair
or an automatic execution fallback. No claim of optimality/completeness.
"""
    if isinstance(max_route_orders, bool) or not isinstance(max_route_orders, int) or max_route_orders < 1:
        raise ValueError('max_route_orders must be a positive integer')
    if problem.scenario_id != 'psr':
        return {'status': 'NUMERIC_RESOURCE_UNKNOWN', 'plan': None}
    from domains.common import atom
    ids = sorted((s for s in world.samples if atom('in_base_storage', s) in problem.goal_facts and s not in world.base_storage))
    if not ids or problem.goal_cardinality or problem.negative_goal_facts or any((world.samples[s]['status'] != 'field' for s in ids)):
        return {'status': 'UNSUPPORTED_DEVELOPMENT_PROTOTYPE_INPUT', 'plan': None}
    packet = resource_information(world, 'psr')
    needed = {'move_to', 'sample_collect', 'sample_store', 'return_to_base', 'sample_offload'}
    actors = [a for a, info in packet['actors'].items() if info['available'] is True and needed <= set(info['capabilities'])]
    started = time.perf_counter()
    candidates, rollout_n, step_n = ([], 0, 0)
    for order_index, order in enumerate(permutations(ids)):
        if order_index >= max_route_orders:
            break
        assignments = [[(a, order)] for a in actors]
        if len(actors) > 1:
            assignments.append([(a, order[i::len(actors)]) for i, a in enumerate(actors)])
        for groups in assignments:
            plan = _batch_plan(groups)
            predicted = predict_resources(world, problem, plan)
            rollout_n += 1
            step_n += predicted['prediction_dispatch_n']
            if predicted['resource_feasible'] is True and predicted['goal_met_after_prediction']:
                symbolic = verify_plan(problem, plan)
                if symbolic['accepted']:
                    used = predicted['energy_used_by_actor_wh']
                    max_fraction = max((used[a] / max(world.agents[a]['energy_wh'], 1e-12) for a in used))
                    candidates.append((max_fraction, predicted['total_energy_used_wh'], plan, predicted))
    best = min(candidates, key=lambda c: c[:2]) if candidates else None
    return {'status': 'CANDIDATE_FOUND' if best else 'BOUNDED_SEARCH_NO_CANDIDATE', 'plan': best[2] if best else None, 'prediction': best[3] if best else None, 'scope': 'Mission assignment and Task regrouping', 'required_followup': 'Rebuild child contracts and parent composition; no silent Task-actor or dependency changes.', 'selection_rule': 'minimize maximum actor energy utilization, then total simulated energy, over declared candidate family', 'completeness': False, 'max_route_orders': max_route_orders, 'local_cost': {'candidate_rollouts': rollout_n, 'world_steps': step_n, 'wall_s': time.perf_counter() - started, 'logical_model_calls': 0, 'physical_model_calls': 0}}

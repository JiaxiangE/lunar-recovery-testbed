"""Common mapped-plan verification and actual-world execution, no repair/search.

All methods receive this same wrapper. It never substitutes a goal, chooses a
fallback, or fabricates a missing action. A native solver's success and intended
task success are separate. Conditional symbolic effects are not injected into
the real world; real steps can still fail on runtime physics/injections.
"""
from copy import deepcopy
from dataclasses import asdict
import time
from domains.actions.spec import _freeze_identity
from validation.contracts.recovery_gate import normalize_candidate_step
from domains.transition import observe
from domains.transition import binding_observation
from domains.transition import authorization_context

def _canonical_params(params, scenario, primitive):
    value = dict(params)
    if scenario == 'psr' and primitive == 'move_to' and isinstance(value.get('target'), str):
        value['target'] = value['target'].removesuffix('.pos')
    if scenario == 'psr' and primitive == 'sample_offload' and (set(value) <= {'base', 'target'}):
        if value and all((v == 'base' for v in value.values())):
            value = {'base': 'base'}
    return _freeze_identity(value)

def verify_plan(problem, plan):
    started = time.perf_counter()
    result = _verify_plan(problem, plan)
    reason = result['reason']
    mapping_failed = reason in {'MAPPER_REJECTED', 'ACTOR_TYPE_MISMATCH'}
    partial = reason == 'PRECONDITION_UNMET'
    malformed = reason == 'MALFORMED_PLAN'
    result['grounding'] = {'accepted': False if mapping_failed else None if partial or malformed else True, 'checked_through_step': result.get('step_index'), 'complete': not (partial or malformed), 'reason': reason if mapping_failed or partial or malformed else 'all steps grounded'}
    result['symbolic_contract'] = {'accepted': None if mapping_failed or malformed else result['accepted'], 'reason': reason, 'scope': 'preconditions, effects and complete supplied goal; no numeric resource guarantee'}
    result['verification_s'] = time.perf_counter() - started
    return result

def _verify_plan(problem, plan):
    started = time.perf_counter()
    if not isinstance(plan, (list, tuple)):
        return {'accepted': False, 'reason': 'MALFORMED_PLAN'}
    state = problem.initial_state
    chosen = []
    by_id = problem.action_by_id()
    for index, raw in enumerate(plan):
        if isinstance(raw, str):
            candidates = [by_id[raw]] if raw in by_id else []
        else:
            step = normalize_candidate_step(raw)
            if step is None or not isinstance(step['params'], dict):
                return {'accepted': False, 'reason': 'MALFORMED_PLAN', 'step_index': index}
            declared_type = step.get('agent_type')
            from planning.repair.actor_interface import VERSION as actor_version
            from planning.repair.actor_interface import actor_error
            if problem.observation.get('model_interface_version') == actor_version:
                error = actor_error(raw, index, problem.observation['actor_types'])
                if error is not None:
                    return {'accepted': False, 'reason': error['reason'], 'step_index': index, 'structured_error': error}
            if declared_type is not None and declared_type != problem.observation.get('actor_types', {}).get(step['agent_id']):
                return {'accepted': False, 'reason': 'ACTOR_TYPE_MISMATCH', 'step_index': index}
            candidates = [a for a in problem.actions if a.primitive == step['primitive'] and a.agent_id == step['agent_id'] and (_canonical_params(a.params, problem.scenario_id, a.primitive) == _canonical_params(step['params'], problem.scenario_id, a.primitive))]
        if not candidates:
            return {'accepted': False, 'reason': 'MAPPER_REJECTED', 'step_index': index}
        action = next((a for a in candidates if a.applicable(state)), None)
        if action is None:
            return {'accepted': False, 'reason': 'PRECONDITION_UNMET', 'step_index': index}
        chosen.append(action)
        state = action.apply(state)
    if not problem.goal_met(state):
        return {'accepted': False, 'reason': 'INTENDED_GOAL_NOT_ENTAILED', 'final_state': sorted(state)}
    import z3
    encode_started = time.perf_counter()
    atoms = set(state) | set(problem.goal_facts) | set(problem.negative_goal_facts)
    for _, group in problem.goal_cardinality:
        atoms.update(group)
    variables = {f: z3.Bool(f'fact_{i}') for i, f in enumerate(sorted(atoms))}
    solver = z3.Solver()
    for f, var in variables.items():
        solver.add(var == (f in state))
    clauses = [variables[f] for f in problem.goal_facts] + [z3.Not(variables[f]) for f in problem.negative_goal_facts]
    clauses += [z3.AtLeast(*[variables[f] for f in sorted(group)], k) if group else z3.BoolVal(k == 0) for k, group in problem.goal_cardinality]
    solver.add(z3.Not(z3.And(*clauses)))
    status = str(solver.check())
    encode_check_s = time.perf_counter() - encode_started
    return {'accepted': status == 'unsat', 'reason': 'VERIFIED' if status == 'unsat' else 'SOLVER_ERROR', 'action_ids': [a.action_id for a in chosen], 'normalized_plan': [deepcopy(a.to_step()) for a in chosen], 'final_state': sorted(state), 'solver_version': z3.get_version_string(), 'solver_status': status, 'solver_encode_check_s': encode_check_s, 'verification_s': time.perf_counter() - started}

def execute_plan(world, problem, plan, *, capture_transitions=False):
    started = time.perf_counter()
    continuous = getattr(world, 'continuous_control', None)
    revision = continuous.checkpoint('before_validation', expected=continuous.entry_revision, problem=problem) if continuous is not None else None
    from domains.offload_audit import custody_state
    from domains.offload_audit import offload_transition
    from domains.offload_audit import execution_offload_report
    offloads = []
    planned = deepcopy(list(plan))

    def finish(result):
        if problem.scenario_id == 'psr' and problem.domain_version == 'paper4_domain_extension_v2':
            result['offload_audit'] = execution_offload_report(world, problem, planned, offloads, result.get('dispatch_n', 0), observe(world, 'psr'))
        return result
    if problem.scenario_id == 'psr':
        from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
        from domains.actions.psr.strict_profile import LEGACY_PSR_PROFILE
        required = STRICT_PSR_PROFILE if problem.domain_version == 'paper4_domain_extension_v2' else LEGACY_PSR_PROFILE
        if getattr(world, 'semantics_profile', LEGACY_PSR_PROFILE) != required:
            return finish({'accepted': False, 'reason': 'SEMANTICS_PROFILE_MISMATCH', 'dispatch_n': 0, 'goal_met': None})
    problem = deepcopy(problem)
    authorized_context = authorization_context(world)
    if observe(world, problem.scenario_id) != problem.initial_state or binding_observation(world, problem.scenario_id) != problem.observation.get('world'):
        return finish({'accepted': False, 'reason': 'INITIAL_STATE_CHANGED', 'dispatch_n': 0, 'goal_met': None})
    verified = verify_plan(problem, deepcopy(list(plan)))
    if not verified['accepted']:
        return finish({**verified, 'dispatch_n': 0, 'execution_success': None, 'goal_met': None})
    if continuous is not None:
        continuous.bind_candidate(world, problem, verified, revision)
    steps = deepcopy(verified['normalized_plan'])
    planned = steps
    expected = problem.initial_state
    by_id = problem.action_by_id()
    actual, executed, transitions = ([], [], [])
    initial_time = world.sim_time_s
    initial_energy = {a: v['energy_wh'] for a, v in world.agents.items()}
    consistency = True
    for action_id, step in zip(verified['action_ids'], steps):
        if observe(world, problem.scenario_id) != expected or authorization_context(world) != authorized_context:
            consistency = False
            break
        if capture_transitions:
            import math
            actor = world.agents[step['agent_id']]
            sid = step['params'].get('sample_id')
            sample = getattr(world, 'samples', {}).get(sid, {})
            observation = {'actor': step['agent_id'], 'primitive': step['primitive'], 'position_before': deepcopy(actor.get('position')), 'sample_id': sid, 'sample_position_before': deepcopy(sample.get('location')), 'sample_status_before': sample.get('status'), 'cargo_before': sorted(actor.get('cargo', ())), 'base_storage_before': sorted(getattr(world, 'base_storage', ())), 'energy_wh_before': actor.get('energy_wh'), 'collection_distance_before_m': math.dist(actor['position'], sample['location']) if step['primitive'] == 'sample_collect' and sample.get('location') is not None else None}
        before_offload = custody_state(world) if problem.scenario_id == 'psr' and step['primitive'] == 'sample_offload' else None
        result = world.step(step['agent_id'], step['primitive'], step['params']) if problem.scenario_id == 'lava' else world.step(step['primitive'], step['agent_id'], step['params'])
        if before_offload is not None:
            offloads.append(offload_transition(before_offload, custody_state(world), step['agent_id'], success=result.success, step_index=len(executed), prior_store_calls=sum((s['primitive'] == 'sample_store' and s['agent_id'] == step['agent_id'] for s in executed))))
        if capture_transitions:
            observation.update(position_after=deepcopy(actor.get('position')), cargo_after=sorted(actor.get('cargo', ())), sample_status_after=sample.get('status'), base_storage_after=sorted(getattr(world, 'base_storage', ())), energy_wh_after=actor.get('energy_wh'), success=result.success)
            transitions.append(observation)
        actual.append(asdict(result))
        executed.append(step)
        if not result.success:
            break
        expected = by_id[action_id].apply(expected)
        if observe(world, problem.scenario_id) != expected:
            consistency = False
            break
    final = observe(world, problem.scenario_id)
    return finish({'accepted': True, 'reason': 'EXECUTED' if consistency else 'DOMAIN_WORLD_DIVERGENCE', 'execution_success': consistency and all((r['success'] for r in actual)), 'goal_met': problem.goal_met(final), 'recovery_required': not problem.goal_met(problem.initial_state), 'dispatch_n': len(actual), 'executed_plan': executed, 'execution_results': actual, **({'transition_observations': transitions} if capture_transitions else {}), 'verification': verified, 'final_state': world.snapshot_state(), 'final_facts': sorted(final), 'elapsed_s': world.sim_time_s - initial_time, 'energy_consumed_wh': sum((initial_energy[a] - world.agents[a]['energy_wh'] for a in world.agents)) if problem.scenario_id == 'psr' else None, 'wrapper_wall_s': time.perf_counter() - started, 'actual_radio_delivery_observed': False, 'timing_kind': 'nominal simulator work time; not hardware latency'})

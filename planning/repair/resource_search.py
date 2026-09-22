"""Bounded PSR delivery regrouping; a method aid, never a formal resource gate.

The declared finite family enumerates sample-to-actor assignments and each
actor's route permutations. Each actor collects its assigned outstanding field
samples, returns once, stores its cargo, and offloads once. Actor blocks use
lexical order. This family does not exhaust all legal PSR plans. The objective
is lexicographic (maximum actor energy utilization, total simulated energy).
Public-observation reconstruction deliberately excludes future injection hooks.
"""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import asdict
from dataclasses import replace
from itertools import permutations
from itertools import product
import math
import time
from domains.common import atom
from domains.common import observe
from domains.common import with_goal
from execution.binding import verify_plan
from validation.resource_prediction import resource_information
from validation.resource_prediction import remaining_obligations
IMPLEMENTATION_ID = 'psr_public_observation_delivery_search_v1'

@dataclass(frozen=True)
class ResourceSearchLimits:
    max_candidate_rollouts: int = 1024
    max_world_steps: int = 30000
    search_wall_s: float = 24.0
    hard_wall_s: float = 30.0
    memory_mb: int = 2048

    def __post_init__(self):
        for name in ('max_candidate_rollouts', 'max_world_steps', 'memory_mb'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(name + ' must be a positive integer')
        if not (0 < self.search_wall_s < self.hard_wall_s and math.isfinite(self.hard_wall_s)):
            raise ValueError('finite 0 < search_wall_s < hard_wall_s required')

class _ObservedTerrain:

    def __init__(self, positions):
        self.positions = positions

    def get_slope(self, x, y):
        return self.positions[x, y][0]

    def is_traversable(self, x, y):
        return self.positions[x, y][1]

def public_forecast_world(world):
    """Reconstruct only observed state and known costs, not world.__dict__.

    Current unreachable/radio/availability facts are observations. A queued
    inject_failure hook is future simulator information and is not copied.
    Terrain queries are restricted to explicitly exposed named destinations.
    """
    from domains.worlds.psr_world import PSRWorld
    packet = resource_information(world, 'psr')
    copied = PSRWorld(energy_config=packet['config'], n_samples=len(world.samples), comm_config=deepcopy(world.comm_config), semantics_profile=world.semantics_profile)
    copied.base_pos = tuple(packet['base']['position'])
    copied.relay_pos = deepcopy(world.relay_pos)
    copied.agents = {}
    for aid, info in packet['actors'].items():
        copied.agents[aid] = {'id': aid, 'position': tuple(info['position']), 'energy_wh': info['energy_wh'], 'initial_energy_wh': info['energy_wh'], 'facts': set(world.agents[aid]['facts']), 'cargo': set(info['cargo']), 'agent_type': info['agent_type'], 'available': info['available'], 'capabilities': set(info['capabilities']), 'docked': bool(world.agents[aid].get('docked', False))}
        for observed_flag in ('disabled', 'tipped'):
            if observed_flag in world.agents[aid]:
                copied.agents[aid][observed_flag] = deepcopy(world.agents[aid][observed_flag])
    copied.samples = {sid: {'location': tuple(info['position']), 'status': info['status']} for sid, info in packet['samples'].items()}
    copied.base_storage = set(world.base_storage)
    copied.injected_comm_offline = set(world.injected_comm_offline)
    copied.injected_unreachable = set(world.injected_unreachable)
    copied.sim_time_s = world.sim_time_s
    copied.agent_busy_s = deepcopy(world.agent_busy_s)
    copied.strict_collect = world.strict_collect
    positions = {tuple(v['position'][:2]): (v['target_slope_deg'], v['traversable']) for v in packet['samples'].values()}
    for p in [copied.base_pos, *copied.relay_pos.values(), *(a['position'] for a in copied.agents.values())]:
        positions[tuple(p[:2])] = (world.terrain.get_slope(*p[:2]), world.terrain.is_traversable(*p[:2]))
    copied.terrain = _ObservedTerrain(positions)
    return copied
public_prediction_world = public_forecast_world

def predict_public_resources(world, problem, plan):
    """Common candidate forecast; zero actual dispatch, no future hook access."""
    from validation.resource_prediction import predict_resources
    if problem.scenario_id != 'psr':
        return predict_resources(world, problem, plan)
    return predict_resources(public_forecast_world(world), problem, plan)

def _routes(ids, actors, owners):

    def actor_routes(groups, index=0, prefix=()):
        if index == len(actors):
            yield prefix
            return
        for route in permutations(groups[actors[index]]):
            yield from actor_routes(groups, index + 1, prefix + (route,))
    choices = [[owners[s]] if s in owners else actors for s in ids]
    for assignment in product(*choices):
        groups = {a: tuple((s for s, assigned in zip(ids, assignment) if assigned == a)) for a in actors}
        for routes in actor_routes(groups):
            yield [(a, route) for a, route in zip(actors, routes) if route]

def _candidate(world, groups):
    plan, children = ([], [])
    for aid, ids in groups:
        chain = []

        def add(name, **params):
            chain.append({'primitive': name, 'agent_id': aid, 'params': params})
        for sid in ids:
            if world.samples[sid]['status'] == 'field':
                add('move_to', target=sid)
                add('sample_collect', sample_id=sid)
        add('return_to_base')
        for sid in ids:
            if world.samples[sid]['status'] != 'stored':
                add('sample_store', sample_id=sid)
        add('sample_offload', base='base')
        goals = [atom('in_base_storage', s) for s in ids] + [atom('at_base', aid)]
        children.append({'id': 'resource_task_' + aid, 'layer': 'T', 'assigned_agent_id': aid, 'dependencies': [], 'precondition': '', 'postcondition': ' & '.join(goals), 'context': {'domain_goal': {'positive': goals, 'negative': [], 'cardinality': []}}, 'required_primitives': sorted({s['primitive'] for s in chain}), 'chain': chain})
        plan.extend(chain)
    return (plan, children)

def _forecast(world, problem, plan):
    copied = deepcopy(world)
    initial = {a: v['energy_wh'] for a, v in copied.agents.items()}
    failure = None
    dispatched = 0
    for index, step in enumerate(plan):
        result = copied.step(step['primitive'], step['agent_id'], step['params'])
        dispatched += 1
        if not result.success:
            failure = {'step_index': index, 'actor': step['agent_id'], 'action': deepcopy(step), 'result': asdict(result)}
            break
    used = {a: initial[a] - copied.agents[a]['energy_wh'] for a in initial}
    state = observe(copied, 'psr')
    return {'resource_feasible': failure is None, 'first_failure': failure, 'world_steps': dispatched, 'goal_met_after_prediction': problem.goal_met(state), 'energy_used_by_actor_wh': used, 'total_energy_used_wh': sum(used.values()), 'maximum_actor_energy_utilization': max((used[a] / initial[a] if initial[a] > 0 else 0.0 if used[a] == 0 else float('inf') for a in initial)), 'remaining_obligations': remaining_obligations(problem, state), 'prediction_scope': 'public-observation same-source simulator forecast; not hardware or formal guarantee'}

def validate_regrouping(problem, children, plan):
    """Check every actual child at its sequential start and then full parent.

    Parent validation is independent of declared children: omitting a sample,
    lying about a child postcondition or deleting a prior result cannot turn
    an incomplete/invalid parent into success.
    """
    flattened = [step for child in children for step in child['chain']]
    if flattened != plan:
        return {'accepted': False, 'reason': 'CHILD_PLAN_BINDING_MISMATCH', 'children': []}
    current, checks = (problem.initial_state, [])
    for child in children:
        if any((step['agent_id'] != child['assigned_agent_id'] for step in child['chain'])):
            return {'accepted': False, 'reason': 'CHILD_ACTOR_MISMATCH', 'children': checks}
        goal = child['context']['domain_goal']
        from domains.predicates import parse_predicate
        declared = parse_predicate(child['postcondition']).atomics
        if {atom(t.name, *t.args) for t in declared if not t.negated} != set(goal['positive']) or {atom(t.name, *t.args) for t in declared if t.negated} != set(goal['negative']):
            return {'accepted': False, 'reason': 'CHILD_CONTRACT_CONTRADICTION', 'children': checks}
        child_problem = with_goal(replace(problem, initial_state=current), facts=goal['positive'], negative=goal['negative'], cardinality=[(k, frozenset(g)) for k, g in goal['cardinality']])
        check = verify_plan(child_problem, child['chain'])
        checks.append({'child_id': child['id'], 'verification': check})
        if not check['accepted']:
            return {'accepted': False, 'reason': 'CHILD_CONTRACT_REJECTED', 'children': checks}
        current = frozenset(check['final_state'])
    parent = verify_plan(problem, plan)
    return {'accepted': parent['accepted'], 'reason': parent.get('reason'), 'children': checks, 'parent': parent}

def _search_observed(world, problem, scope, task_actor, limits):
    started = time.perf_counter()
    report = {'implementation': IMPLEMENTATION_ID, 'plan': None, 'children': [], 'prediction': None, 'scope': scope, 'formal_resource_guarantee': False, 'family': 'sample-to-actor assignments x within-actor route permutations; one return/store/offload batch per actor; lexical actor block order', 'selection': 'lexicographic maximum actor energy utilization, then total simulated energy', 'limits': asdict(limits), 'enumeration_complete': False, 'metrics': {'candidate_rollouts': 0, 'world_steps': 0, 'prediction_wall_s': 0.0, 'proposal_validation_wall_s': 0.0, 'logical_model_calls': 0, 'physical_model_calls': 0}}
    ids = sorted((s for s in world.samples if atom('in_base_storage', s) in problem.goal_facts and s not in world.base_storage))
    if not ids or problem.goal_cardinality or any((not f.startswith('in_base_storage(') and (not f.startswith('at_base(')) for f in problem.goal_facts)):
        return {**report, 'status': 'UNSUPPORTED_CANDIDATE_FAMILY'}
    needed = {'move_to', 'sample_collect', 'sample_store', 'return_to_base', 'sample_offload'}
    actors = [a for a, info in sorted(world.agents.items()) if info.get('available', True) is True and (not info.get('disabled')) and (not info.get('tipped')) and (needed <= set(info['capabilities']))]
    if scope == 'T':
        actors = [a for a in actors if a == task_actor]
    owners = {}
    for sid in ids:
        if world.samples[sid]['status'] != 'field':
            owning = [a for a in actors if sid in world.agents[a]['cargo']]
            if len(owning) != 1:
                return {**report, 'status': 'NO_LEGAL_OWNER_IN_CANDIDATE_FAMILY'}
            owners[sid] = owning[0]
    best_key, reason = (None, None)
    failures = []
    for groups in _routes(ids, actors, owners):
        plan, children = _candidate(world, groups)
        m = report['metrics']
        if m['candidate_rollouts'] >= limits.max_candidate_rollouts:
            reason = 'candidate_rollout_limit'
            break
        if m['world_steps'] + len(plan) > limits.max_world_steps:
            reason = 'world_step_limit'
            break
        if time.perf_counter() - started >= limits.search_wall_s:
            reason = 'search_wall_limit'
            break
        prediction_started = time.perf_counter()
        predicted = _forecast(world, problem, plan)
        m['prediction_wall_s'] += time.perf_counter() - prediction_started
        m['candidate_rollouts'] += 1
        m['world_steps'] += predicted['world_steps']
        if not (predicted['resource_feasible'] and predicted['goal_met_after_prediction']):
            if len(failures) < 8:
                failures.append({'groups': deepcopy(groups), 'prediction': predicted})
            continue
        key = (predicted['maximum_actor_energy_utilization'], predicted['total_energy_used_wh'])
        if best_key is not None and key >= best_key:
            continue
        validation_started = time.perf_counter()
        validation = validate_regrouping(problem, children, plan)
        m['proposal_validation_wall_s'] += time.perf_counter() - validation_started
        if validation['accepted']:
            best_key = key
            report.update(plan=plan, children=children, prediction=predicted, validation=validation, selected_actor_routes=deepcopy(groups))
    report['enumeration_complete'] = reason is None
    report['termination_reason'] = reason or 'declared_family_enumerated'
    report['status'] = ('FAMILY_BEST' if reason is None else 'SEARCHED_BEST') if report['plan'] else 'BOUNDED_SEARCH_NO_CANDIDATE'
    report['failure_examples'] = failures
    report['metrics']['search_wall_s'] = time.perf_counter() - started
    report['metrics']['time_accounting'] = 'search_wall contains prediction and proposal validation; do not add these nested times'
    return report

def search(world, problem, *, scope='M', task_actor=None, limits=ResourceSearchLimits()):
    """Independent search invocation with externally enforced wall/memory caps.

    No model, scorer, expected scope, hidden RNG or future schedule is supplied.
    Failure of the hard worker limit returns no plan; no stale best is replayed.
    """
    if scope not in {'M', 'S', 'T'}:
        raise ValueError('resource regrouping scope must be T, M or S')
    if scope == 'T' and (not task_actor):
        raise ValueError('Task-only search needs its existing assigned actor')
    if problem.scenario_id != 'psr':
        return {'implementation': IMPLEMENTATION_ID, 'status': 'NUMERIC_RESOURCE_UNKNOWN', 'plan': None, 'children': [], 'metrics': {'candidate_rollouts': 0, 'world_steps': 0}}
    from baselines.common.limited_worker import run_limited
    public = public_forecast_world(world)
    limited = run_limited(__name__, '_search_observed', (public, replace(problem, observation={}), scope, task_actor, limits), timeout_s=limits.hard_wall_s, memory_mb=limits.memory_mb)
    if limited.status != 'COMPLETED':
        return {'implementation': IMPLEMENTATION_ID, 'status': 'SEARCH_' + limited.status, 'plan': None, 'children': [], 'error': limited.error, 'metrics': {'candidate_rollouts': None, 'world_steps': None, 'hard_worker': limited.metrics}}
    report = limited.result
    report['metrics']['hard_worker'] = limited.metrics
    report['metrics']['total_wall_s'] = limited.metrics['wall_s']
    return report

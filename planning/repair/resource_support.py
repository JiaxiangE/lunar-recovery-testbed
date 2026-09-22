"""Per-arm resource assistance with independent work limits and no gate rewrite."""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import asdict
from dataclasses import replace
import time
import math
from baselines.common.limited_worker import run_limited
from execution.binding import verify_plan
from planning.schema.decomposition import Task
from planning.repair.domain_hierarchy import problem_for_node
from planning.repair.domain_hierarchy import _pre_met
from planning.repair.resource_search import ResourceSearchLimits
from planning.repair.resource_search import public_prediction_world
from planning.repair.resource_search import search
RESOURCE_IMPLEMENTATION = 'resource_assistance_v1'

@dataclass(frozen=True)
class AssistanceLimits:
    prediction_calls: int = 64
    search_calls: int = 2
    candidate_rollouts: int = 2112
    world_steps: int = 100000
    work_wall_s: float = 120.0
    worker_wall_s: float = 30.0
    memory_mb: int = 2048

    def __post_init__(self):
        for name in ('prediction_calls', 'search_calls', 'candidate_rollouts', 'world_steps'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(name + ' must be a nonnegative integer')
        if type(self.memory_mb) is not int or self.memory_mb < 1:
            raise ValueError('memory_mb must be a positive integer')
        for name in ('work_wall_s', 'worker_wall_s'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(name + ' must be finite and positive')

def validate_resource_children(problem, children, plan):
    """Rebuild real Task nodes; use actual sequential states and final parent."""
    if [s for c in children for s in c['chain']] != plan:
        return {'accepted': False, 'reason': 'CHILD_PLAN_BINDING_MISMATCH', 'children': []}
    state, done, records = (problem.initial_state, set(), [])
    for child in children:
        node = Task.model_validate({k: deepcopy(v) for k, v in child.items() if k != 'chain'})
        if node.id in done or not set(node.dependencies) <= done or (not _pre_met(node, state)):
            return {'accepted': False, 'reason': 'CHILD_STRUCTURE_OR_PRECONDITION', 'children': records}
        if any((s.get('agent_id') != node.assigned_agent_id for s in child['chain'])):
            return {'accepted': False, 'reason': 'CHILD_ACTOR_MISMATCH', 'children': records}
        if not set(node.required_primitives) <= {s['primitive'] for s in child['chain']}:
            return {'accepted': False, 'reason': 'CHILD_REQUIRED_PRIMITIVE_MISSING', 'children': records}
        cp = problem_for_node(replace(problem, initial_state=state), node)
        check = verify_plan(cp, child['chain'])
        records.append({'task': node.model_dump(mode='json'), 'chain': deepcopy(child['chain']), 'verification': check})
        if not check['accepted']:
            return {'accepted': False, 'reason': 'CHILD_CONTRACT_REJECTED', 'children': records}
        state = frozenset(check['final_state'])
        done.add(node.id)
    parent = verify_plan(problem, plan)
    return {'accepted': parent['accepted'], 'children': records, 'parent': parent, 'reason': parent['reason']}

class ResourceSupport:
    """Fresh per arm. No shared winning plan or cross-arm search cache."""

    def __init__(self, limits=AssistanceLimits(), *, search_limits=ResourceSearchLimits()):
        self.limits, self.search_limits = (limits, search_limits)
        self.events = []
        self.metrics = {'candidate_rollouts': 0, 'prediction_world_steps': 0, 'prediction_wall_s': 0.0, 'standalone_prediction_wall_s': 0.0, 'local_search_wall_s': 0.0, 'proposal_validation_wall_s': 0.0, 'prediction_calls': 0, 'search_calls': 0, 'work_counters_complete': True}
        self.reserved_steps = self.reserved_rollouts = 0
        self.source_obligation_obstacles = []
        self.permitted_regrouping_scopes = {'M', 'S'}

    def bind_source_obligations(self, information, parent):
        """Regroup incidental delivery Tasks, not unrepresented source duties."""
        obstacles = list(information.get('mandatory_network_obligations', ()))
        contract = information.get('resource_assistance_contract', {})
        self.permitted_regrouping_scopes = set(contract.get('permitted_regrouping_scopes', ('M', 'S')))
        tree = information.get('tree', {})
        for raw in tree.get('nodes', {}).values():
            if raw.get('layer') != 'T':
                continue
            node = Task.model_validate(raw)
            goal = problem_for_node(parent, node)
            if node.precondition or node.required_primitives or goal.negative_goal_facts or goal.goal_cardinality or any((not (g.startswith('at_base(') or (g.startswith('in_base_storage(') and g in parent.goal_facts)) for g in goal.goal_facts)):
                obstacles.append({'task_id': node.id, 'reason': 'source/process obligations outside pure delivery regrouping family'})
        self.source_obligation_obstacles = obstacles

    def _remaining_wall(self):
        return self.limits.work_wall_s - self.metrics['standalone_prediction_wall_s'] - self.metrics['local_search_wall_s'] - self.metrics['proposal_validation_wall_s']

    def verify(self, problem, plan):
        started = time.perf_counter()
        try:
            return verify_plan(problem, plan)
        finally:
            self.metrics['proposal_validation_wall_s'] += time.perf_counter() - started

    def forecast(self, world, problem, plan):
        if problem.scenario_id != 'psr':
            result = {'resource_feasible': None, 'status': 'NUMERIC_RESOURCE_UNKNOWN', 'prediction_dispatch_n': 0}
            self.events.append({'kind': 'forecast', 'result': result})
            return result
        if self.metrics['prediction_calls'] >= self.limits.prediction_calls or self.reserved_steps + len(plan) > self.limits.world_steps or self.reserved_rollouts + 1 > self.limits.candidate_rollouts or (self._remaining_wall() <= 0) or (not self.metrics['work_counters_complete']):
            return {'resource_feasible': None, 'status': 'RESOURCE_WORK_LIMIT'}
        self.metrics['prediction_calls'] += 1
        self.reserved_rollouts += 1
        self.reserved_steps += len(plan)
        started = time.perf_counter()
        public = public_prediction_world(world)
        limited = run_limited('planning.repair.resource_search', 'predict_public_resources', (public, replace(problem, observation={}), deepcopy(plan)), {}, timeout_s=min(self.limits.worker_wall_s, self._remaining_wall()), memory_mb=self.limits.memory_mb)
        elapsed = time.perf_counter() - started
        self.metrics['standalone_prediction_wall_s'] += elapsed
        result = limited.result if limited.status == 'COMPLETED' else {'status': 'PREDICTION_' + limited.status, 'resource_feasible': None, 'error': limited.error}
        if limited.status == 'COMPLETED':
            self.metrics['candidate_rollouts'] += 1
            self.metrics['prediction_world_steps'] += result['prediction_dispatch_n']
            self.metrics['prediction_wall_s'] += result.get('prediction_wall_s', 0.0)
            result['status'] = 'PREDICTED'
        else:
            self.metrics['work_counters_complete'] = False
        self.events.append({'kind': 'forecast', 'result': result, 'worker': limited.metrics, 'formal_gate_changed': False, 'actual_dispatch_n': 0})
        return result

    @staticmethod
    def may_execute(prediction):
        return prediction.get('resource_feasible') is True or prediction.get('status') == 'NUMERIC_RESOURCE_UNKNOWN'

    def reorganize(self, world, parent, selected, before, after, *, scope, hierarchical, original_candidate=None):
        if scope not in {'M', 'S'} or scope not in self.permitted_regrouping_scopes:
            return {'plan': None, 'status': 'SCOPE_PRESERVES_TASK_OBLIGATIONS'}
        if self.source_obligation_obstacles:
            return {'plan': None, 'status': 'SOURCE_OBLIGATIONS_REQUIRE_PRESERVATION', 'obligations': self.source_obligation_obstacles}
        if self.metrics['search_calls'] >= self.limits.search_calls or self._remaining_wall() <= 1 or (not self.metrics['work_counters_complete']):
            return {'plan': None, 'status': 'RESOURCE_WORK_LIMIT'}
        if parent.scenario_id != 'psr':
            return {'plan': None, 'status': 'NUMERIC_RESOURCE_UNKNOWN'}
        prepare_started = time.perf_counter()
        public = public_prediction_world(world)
        if self.reserved_steps + len(before) > self.limits.world_steps or self.reserved_rollouts + bool(before) >= self.limits.candidate_rollouts:
            return {'plan': None, 'status': 'RESOURCE_WORK_LIMIT'}
        if before:
            self.metrics['candidate_rollouts'] += 1
            self.reserved_rollouts += 1
        for step in before:
            outcome = public.step(step['primitive'], step['agent_id'], step['params'])
            self.metrics['prediction_world_steps'] += 1
            self.reserved_steps += 1
            if not outcome.success:
                self.metrics['standalone_prediction_wall_s'] += time.perf_counter() - prepare_started
                return {'plan': None, 'status': 'PRESERVED_PREFIX_RESOURCE_FAILURE'}
        self.metrics['standalone_prediction_wall_s'] += time.perf_counter() - prepare_started
        remaining_rollouts = self.limits.candidate_rollouts - self.reserved_rollouts
        remaining_steps = self.limits.world_steps - self.reserved_steps
        if min(remaining_rollouts, remaining_steps) < 1:
            return {'plan': None, 'status': 'RESOURCE_WORK_LIMIT'}
        hard_wall = min(self.search_limits.hard_wall_s, self._remaining_wall())
        if hard_wall <= 0:
            return {'plan': None, 'status': 'RESOURCE_WORK_LIMIT'}
        limits = replace(self.search_limits, max_candidate_rollouts=min(remaining_rollouts, self.search_limits.max_candidate_rollouts), max_world_steps=min(remaining_steps, self.search_limits.max_world_steps), hard_wall_s=hard_wall, search_wall_s=min(self.search_limits.search_wall_s, hard_wall * 0.8))
        self.metrics['search_calls'] += 1
        started = time.perf_counter()
        from validation.resource_prediction import resource_information
        search_input = {'public_resources': resource_information(public, 'psr'), 'initial_facts': sorted(selected.initial_state), 'goal': {'positive': sorted(selected.goal_facts), 'negative': sorted(selected.negative_goal_facts), 'cardinality': [[k, sorted(g)] for k, g in selected.goal_cardinality]}, 'preserved_before': deepcopy(before), 'preserved_after': deepcopy(after), 'candidate_before_search': deepcopy(original_candidate)}
        found = search(public, selected, scope=scope, limits=limits)
        self.metrics['local_search_wall_s'] += time.perf_counter() - started
        work = found['metrics']
        for source, target in (('candidate_rollouts', 'candidate_rollouts'), ('world_steps', 'prediction_world_steps')):
            if work.get(source) is None:
                self.metrics['work_counters_complete'] = False
            else:
                self.metrics[target] += work[source]
        self.reserved_rollouts += limits.max_candidate_rollouts if work.get('candidate_rollouts') is None else work['candidate_rollouts']
        self.reserved_steps += limits.max_world_steps if work.get('world_steps') is None else work['world_steps']
        self.metrics['prediction_wall_s'] += work.get('prediction_wall_s', 0.0)
        event = {'kind': 'resource_search', 'scope': scope if hierarchical else 'flat_whole_plan', 'input': search_input, 'search': found, 'candidate_changed': None, 'hierarchical_child_controller_used': hierarchical}
        event_index = len(self.events)
        self.events.append(event)
        if found.get('plan') is None:
            return {'plan': None, 'status': found['status']}
        started = time.perf_counter()
        if hierarchical:
            rebuilt = validate_resource_children(selected, found['children'], found['plan'])
            event['rebuilt_child_validation'] = rebuilt
            if not rebuilt['accepted']:
                self.metrics['proposal_validation_wall_s'] += time.perf_counter() - started
                return {'plan': None, 'status': 'RESOURCE_CHILD_REJECTED'}
        combined = before + found['plan'] + after
        event['candidate_after_search'] = deepcopy(combined)
        event['candidate_changed'] = combined != original_candidate if original_candidate is not None else None
        check = verify_plan(parent, combined)
        self.metrics['proposal_validation_wall_s'] += time.perf_counter() - started
        event['full_parent_validation'] = check
        if not check['accepted']:
            return {'plan': None, 'status': 'RESOURCE_PARENT_REJECTED'}
        forecast = self.forecast(world, parent, combined)
        event['full_parent_forecast'] = forecast
        return {'plan': combined if self.may_execute(forecast) else None, 'search_event_index': event_index, 'status': 'RESOURCE_CANDIDATE_READY' if self.may_execute(forecast) else 'RESOURCE_CANDIDATE_INFEASIBLE', 'parent_verification': check}

    def report(self):
        metrics = deepcopy(self.metrics)
        if not metrics['work_counters_complete']:
            metrics['candidate_rollouts'] = metrics['prediction_world_steps'] = None
            metrics['completed_prediction_wall_s_lower_bound'] = metrics['prediction_wall_s']
            metrics['prediction_wall_s'] = None
        metrics['resource_wall_s'] = metrics['standalone_prediction_wall_s'] + metrics['local_search_wall_s'] + metrics['proposal_validation_wall_s']
        return {'implementation': RESOURCE_IMPLEMENTATION, 'limits': asdict(self.limits), 'metrics': metrics, 'events': self.events, 'formal_resource_guarantee': False, 'time_accounting': 'resource_wall is standalone forecast + inclusive search + external validation; prediction_wall includes nested search forecasts and must not be added again'}

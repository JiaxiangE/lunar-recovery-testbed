"""Opt-in deterministic checks of existing obligations, never new planning."""
from copy import deepcopy
from dataclasses import replace
import time
from execution.binding import verify_plan
from domains.common import observe
from domains.common import binding_observation
from domains.common import with_goal
from planning.schema.decomposition import Task

class SharedPreflight:

    def __init__(self, world, scenario, resource_support=None):
        self.world = world
        self.scenario = scenario
        self.resource = resource_support
        self.bound = binding_observation(world, scenario)
        self.events = []

    def fresh(self):
        return binding_observation(self.world, self.scenario) == self.bound

    def report(self):
        return {'version': 'shared_preflight_v1', 'events': deepcopy(self.events), 'validation_wall_s': sum((e.get('validation_wall_s', 0.0) for e in self.events)), 'note': 'preflight validation wall is a subset of resource_assistance proposal_validation_wall_s, not additive; forecast and final execution checks remain separate'}

    def _timed(self, event, start):
        event['validation_wall_s'] = time.perf_counter() - start
        if self.resource is not None:
            self.resource.metrics['proposal_validation_wall_s'] += event['validation_wall_s']

    def _obstacles(self, node, information):
        if information.get('mandatory_network_obligations'):
            return 'uninterpreted_external_process_obligations'
        if set(node.context) - {'domain_goal'}:
            return 'uninterpreted_node_context'
        return None

    @staticmethod
    def _known_contract(problem):
        atoms = set()
        for action in problem.actions:
            atoms.update(action.preconditions | action.negative_preconditions | action.add_effects | action.delete_effects)
            for effect in action.conditional_effects:
                atoms.update(effect.conditions | effect.negative_conditions | effect.add_effects | effect.delete_effects)
        goals = set(problem.goal_facts | problem.negative_goal_facts) | {f for _, group in problem.goal_cardinality for f in group}
        return bool(goals) and goals <= atoms

    def task(self, node, problem, information, completed_dependencies=None):
        from planning.repair.domain_hierarchy import _pre_met
        event = {'kind': 'task_empty', 'node_id': node.id, 'node': node.model_dump(mode='json'), 'start_state': sorted(problem.initial_state), 'completed_dependencies': sorted(completed_dependencies or ()), 'hit': False, 'state_kind': 'planning_state_not_physical_completion', 'schema': 'not_applicable_no_model_response', 'model_calls': 0, 'child_dispatch_n': 0}
        self.events.append(event)
        start = time.perf_counter()
        try:
            reason = self._obstacles(node, information)
            if not isinstance(node, Task):
                reason = 'only_Task_supported'
            elif node.required_primitives:
                reason = 'required_primitives_must_occur'
            elif node.dependencies and (not set(node.dependencies) <= set(completed_dependencies or ())):
                reason = 'dependencies_not_established'
            elif node.assigned_agent_id not in problem.observation['actor_types']:
                reason = 'unknown_actor'
            elif not self._known_contract(problem):
                reason = 'empty_or_unrecognized_contract_atoms'
            elif not _pre_met(node, problem.initial_state):
                reason = 'node_precondition_unmet'
            elif not self.fresh():
                reason = 'source_state_changed'
            elif self.resource is None or not self.resource.metrics['work_counters_complete'] or self.resource._remaining_wall() <= 0:
                reason = 'resource_interface_not_available'
            if reason:
                event['reason'] = reason
                return None
            check = verify_plan(problem, [])
            event['verification'] = check
            if not check['accepted']:
                event['reason'] = check['reason']
                return None
            if not self.fresh():
                event['reason'] = 'source_state_changed_after_validation'
                return None
            if self.resource is not None and self.resource._remaining_wall() <= time.perf_counter() - start:
                event['reason'] = 'resource_work_limit_after_validation'
                return None
            event.update(hit=True, reason='empty_Task_contract_verified', candidate=[], resource_scope='zero-action child; complete candidate resource check still required')
            return ([], problem.initial_state)
        except Exception as exc:
            event.update(hit=False, reason='preflight_verification_interface_error', error={'type': type(exc).__name__, 'detail': str(exc)})
            return None
        finally:
            self._timed(event, start)

    def source(self, case, problem):
        from planning.repair.domain_hierarchy import leaves
        from planning.repair.domain_hierarchy import ordered_children
        from planning.repair.domain_hierarchy import problem_for_node
        from planning.repair.domain_hierarchy import _pre_met
        event = {'kind': 'source_plan', 'hit': False, 'model_calls': 0, 'native_invoked': False}
        self.events.append(event)
        start = time.perf_counter()

        def miss(reason):
            event['reason'] = reason
            return event
        try:
            if not self.fresh() or observe(self.world, self.scenario) != problem.initial_state:
                return miss('source_state_changed')
            if case.original_information.get('tree') != case.original_tree.model_dump(mode='json'):
                return miss('source_tree_binding_unclear')
            all_leaves = leaves(case.original_tree)
            if [s for _, s in all_leaves] != case.original_information.get('original_plan'):
                return miss('source_plan_binding_unclear')
            executed = set(case.executed_prefix_ids)
            prefix = case.original_information.get('executed_prefix', [])
            if executed != {p['node_id'] for p in prefix if p.get('result', {}).get('success') is True}:
                return miss('executed_prefix_not_established')
            for item in prefix:
                if dict(all_leaves).get(item['node_id']) != item['step']:
                    return miss('executed_prefix_content_mismatch')
            candidate = [s for nid, s in all_leaves if nid not in executed]
            event['candidate'] = deepcopy(candidate)

            def visit(node, state):
                obstacle = self._obstacles(node, case.original_information)
                if obstacle:
                    raise ValueError(obstacle)
                if not _pre_met(node, state):
                    raise ValueError('source_node_precondition_unmet')
                own = leaves(case.original_tree, node)
                if isinstance(node, Task):
                    if node.assigned_agent_id not in problem.observation['actor_types']:
                        raise ValueError('source_actor_unknown')
                    names = {s['primitive'] for _, s in own}
                    if not set(node.required_primitives) <= names:
                        raise ValueError('source_required_primitive_missing')
                    pending = [s for nid, s in own if nid not in executed]
                    if any((s['agent_id'] != node.assigned_agent_id for s in pending)):
                        raise ValueError('source_Task_actor_mismatch')
                    if own and (not pending):
                        return state
                    cp = problem_for_node(replace(problem, initial_state=state), node)
                    if not self._known_contract(cp):
                        raise ValueError('unrecognized_source_contract')
                    check = verify_plan(cp, pending)
                    if not check['accepted']:
                        raise ValueError('source_Task_' + check['reason'])
                    return frozenset(check['final_state'])
                if getattr(node, 'required_agent_types', None):
                    available = {t for a, t in problem.observation['actor_types'].items() if f'available({a})' in state}
                    if not set(node.required_agent_types) & available:
                        raise ValueError('source_agent_types_unavailable')
                children = ordered_children(case.original_tree, node)
                budget = problem.observation.get('resource_constraints', {}).get('time_budget_s')
                if node.layer == 'S' and budget is not None and (sum((c.estimated_duration_s for c in children)) > budget):
                    raise ValueError('declared_source_duration_exceeds_budget')
                for child in children:
                    state = visit(child, state)
                goal = problem if node.id == case.original_tree.root_id else problem_for_node(problem, node)
                if not goal.goal_met(state):
                    raise ValueError('source_parent_contract_unmet')
                return state
            try:
                visit(case.original_tree.nodes[case.original_tree.root_id], problem.initial_state)
            except (ValueError, TypeError, KeyError) as exc:
                return miss(str(exc))
            check = verify_plan(problem, candidate)
            event['verification'] = check
            if not check['accepted']:
                return miss(check['reason'])
            if not self.fresh():
                return miss('source_state_changed_after_validation')
        except Exception as exc:
            event['error'] = {'type': type(exc).__name__, 'detail': str(exc)}
            return miss('preflight_verification_interface_error')
        finally:
            self._timed(event, start)
        if self.resource is None:
            return miss('resource_interface_not_available')
        try:
            prediction = self.resource.forecast(self.world, problem, candidate)
        except Exception as exc:
            event['error'] = {'type': type(exc).__name__, 'detail': str(exc)}
            return miss('preflight_resource_interface_error')
        event['resource_prediction'] = prediction
        if not isinstance(prediction, dict):
            return miss('resource_interface_not_understood')
        if not self.resource.may_execute(prediction):
            return miss('resource_policy_did_not_allow_execution')
        if not self.fresh():
            return miss('source_state_changed_after_resource_check')
        event.update(hit=True, reason='source_remaining_plan_verified', classification='initial_parent_met_maintenance' if problem.goal_met(problem.initial_state) else 'normal_source_continuation' if case.failure.symptom == 'pending_obligation' else 'observed_fault_source_suffix_still_valid')
        return event

"""Conservative positive-goal impossibility in the registered fixed-actor model."""
from copy import deepcopy
import time
from domains.transition import GroundAction
from domains.transition import ConditionalEffect
from domains.common import build_problem
from domains.common import binding_observation
from domains.common import DOMAIN_VERSION
from planning.schema.decomposition import Task

def positive_closure(state, actions, goals):
    """Overapproximate every executable prefix; never a feasibility certificate.

    Induction: initial facts are included. Any executable action's positive
    preconditions are included, and every actually fired conditional effect's
    positive guards are included. Ignoring deletes, negative guards and resources
    can only add possibilities. Thus an absent necessary positive goal is
    unreachable in this static model. Presence proves nothing about full goals.
    """
    if any((type(a) is not GroundAction or any((type(e) is not ConditionalEffect for e in a.conditional_effects)) for a in actions)):
        raise TypeError('only registered finite grounded transition types supported')
    reached = set(state)
    rounds = checks = 0
    while True:
        before = set(reached)
        rounds += 1
        for action in actions:
            checks += 1
            if action.preconditions <= reached:
                reached.update(action.add_effects)
                for effect in action.conditional_effects:
                    checks += 1
                    if effect.conditions <= reached:
                        reached.update(effect.add_effects)
        if reached == before:
            break
    return {'reached': sorted(reached), 'missing_positive': sorted(set(goals) - reached), 'rounds': rounds, 'transition_checks': checks}

class TaskCapabilityPruner:

    def __init__(self, world, scenario, resource=None):
        self.world, self.scenario, self.resource = (world, scenario, resource)
        self.bound = binding_observation(world, scenario)
        self.events = []

    def check(self, node, problem, information, *, parent_event_index=None):
        start = time.perf_counter()
        event = {'node': node.model_dump(mode='json'), 'parent_event_index': parent_event_index, 'start_state': sorted(problem.initial_state), 'actor': getattr(node, 'assigned_agent_id', None), 'pruned': False, 'model_calls': 0, 'state_kind': 'current child planning state, not physical execution'}
        self.events.append(event)
        try:
            if not isinstance(node, Task):
                event['reason'] = 'only_T_supported'
                return False
            if binding_observation(self.world, self.scenario) != self.bound:
                event['reason'] = 'state_or_capabilities_changed'
                return False
            if not problem.goal_facts:
                event['reason'] = 'no_necessary_positive_goal'
                return False
            if set(node.context) - {'domain_goal'} or information.get('mandatory_network_obligations'):
                event['reason'] = 'unknown_contract'
                return False
            fresh = build_problem(self.world, self.scenario)
            if problem.domain_version != DOMAIN_VERSION or problem.actions != fresh.actions or problem.observation['actor_types'] != fresh.observation['actor_types']:
                event['reason'] = 'unrecognized_or_stale_action_model'
                return False
            if node.assigned_agent_id not in fresh.observation['actor_types']:
                event['reason'] = 'unknown_actor'
                return False
            if not set(node.required_primitives) <= {a.primitive for a in fresh.actions}:
                event['reason'] = 'unknown_required_primitive'
                return False
            from domains.predicates import parse_predicate
            from domains.common import atom
            known = set(fresh.initial_state)
            for a in fresh.actions:
                known.update(a.preconditions | a.negative_preconditions | a.add_effects | a.delete_effects)
                for e in a.conditional_effects:
                    known.update(e.conditions | e.negative_conditions | e.add_effects | e.delete_effects)
            if any((atom(p.name, *p.args) not in known for p in parse_predicate(node.precondition).atomics)):
                event['reason'] = 'unknown_precondition'
                return False
            from planning.repair.shared_preflight import SharedPreflight
            if not SharedPreflight._known_contract(problem):
                event['reason'] = 'unknown_contract_atoms'
                return False
            allowed = [a for a in problem.actions if a.agent_id == node.assigned_agent_id]
            event.update(action_n=len(allowed), closure=positive_closure(problem.initial_state, allowed, problem.goal_facts))
            if binding_observation(self.world, self.scenario) != self.bound:
                event['reason'] = 'state_changed_during_closure'
                return False
            event['pruned'] = bool(event['closure']['missing_positive'])
            event['reason'] = 'TASK_POSITIVE_GOAL_UNREACHABLE' if event['pruned'] else 'not_excluded_by_positive_relaxation'
            return event['pruned']
        except Exception as exc:
            event.update(pruned=False, reason='pruning_interface_unknown', error={'type': type(exc).__name__, 'detail': str(exc)})
            return False
        finally:
            event['cpu_wall_s'] = time.perf_counter() - start
            if self.resource is not None:
                self.resource.metrics['proposal_validation_wall_s'] += event['cpu_wall_s']

    def report(self):
        return {'version': 'task_capability_pruning_v1', 'events': deepcopy(self.events), 'cpu_wall_s': sum((e['cpu_wall_s'] for e in self.events)), 'accounting': 'subset of proposal_validation_wall_s, not additive; no cache or model response', 'guarantee': 'only absence of necessary positive facts under optimistic fixed-actor closure; not physical UNSAT or feasibility when reachable'}

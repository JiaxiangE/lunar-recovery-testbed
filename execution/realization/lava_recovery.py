"""Bound, source-limited Lava recovery with an actual world.step dispatch boundary.

The isolated interpreter checks named preconditions and effects; runtime injection
and energy failures remain execution outcomes. Accepted content cannot be replaced
by a new candidate, goal, or state. No certificate cache or external approval exists.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
from dataclasses import dataclass
import json
from domains.predicates import parse_predicate
from domains.worlds.lava_execution_world import LavaExecutionWorld
from domains.worlds.lava_execution_world import SEMANTICS_VERSION

@dataclass(frozen=True)
class LavaGoal:
    stored_samples: tuple[str, ...] = ()
    return_actors: tuple[str, ...] = ()
    actor_targets: tuple[tuple[str, str], ...] = ()
    minimum_stored: int = 0
    sample_domain: tuple[str, ...] = ()
    local_facts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for name in ('stored_samples', 'return_actors', 'sample_domain'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in ('actor_targets', 'local_facts'):
            object.__setattr__(self, name, tuple((tuple(value) for value in getattr(self, name))))

    def validate(self, world):
        if type(self.minimum_stored) is not int or self.minimum_stored < 0:
            raise ValueError('invalid sample threshold')
        if not self.stored_samples and (not self.return_actors) and (not self.actor_targets) and (not self.minimum_stored) and (not self.local_facts):
            raise ValueError('empty recovery goal')
        if not set(self.stored_samples) <= set(world.samples):
            raise ValueError('unknown goal sample')
        if not set(self.return_actors) <= set(world.agents):
            raise ValueError('unknown return actor')
        if any((a not in world.agents or t not in {'entry', *world.samples} for a, t in self.actor_targets)):
            raise ValueError('unknown goal actor or target')
        if any((a not in world.agents or token != 'headlight_on' for a, token in self.local_facts)):
            raise ValueError('unsupported local-token goal')
        if self.minimum_stored and (self.minimum_stored != 3 or set(self.sample_domain) != {f'sample_{i}' for i in range(1, 5)} or set(world.samples) != set(self.sample_domain)):
            raise ValueError('only the fixed 3/4 abandonment sample criterion is supported')

    def met(self, world):
        return all((world.samples[s]['status'] == 'stored' for s in self.stored_samples)) and all((world.at_entry(a) for a in self.return_actors)) and all((world.at_target(a, t) for a, t in self.actor_targets)) and all((token in world.agents[a]['facts'] for a, token in self.local_facts)) and (sum((world.samples[s]['status'] == 'stored' for s in self.sample_domain)) >= self.minimum_stored)

@dataclass(frozen=True)
class BoundLavaPlan:
    steps_json: str
    initial_json: str
    goal: LavaGoal
    semantics_version: str = SEMANTICS_VERSION

def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))

def validate_lava_plan(world, steps, goal):
    if not isinstance(world, LavaExecutionWorld):
        return (None, 'UNSUPPORTED_WORLD')
    if not isinstance(goal, LavaGoal):
        return (None, 'UNSUPPORTED_GOAL')
    try:
        goal.validate(world)
        plan = deepcopy(list(steps))
        if not plan:
            raise ValueError('empty plan')
        projection = deepcopy(world)
        for i, step in enumerate(plan):
            if not isinstance(step, dict) or set(step) - {'primitive', 'params', 'agent_id', 'agent_type'}:
                raise ValueError('invalid step schema')
            aid, name = (step['agent_id'], step['primitive'])
            if step.get('agent_type') is not None and step['agent_type'] != projection.agents.get(aid, {}).get('agent_type'):
                raise ValueError('actor type claim mismatch')
            result = projection._transition(aid, name, step.get('params', {}), runtime=False)
            if not result.success:
                return (None, f'{result.failure_mode}:step={i}:{result.detail}')
        if not goal.met(projection):
            return (None, 'GOAL_NOT_ENTAILED')
        return (BoundLavaPlan(_json(plan), _json(world.snapshot_state()), goal), 'ACCEPTED')
    except (KeyError, TypeError, ValueError) as exc:
        return (None, f'BAD_ARGUMENTS:{exc}')

def validate_lava_task(task, chain, prompt_context, runtime_world):
    """Versioned D_T adapter used by both common validation and the local runner.

    Unparameterized agent_at is resolved to the Task's trusted assigned actor.
    Aggregate entry facts are read from positions, not candidate token declarations.
    """
    from validation.contracts.contracts import D_TValidationResult
    try:
        chain = list(chain)
        if any((not isinstance(step, dict) for step in chain)):
            return D_TValidationResult(False, 'invalid', 'BAD_ARGUMENTS', ['Task primitive must be an object'])
        if any((step.get('agent_id', task.assigned_agent_id) != task.assigned_agent_id for step in chain)):
            return D_TValidationResult(False, 'invalid', 'AGENT_BINDING_MISMATCH', ['Task primitive actor differs from assigned_agent_id'])
        if not _task_precondition_met(task, runtime_world):
            return D_TValidationResult(False, 'invalid', 'PRECONDITION_UNMET', ['Task initial condition is not observed in this world'])
        goal = lava_goal_from_task(task, runtime_world)
        steps = [dict(step, agent_id=step.get('agent_id', task.assigned_agent_id)) for step in chain]
        binding, reason = validate_lava_plan(runtime_world, steps, goal)
        return D_TValidationResult(bool(binding), 'valid' if binding else 'invalid', '' if binding else reason.split(':')[0], [] if binding else [reason])
    except (ValueError, KeyError, TypeError) as exc:
        return D_TValidationResult(False, 'invalid', 'UNSUPPORTED_GOAL', [str(exc)])

def _task_precondition_met(task, world):
    for atom in parse_predicate(task.precondition or '').atomics:
        args = tuple((str(value) for value in atom.args))
        if atom.negated:
            return False
        if atom.name == 'low_visibility' and (not args):
            met = world.low_visibility
        elif atom.name == 'agent_at' and len(args) == 1:
            met = world.at_target(task.assigned_agent_id, args[0])
        elif atom.name == 'in_storage' and len(args) == 1:
            met = world.samples.get(args[0], {}).get('status') == 'stored'
        elif atom.name == 'all_agents_at_entry' and (not args):
            met = all((world.at_entry(a) for a in world.agents))
        elif atom.name == 'headlight_on' and (not args):
            met = 'headlight_on' in world.agents.get(task.assigned_agent_id, {}).get('facts', ())
        else:
            return False
        if not met:
            return False
    return True

def lava_goal_from_task(task, world):
    stored, actors, targets, local = ([], [], [], [])
    for atom in parse_predicate(task.postcondition).atomics:
        if atom.negated:
            raise ValueError('negative goals are outside this execution subset')
        if atom.name == 'in_storage' and len(atom.args) == 1:
            stored.append(str(atom.args[0]))
        elif atom.name == 'agent_at' and len(atom.args) == 1:
            targets.append((task.assigned_agent_id, str(atom.args[0])))
        elif atom.name == 'all_agents_at_entry' and (not atom.args):
            actors.extend(world.agents)
        elif atom.name == 'headlight_on' and (not atom.args):
            local.append((task.assigned_agent_id, 'headlight_on'))
        else:
            raise ValueError(f'unsupported Lava Task goal: {atom}')
    return LavaGoal(tuple(stored), tuple(actors), tuple(targets), local_facts=tuple(local))

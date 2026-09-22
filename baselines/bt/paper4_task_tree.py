"""Paper4 task tree."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
import time
import py_trees
from domains.transition import DomainProblem
from domains.transition import GroundAction

class _LimitReached(RuntimeError):
    pass

@dataclass
class _Session:
    state: frozenset[str]
    max_work: int
    deadline: float
    max_reactive_ticks: int = 64
    state_actions: list[GroundAction] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {'condition_checks': 0, 'action_checks': 0, 'action_simulations': 0, 'goal_expansions': 0, 'sequence_rollbacks': 0, 'root_ticks': 0, 'prerequisite_ticks': 0, 'cycle_cuts': 0, 'goal_options_attempted': 0})
    events: list[dict] = field(default_factory=list)
    depth_limited: bool = False

    def work(self, key):
        self.counts[key] += 1
        if sum(self.counts.values()) > self.max_work:
            raise _LimitReached('max_work')
        if time.perf_counter() >= self.deadline:
            raise _LimitReached('timeout_s')

class _SpeculativeSequence(py_trees.composites.Sequence):
    """Native candidate backtracking; there is no robot dispatch to undo."""

    def __init__(self, session, name, children):
        super().__init__(name=name, memory=False, children=children)
        self.session = session

    def initialise(self):
        self.before = self.session.state
        self.action_start = len(self.session.state_actions)

    def terminate(self, new_status):
        if new_status == py_trees.common.Status.FAILURE:
            self.session.work('sequence_rollbacks')
            discarded = [a.action_id for a in self.session.state_actions[self.action_start:]]
            self.session.events.append({'branch': self.name, 'status': 'failure', 'discarded_actions': discarded})
            self.session.state = self.before
            del self.session.state_actions[self.action_start:]
        elif new_status == py_trees.common.Status.SUCCESS:
            self.session.events.append({'branch': self.name, 'status': 'success'})

def _supporters(actions):
    result = {}
    for action in sorted(actions, key=lambda a: a.action_id):
        for polarity, atoms in ((True, action.add_effects), (False, action.delete_effects)):
            for atom in sorted(atoms):
                result.setdefault((polarity, atom), []).append((action, frozenset(), frozenset(), 'unconditional'))
        for i, effect in enumerate(action.conditional_effects):
            for polarity, atoms in ((True, effect.add_effects), (False, effect.delete_effects)):
                for atom in sorted(atoms):
                    result.setdefault((polarity, atom), []).append((action, effect.conditions, effect.negative_conditions, f'conditional:{i}'))
    return result

class _Conjunction(py_trees.behaviour.Behaviour):

    def __init__(self, session, positives, negatives):
        super().__init__('all_goal_literals_still_hold')
        self.session, self.positives, self.negatives = (session, positives, negatives)

    def update(self):
        self.session.work('condition_checks')
        ok = self.positives <= self.session.state and (not self.negatives & self.session.state)
        return py_trees.common.Status.SUCCESS if ok else py_trees.common.Status.FAILURE

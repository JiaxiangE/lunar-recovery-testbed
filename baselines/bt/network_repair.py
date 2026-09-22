"""Q2 explicit original-network repair using independent continuation BTs.

Task/context/result data types are shared with the comparison contract, but this
module never calls the HTN solver. Actual py_trees Selectors choose method or
enabled-DAG alternatives; each Sequence includes the remaining continuation, so
a later Task failure can backtrack an earlier choice. All rollback is speculative
candidate state, never robot execution. The legacy goal-only BT stays available.
"""
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from copy import deepcopy
import time
import py_trees
from baselines.bt.paper4_task_tree import _Session
from baselines.bt.paper4_task_tree import _SpeculativeSequence
from baselines.bt.paper4_task_tree import _Conjunction
from baselines.bt.paper4_task_tree import _supporters
from baselines.bt.paper4_task_tree import _LimitReached
from baselines.htn_repair_holler.domain_repair import PrimitiveTask
from baselines.htn_repair_holler.domain_repair import CompoundTask
from baselines.htn_repair_holler.domain_repair import EnsureGoals
from baselines.htn_repair_holler.domain_repair import AchieveLiteral
from baselines.htn_repair_holler.domain_repair import TaskNetwork
from baselines.htn_repair_holler.domain_repair import ExplicitRepairResult
from baselines.htn_repair_holler.domain_repair import REPAIRED
from baselines.htn_repair_holler.domain_repair import NO_REPAIR_NEEDED
from baselines.htn_repair_holler.domain_repair import MAINTENANCE
from baselines.htn_repair_holler.domain_repair import SEARCH_LIMIT
from baselines.htn_repair_holler.domain_repair import NO_REPAIR_FOUND
from baselines.htn_repair_holler.domain_repair import PREFIX_VIOLATION
from baselines.htn_repair_holler.domain_repair import INVALID_CONTEXT
IMPLEMENTATION_ID = 'paper4_explicit_network_bt_repair_v2'

@dataclass(frozen=True)
class _BTNetworkChoice:
    network: TaskNetwork
    node_id: str
    term: object

@dataclass
class _RepairSession(_Session):
    prefix: tuple = ()
    prefix_cursor: int = 0
    original_actions: dict = field(default_factory=dict)
    observed: frozenset = frozenset()
    required_steps: tuple | None = None
    max_depth: int = 256
    producers: dict = field(default_factory=dict)
    original_producers: dict = field(default_factory=dict)
    possible: set = field(default_factory=set)
    original_possible: set = field(default_factory=set)

class _Sequence(_SpeculativeSequence):

    def initialise(self):
        super().initialise()
        self.prefix_start = self.session.prefix_cursor

    def terminate(self, new_status):
        super().terminate(new_status)
        if new_status == py_trees.common.Status.FAILURE:
            self.session.prefix_cursor = self.prefix_start

class _NetworkAction(py_trees.behaviour.Behaviour):

    def __init__(self, session, action):
        super().__init__('repair-action:' + action.action_id)
        self.session, self.action = (session, action)

    def update(self):
        s = self.session
        s.work('action_checks')
        if s.prefix_cursor < len(s.prefix):
            actual = s.original_actions.get(s.prefix[s.prefix_cursor])
            if actual is None or self.action.to_step() != actual.to_step() or (not actual.applicable(s.state)):
                return py_trees.common.Status.FAILURE
            s.work('prefix_actions_replayed')
            s.state = actual.apply(s.state)
            s.prefix_cursor += 1
            if s.prefix_cursor == len(s.prefix):
                s.state = s.observed
            return py_trees.common.Status.SUCCESS
        if s.required_steps is not None and (len(s.state_actions) >= len(s.required_steps) or self.action.to_step() != s.required_steps[len(s.state_actions)]):
            return py_trees.common.Status.FAILURE
        if not self.action.applicable(s.state):
            return py_trees.common.Status.FAILURE
        s.work('action_simulations')
        s.state = self.action.apply(s.state)
        s.state_actions.append(self.action)
        return py_trees.common.Status.SUCCESS

class _Terminal(py_trees.behaviour.Behaviour):

    def __init__(self, session, problem):
        super().__init__('complete-network-and-parent-goal')
        self.session, self.problem = (session, problem)

    def update(self):
        s = self.session
        s.work('condition_checks')
        ok = s.prefix_cursor == len(s.prefix) and self.problem.goal_met(s.state) and (s.required_steps is None or len(s.state_actions) == len(s.required_steps))
        return py_trees.common.Status.SUCCESS if ok else py_trees.common.Status.FAILURE

def _relaxed_literals(actions, initial, session):
    """Overapproximate possible signed facts; never select or return a plan."""
    universe = set(initial)
    rules = []
    for a in actions:
        universe.update(a.preconditions | a.negative_preconditions | a.add_effects | a.delete_effects)
        rules.append((a.preconditions, a.negative_preconditions, a.add_effects, a.delete_effects))
        for e in a.conditional_effects:
            universe.update(e.conditions | e.negative_conditions | e.add_effects | e.delete_effects)
            rules.append((a.preconditions | e.conditions, a.negative_preconditions | e.negative_conditions, e.add_effects, e.delete_effects))
    possible = {(True, f) for f in initial} | {(False, f) for f in universe - initial}
    changed = True
    while changed:
        changed = False
        for pre, neg, add, delete in rules:
            session.work('relaxed_rule_checks')
            if all(((True, f) in possible for f in pre)) and all(((False, f) in possible for f in neg)):
                effects = {(True, f) for f in add} | {(False, f) for f in delete}
                if not effects <= possible:
                    possible.update(effects)
                    changed = True
    return possible

class _RepairNode(py_trees.composites.Selector):

    def __init__(self, session, problem, context, frontier, path=(), depth=0):
        super().__init__('remaining-task-network', memory=False)
        self.session, self.problem, self.context = (session, problem, context)
        self.frontier, self.path, self.depth = (tuple(frontier), path, depth)
        self.built_key = None

    def initialise(self):
        s = self.session
        key = (s.state, self.frontier, s.prefix_cursor)
        if self.built_key == key:
            return
        self.remove_all_children()
        self.built_key = key
        s.work('network_nodes_expanded')
        if self.depth > s.max_depth:
            raise _LimitReached('max_depth')
        if key in self.path:
            s.work('cycle_cuts')
            self.add_child(py_trees.behaviours.Failure('causal-cycle-cut'))
            return
        path = self.path + (key,)
        if not self.frontier:
            self.add_child(_Terminal(s, self.problem))
            return
        task, rest = (self.frontier[0], self.frontier[1:])
        choice = task if isinstance(task, _BTNetworkChoice) else None
        if choice:
            task = choice.term

        def follow(terms):
            return _RepairNode(s, self.problem, self.context, tuple(terms), path, self.depth + 1)

        def expand(subtasks):
            return follow((choice.network.expand(choice.node_id, subtasks),) + rest if choice else tuple(subtasks) + rest)

        def branch(name, children):
            self.add_child(_Sequence(s, name, children))
        if choice and isinstance(task, TaskNetwork):
            branch('embed-partial-child-network', [follow((choice.network.embed(choice.node_id, task),) + rest)])
            return
        if isinstance(task, PrimitiveTask):
            if s.prefix_cursor < len(s.prefix):
                candidates = (s.original_actions[task.action_id],) if task.action_id in s.original_actions else ()
            elif task.original:
                original = s.original_actions.get(task.action_id)
                candidates = tuple((a for a in self.problem.actions if original and a.to_step() == original.to_step()))
            else:
                candidates = tuple((a for a in self.problem.actions if a.action_id == task.action_id))
            for action in candidates:
                continuation = (choice.network.without(choice.node_id),) + rest if choice else rest
                branch('primitive-continuation:' + action.action_id, [_NetworkAction(s, action), follow(continuation)])
        elif isinstance(task, TaskNetwork):
            if not task.nodes:
                branch('empty-network', [expand(())])
            for name, term in task.enabled():
                s.work('enabled_task_choices')
                branch('enabled-task:' + name, [expand((_BTNetworkChoice(task, name, term),))])
        elif isinstance(task, CompoundTask):
            for method in self.context.methods:
                if method.task == task.name:
                    branch('method:' + method.name, [_Conjunction(s, method.preconditions, method.negative_preconditions), expand(method.subtasks)])
        elif isinstance(task, EnsureGoals):
            possible = s.original_possible if s.prefix_cursor < len(s.prefix) else s.possible
            impossible = any(((True, f) not in possible for f in task.positive)) or any(((False, f) not in possible and (True, f) in possible for f in task.negative))
            if impossible:
                s.work('unreachable_obligation_prunes')
                self.add_child(py_trees.behaviours.Failure('unreachable-network-obligation'))
                return
            missing, violated = (sorted(task.positive - s.state), sorted(task.negative & s.state))
            if not missing and (not violated):
                branch('observed-obligation', [_Conjunction(s, task.positive, task.negative), expand(())])
            else:
                goal = AchieveLiteral(missing[0]) if missing else AchieveLiteral(violated[0], True)
                branch('remaining-obligation', [expand((goal, task))])
        elif isinstance(task, AchieveLiteral):
            met = task.fact not in s.state if task.negative else task.fact in s.state
            if met:
                branch('observed-literal', [expand(())])
            else:
                before_prefix_end = s.prefix_cursor < len(s.prefix)
                producers = s.original_producers if before_prefix_end else s.producers
                for action, positive, negative, effect in producers.get((not task.negative, task.fact), ()):
                    pre, neg = (action.preconditions | positive, action.negative_preconditions | negative)
                    impossible = any((f not in s.state and (not producers.get((True, f))) for f in pre)) or any((f in s.state and (not producers.get((False, f))) for f in neg))
                    if impossible:
                        s.work('permanent_precondition_prunes')
                        continue
                    if not pre & neg:
                        branch('producer:' + action.action_id + ':' + effect, [expand((EnsureGoals(pre, neg), PrimitiveTask(action.action_id, original=before_prefix_end), task))])
        else:
            raise TypeError(f'unsupported network task {task!r}')
        if not self.children:
            self.add_child(py_trees.behaviours.Failure('no-native-network-alternative'))

def solve_bt_repair(problem, *, context, max_work=100000, max_depth=256, timeout_s=30.0):
    """Repair an explicit original task network; never shortcut on initial goal."""
    if context.original_plan is None:
        raise ValueError('explicit BT repair requires original_plan and original task network')
    started = time.perf_counter()
    lookup = {a.action_id: a for a in context.original_actions or problem.actions}
    phases, spent = ([], 0)
    trace = {'original_plan': list(context.original_plan), 'executed_prefix': list(context.executed_prefix), 'remaining_original_plan': list(context.original_plan[len(context.executed_prefix):]), 'original_state': sorted(context.original_state), 'observed_state': sorted(problem.initial_state), 'deviation': {'add': sorted(context.observed_deviation_add), 'delete': sorted(context.observed_deviation_delete)}, 'phases': phases, 'search_complete': False, 'robot_rollback': False, 'domain_version': problem.domain_version, 'knowledge': 'original methods/DAG + independent continuation BT action-achievement branches'}
    trace['action_domain_change'] = {'removed_original_action_ids': [a.action_id for a in lookup.values() if not any((b.to_step() == a.to_step() for b in problem.actions))], 'added_current_action_ids': [a.action_id for a in problem.actions if not any((b.to_step() == a.to_step() for b in lookup.values()))], 'meaning': 'authorization/grounding changes can alter operators independently of fact delta'}

    def result(status, session=None, reason=''):
        success = status in {REPAIRED, NO_REPAIR_NEEDED, MAINTENANCE}
        acts = session.state_actions if session and success else ()
        trace['reason'] = reason
        return ExplicitRepairResult(status, tuple((a.action_id for a in acts)), tuple((deepcopy(a.to_step()) for a in acts)), session.state if session else problem.initial_state, trace, {'work_units': spent, 'elapsed_s': time.perf_counter() - started}, IMPLEMENTATION_ID)

    def attempt(name, p, ctx, required=None):
        nonlocal spent
        if spent >= max_work or time.perf_counter() - started >= timeout_s:
            return ('limit', None)
        current = ctx.original_state if ctx.executed_prefix else p.initial_state
        s = _RepairSession(frozenset(current), max_work - spent, started + timeout_s, prefix=ctx.executed_prefix, original_actions=lookup, observed=frozenset(p.initial_state), required_steps=None if required is None else tuple(required), max_depth=max_depth, producers=_supporters(p.actions), original_producers=_supporters(lookup.values()))
        s.counts.update(network_nodes_expanded=0, prefix_actions_replayed=0, enabled_task_choices=0, permanent_precondition_prunes=0, relaxed_rule_checks=0, unreachable_obligation_prunes=0)
        terms = (ctx.original_network,) if isinstance(ctx.original_network, TaskNetwork) else tuple(ctx.original_network)
        tree = _RepairNode(s, p, ctx, terms)
        status, detail = ('failure', 'tree alternatives exhausted; not an UNSAT certificate')
        try:
            s.possible = _relaxed_literals(p.actions, p.initial_state, s)
            s.original_possible = _relaxed_literals(lookup.values(), ctx.original_state, s) if ctx.executed_prefix else s.possible
            s.work('root_ticks')
            tree.tick_once()
            if tree.status == py_trees.common.Status.SUCCESS:
                status, detail = ('success', 'complete task network and goal')
        except (_LimitReached, RecursionError) as exc:
            status, detail = ('limit', str(exc) or 'python_recursion_limit')
        spent += sum(s.counts.values())
        phases.append({'phase': name, 'status': status, 'reason': detail, 'work': dict(s.counts), 'candidate_action_ids': [a.action_id for a in s.state_actions], 'tree_branches': deepcopy(s.events), 'prefix_cursor': s.prefix_cursor})
        return (status, s)
    if context.executed_prefix != context.original_plan[:len(context.executed_prefix)]:
        return result(PREFIX_VIOLATION, reason='actual prefix differs from supplied original plan')
    if any((a not in lookup for a in context.original_plan)):
        return result(INVALID_CONTEXT, reason='unknown original plan action')
    replay = frozenset(context.original_state)
    for aid in context.executed_prefix:
        if not lookup[aid].applicable(replay):
            return result(PREFIX_VIOLATION, reason='original prefix action inapplicable')
        replay = lookup[aid].apply(replay)
    transformed = replay - context.observed_deviation_delete | context.observed_deviation_add
    if transformed != problem.initial_state:
        return result(PREFIX_VIOLATION, reason='prefix plus deviation does not equal actual observation')
    original = replace(problem, actions=tuple(lookup.values()), initial_state=context.original_state, goal_facts=frozenset(), negative_goal_facts=frozenset(), goal_cardinality=())
    original_context = replace(context, executed_prefix=(), observed_deviation_add=frozenset(), observed_deviation_delete=frozenset())
    status, _ = attempt('original_network_witness', original, original_context, [lookup[a].to_step() for a in context.original_plan])
    if status == 'limit':
        return result(SEARCH_LIMIT, reason='original witness validation limit')
    if status != 'success':
        return result(INVALID_CONTEXT, reason='original plan is not a complete legal network witness')
    required = [lookup[a].to_step() for a in context.original_plan[len(context.executed_prefix):]]
    if required and (not any((a.to_step() == required[0] and a.applicable(problem.initial_state) for a in problem.actions))):
        status, s = ('failure', None)
        phases.append({'phase': 'unchanged_remaining_suffix', 'status': status, 'reason': 'first fixed suffix action has no applicable current grounding', 'work': {'fixed_action_precondition_checks': len(problem.actions)}, 'candidate_action_ids': [], 'tree_branches': [], 'prefix_cursor': len(context.executed_prefix)})
        spent += len(problem.actions)
    else:
        status, s = attempt('unchanged_remaining_suffix', problem, context, required)
    if status == 'limit':
        return result(SEARCH_LIMIT, reason='unchanged suffix validation limit')
    if status == 'success':
        return result(NO_REPAIR_NEEDED if s.state_actions else MAINTENANCE, s, 'original suffix still completes all mandatory obligations')
    status, s = attempt('repair_remaining_network', problem, context)
    if status == 'limit':
        return result(SEARCH_LIMIT, reason='repair search limit')
    return result(REPAIRED if status == 'success' else NO_REPAIR_FOUND, s, 'changed complete suffix' if status == 'success' else 'no repair found; no UNSAT claim')

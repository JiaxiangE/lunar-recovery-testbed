"""Domain repair."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from itertools import chain
from itertools import product
from time import perf_counter
from typing import Any
from typing import Mapping
from baselines.common.native_result import NATIVE_EXHAUSTED
from baselines.common.native_result import NATIVE_NO_METHOD
from baselines.common.native_result import NATIVE_PREFIX_VIOLATION
from baselines.common.native_result import NATIVE_SOLVED
from baselines.common.native_result import NativePlanResult
IMPLEMENTATION_ID = 'holler_clean_room_network_repair_domain_v2'
METHOD_KNOWLEDGE = 'generic literal-achievement and multi-goal batch-support methods compiled from shared action schemas; grouped conditional guards before shared action prerequisites; optional supplied original HTN methods'

@dataclass(frozen=True)
class PrimitiveTask:
    action_id: str
    original: bool = False

@dataclass(frozen=True)
class CompoundTask:
    name: str

@dataclass(frozen=True)
class AchieveLiteral:
    fact: str
    negative: bool = False

@dataclass(frozen=True)
class EnsureGoals:
    positive: frozenset[str] = frozenset()
    negative: frozenset[str] = frozenset()

    def __post_init__(self):
        object.__setattr__(self, 'positive', frozenset(self.positive))
        object.__setattr__(self, 'negative', frozenset(self.negative))

@dataclass(frozen=True)
class TaskNetwork:
    """Explicit partial order. No arbitrary topological order is committed."""
    nodes: tuple[tuple[str, Any], ...]
    before: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self):
        object.__setattr__(self, 'nodes', tuple(self.nodes))
        object.__setattr__(self, 'before', frozenset(self.before))
        ids = {name for name, _ in self.nodes}
        if len(ids) != len(self.nodes) or any((a not in ids or b not in ids or a == b for a, b in self.before)):
            raise ValueError('task DAG has duplicate/unknown/self node references')
        remaining = set(ids)
        while remaining:
            ready = {n for n in remaining if not any((b == n and a in remaining for a, b in self.before))}
            if not ready:
                raise ValueError('task precedence must be acyclic')
            remaining -= ready

    def enabled(self):
        return tuple(((n, t) for n, t in self.nodes if not any((b == n for _, b in self.before))))

    def without(self, name):
        return TaskNetwork(tuple(((n, t) for n, t in self.nodes if n != name)), frozenset(((a, b) for a, b in self.before if a != name and b != name)))

    def expand(self, name, subtasks):
        """Replace an enabled compound node, retaining unrelated enabled tasks.

        Ordered child edges are internal only. This permits legal interleaving
        with another compound Task's children instead of forcing a whole Task
        to execute atomically merely because it was selected for decomposition.
        """
        subtasks = tuple(subtasks)
        remaining = tuple(((n, t) for n, t in self.nodes if n != name))
        existing = {n for n, _ in remaining}
        prefix = name + '/child'
        while any((f'{prefix}{i}' in existing for i in range(len(subtasks)))):
            prefix += '/'
        inserted = tuple(((f'{prefix}{i}', t) for i, t in enumerate(subtasks)))
        predecessors = {a for a, b in self.before if b == name}
        successors = {b for a, b in self.before if a == name}
        edges = {(a, b) for a, b in self.before if a != name and b != name}
        if inserted:
            edges.update(((inserted[i][0], inserted[i + 1][0]) for i in range(len(inserted) - 1)))
            edges.update(((a, inserted[0][0]) for a in predecessors))
            edges.update(((inserted[-1][0], b) for b in successors))
        else:
            edges.update(((a, b) for a in predecessors for b in successors))
        return TaskNetwork(remaining + inserted, frozenset(edges))

    def embed(self, name, child):
        """Splice an explicitly partial-order child network without losing edges."""
        remaining = tuple(((n, t) for n, t in self.nodes if n != name))
        existing = {n for n, _ in remaining}
        prefix = name + '/network/'
        while any((prefix + n in existing for n, _ in child.nodes)):
            prefix += '/'
        rename = {n: prefix + n for n, _ in child.nodes}
        edges = {(a, b) for a, b in self.before if a != name and b != name}
        predecessors = {a for a, b in self.before if b == name}
        successors = {b for a, b in self.before if a == name}
        if child.nodes:
            edges.update(((rename[a], rename[b]) for a, b in child.before))
            roots = {n for n, _ in child.nodes if not any((b == n for _, b in child.before))}
            tails = {n for n, _ in child.nodes if not any((a == n for a, _ in child.before))}
            edges.update(((a, rename[b]) for a in predecessors for b in roots))
            edges.update(((rename[a], b) for a in tails for b in successors))
        else:
            edges.update(((a, b) for a in predecessors for b in successors))
        return TaskNetwork(remaining + tuple(((rename[n], t) for n, t in child.nodes)), frozenset(edges))

@dataclass(frozen=True)
class _NetworkChoice:
    network: TaskNetwork
    node_id: str
    term: Any
TaskTerm = PrimitiveTask | CompoundTask | AchieveLiteral | EnsureGoals | TaskNetwork

@dataclass(frozen=True)
class OrderedMethod:
    name: str
    task: str
    subtasks: tuple[TaskTerm, ...]
    preconditions: frozenset[str] = frozenset()
    negative_preconditions: frozenset[str] = frozenset()
    source: str = 'explicit original HTN domain method'

    def __post_init__(self):
        object.__setattr__(self, 'subtasks', tuple(self.subtasks))
        object.__setattr__(self, 'preconditions', frozenset(self.preconditions))
        object.__setattr__(self, 'negative_preconditions', frozenset(self.negative_preconditions))

@dataclass(frozen=True)
class HTNRepairContext:
    """Pre-failure hierarchy and the actual prefix; no failed-task-only shortcut.

    ``original_state`` is before the prefix; shared ``problem.initial_state`` is
    the observed repair state. Replay plus deviation must equal that observation.
    The prefix must also be a prefix of a decomposition of ``original_network``.
    A merely executable prefix from an unrelated task network is rejected.
    """
    original_network: tuple[TaskTerm, ...]
    original_state: frozenset[str]
    executed_prefix: tuple[str, ...] = ()
    methods: tuple[OrderedMethod, ...] = ()
    observed_deviation_add: frozenset[str] = frozenset()
    observed_deviation_delete: frozenset[str] = frozenset()
    failed_task: str | None = None
    method_source: str = 'explicit original task network and method domain'
    original_plan: tuple[str, ...] | None = None
    original_actions: tuple[Any, ...] = ()

    def __post_init__(self):
        for name in ('executed_prefix', 'methods', 'original_actions'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not isinstance(self.original_network, TaskNetwork):
            object.__setattr__(self, 'original_network', tuple(self.original_network))
        if self.original_plan is not None:
            object.__setattr__(self, 'original_plan', tuple(self.original_plan))
        for name in ('original_state', 'observed_deviation_add', 'observed_deviation_delete'):
            object.__setattr__(self, name, frozenset(getattr(self, name)))

@dataclass(frozen=True)
class SearchLimits:
    max_nodes: int = 100000
    max_depth: int = 256
    timeout_s: float = 10.0

    def __post_init__(self):
        if self.max_nodes < 1 or self.max_depth < 0 or self.timeout_s <= 0:
            raise ValueError('HTN search limits must permit positive work/time')

@dataclass(frozen=True)
class HTNRepairResult:
    status: str
    action_ids: tuple[str, ...] = ()
    plan: tuple[dict, ...] = ()
    work_counters: Mapping[str, Any] = field(default_factory=dict)
    transformation_trace: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ''
    implementation_id: str = IMPLEMENTATION_ID
    domain_version: str = 'paper4_domain_extension_v1'

    @property
    def solved(self):
        return self.status == NATIVE_SOLVED

    def as_native(self):
        return NativePlanResult(method_id='htn_repair_holler', implementation_id=self.implementation_id, native_status=self.status, canonical_plan=self.plan, native_artifact={'domain_version': self.domain_version, 'transformation_trace': dict(self.transformation_trace), 'action_ids': list(self.action_ids)}, diagnostics={'reason': self.reason, 'work_counters': dict(self.work_counters), 'method_knowledge': METHOD_KNOWLEDGE, 'used_llm': False, 'official_native_run': False})

def _task_record(task):
    if isinstance(task, PrimitiveTask):
        return {'primitive': task.action_id, **({'original_domain': True} if task.original else {})}
    if isinstance(task, TaskNetwork):
        return {'nodes': [{'id': n, 'task': _task_record(t)} for n, t in task.nodes], 'precedence': sorted(task.before)}
    if isinstance(task, _NetworkChoice):
        return {'enabled_node': task.node_id, 'task': _task_record(task.term)}
    if isinstance(task, CompoundTask):
        return {'compound': task.name}
    if isinstance(task, AchieveLiteral):
        return {'achieve': task.fact, 'negative': task.negative}
    if isinstance(task, EnsureGoals):
        return {'ensure_positive': sorted(task.positive), 'ensure_negative': sorted(task.negative)}
    raise TypeError(f'invalid HTN task term: {task!r}')

def _method_record(method):
    return {'name': method.name, 'task': method.task, 'subtasks': [_task_record(task) for task in method.subtasks], 'preconditions': sorted(method.preconditions), 'negative_preconditions': sorted(method.negative_preconditions), 'source': method.source}

def _condition_parts(effect):
    if hasattr(effect, 'conditions'):
        return (effect.conditions, effect.negative_conditions, effect.add_effects, effect.delete_effects)
    return effect

class _LimitReached(Exception):
    pass

def solve_htn(problem, *, context: HTNRepairContext | None=None, limits: SearchLimits | None=None, enable_batch_methods: bool=True, _required_suffix_steps=None) -> HTNRepairResult:
    """Return an independently searched repaired suffix, never re-execute prefix.

    Without supplied history the input is explicitly an empty-prefix development
    fixture at the shared observed state. With history, hierarchy and state replay
    are both mandatory. Node/depth/time exhaustion is distinct from no HTN repair.
    """
    limits = limits or SearchLimits()
    started = perf_counter()
    actions = {a.action_id: a for a in problem.actions}
    if len(actions) != len(problem.actions):
        raise ValueError('duplicate grounded action IDs')
    domain_version = getattr(problem, 'domain_version', 'paper4_domain_extension_v1')
    generated_methods = ()
    if context is None:
        options = tuple(problem.goal_options()) if hasattr(problem, 'goal_options') else (frozenset(problem.goal_facts),)
        negative_goal = frozenset(getattr(problem, 'negative_goal_facts', ()))
        generated_methods = tuple((OrderedMethod(f'goal-option-{index}', 'paper4-goal', (EnsureGoals(option, negative_goal),), source='finite conjunction expansion of shared goal/cardinality, not a solved plan') for index, option in enumerate(options)))
        context = HTNRepairContext(original_network=(CompoundTask('paper4-goal'),), original_state=frozenset(problem.initial_state), methods=generated_methods, method_source=METHOD_KNOWLEDGE + '; empty-prefix observation fixture')
    counts = {'expanded_nodes': 0, 'method_choices': 0, 'primitive_attempts': 0, 'backtracks': 0, 'cycle_prunes': 0, 'depth_prunes': 0, 'prefix_replayed_actions': 0, 'terminal_goal_rejections': 0, 'unreachable_literal_prunes': 0, 'relaxation_rounds': 0, 'enabled_task_choices': 0}
    original_actions = {a.action_id: a for a in context.original_actions} or actions

    def current_matches(original_id):
        original = original_actions.get(original_id)
        return tuple((a for a in actions.values() if original and a.to_step() == original.to_step()))
    trace = {'original_network': _task_record(context.original_network) if isinstance(context.original_network, TaskNetwork) else [_task_record(task) for task in context.original_network], 'original_state': sorted(context.original_state), 'executed_prefix': list(context.executed_prefix), 'observed_state': sorted(problem.initial_state), 'failed_task': context.failed_task, 'deviation': {'add': sorted(context.observed_deviation_add), 'delete': sorted(context.observed_deviation_delete)}, 'method_source': context.method_source, 'methods': [_method_record(method) for method in context.methods] if not generated_methods else [], 'generated_goal_option_n': len(generated_methods), 'method_generator': METHOD_KNOWLEDGE, 'batch_methods_enabled': enable_batch_methods, 'batch_method_knowledge': 'Prepare each selected conditional guard, recheck their conjunction with the common action preconditions, then execute that one physical action; no saved plan or runtime-energy change', 'prefix_preserved': False, 'repair_search_network': 'entire remaining original task network', 'limits': {'max_nodes': limits.max_nodes, 'max_depth': limits.max_depth, 'timeout_s': limits.timeout_s}, 'official_native_run': False}

    def outcome(status, reason, solution=None):
        counts['elapsed_s'] = perf_counter() - started
        ids = solution[0] if solution else ()
        if solution:
            trace['selected_decomposition'] = solution[2]
            trace['final_state'] = sorted(solution[1])
            trace['full_plan_action_ids'] = list(context.executed_prefix + ids)
        return HTNRepairResult(status, ids, tuple(({'primitive': actions[aid].primitive, 'agent_id': actions[aid].agent_id, 'params': dict(actions[aid].params)} for aid in ids)), dict(counts), dict(trace), reason, domain_version=domain_version)
    replay = frozenset(context.original_state)
    for aid in context.executed_prefix:
        action = original_actions.get(aid)
        if action is None or not action.applicable(replay):
            return outcome(NATIVE_PREFIX_VIOLATION, 'executed prefix action is unknown or inapplicable in the original state')
        replay = frozenset(action.apply(replay))
        counts['prefix_replayed_actions'] += 1
    observed = frozenset(replay - context.observed_deviation_delete | context.observed_deviation_add)
    trace['replayed_prefix_state'] = sorted(replay)
    trace['transformed_observed_state'] = sorted(observed)
    if observed != frozenset(problem.initial_state):
        return outcome(NATIVE_PREFIX_VIOLATION, 'prefix plus supplied deviation does not equal the shared observed state')
    producers: dict[tuple[str, bool], list[tuple[Any, frozenset, frozenset, str]]] = {}
    for action in actions.values():
        effects = [(frozenset(), frozenset(), action.add_effects, action.delete_effects)]
        effects += [_condition_parts(effect) for effect in getattr(action, 'conditional_effects', ())]
        for index, (conditions, negative_conditions, added, deleted) in enumerate(effects):
            pre = frozenset(action.preconditions) | frozenset(conditions)
            neg = frozenset(action.negative_preconditions) | frozenset(negative_conditions)
            for negative, facts in ((False, added), (True, deleted)):
                for fact in facts:
                    producers.setdefault((fact, negative), []).append((action, pre, neg, f'{action.action_id}:effect-{index}'))
    original_producers = producers
    if context.original_actions:
        original_producers = {}
        for action in original_actions.values():
            effects = [(frozenset(), frozenset(), action.add_effects, action.delete_effects)]
            effects += [_condition_parts(effect) for effect in action.conditional_effects]
            for index, (conditions, negative_conditions, added, deleted) in enumerate(effects):
                pre = frozenset(action.preconditions) | frozenset(conditions)
                neg = frozenset(action.negative_preconditions) | frozenset(negative_conditions)
                for negative, facts in ((False, added), (True, deleted)):
                    for f in facts:
                        original_producers.setdefault((f, negative), []).append((action, pre, neg, f'{action.action_id}:effect-{index}'))
    methods_by_task = {}
    for method in context.methods:
        methods_by_task.setdefault(method.task, []).append(method)
    universe = set(observed) | set(problem.goal_facts) | set(getattr(problem, 'negative_goal_facts', ()))
    for literal, alternatives in producers.items():
        universe.add(literal[0])
        for _, pre, neg, _ in alternatives:
            universe.update(pre | neg)
    possible = {(f, False) for f in observed} | {(f, True) for f in universe - observed}
    changed = True
    while changed:
        counts['relaxation_rounds'] += 1
        changed = False
        for literal, alternatives in producers.items():
            if literal in possible:
                continue
            if any((all(((f, False) in possible for f in pre)) and all(((f, True) in possible for f in neg)) for _, pre, neg, _ in alternatives)):
                possible.add(literal)
                changed = True
    hierarchy_prefix_seen = not context.executed_prefix
    limit_reason = None

    def batch_alternatives(task, state):
        """Generic HTN domain methods for one action supporting multiple goals.

        Per-effect guards stay grouped: e.g. acquire and store each cargo item
        before moving to the shared delivery location. The full guard/precondition
        conjunction is rechecked before dispatch, so interacting guards cannot
        turn into a false macro success. Alternative guards remain backtrackable.
        """
        wanted = {(f, False) for f in task.positive - state} | {(f, True) for f in task.negative & state}
        if len(wanted) < 2:
            return
        for action in actions.values():
            effects = [(frozenset(), frozenset(), action.add_effects, action.delete_effects)]
            effects += [_condition_parts(effect) for effect in getattr(action, 'conditional_effects', ())]
            by_literal = {}
            for index, (conditions, negative_conditions, adds, deletes) in enumerate(effects):
                covered = ({(f, False) for f in adds} | {(f, True) for f in deletes}) & wanted
                for literal in covered:
                    by_literal.setdefault(literal, []).append(index)
            if len(by_literal) < 2:
                continue
            seen_guards = set()
            for indexes in product(*(by_literal[literal] for literal in sorted(by_literal))):
                selected = tuple(dict.fromkeys(indexes))
                guards = tuple(dict.fromkeys(((frozenset(effects[i][0]), frozenset(effects[i][1])) for i in selected)))
                if guards in seen_guards:
                    continue
                seen_guards.add(guards)
                combined_pre = frozenset(action.preconditions).union(*(p for p, _ in guards))
                combined_neg = frozenset(action.negative_preconditions).union(*(n for _, n in guards))
                if combined_pre & combined_neg:
                    continue
                prepare = tuple((EnsureGoals(p, n) for p, n in guards if p or n))
                subtasks = prepare + (EnsureGoals(combined_pre, combined_neg), PrimitiveTask(action.action_id), task)
                yield (f"batch-support:{action.action_id}:effects={','.join(map(str, selected))}", subtasks)

    def search(state, frontier, cursor, suffix, derivation, ancestors):
        nonlocal hierarchy_prefix_seen, limit_reason
        if counts['expanded_nodes'] >= limits.max_nodes:
            limit_reason = 'node_limit'
            raise _LimitReached
        if perf_counter() - started >= limits.timeout_s:
            limit_reason = 'time_limit'
            raise _LimitReached
        counts['expanded_nodes'] += 1
        if not frontier:
            if cursor != len(context.executed_prefix):
                return None
            if _required_suffix_steps is not None and len(suffix) != len(_required_suffix_steps):
                return None
            final_goal = problem.goal_met(state) if hasattr(problem, 'goal_met') else frozenset(problem.goal_facts) <= state
            if final_goal:
                return (suffix, state, derivation)
            counts['terminal_goal_rejections'] += 1
            return None
        task, depth = frontier[0]
        rest = frontier[1:]
        network_choice = task if isinstance(task, _NetworkChoice) else None
        if depth > limits.max_depth:
            counts['depth_prunes'] += 1
            return None
        key = (state, task, cursor) if isinstance(task, AchieveLiteral) else (state, tuple((term for term, _ in frontier)), cursor)
        if key in ancestors:
            counts['cycle_prunes'] += 1
            return None
        ancestors = ancestors | {key}
        if network_choice:
            task = network_choice.term
            if isinstance(task, TaskNetwork):
                embedded = network_choice.network.embed(network_choice.node_id, task)
                return search(state, ((embedded, depth + 1),) + rest, cursor, suffix, derivation, ancestors)
        primitive_rest = ((network_choice.network.without(network_choice.node_id), depth),) + rest if network_choice else rest
        if isinstance(task, PrimitiveTask):
            counts['primitive_attempts'] += 1
            if cursor < len(context.executed_prefix):
                if task.action_id != context.executed_prefix[cursor]:
                    return None
                action = original_actions.get(task.action_id)
                if action is None or not action.applicable(state):
                    return None
                cursor += 1
                next_state = frozenset(action.apply(state))
                if cursor == len(context.executed_prefix):
                    hierarchy_prefix_seen = True
                    next_state = observed
                return search(next_state, primitive_rest, cursor, suffix, derivation, ancestors)
            candidates = current_matches(task.action_id) if task.original else (actions[task.action_id],) if task.action_id in actions else ()
            for action in candidates:
                if not action.applicable(state):
                    continue
                if _required_suffix_steps is not None and (len(suffix) >= len(_required_suffix_steps) or action.to_step() != _required_suffix_steps[len(suffix)]):
                    continue
                found = search(frozenset(action.apply(state)), primitive_rest, cursor, suffix + (action.action_id,), derivation, ancestors)
                if found is not None:
                    return found
            return None
        alternatives = []
        if isinstance(task, TaskNetwork):
            if not task.nodes:
                alternatives = [('empty-network', ())]
            else:
                alternatives = [(f'enabled:{node}', (_NetworkChoice(task, node, term),)) for node, term in task.enabled()]
                counts['enabled_task_choices'] += len(alternatives)
        elif isinstance(task, CompoundTask):
            alternatives = [(method.name, method.subtasks) for method in methods_by_task.get(task.name, ()) if method.preconditions <= state and (not method.negative_preconditions.intersection(state))]
        elif isinstance(task, EnsureGoals):
            if cursor == len(context.executed_prefix) and (any(((f, False) not in possible for f in task.positive)) or any(((f, True) not in possible for f in task.negative))):
                counts['unreachable_literal_prunes'] += 1
                return None
            missing = sorted(task.positive - state)
            violated = sorted(task.negative & state)
            if not missing and (not violated):
                alternatives = [('goals-observed', ())]
            else:
                first = AchieveLiteral(missing[0]) if missing else AchieveLiteral(violated[0], True)
                alternatives = [('achieve-next-obligation', (first, task))]
                if enable_batch_methods:
                    alternatives = chain(batch_alternatives(task, state), alternatives)
        elif isinstance(task, AchieveLiteral):
            if cursor == len(context.executed_prefix) and (task.fact, task.negative) not in possible:
                counts['unreachable_literal_prunes'] += 1
                return None
            met = task.fact not in state if task.negative else task.fact in state
            if met:
                alternatives = [('literal-observed', ())]
            else:
                before_prefix_end = cursor < len(context.executed_prefix)
                active_producers = original_producers if before_prefix_end else producers
                for action, pre, neg, source in active_producers.get((task.fact, task.negative), ()):
                    if pre & neg:
                        continue
                    subtasks = (EnsureGoals(pre, neg), PrimitiveTask(action.action_id, original=before_prefix_end), task)
                    alternatives.append((f'producer:{source}', subtasks))
        else:
            raise TypeError(f'invalid HTN task: {task!r}')
        for method_name, subtasks in alternatives:
            counts['method_choices'] += 1
            next_frontier = ((network_choice.network.expand(network_choice.node_id, subtasks), depth + 1),) + rest if network_choice else tuple(((subtask, depth + 1) for subtask in subtasks)) + rest
            entry = {'task': _task_record(task), 'method': method_name, 'prefix_cursor': cursor}
            result = search(state, next_frontier, cursor, suffix, derivation + (entry,), ancestors)
            if result is not None:
                return result
            counts['backtracks'] += 1
        return None
    try:
        start_state = context.original_state if context.executed_prefix else observed
        initial_frontier = ((context.original_network, 0),) if isinstance(context.original_network, TaskNetwork) else tuple(((t, 0) for t in context.original_network))
        solution = search(frozenset(start_state), initial_frontier, 0, (), (), frozenset())
    except _LimitReached:
        solution = None
    except RecursionError:
        limit_reason = 'python_recursion_limit'
        solution = None
    trace['prefix_preserved'] = hierarchy_prefix_seen
    if solution:
        return outcome(NATIVE_SOLVED, 'entire repaired HTN network and complete shared goal satisfied', solution)
    if limit_reason or counts['depth_prunes']:
        return outcome(NATIVE_EXHAUSTED, limit_reason or 'depth_limit; not a proof of unsolvability')
    if not hierarchy_prefix_seen:
        return outcome(NATIVE_PREFIX_VIOLATION, 'executable prefix is not a prefix of any applicable decomposition of the original network')
    return outcome(NATIVE_NO_METHOD, 'no repair found in the supplied HTN method domain; not a claim that the common action domain is unsolvable')
REPAIR_IMPLEMENTATION_ID = 'holler_explicit_network_repair_v3'
REPAIRED = 'REPAIRED'
NO_REPAIR_NEEDED = 'NO_REPAIR_NEEDED'
MAINTENANCE = 'MAINTENANCE'
SEARCH_LIMIT = 'SEARCH_LIMIT'
NO_REPAIR_FOUND = 'NO_REPAIR_FOUND'
PREFIX_VIOLATION = 'PREFIX_VIOLATION'
INVALID_CONTEXT = 'INVALID_REPAIR_CONTEXT'

@dataclass(frozen=True)
class ExplicitRepairResult:
    status: str
    action_ids: tuple[str, ...]
    plan: tuple[dict, ...]
    final_state: frozenset[str]
    trace: Mapping[str, Any]
    work: Mapping[str, Any]
    implementation_id: str = REPAIR_IMPLEMENTATION_ID

    @property
    def solved(self):
        return self.status in {REPAIRED, NO_REPAIR_NEEDED, MAINTENANCE}

    @property
    def native_success(self):
        return self.solved

    def to_dict(self):
        return {'status': self.status, 'implementation_id': self.implementation_id, 'solved': self.solved, 'action_ids': list(self.action_ids), 'plan': list(self.plan), 'final_state': sorted(self.final_state), 'repair_performed': self.status == REPAIRED, 'remaining_execution_required': bool(self.plan), 'maintenance': self.status == MAINTENANCE, 'world_dispatch_n': 0, 'final_state_kind': 'native symbolic prediction, not an executed world observation', 'trace': dict(self.trace), 'work': dict(self.work)}

def _original_term(term):
    if isinstance(term, PrimitiveTask):
        return replace(term, original=True)
    if isinstance(term, TaskNetwork):
        return TaskNetwork(tuple(((n, _original_term(t)) for n, t in term.nodes)), term.before)
    return term

def make_repair_context(original_problem, observed_problem, *, original_network, original_plan, executed_prefix=(), methods=(), failed_task=None):
    """Bind supplied pre-failure history to the actual observation; no plan is generated.

    Original action IDs belong to original_problem. The repair suffix maps by
    complete primitive/actor/params to observed_problem's current grounded table.
    Both methods and all other methods in a comparison can receive this same data.
    """
    if original_problem.domain_version != observed_problem.domain_version:
        raise ValueError('repair history cannot mix domain profiles')
    state = frozenset(original_problem.initial_state)
    lookup = original_problem.action_by_id()
    for aid in executed_prefix:
        if aid not in lookup or not lookup[aid].applicable(state):
            raise ValueError('recorded executed prefix is not legal in the original domain')
        state = lookup[aid].apply(state)
    network = _original_term(original_network) if isinstance(original_network, TaskNetwork) else tuple((_original_term(t) for t in original_network))
    marked_methods = tuple((replace(m, subtasks=tuple((_original_term(t) for t in m.subtasks))) for m in methods))
    return HTNRepairContext(network, original_problem.initial_state, tuple(executed_prefix), marked_methods, observed_deviation_add=frozenset(observed_problem.initial_state - state), observed_deviation_delete=frozenset(state - observed_problem.initial_state), failed_task=failed_task, method_source='supplied original task network/witness and observed deviation', original_plan=tuple(original_plan), original_actions=tuple(original_problem.actions))

def make_task_repair_context(original_problem, observed_problem, *, tasks, executed_prefix=(), original_plan=None, before=None, failed_task=None):
    """Represent supplied original Task plans and permitted child obligations.

    Each task mapping supplies task_id, action_ids, goal_facts (or goal_options),
    and optional negative_goal_facts. Original actions are a genuine reference
    method; a repair method preserves that Task's actually executed prefix and
    achieves its current allowed obligations. No new plan or task goal is invented.
    before=None keeps original Task order; an explicit edge set supplies a DAG.
    This convenience adapter accepts noninterleaved reference Task traces; use
    make_repair_context/TaskNetwork directly for other declared task networks.
    """
    tasks = tuple(tasks)
    flattened = tuple((a for task in tasks for a in task['action_ids']))
    if original_plan is not None and tuple(original_plan) != flattened:
        raise ValueError('Task convenience adapter requires the supplied noninterleaved original Task trace')
    if tuple(executed_prefix) != flattened[:len(executed_prefix)]:
        raise ValueError('executed prefix differs from original Task trace')
    methods, nodes, offset = ([], [], 0)
    for task in tasks:
        tid, ids = (str(task['task_id']), tuple(task['action_ids']))
        negatives = frozenset(task.get('negative_goal_facts', ()))
        options = tuple((frozenset(x) for x in task.get('goal_options', (task.get('goal_facts', ()),))))
        if not options or any((not option and (not negatives) for option in options)):
            raise ValueError('original Task requires explicit nonempty allowed obligations')
        checker = 'check-post:' + tid
        methods.extend((OrderedMethod(f'{checker}:{i}', checker, (), option, negatives, 'condition-only original/current Task postcondition') for i, option in enumerate(options)))
        methods.append(OrderedMethod('planned:' + tid, tid, tuple((PrimitiveTask(a) for a in ids)) + (CompoundTask(checker),), source='supplied pre-failure primitive plan and its Task postcondition'))
        prefix_in_task = ids[:max(0, min(len(ids), len(executed_prefix) - offset))]
        methods.extend((OrderedMethod(f'repair:{tid}:{i}', tid, tuple((PrimitiveTask(a) for a in prefix_in_task)) + (EnsureGoals(option, negatives), CompoundTask(checker)), source='prefix-preserving transformation plus publicly supplied current Task obligations') for i, option in enumerate(options)))
        nodes.append((tid, CompoundTask(tid)))
        offset += len(ids)
    network = tuple((term for _, term in nodes)) if before is None else TaskNetwork(tuple(nodes), frozenset(before))
    return make_repair_context(original_problem, observed_problem, original_network=network, original_plan=flattened, executed_prefix=executed_prefix, methods=methods, failed_task=failed_task)

def solve_htn_repair(problem, *, context: HTNRepairContext, limits=None, enable_batch_methods=True):
    """Classify actual repair while preserving every original remaining obligation.

    First validate the supplied original witness against its own task network,
    then try its exact remaining suffix in the deviated state. Only then search
    for a changed suffix. No state-only goal short circuit exists. NO_REPAIR_NEEDED
    can carry physical actions; MAINTENANCE requires a complete zero-action suffix.
    Failure of this bounded/incompletely pruned search is never called UNSAT.
    """
    if context.original_plan is None:
        raise ValueError('explicit repair requires a supplied original_plan witness')
    limits = limits or SearchLimits(timeout_s=30)
    started, total_nodes, phases = (perf_counter(), 0, [])
    original_actions = tuple(context.original_actions or problem.actions)
    lookup = {a.action_id: a for a in original_actions}
    trace = {'original_network': _task_record(context.original_network) if isinstance(context.original_network, TaskNetwork) else [_task_record(t) for t in context.original_network], 'original_plan': list(context.original_plan), 'executed_prefix': list(context.executed_prefix), 'remaining_original_plan': list(context.original_plan[len(context.executed_prefix):]), 'original_state': sorted(context.original_state), 'observed_state': sorted(problem.initial_state), 'deviation': {'add': sorted(context.observed_deviation_add), 'delete': sorted(context.observed_deviation_delete)}, 'search_complete': False, 'robot_rollback': False, 'domain_version': problem.domain_version, 'network_semantics': 'ordered sequences and explicit DAG enabled-task backtracking; no invented order relaxation', 'phases': phases}
    trace['action_domain_change'] = {'removed_original_action_ids': [a.action_id for a in original_actions if not any((b.to_step() == a.to_step() for b in problem.actions))], 'added_current_action_ids': [a.action_id for a in problem.actions if not any((b.to_step() == a.to_step() for b in original_actions))], 'meaning': 'authorization/grounding changes can alter available operators even when fact delta is empty'}

    def result(status, native=None, reason=''):
        ids = native.action_ids if native and native.solved else ()
        plan = native.plan if native and native.solved else ()
        state = frozenset(problem.initial_state)
        for aid in ids:
            state = problem.action_by_id()[aid].apply(state)
        trace['reason'] = reason
        return ExplicitRepairResult(status, ids, plan, state, trace, {'expanded_nodes': total_nodes, 'elapsed_s': perf_counter() - started})

    def run_phase(name, p, ctx, required=None):
        nonlocal total_nodes
        remaining_time = limits.timeout_s - (perf_counter() - started)
        if remaining_time <= 0 or total_nodes >= limits.max_nodes:
            return None
        remaining = replace(limits, timeout_s=remaining_time, max_nodes=limits.max_nodes - total_nodes)
        native = solve_htn(p, context=ctx, limits=remaining, enable_batch_methods=enable_batch_methods, _required_suffix_steps=required)
        total_nodes += native.work_counters.get('expanded_nodes', 0)
        phases.append({'phase': name, 'status': native.status, 'reason': native.reason, 'work': dict(native.work_counters), 'transformation': dict(native.transformation_trace)})
        return native
    if context.executed_prefix != context.original_plan[:len(context.executed_prefix)]:
        return result(PREFIX_VIOLATION, reason='actual prefix differs from the supplied current original plan')
    if any((a not in lookup for a in context.original_plan)):
        return result(INVALID_CONTEXT, reason='original plan references an unknown original action')
    original = replace(problem, initial_state=context.original_state, actions=original_actions, goal_facts=frozenset(), negative_goal_facts=frozenset(), goal_cardinality=())
    witness_context = replace(context, executed_prefix=(), observed_deviation_add=frozenset(), observed_deviation_delete=frozenset())
    witness = run_phase('original_network_witness', original, witness_context, [lookup[a].to_step() for a in context.original_plan])
    if witness is None or witness.status == NATIVE_EXHAUSTED:
        return result(SEARCH_LIMIT, reason='original network witness validation exhausted its search budget')
    if not witness.solved:
        return result(INVALID_CONTEXT, reason='supplied original plan is not a legal complete network witness')
    unchanged = run_phase('unchanged_remaining_suffix', problem, context, [lookup[a].to_step() for a in context.original_plan[len(context.executed_prefix):]])
    if unchanged is None or unchanged.status == NATIVE_EXHAUSTED:
        return result(SEARCH_LIMIT, reason='unchanged suffix check exhausted its budget')
    if unchanged.status == NATIVE_PREFIX_VIOLATION:
        return result(PREFIX_VIOLATION, reason=unchanged.reason)
    if unchanged.solved:
        return result(NO_REPAIR_NEEDED if unchanged.plan else MAINTENANCE, unchanged, 'same remaining actions complete all network obligations and the goal')
    repaired = run_phase('repair_remaining_network', problem, context)
    if repaired is None or repaired.status == NATIVE_EXHAUSTED:
        return result(SEARCH_LIMIT, reason='repair search exhausted its budget')
    if repaired.status == NATIVE_PREFIX_VIOLATION:
        return result(PREFIX_VIOLATION, reason=repaired.reason)
    if repaired.solved:
        return result(REPAIRED, repaired, 'changed suffix completes the full remaining network and goal')
    return result(NO_REPAIR_FOUND, reason='no repair found; this bounded HTN procedure is not an UNSAT certificate')

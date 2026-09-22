"""Contracts."""
from __future__ import annotations
from copy import deepcopy
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Set
from typing import Tuple
from domains.predicates import Conjunction
from domains.predicates import AtomicPredicate
from domains.predicates import entails
from domains.predicates import parse_predicate
from domains.actions.psr.tokens import PSR_PRIMITIVES
from domains.actions.psr.tokens import chain_legality as psr_chain_legality
from domains.actions.psr.tokens import chain_produces as psr_chain_produces
from domains.actions.psr.tokens import goal_facts_of as psr_goal_facts_of
from domains.actions.lava import LAVA_SYMBOLIC_PRIMITIVE_SPECS
from domains.actions.spec import ActorBindingError
from domains.actions.spec import Fact
from domains.actions.spec import fact
from domains.actions.spec import forward_chain_legality
from domains.actions.spec import trusted_agent_types_by_id
from domains.worlds.psr_world import PSRWorld
_ENTITY_RE = re.compile('^(?:sample|rover|relay|sampler)_\\w+$')

def referenced_entities(pred_str: str) -> Set[str]:
    """Entity-id tokens (sample_*/rover_*/relay_*/sampler_*) used as atom arguments in a
    predicate string. Used to catch a decomposition that invents a non-existent entity
    (e.g. 'sample_ice') — an argument-validity gap the predicate-only contracts miss."""
    out: Set[str] = set()
    for at in parse_predicate(pred_str or '').atomics:
        for a in at.args:
            if _ENTITY_RE.match(str(a)):
                out.add(str(a))
    return out

@dataclass
class ValidationResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)
    contract_violations: List[str] = field(default_factory=list)

@dataclass
class D_TValidationResult:
    passed: bool
    status: str
    reason_code: str = ''
    reasons: List[str] = field(default_factory=list)
    goal_discharge: Dict[str, str] = field(default_factory=dict)
    final_facts: Set[Fact] = field(default_factory=set)
    produced_facts: Set[Fact] = field(default_factory=set)

class SourceBackedBridgeError(ValueError):
    """A source-backed primitive→predicate projection failed closed."""

    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        super().__init__(detail)

@dataclass(frozen=True)
class PSRSourceBackedProjection:
    """Neutral PSR projection shared by decomposition and parent-contract plumbing."""
    pre_projection_facts: Tuple[Fact, ...]
    projected_facts: Tuple[Fact, ...]
    produced_tokens: FrozenSet[str]
    goal_tokens: FrozenSet[str]

def _one_fact(value: Any) -> Fact:
    if isinstance(value, Fact):
        return value
    parsed = parse_predicate(str(value))
    if len(parsed.atomics) != 1 or parsed.atomics[0].negated:
        raise ValueError(f'expected one positive fact, got {value!r}')
    atom = parsed.atomics[0]
    return fact(atom.name, *atom.args)

def project_psr_chain_to_facts(chain: Iterable[Mapping[str, Any]], *, actor_id: str, required_goal_facts: Iterable[Fact]=(), initial_facts: Optional[Iterable[str]]=None, n_samples: int=5, runtime_world: Optional[PSRWorld]=None) -> PSRSourceBackedProjection:
    """Project a PSR chain through the one approved parameterized world bridge.

    This function is intentionally neutral: corrected ``D_T`` and the Stage-3 parent
    adapter both consume it, so there is only one formal/offline implementation of the
    ``apply_structured_effects`` → ``global_facts`` bridge.  It reuses the legacy
    token-level legality/goal helpers and adds only fail-closed parameter-identity checks
    at that bridge boundary.
    """
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise SourceBackedBridgeError('AGENT_BINDING_MISMATCH', 'PSR bridge actor is missing')
    if not isinstance(n_samples, int) or isinstance(n_samples, bool) or n_samples < 1:
        raise SourceBackedBridgeError('BAD_ARGUMENTS', 'n_samples must be a positive integer')
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(chain):
        if not isinstance(step, Mapping):
            raise SourceBackedBridgeError('BAD_ARGUMENTS', f'PSR step {index} is not an object')
        name = step.get('primitive')
        params = step.get('params', {})
        if name not in PSR_PRIMITIVES:
            raise SourceBackedBridgeError('UNRESOLVED_SPEC', f'PSR step {index} has no authoritative primitive')
        if not isinstance(params, Mapping):
            raise SourceBackedBridgeError('BAD_ARGUMENTS', f'PSR step {index} params must be an object')
        supplied_actor = step.get('agent_id')
        if supplied_actor is not None and supplied_actor != actor_id:
            raise SourceBackedBridgeError('AGENT_BINDING_MISMATCH', f'PSR step {index} changes the bound actor')
        normalized.append({'primitive': str(name), 'params': dict(params)})
    legal, detail = psr_chain_legality(normalized, frozenset(initial_facts or ()))
    if not legal:
        raise SourceBackedBridgeError('CONTRACT_NOT_ENTAILED', detail)
    produced_tokens = frozenset(psr_chain_produces(normalized))
    goal_tokens = frozenset(psr_goal_facts_of(normalized))
    if not produced_tokens or not goal_tokens:
        raise SourceBackedBridgeError('GOAL_INCOMPLETE', 'existing chain_produces/goal_facts_of found no achieved PSR goal')
    if runtime_world is not None and (not isinstance(runtime_world, PSRWorld)):
        raise SourceBackedBridgeError('BAD_ARGUMENTS', 'runtime_world must be a PSRWorld snapshot')
    if runtime_world is not None:
        from domains.actions.psr.strict_profile import LEGACY_PSR_PROFILE
        if getattr(runtime_world, 'semantics_profile', LEGACY_PSR_PROFILE) != LEGACY_PSR_PROFILE:
            raise SourceBackedBridgeError('PSR_PROFILE_MISMATCH', 'legacy token projector cannot validate the strict PSR profile; use the versioned named gate')
    world = deepcopy(runtime_world) if runtime_world is not None else PSRWorld(n_samples=n_samples)
    if runtime_world is None:
        world.reset(seed=0)
    world.strict_collect = True
    if actor_id not in world.agents:
        raise SourceBackedBridgeError('AGENT_BINDING_MISMATCH', f'PSR actor {actor_id!r} is absent from the world roster')
    pre_projection = tuple(sorted({_one_fact(value) for value in world.global_facts()}, key=lambda item: item.render()))
    valid_samples = set(world.samples)
    required_facts = tuple(required_goal_facts)
    for required_fact in required_facts:
        if required_fact.predicate not in {'in_base_storage', 'stored'}:
            raise SourceBackedBridgeError('UNRESOLVED_SPEC', f'PSRWorld.global_facts has no approved bridge for parent predicate {required_fact.render()!r}')
        if len(required_fact.args) != 1 or required_fact.args[0] not in valid_samples:
            raise SourceBackedBridgeError('BAD_ARGUMENTS', f'PSR parent has an invalid sample identity: {required_fact.render()!r}')
    for index, step in enumerate(normalized):
        name = step['primitive']
        params = step['params']
        if name in {'sample_collect', 'sample_store'}:
            sample_id = params.get('sample_id')
            if not isinstance(sample_id, str) or sample_id not in valid_samples:
                raise SourceBackedBridgeError('BAD_ARGUMENTS', f'step {index} {name} has invalid sample_id {sample_id!r}')
        try:
            world.apply_structured_effects(name, actor_id, params)
        except Exception as exc:
            raise SourceBackedBridgeError('BAD_ARGUMENTS', f'PSR authoritative bridge rejected step {index}: {type(exc).__name__}: {exc}') from exc
    projected = tuple(sorted({_one_fact(value) for value in world.global_facts()}, key=lambda item: item.render()))
    missing = set(required_facts) - set(projected)
    if missing:
        raise SourceBackedBridgeError('GOAL_INCOMPLETE', f'PSR authoritative world bridge misses Task goal facts: {sorted((value.render() for value in missing))}')
    return PSRSourceBackedProjection(pre_projection_facts=pre_projection, projected_facts=projected, produced_tokens=produced_tokens, goal_tokens=goal_tokens)

def validate_D_T_output(task: Any, chain: List[Mapping[str, Any]], *, scenario: str, prompt_context: Any, initial_facts: Optional[Iterable[str]]=None, runtime_world: Optional[PSRWorld]=None) -> D_TValidationResult:
    """Validate corrected Task→Primitive output against the immutable Task postcondition.

    The parent contract is read exclusively from ``task.postcondition``.  Lava uses
    the parameterized symbolic registry.  PSR reuses the existing monotonic goal
    helpers and the approved ``apply_structured_effects`` -> ``global_facts`` bridge;
    it does not define a second effect registry.
    """
    if scenario == 'psr':
        from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
        if getattr(runtime_world, 'semantics_profile', None) == STRICT_PSR_PROFILE:
            from validation.contracts.psr_named import validate_strict_psr_task
            return validate_strict_psr_task(task, chain, prompt_context, runtime_world)
    if scenario == 'construction':
        from domains.worlds.construction_execution import ConstructionExecutionWorld
        if isinstance(runtime_world, ConstructionExecutionWorld):
            from execution.realization.construction_recovery import validate_construction_task
            return validate_construction_task(task, chain, prompt_context, runtime_world)
    if scenario == 'lava' and getattr(runtime_world, 'semantics_version', None) == 'lava_source_execution_v1':
        from execution.realization.lava_recovery import validate_lava_task
        return validate_lava_task(task, chain, prompt_context, runtime_world)
    if scenario not in {'lava', 'psr'}:
        return D_TValidationResult(False, 'failed', 'UNRESOLVED_SPEC', [f'corrected D_T validator has no source-backed registry for {scenario!r}'])
    parent = parse_predicate(getattr(task, 'postcondition', '') or '')
    if not parent.atomics:
        return D_TValidationResult(False, 'failed', 'GOAL_INCOMPLETE', ['Task.postcondition is empty'])
    required = {fact(atom.name, *atom.args) for atom in parent.atomics}
    state = getattr(prompt_context, 'observable_state', {}) or {}
    try:
        initial = {_one_fact(value) for value in state.get('facts', ())}
    except Exception as exc:
        return D_TValidationResult(False, 'failed', 'BAD_ARGUMENTS', [f'invalid initial fact: {exc}'])
    provenance = {str(key): str(value) for key, value in state.get('fact_provenance', {}).items()}
    unprovenanced = sorted((value.render() for value in initial if provenance.get(value.render()) != 'observed_initial'))
    if unprovenanced:
        return D_TValidationResult(False, 'failed', 'INITIAL_PROVENANCE_MISSING', [f'initial facts lack observed_initial provenance: {unprovenanced}'])
    if required <= initial:
        discharge = {value.render(): 'observed_initial' for value in required}
        return D_TValidationResult(False, 'not_applicable', 'NO_RECOVERY_OBLIGATION', ['Task postcondition is already fully discharged by observed initial facts'], goal_discharge=discharge, final_facts=set(initial))
    if scenario == 'psr':
        return _validate_psr_D_T_output(task, chain, prompt_context=prompt_context, required=required, observed_initial=initial, initial_facts=initial_facts, runtime_world=runtime_world)
    actors = tuple(getattr(prompt_context, 'agents', ()) or ())
    try:
        actor_types = trusted_agent_types_by_id(actors)
    except ActorBindingError as exc:
        return D_TValidationResult(False, 'failed', exc.reason_code, [str(exc)])
    actor_id = str(getattr(task, 'assigned_agent_id', '') or '')
    if actor_id not in actor_types:
        return D_TValidationResult(False, 'failed', 'AGENT_BINDING_MISMATCH', [f'Task actor {actor_id!r} is absent from the trusted roster'])
    actor_record = next((agent for agent in actors if isinstance(agent, Mapping) and agent.get('id') == actor_id), None)
    if actor_record is not None and 'capabilities' in actor_record:
        capabilities = set(actor_record.get('capabilities') or ())
        missing_capabilities = sorted((str(step.get('primitive', '')) for step in chain if str(step.get('primitive', '')) not in capabilities))
        if missing_capabilities:
            return D_TValidationResult(False, 'failed', 'AGENT_CONSTRAINT_FAIL', [f'Task actor lacks primitive capabilities: {missing_capabilities}'])
    bound_chain = [{'primitive': step.get('primitive', ''), 'params': dict(step.get('params', {}) or {}), 'agent_id': actor_id} for step in chain]
    legality = forward_chain_legality(bound_chain, LAVA_SYMBOLIC_PRIMITIVE_SPECS, initial_facts=initial, agent_types_by_id=actor_types)
    if not legality.accepted:
        return D_TValidationResult(False, 'failed', legality.reason_code, [legality.detail], final_facts=set(legality.final_facts), produced_facts=set(legality.produced_facts))
    final = set(legality.final_facts)
    produced = set(legality.produced_facts)
    missing = required - final
    if missing:
        return D_TValidationResult(False, 'failed', 'GOAL_INCOMPLETE', [f'candidate misses Task.postcondition facts: {sorted((x.render() for x in missing))}'], final_facts=final, produced_facts=produced)
    discharge: Dict[str, str] = {}
    for required_fact in required:
        if required_fact in produced:
            discharge[required_fact.render()] = 'candidate_entailed'
        elif required_fact in initial:
            discharge[required_fact.render()] = 'observed_initial'
    if not any((value == 'candidate_entailed' for value in discharge.values())):
        return D_TValidationResult(False, 'not_applicable', 'NO_RECOVERY_OBLIGATION', ['Task postcondition is already fully discharged by observed initial facts'], goal_discharge=discharge, final_facts=final, produced_facts=produced)
    return D_TValidationResult(True, 'valid', goal_discharge=discharge, final_facts=final, produced_facts=produced)

def _validate_psr_D_T_output(task: Any, chain: List[Mapping[str, Any]], *, prompt_context: Any, required: Set[Fact], observed_initial: Set[Fact], initial_facts: Optional[Iterable[str]], runtime_world: Optional[PSRWorld]) -> D_TValidationResult:
    """Source-backed PSR Task-goal check used by the corrected D_T path.

    The legacy helpers retain the approved token-level legality/achievement check.
    Parameter identity is then checked at the boundary of the existing PSRWorld bridge
    before the bridge is evaluated in strict-collect mode.  No predicate alias or
    duplicate effect table is introduced here.
    """
    from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
    if getattr(runtime_world, 'semantics_profile', None) == STRICT_PSR_PROFILE:
        from validation.contracts.psr_named import validate_strict_psr_task
        return validate_strict_psr_task(task, chain, prompt_context, runtime_world)
    actor_id = str(getattr(task, 'assigned_agent_id', '') or '')
    actors = tuple(getattr(prompt_context, 'agents', ()) or ())
    actor_record = next((agent for agent in actors if isinstance(agent, Mapping) and agent.get('id') == actor_id), None)
    if actors and actor_record is None:
        return D_TValidationResult(False, 'failed', 'AGENT_BINDING_MISMATCH', [f'Task actor {actor_id!r} is absent from the trusted roster'])
    if actor_record is not None and 'capabilities' in actor_record:
        capabilities = set(actor_record.get('capabilities') or ())
        missing_capabilities = sorted((str(step.get('primitive', '')) for step in chain if str(step.get('primitive', '')) not in capabilities))
        if missing_capabilities:
            return D_TValidationResult(False, 'failed', 'AGENT_CONSTRAINT_FAIL', [f'Task actor lacks primitive capabilities: {missing_capabilities}'])
    try:
        projection = project_psr_chain_to_facts(chain, actor_id=actor_id, required_goal_facts=required - observed_initial, initial_facts=initial_facts, n_samples=5, runtime_world=runtime_world)
    except SourceBackedBridgeError as exc:
        return D_TValidationResult(False, 'failed', exc.reason_code, [str(exc)])
    world_before = set(projection.pre_projection_facts)
    world_final = set(projection.projected_facts)
    produced = world_final - world_before
    final = set(observed_initial) | produced
    missing = required - final
    if missing:
        return D_TValidationResult(False, 'failed', 'GOAL_INCOMPLETE', [f'PSR authoritative world bridge misses Task goal facts: {sorted((value.render() for value in missing))}'], final_facts=final, produced_facts=produced)
    discharge = {value.render(): 'candidate_entailed' if value in produced else 'observed_initial' for value in required}
    if not any((value == 'candidate_entailed' for value in discharge.values())):
        return D_TValidationResult(False, 'not_applicable', 'NO_RECOVERY_OBLIGATION', ['Task postcondition is already fully discharged by observed initial facts'], goal_discharge=discharge, final_facts=final, produced_facts=produced)
    return D_TValidationResult(True, 'valid', goal_discharge=discharge, final_facts=final, produced_facts=produced)

def _combine(a: Conjunction, b: Conjunction) -> Conjunction:
    seen, atomics = (set(), [])
    for at in (*a.atomics, *b.atomics):
        if at.key() not in seen:
            seen.add(at.key())
            atomics.append(at)
    return Conjunction(tuple(atomics))

def _conj_all(preds: List[Conjunction]) -> Conjunction:
    out = Conjunction(())
    for p in preds:
        out = _combine(out, p)
    return out

def _topo_order(nodes: List[Any]) -> Optional[List[Any]]:
    """Kahn topo-sort by `.dependencies`; None if a cycle exists."""
    by_id = {n.id: n for n in nodes}
    indeg = {n.id: 0 for n in nodes}
    adj: Dict[str, List[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        for dep in getattr(n, 'dependencies', []):
            if dep in by_id:
                adj[dep].append(n.id)
                indeg[n.id] += 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    order = []
    while queue:
        u = queue.pop()
        order.append(by_id[u])
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return order if len(order) == len(nodes) else None

def _agent_field(agent: Any, *names: str) -> Any:
    for nm in names:
        if isinstance(agent, dict) and nm in agent:
            return agent[nm]
        if hasattr(agent, nm):
            return getattr(agent, nm)
    return None

def _seq_consistency(children: List[Any], initial: Conjunction, res: ValidationResult, layer: str) -> None:
    order = _topo_order(children)
    if order is None:
        res.reasons.append(f'{layer}: dependencies form a cycle')
        res.contract_violations.append('DAG')
        return
    accumulated = initial
    for c in order:
        pre = parse_predicate(c.precondition)
        if not entails(accumulated, pre):
            res.reasons.append(f'{layer}: {c.id} precondition ({pre}) not implied by initial state + prior postconditions')
            res.contract_violations.append('C2/C3')
        accumulated = _combine(accumulated, parse_predicate(c.postcondition))

def validate_D_S_output(d_s_output: List[Any], scene_goal: Any, environment: Optional[dict]=None, agents: Optional[List[Any]]=None) -> ValidationResult:
    """Validate a Scene→Mission decomposition (A2 §1.6)."""
    environment = environment or {}
    agents = agents or []
    res = ValidationResult(passed=True)
    if not d_s_output:
        return ValidationResult(False, ['D_S produced no missions'], ['empty'])
    goal = parse_predicate(getattr(scene_goal, 'formal_postcondition', '') or getattr(scene_goal, 'postcondition', ''))
    posts = _conj_all([parse_predicate(m.postcondition) for m in d_s_output])
    if not entails(posts, goal):
        res.reasons.append(f'Contract 1: mission postconditions do not jointly entail the Scene goal ({goal})')
        res.contract_violations.append('C1')
    initial = parse_predicate(environment.get('initial_state', ''))
    _seq_consistency(d_s_output, initial, res, 'D_S')
    avail_types = {str(_agent_field(a, 'type', 'agent_type')) for a in agents}
    for m in d_s_output:
        req = getattr(m, 'required_agent_types', []) or []
        if req and (not set(map(str, req)) & avail_types):
            res.reasons.append(f'Mission {m.id} requires agent types {req} but none available ({sorted(avail_types)})')
            res.contract_violations.append('agent')
    budget = environment.get('time_budget_s')
    if budget is not None:
        total = sum((float(getattr(m, 'estimated_duration_s', 0.0)) for m in d_s_output))
        if total > budget:
            res.reasons.append(f'total estimated duration {total}s exceeds budget {budget}s')
            res.contract_violations.append('budget')
    res.passed = not res.reasons
    return res

def validate_D_M_output(d_m_output: List[Any], mission: Any, current_state: Optional[dict]=None, agents: Optional[List[Any]]=None, scene_goal: Any=None) -> ValidationResult:
    """Validate a Mission→Task decomposition (A2 §2.4).

    `scene_goal` (optional Scene) widens the legitimate-entity set for the argument-validity
    check to include Scene-level goal entities, so a Task may legitimately reference a Scene
    goal sample its own Mission's postcondition does not restate (Bug-3 fix) — while still
    rejecting genuinely hallucinated ids."""
    current_state = current_state or {}
    agents = agents or []
    res = ValidationResult(passed=True)
    if not d_m_output:
        return ValidationResult(False, ['D_M produced no tasks'], ['empty'])
    posts = _conj_all([parse_predicate(t.postcondition) for t in d_m_output])
    mpost = parse_predicate(getattr(mission, 'postcondition', ''))
    if not entails(posts, mpost):
        res.reasons.append(f"Contract 1: task postconditions do not jointly entail Mission {getattr(mission, 'id', '?')} post ({mpost})")
        res.contract_violations.append('C1')
    initial = parse_predicate(current_state.get('facts_predicate', '')) if 'facts_predicate' in current_state else parse_predicate(getattr(mission, 'precondition', ''))
    _seq_consistency(d_m_output, initial, res, 'D_M')
    agent_ids = {str(_agent_field(a, 'id')) for a in agents}
    caps = {str(_agent_field(a, 'id')): set(_agent_field(a, 'capabilities', 'caps') or []) for a in agents}
    for t in d_m_output:
        aid = getattr(t, 'assigned_agent_id', None)
        if agent_ids and aid not in agent_ids:
            res.reasons.append(f'Task {t.id} assigned to unknown agent {aid!r}')
            res.contract_violations.append('agent')
        elif aid in caps and caps[aid]:
            missing = set(getattr(t, 'required_primitives', []) or []) - caps[aid]
            if missing:
                res.reasons.append(f"Task {t.id} needs primitives {sorted(missing)} outside agent {aid}'s capability set")
                res.contract_violations.append('capability')
    scene_pred = getattr(scene_goal, 'formal_postcondition', '') or getattr(scene_goal, 'postcondition', '') if scene_goal is not None else ''
    known = referenced_entities(getattr(mission, 'precondition', '')) | referenced_entities(getattr(mission, 'postcondition', '')) | referenced_entities(current_state.get('facts_predicate', '')) | referenced_entities(scene_pred) | agent_ids
    if known:
        for t in d_m_output:
            refs = referenced_entities(t.precondition) | referenced_entities(t.postcondition)
            unknown = sorted(refs - known)
            if unknown:
                res.reasons.append(f"Task {t.id} references unknown entit{('y' if len(unknown) == 1 else 'ies')} {unknown} (not in the Mission goal/state or agent roster)")
                res.contract_violations.append('argument')
    res.passed = not res.reasons
    return res

def render_feedback_for_llm(rejected_output: Any, validation_result: ValidationResult) -> str:
    """Render rejection reasons into compact LLM feedback (≤ ~200 tokens; A2 §1.7)."""
    if validation_result.passed:
        return ''
    lines = ['Your previous decomposition was rejected for these reasons:']
    for i, r in enumerate(validation_result.reasons, 1):
        lines.append(f'{i}. {r}')
    lines.append('Please revise to address each issue while still satisfying all original constraints. Common fixes: add an intermediate Mission/Task to bridge a precondition gap; reorder dependencies; reassign to a compatible agent.')
    text = '\n'.join(lines)
    return text[:1200]

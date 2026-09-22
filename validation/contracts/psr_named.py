"""Psr named."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
import math
from typing import Any
from typing import FrozenSet
from typing import Iterable
from typing import Mapping
from domains.actions.psr.tokens import PSR_PRIMITIVES
from domains.actions.spec import Fact
from domains.actions.spec import fact
from planning.repair import GeneratedCandidate
from planning.repair import RecoveryRequest
from domains.scenarios.psr import PSR_SCENARIO_SPEC
from domains.scenarios.spec import ArgumentSchema
from domains.scenarios.spec import EffectResolutionError
from domains.scenarios.spec import RecoveryContract
from domains.scenarios.spec import SymbolicEffect
from controller.scope import Scope
from models.routing import ClientTier
from models.routing import CommunicationState
from models.routing import PipelineStage
from models.routing import RoutingPolicy
from domains.worlds.psr_world import PSRWorld
from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
from domains.actions.psr.strict_profile import STRICT_NAMED_SEMANTICS
from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
from domains.actions.psr.strict_profile import require_psr_profile
from validation.contracts.recovery_gate import BAD_ARGUMENTS
from validation.contracts.recovery_gate import CONTRACT_NOT_ENTAILED
from validation.contracts.recovery_gate import UNRESOLVED_SPEC
from validation.contracts.recovery_gate import GateInput
from validation.contracts.recovery_gate import GateResult
from validation.contracts.recovery_gate import RecoveryGate
from validation.contracts.recovery_gate import normalize_candidate_step
from validation.contracts.recovery_gate import _normalize_facts
SEMANTICS_VERSION = 'psr_named_state_v1'
RUNTIME_SEMANTICS_VERSION = 'psr_named_state_v2'
STRICT_SEMANTICS_VERSION = STRICT_NAMED_SEMANTICS
SUPPORTED_ACTIONS = frozenset({'move_to', 'sample_collect', 'sample_store', 'return_to_base', 'dock_with_base', 'sample_offload'})

def _move_target_valid(value: Any) -> bool:
    return isinstance(value, str) and bool(value) or (isinstance(value, (list, tuple)) and len(value) in (2, 3) and all((isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v) for v in value)))
_SCHEMAS = dict(PSR_SCENARIO_SPEC.primitive_argument_schema)
_SCHEMAS['move_to'] = ArgumentSchema(required={'target': (str, list, tuple)}, validators={'target': _move_target_valid})
_NAMED_SPEC = replace(PSR_SCENARIO_SPEC, primitive_argument_schema=_SCHEMAS)
_RUNTIME_SPEC = replace(_NAMED_SPEC, base_contact_primitives=frozenset())
_RUNTIME_TOKEN_GOALS = frozenset({'scanned', 'energy_checked', 'status_sent', 'relay_linked', 'at_relay'})

def authorize_psr_message(step: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    """Use existing routing policy; relay is the immediate peer, not proof of forwarding.

    The offline world has no transport. Any future base-forwarding transport must
    additionally authorize its BASE hop; local status_sent never authorizes it.
    """
    name, args = (step['primitive'], step['params'])
    if name == 'communicate_status':
        tier = ClientTier.BASE if args.get('target') == 'base' else ClientTier.PEER
    elif name == 'communicate_relay':
        tier = ClientTier.PEER
    else:
        tier = ClientTier.LOCAL_EXECUTION
    RoutingPolicy().authorize(CommunicationState(policy.get('state', 'Connected')), PipelineStage.EXECUTION_COMMUNICATION, tier)

def _target_positions(world: PSRWorld) -> dict[str, tuple]:
    return {'base': tuple(world.base_pos), **{name: tuple(position) for name, position in world.relay_pos.items()}, **{name: tuple(s['location']) for name, s in world.samples.items()}}

def observed_named_facts(world: PSRWorld) -> FrozenSet[Fact]:
    """Read global storage plus actor-local tokens, cargo and actual positions."""
    values = set(_normalize_facts(world.global_facts()))
    targets = _target_positions(world)
    for actor_id, actor in world.agents.items():
        values.update((fact(token, actor_id) for token in actor['facts']))
        values.update((fact('cargo', actor_id, sid) for sid in actor['cargo']))
        values.update((fact('at_target', actor_id, name) for name, pos in targets.items() if (world.at_named_target(actor_id, name) if world.semantics_profile == STRICT_PSR_PROFILE else tuple(actor['position']) == pos)))
    values.update((fact('sample_status', sid, sample['status']) for sid, sample in world.samples.items()))
    if world.semantics_profile == STRICT_PSR_PROFILE:
        values.add(fact('psr_semantics_profile', STRICT_PSR_PROFILE))
        values.update((fact('unreachable', sid) for sid in world.injected_unreachable))
    return frozenset(values)

def validate_psr_entities(name: str, args: Mapping[str, Any], world: PSRWorld) -> None:
    """Entity-domain checks only: no cargo, action preconditions or task goals.

    Shared by named verification and the paired experiment's common safety boundary.
    Movement aliases are handled by the grounding layer before movement resolution.
    """
    if name in {'navigate_to_relay', 'communicate_relay'} and args.get('relay') not in world.relay_pos:
        raise EffectResolutionError(BAD_ARGUMENTS, f"unknown relay: {args.get('relay')!r}")
    if name == 'scan_spectral' and args.get('target') not in _target_positions(world):
        raise EffectResolutionError(BAD_ARGUMENTS, f"unknown scan target: {args.get('target')!r}")
    if name == 'communicate_status' and args.get('target') not in {'base', *world.agents}:
        raise EffectResolutionError(BAD_ARGUMENTS, f"unknown recipient: {args.get('target')!r}")
    if name in {'sample_collect', 'sample_store'} and args.get('sample_id') not in world.samples:
        raise EffectResolutionError(BAD_ARGUMENTS, f"unknown sample: {args.get('sample_id')!r}")
    if name == 'sample_offload':
        for key, value in args.items():
            if value != 'base':
                raise EffectResolutionError(BAD_ARGUMENTS, f'unknown {key}: {value!r}; expected base')

class _NamedEffects:
    """Fresh per evaluation; receives no goal, scope, labels or arm identity."""

    def __init__(self, world: PSRWorld, *, semantics_version: str=SEMANTICS_VERSION, communication_policy: Mapping[str, Any] | None=None):
        require_psr_profile(world, semantics_version)
        self.world = deepcopy(world)
        self.semantics_version = semantics_version
        self.communication_policy = dict(communication_policy or {'state': 'Connected'})

    def __call__(self, step: Mapping[str, Any], state: FrozenSet[Fact]) -> SymbolicEffect:
        require_psr_profile(self.world, self.semantics_version)
        name, args, actor_id = (step['primitive'], step['params'], step['agent_id'])
        runtime = self.semantics_version in {RUNTIME_SEMANTICS_VERSION, STRICT_SEMANTICS_VERSION}
        strict = self.semantics_version == STRICT_SEMANTICS_VERSION
        supported = PSR_PRIMITIVES if runtime else SUPPORTED_ACTIONS
        if name not in supported:
            raise EffectResolutionError(UNRESOLVED_SPEC, f'{SEMANTICS_VERSION} unsupported: {name}')
        world = self.world
        before = observed_named_facts(world)
        if state != before:
            raise EffectResolutionError(BAD_ARGUMENTS, 'effect context and current state differ')
        source = STRICT_TOKEN_SPECS[name] if strict else PSR_PRIMITIVES[name]
        requires = {fact(token, actor_id) for token in source.requires}
        validate_psr_entities(name, args, world)
        if runtime:
            try:
                authorize_psr_message(step, self.communication_policy)
            except Exception as exc:
                raise EffectResolutionError('COMM_POLICY_FAIL', str(exc)) from exc
        if name in {'sample_collect', 'sample_store'}:
            sid = args['sample_id']
            if name == 'sample_collect' and strict:
                requires.add(fact('at_target', actor_id, sid))
            if name == 'sample_store':
                requires.update({fact('cargo', actor_id, sid), fact('sample_status', sid, 'in_rover')})
        if requires - state:
            return SymbolicEffect(requires=frozenset(requires))
        failure = world.validate_structured_preconditions(name, actor_id, dict(args))
        if failure is not None:
            raise EffectResolutionError(CONTRACT_NOT_ENTAILED, failure[1])
        actor = world.agents[actor_id]
        if name in {'move_to', 'return_to_base', 'navigate_to_relay'}:
            target = world.resolve_target(name, dict(args), actor_id)
            if target is None:
                raise EffectResolutionError(BAD_ARGUMENTS, f'unknown or unreachable target: {args!r}')
            actor['position'] = target
        if strict:
            world.apply_structured_effects(name, actor_id, dict(args))
        actor['facts'].difference_update(source.clears)
        actor['facts'].update(source.produces)
        if not strict:
            world.apply_structured_effects(name, actor_id, dict(args))
        after = observed_named_facts(world)
        return SymbolicEffect(frozenset(requires), after - before, before - after)

@dataclass(frozen=True)
class NamedPSRValidation:
    candidate: GeneratedCandidate
    gate_result: GateResult
    initial_facts: FrozenSet[Fact]
    recovery_required: bool
    semantics_version: str = SEMANTICS_VERSION
    run_profile: str = 'offline_test'

    def to_trace_dict(self) -> dict[str, Any]:
        return {'semantics_version': self.semantics_version, 'run_profile': self.run_profile, 'normalized_plan': deepcopy(list(self.candidate.plan)), 'initial_facts': sorted((f.render() for f in self.initial_facts)), 'recovery_required': self.recovery_required, 'gate': self.gate_result.to_trace_dict()}

def evaluate_psr_named(candidate: GeneratedCandidate, *, world: PSRWorld, contract: RecoveryContract, scope: Scope, communication_policy: Mapping[str, Any] | None=None, resource_constraints: Mapping[str, Any] | None=None, semantics_version: str=SEMANTICS_VERSION, run_profile: str='offline_test') -> NamedPSRValidation:
    """Check a normalized plan without executing or mutating the observed world.

    Invalid task contracts raise ValueError; invalid candidates return gate rejection.
    Every step needs an explicit actor ID. Inputs use the source execution form
    ``move_to(target=sample_id | coordinates)`` and ``sample_offload(base='base')``
    (target='base' is also accepted). Other argument forms require caller grounding
    before this entry. Fully satisfied initial goals are maintenance, reported via
    recovery_required=False, never new recovery success.
    """
    if not isinstance(world, PSRWorld):
        raise ValueError('named PSR validation requires an observed PSRWorld')
    if semantics_version not in {SEMANTICS_VERSION, RUNTIME_SEMANTICS_VERSION, STRICT_SEMANTICS_VERSION}:
        raise ValueError(f'unsupported PSR semantics version: {semantics_version}')
    require_psr_profile(world, semantics_version)
    runtime = semantics_version in {RUNTIME_SEMANTICS_VERSION, STRICT_SEMANTICS_VERSION}
    if scope not in {Scope.P, Scope.T, Scope.M}:
        raise ValueError('named PSR validation supports P/T/M only')
    required = _normalize_facts(contract.required_goal_facts)
    targets = _target_positions(world)
    if not required:
        raise ValueError('named contract requires a nonempty goal')
    for goal in required:
        valid = goal.predicate == 'at_target' and len(goal.args) == 2 and (goal.args[0] in world.agents) and (goal.args[1] in targets) or (goal.predicate in {'stored', 'in_base_storage'} and len(goal.args) == 1 and (goal.args[0] in world.samples)) or (runtime and goal.predicate in _RUNTIME_TOKEN_GOALS and (len(goal.args) == 1) and (goal.args[0] in world.agents))
        if not valid:
            raise ValueError(f'unknown entity or unsupported named goal: {goal.render()}')
    initial = observed_named_facts(world)
    normalized = tuple((normalize_candidate_step(step) for step in candidate.plan))
    for step in normalized:
        if step is not None and isinstance(step['agent_id'], str):
            step['agent_id'] = step['agent_id'].strip()
    bound_candidate = replace(candidate, plan=deepcopy(normalized))
    request = RecoveryRequest(request_id=f'{candidate.candidate_id}:named', scenario_id='psr', observable_failure={}, symbolic_state={'facts': initial, 'fact_provenance': {f.render(): 'observed_initial' for f in initial}}, available_agents=tuple((deepcopy(actor) for actor in world.agents.values())), resource_constraints=dict(resource_constraints or {}), communication_policy=dict(communication_policy or {'state': 'Connected'}), parent_contract={'contract_id': contract.contract_id, 'required_primitives': sorted(contract.required_primitives), 'required_goal_facts': sorted(contract.required_goal_facts)})
    result = RecoveryGate().evaluate(GateInput(bound_candidate, scope, request, _RUNTIME_SPEC if runtime else _NAMED_SPEC, run_profile, contextual_effect_builder=_NamedEffects(world, semantics_version=semantics_version, communication_policy=communication_policy), allow_empty_maintenance=runtime and required <= initial))
    return NamedPSRValidation(bound_candidate, result, initial, bool(required - initial), semantics_version, run_profile)

def validate_strict_psr_task(task, chain, prompt_context, runtime_world):
    """Opt-in corrected D_T bridge using the strict named projector and gate.

    The old token-only projection helper is deliberately not reused. Child actor
    assignment and observed initial preconditions are obligations of this Task;
    a newly decomposed/reassigned Task must explicitly carry its new assignment.
    """
    from domains.predicates import parse_predicate
    from validation.contracts.contracts import D_TValidationResult
    try:
        require_psr_profile(runtime_world, STRICT_SEMANTICS_VERSION)
        terms = parse_predicate(task.postcondition).atomics
        if any((term.negated for term in terms)):
            raise ValueError('strict named Task bridge supports positive goal conjunctions')
        goal = frozenset((fact(term.name, *term.args).render() for term in terms))
        initial = observed_named_facts(runtime_world)
        preconditions = parse_predicate(getattr(task, 'precondition', '') or '').atomics
        if any((term.negated or fact(term.name, *term.args) not in initial for term in preconditions)):
            return D_TValidationResult(False, 'invalid', 'PRECONDITION_UNMET', ['Task precondition not observed'])
        normalized = []
        for raw in chain:
            value = normalize_candidate_step(raw)
            if value is None:
                raise ValueError('Task primitive is malformed')
            value['agent_id'] = value['agent_id'] or task.assigned_agent_id
            if value['agent_id'] != task.assigned_agent_id:
                return D_TValidationResult(False, 'invalid', 'AGENT_BINDING_MISMATCH', ['Task actor changed'])
            normalized.append(value)
        candidate = GeneratedCandidate(f'{task.id}:strict', tuple(normalized), 'provided_task_chain', False, 'no-model-call')
        contract = RecoveryContract(f'{task.id}:strict', frozenset(getattr(task, 'required_primitives', ())), goal)
        verdict = evaluate_psr_named(candidate, world=runtime_world, contract=contract, scope=Scope.T, semantics_version=STRICT_SEMANTICS_VERSION, communication_policy={'state': getattr(prompt_context, 'communication_state', 'Connected')}, resource_constraints=getattr(prompt_context, 'resource_constraints', None)).gate_result
        return D_TValidationResult(verdict.accepted, 'valid' if verdict.accepted else 'invalid', '' if verdict.accepted else verdict.reason_codes[0], [] if verdict.accepted else [verdict.detail], final_facts=set(verdict.final_facts), produced_facts=set(verdict.produced_facts))
    except (ValueError, TypeError, KeyError) as exc:
        return D_TValidationResult(False, 'invalid', 'BAD_ARGUMENTS', [str(exc)])

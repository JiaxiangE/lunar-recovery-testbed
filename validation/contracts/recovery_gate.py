"""Independent recovery-contract gate for corrected candidates."""
from __future__ import annotations
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from domains.actions.psr.tokens import PSR_PRIMITIVES as PSR_SYMBOLIC_SPECS
from domains.predicates import parse_predicate
from domains.actions.construction import CONSTRUCTION_PRIMITIVE_SPECS
from domains.actions.lava import LAVA_SYMBOLIC_PRIMITIVE_SPECS
from domains.actions.spec import ActorBindingError
from domains.actions.spec import Fact
from domains.actions.spec import bind_resolution_context
from domains.actions.spec import fact
from domains.actions.spec import resolve_primitive
from domains.actions.spec import trusted_agent_types_by_id
from planning.repair import GateVerdict
from planning.repair import GeneratedCandidate
from planning.repair import RecoveryRequest
from models.routing import ClientTier
from models.routing import CommunicationState
from models.routing import PipelineStage
from models.routing import RoutingPolicy
from domains.scenarios import RecoveryContract
from domains.scenarios import ScenarioSpec
from domains.scenarios.spec import ContextualEffectBuilder
from domains.scenarios.spec import EffectResolutionError
from domains.scenarios.construction import get_construction_cardinality_contract
from controller.scope import Scope
from validation.contracts.cardinality import CARDINALITY_FAIL
from validation.contracts.cardinality import CardinalityCertificate
from validation.contracts.cardinality import CardinalityCheckResult
from validation.contracts.cardinality import check_construction_cardinality
from validation.contracts.z3_backend import Z3BackendError
from validation.contracts.z3_backend import Z3EntailmentResult
from validation.contracts.z3_backend import build_conjunction
from validation.contracts.z3_backend import check_entailment
from validation.contracts.gate_capability import TRUE_CONTRACT_GATE_KIND
from validation.contracts.effect_projection import CandidateEffectProjection
from validation.contracts.effect_projection import CandidateEffectProjector
from validation.contracts.effect_projection import EffectProjectorCapabilityError
from validation.contracts.effect_projection import require_source_backed_effect_projector
EMPTY_CANDIDATE = 'EMPTY_CANDIDATE'
OFF_VOCAB = 'OFF_VOCAB'
BAD_ARGUMENTS = 'BAD_ARGUMENTS'
GOAL_INCOMPLETE = 'GOAL_INCOMPLETE'
CONTRACT_NOT_ENTAILED = 'CONTRACT_NOT_ENTAILED'
COMM_POLICY_FAIL = 'COMM_POLICY_FAIL'
SOLVER_ERROR = 'SOLVER_ERROR'
UNRESOLVED_SPEC = 'UNRESOLVED_SPEC'
AGENT_CONSTRAINT_FAIL = 'AGENT_CONSTRAINT_FAIL'
RESOURCE_CONSTRAINT_FAIL = 'RESOURCE_CONSTRAINT_FAIL'
INITIAL_PROVENANCE_MISSING = 'INITIAL_PROVENANCE_MISSING'

@dataclass(frozen=True)
class GateInput:
    candidate: GeneratedCandidate
    attempted_scope: Scope
    recovery_request: RecoveryRequest
    scenario_spec: ScenarioSpec
    run_profile: str = 'formal'
    z3_module: Any = None
    solver_factory: Any = None
    effect_projector: Optional[CandidateEffectProjector] = None
    contextual_effect_builder: Optional[ContextualEffectBuilder] = None
    allow_empty_maintenance: bool = False

@dataclass(frozen=True)
class GateResult:
    candidate_id: str
    accepted: bool
    reason_codes: Tuple[str, ...]
    structural_valid: bool
    vocabulary_valid: bool
    arguments_valid: bool
    effects_valid: bool
    goal_complete: bool
    entailment_holds: bool
    cardinality_valid: Optional[bool]
    agent_constraints_valid: bool
    communication_valid: bool
    solver_status: Optional[str]
    solver_result: Optional[str]
    solver_latency_s: float
    gate_latency_s: float
    contract_id: Optional[str]
    goal_discharge: Mapping[str, str] = field(default_factory=dict)
    final_facts: FrozenSet[Fact] = frozenset()
    produced_facts: FrozenSet[Fact] = frozenset()
    detail: str = ''
    cardinality_certificate: Optional[CardinalityCertificate] = None
    effect_projection_id: Optional[str] = None
    effect_projection_input_state_hash: Optional[str] = None
    effect_projection_sources: Tuple[str, ...] = ()
    effect_projected_facts: FrozenSet[Fact] = frozenset()
    solver_version: Optional[str] = None

    def to_trace_dict(self) -> Dict[str, Any]:
        """Return the complete candidate-bound verdict as JSON-safe trace evidence.

        Facts are rendered canonically instead of serializing Python dataclass/set
        internals.  The full cardinality certificate is retained for Construction,
        including its solver input and timing, so an earlier rejected attempt is
        not overwritten when the same gate instance evaluates a later scope.
        """
        cardinality = asdict(self.cardinality_certificate) if self.cardinality_certificate is not None else None
        return {'gate_kind': TRUE_CONTRACT_GATE_KIND, 'candidate_id': self.candidate_id, 'accepted': self.accepted, 'reason_codes': list(self.reason_codes), 'checks': {'structural_valid': self.structural_valid, 'vocabulary_valid': self.vocabulary_valid, 'arguments_valid': self.arguments_valid, 'effects_valid': self.effects_valid, 'goal_complete': self.goal_complete, 'entailment_holds': self.entailment_holds, 'cardinality_valid': self.cardinality_valid, 'agent_constraints_valid': self.agent_constraints_valid, 'communication_valid': self.communication_valid}, 'solver': {'status': self.solver_status, 'result': self.solver_result, 'latency_s': self.solver_latency_s, 'solver_version': self.solver_version}, 'gate_latency_s': self.gate_latency_s, 'contract_id': self.contract_id, 'goal_discharge': dict(self.goal_discharge), 'final_facts': sorted((value.render() for value in self.final_facts)), 'produced_facts': sorted((value.render() for value in self.produced_facts)), 'detail': self.detail, 'cardinality_certificate': cardinality, 'effect_projection': {'projector_id': self.effect_projection_id, 'input_state_hash': self.effect_projection_input_state_hash, 'source_references': list(self.effect_projection_sources), 'projected_facts': sorted((value.render() for value in self.effect_projected_facts)), 'classified_as': 'candidate_entailed'} if self.effect_projection_id is not None else None}

def _result(gate_input: GateInput, started: float, reason: str, detail: str, *, structural: bool=False, vocabulary: bool=False, arguments: bool=False, effects: bool=False, goal: bool=False, entailment: bool=False, agent: bool=False, communication: bool=False, solver: Optional[Z3EntailmentResult]=None, cardinality: Optional[CardinalityCheckResult]=None, discharge: Optional[Mapping[str, str]]=None, final_facts: FrozenSet[Fact]=frozenset(), produced_facts: FrozenSet[Fact]=frozenset(), effect_projection: Optional[CandidateEffectProjection]=None) -> GateResult:
    cardinality_certificate = cardinality.certificate if cardinality else None
    cardinality_discharge = dict(cardinality_certificate.goal_discharge) if cardinality_certificate is not None else {}
    cardinality_discharge.update(discharge or {})
    cardinality_solver_status = cardinality_certificate.solver_status if cardinality_certificate is not None else None
    cardinality_solver_latency = cardinality_certificate.total_latency_s if cardinality_certificate is not None else 0.0
    return GateResult(candidate_id=gate_input.candidate.candidate_id, accepted=False, reason_codes=(reason,), structural_valid=structural, vocabulary_valid=vocabulary, arguments_valid=arguments, effects_valid=effects, goal_complete=goal, entailment_holds=entailment, cardinality_valid=cardinality.cardinality_holds if cardinality else None, agent_constraints_valid=agent, communication_valid=communication, solver_status=solver.status if solver else cardinality_solver_status, solver_result=solver.status if solver else cardinality_solver_status, solver_latency_s=(solver.total_latency_s if solver else 0.0) + cardinality_solver_latency, gate_latency_s=time.perf_counter() - started, contract_id=str(gate_input.recovery_request.parent_contract.get('contract_id') or '') or None, goal_discharge=cardinality_discharge, final_facts=final_facts, produced_facts=produced_facts, detail=detail, cardinality_certificate=cardinality_certificate, effect_projection_id=effect_projection.projector_id if effect_projection else None, effect_projection_input_state_hash=effect_projection.input_state_hash if effect_projection else None, effect_projection_sources=effect_projection.source_references if effect_projection else (), effect_projected_facts=effect_projection.projected_facts if effect_projection else frozenset(), solver_version=solver.solver_version if solver else cardinality_certificate.solver_version if cardinality_certificate else None)

def normalize_candidate_step(step: Any) -> Optional[Dict[str, Any]]:
    if isinstance(step, Mapping):
        name = step.get('primitive', step.get('primitive_name', step.get('name', '')))
        params = step.get('params', step.get('primitive_args', step.get('args', {})))
        return {'primitive': str(name or ''), 'params': dict(params) if isinstance(params, Mapping) else params, 'agent_id': step.get('agent_id'), 'agent_type': step.get('agent_type')}
    if isinstance(step, (tuple, list)) and len(step) >= 2:
        return {'primitive': str(step[0]), 'params': dict(step[1]) if isinstance(step[1], Mapping) else step[1], 'agent_id': None, 'agent_type': str(step[2]) if len(step) >= 3 else None}
    name = getattr(step, 'primitive_name', '')
    if name:
        return {'primitive': str(name), 'params': dict(getattr(step, 'primitive_args', {}) or {}), 'agent_id': getattr(step, 'assigned_agent_id', None), 'agent_type': None}
    return None

def _fact_from_value(value: Any) -> Fact:
    if isinstance(value, Fact):
        return value
    if isinstance(value, str):
        parsed = parse_predicate(value)
        if len(parsed.atomics) != 1 or parsed.atomics[0].negated:
            raise ValueError(f'expected one positive fact, got {value!r}')
        atom = parsed.atomics[0]
        return fact(atom.name, *atom.args)
    raise TypeError(f'unsupported fact value: {value!r}')

def _normalize_facts(values: Any) -> FrozenSet[Fact]:
    if values is None:
        return frozenset()
    if isinstance(values, (str, Fact)):
        values = (values,)
    return frozenset((_fact_from_value(value) for value in values))

def _render_facts(values: Sequence[Fact] | FrozenSet[Fact]) -> list[str]:
    return [value.render() for value in values]

def _available_agent_type(step: Mapping[str, Any], request: RecoveryRequest, agent_types_by_id: Mapping[str, str]) -> Optional[str]:
    available = list(request.available_agents)
    requested_id = step.get('agent_id')
    if requested_id is not None:
        return agent_types_by_id.get(str(requested_id))
    requested_type = step.get('agent_type')
    if requested_type is not None:
        normalized = str(requested_type).upper()
        return normalized if any((str(agent.get('type', agent.get('agent_type', ''))).upper() == normalized for agent in available)) else None
    failure_agent = request.observable_failure.get('agent_id')
    if failure_agent:
        return agent_types_by_id.get(str(failure_agent))
    return None

def _specific_registry(scenario_id: str) -> Mapping[str, Any]:
    if scenario_id == 'lava':
        return LAVA_SYMBOLIC_PRIMITIVE_SPECS
    if scenario_id == 'construction':
        return CONSTRUCTION_PRIMITIVE_SPECS
    return {}

def _allowed_agent_types(scenario_id: str, primitive_name: str) -> FrozenSet[str]:
    specific = _specific_registry(scenario_id).get(primitive_name)
    if specific is not None:
        return specific.allowed_agent_types
    psr = PSR_SYMBOLIC_SPECS.get(primitive_name)
    return frozenset((str(value).upper() for value in psr.agent_types)) if psr else frozenset()

class RecoveryGate:
    """Conjunctive true gate; candidate-declared postconditions are ignored."""

    def evaluate(self, gate_input: GateInput) -> GateResult:
        started = time.perf_counter()
        candidate = gate_input.candidate
        request = gate_input.recovery_request
        scenario = gate_input.scenario_spec
        if gate_input.run_profile not in {'formal', 'offline_test', 'demo'}:
            raise ValueError(f'unknown gate profile: {gate_input.run_profile!r}')
        effect_projector = gate_input.effect_projector
        if effect_projector is not None and gate_input.contextual_effect_builder is not None:
            return _result(gate_input, started, BAD_ARGUMENTS, 'choose one effect interpretation path')
        if effect_projector is not None:
            try:
                effect_projector = require_source_backed_effect_projector(effect_projector, gate_input.run_profile)
            except EffectProjectorCapabilityError as exc:
                return _result(gate_input, started, UNRESOLVED_SPEC, f'uncertified effect projector: {exc}')
        if request.scenario_id != scenario.scenario_id:
            return _result(gate_input, started, OFF_VOCAB, 'scenario/request mismatch')
        if not candidate.candidate_id or not isinstance(candidate.plan, tuple):
            return _result(gate_input, started, EMPTY_CANDIDATE, 'malformed candidate')
        if not candidate.plan and (not gate_input.allow_empty_maintenance):
            return _result(gate_input, started, EMPTY_CANDIDATE, 'candidate is empty')
        steps = [normalize_candidate_step(step) for step in candidate.plan]
        if any((step is None or not step['primitive'] for step in steps)):
            return _result(gate_input, started, EMPTY_CANDIDATE, 'malformed candidate step')
        normalized = [step for step in steps if step is not None]
        names = [step['primitive'] for step in normalized]
        off_vocab = [name for name in names if name not in scenario.allowed_primitives]
        if off_vocab:
            return _result(gate_input, started, OFF_VOCAB, f'off-vocabulary primitive(s): {off_vocab}', structural=True)
        structural_contract = RecoveryContract(contract_id=str(request.parent_contract.get('contract_id') or 'observable_contract'), required_primitives=frozenset(request.parent_contract.get('required_primitives', ())))
        structural_verdict = scenario.validate_candidate(normalized, structural_contract)
        if not normalized and gate_input.allow_empty_maintenance and (not structural_contract.required_primitives):
            from domains.scenarios.spec import CandidateValidation
            structural_verdict = CandidateValidation(True)
        if not structural_verdict.accepted:
            reason = structural_verdict.reason_codes[0]
            return _result(gate_input, started, reason, structural_verdict.detail, structural=True, vocabulary=True)
        try:
            initial = _normalize_facts(request.symbolic_state.get('facts', ()))
        except Exception as exc:
            return _result(gate_input, started, BAD_ARGUMENTS, f'invalid symbolic initial facts: {exc}', structural=True, vocabulary=True, arguments=True)
        provenance = {str(key): str(value) for key, value in request.symbolic_state.get('fact_provenance', {}).items()}
        unprovenanced = sorted((value.render() for value in initial if provenance.get(value.render()) != 'observed_initial'))
        if unprovenanced:
            return _result(gate_input, started, INITIAL_PROVENANCE_MISSING, f'initial fact(s) lack observed_initial provenance: {unprovenanced}', structural=True, vocabulary=True, arguments=True, final_facts=initial)
        state = set(initial)
        produced: set[Fact] = set()
        specific_registry = _specific_registry(scenario.scenario_id)
        try:
            agent_types_by_id = trusted_agent_types_by_id(request.available_agents)
        except ActorBindingError as exc:
            return _result(gate_input, started, AGENT_CONSTRAINT_FAIL, str(exc), structural=True, vocabulary=True, arguments=True, final_facts=frozenset(state))
        agents_by_id = {str(agent.get('id')): agent for agent in request.available_agents if isinstance(agent, Mapping)}
        unavailable = set(request.resource_constraints.get('unavailable_primitives', ()))
        blocked = sorted(set(names) & unavailable)
        if blocked:
            return _result(gate_input, started, RESOURCE_CONSTRAINT_FAIL, f'runtime-unavailable primitive(s): {blocked}', structural=True, vocabulary=True, arguments=True)
        comm_state = request.communication_policy.get('state', 'Connected')
        try:
            RoutingPolicy().authorize(CommunicationState(comm_state), PipelineStage.VERIFICATION, ClientTier.LOCAL_SOLVER)
        except Exception as exc:
            return _result(gate_input, started, COMM_POLICY_FAIL, str(exc), structural=True, vocabulary=True, arguments=True)
        if comm_state == CommunicationState.DISCONNECTED.value:
            forbidden_contacts = sorted(set(names) & set(scenario.base_contact_primitives))
            if forbidden_contacts:
                return _result(gate_input, started, COMM_POLICY_FAIL, f'base-contact primitive under Disconnected: {forbidden_contacts}', structural=True, vocabulary=True, arguments=True, communication=False)
        for index, step in enumerate(normalized):
            name = step['primitive']
            params = step['params']
            specific = specific_registry.get(name)
            resolution_context = None
            if specific is not None and specific.actor_sensitive or step.get('agent_id') is not None or gate_input.contextual_effect_builder is not None:
                try:
                    resolution_context = bind_resolution_context(step, agent_types_by_id)
                except ActorBindingError as exc:
                    return _result(gate_input, started, AGENT_CONSTRAINT_FAIL, f'step {index} ({name}): {exc}', structural=True, vocabulary=True, arguments=True, communication=True)
                actor_type = resolution_context.agent_type
                actor_id = resolution_context.actor_id
            else:
                actor_type = _available_agent_type(step, request, agent_types_by_id)
                actor_id = step.get('agent_id') or request.observable_failure.get('agent_id')
            allowed_agents = _allowed_agent_types(scenario.scenario_id, name)
            if actor_type is None or actor_type not in allowed_agents:
                return _result(gate_input, started, AGENT_CONSTRAINT_FAIL, f'step {index} ({name}) has no authorized runtime actor', structural=True, vocabulary=True, arguments=True, communication=True)
            actor_record = agents_by_id.get(str(actor_id)) if actor_id is not None else None
            if actor_record is not None and actor_record.get('available', True) is not True:
                return _result(gate_input, started, AGENT_CONSTRAINT_FAIL, f'step {index} ({name}) actor {actor_id!r} is unavailable', structural=True, vocabulary=True, arguments=True, communication=True)
            if actor_record is not None and 'capabilities' in actor_record:
                capabilities = set(actor_record.get('capabilities') or ())
                if name not in capabilities:
                    return _result(gate_input, started, AGENT_CONSTRAINT_FAIL, f'step {index} ({name}) is outside actor {actor_id!r} capabilities', structural=True, vocabulary=True, arguments=True, communication=True)
            try:
                if gate_input.contextual_effect_builder is not None:
                    effect = gate_input.contextual_effect_builder({**step, 'agent_id': actor_id, 'agent_type': actor_type}, frozenset(state))
                    requires = {_fact_from_value(value) for value in effect.requires}
                    adds = {_fact_from_value(value) for value in effect.produces}
                    clears = {_fact_from_value(value) for value in effect.clears}
                elif specific is not None:
                    resolved = resolve_primitive(specific, params, actor_type, resolution_context=resolution_context)
                    if not resolved.accepted or resolved.resolved is None:
                        code = resolved.reason_code or UNRESOLVED_SPEC
                        return _result(gate_input, started, code, resolved.detail, structural=True, vocabulary=True, arguments=True, communication=True)
                    requires = set(resolved.resolved.requires)
                    adds = set(resolved.resolved.produces)
                    clears = set(resolved.resolved.clears)
                else:
                    builder = scenario.primitive_symbolic_effects.get(name)
                    if builder is None:
                        return _result(gate_input, started, UNRESOLVED_SPEC, f'no authoritative effects for {name}', structural=True, vocabulary=True, arguments=True, communication=True)
                    effect = builder(params)
                    requires = {_fact_from_value(value) for value in effect.requires}
                    adds = {_fact_from_value(value) for value in effect.produces}
                    clears = {_fact_from_value(value) for value in effect.clears}
            except Exception as exc:
                return _result(gate_input, started, exc.reason_code if isinstance(exc, EffectResolutionError) else UNRESOLVED_SPEC, f'effect resolution failed at step {index}: {type(exc).__name__}: {exc}', structural=True, vocabulary=True, arguments=True, communication=True, final_facts=frozenset(state), produced_facts=frozenset(produced))
            missing = requires - state
            if missing:
                return _result(gate_input, started, CONTRACT_NOT_ENTAILED, f'step {index} unmet precondition(s): {sorted((x.render() for x in missing))}', structural=True, vocabulary=True, arguments=True, communication=True, final_facts=frozenset(state), produced_facts=frozenset(produced))
            state.difference_update(clears)
            state.update(adds)
            produced.update(adds)
        effect_projection: Optional[CandidateEffectProjection] = None
        if effect_projector is not None:
            projector = effect_projector
            try:
                effect_projection = projector.project(candidate)
                if not isinstance(effect_projection, CandidateEffectProjection):
                    raise TypeError('projector returned the wrong result type')
                effect_projection.validate()
                if effect_projection.candidate_id != candidate.candidate_id:
                    raise ValueError('projector/candidate ID mismatch')
                if effect_projection.scenario_id != scenario.scenario_id:
                    raise ValueError('projector/scenario mismatch')
                if projector.projector_id != effect_projection.projector_id:
                    raise ValueError('projector identity mismatch')
                request_state_hash = request.symbolic_state.get('source_observation_hash')
                if effect_projection.input_state_hash != request_state_hash:
                    raise ValueError('projector/request input state hash mismatch')
            except Exception as exc:
                reason = str(getattr(exc, 'reason_code', '') or UNRESOLVED_SPEC)
                return _result(gate_input, started, reason, f'source-backed effect projection failed: {type(exc).__name__}: {exc}', structural=True, vocabulary=True, arguments=True, effects=False, agent=True, communication=True, final_facts=frozenset(state), produced_facts=frozenset(produced))
            state.update(effect_projection.projected_facts)
            produced.update(effect_projection.projected_facts)
        contract_id = str(request.parent_contract.get('contract_id') or '')
        cardinality_contract = get_construction_cardinality_contract(contract_id) if scenario.scenario_id == 'construction' else None
        cardinality: Optional[CardinalityCheckResult] = None
        if cardinality_contract is not None:
            cardinality = check_construction_cardinality(contract_id, frozenset(produced), initial, z3_module=gate_input.z3_module, solver_factory=gate_input.solver_factory)
            if not cardinality.accepted:
                return _result(gate_input, started, cardinality.reason_code, cardinality.detail, structural=True, vocabulary=True, arguments=cardinality.reason_code != BAD_ARGUMENTS, effects=True, goal=False, entailment=False, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, final_facts=frozenset(state), produced_facts=frozenset(produced))
        required_raw = request.parent_contract.get('required_goal_facts', ())
        if cardinality_contract is not None:
            if isinstance(required_raw, (str, Fact)):
                required_raw = (required_raw,)
            required_raw = tuple(required_raw or ()) + tuple(cardinality_contract.required_observed_initial_facts)
        if not required_raw:
            return _result(gate_input, started, GOAL_INCOMPLETE, 'parent contract has no machine-readable required_goal_facts', structural=True, vocabulary=True, arguments=True, effects=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, final_facts=frozenset(state), produced_facts=frozenset(produced))
        try:
            required = _normalize_facts(required_raw)
        except Exception as exc:
            return _result(gate_input, started, BAD_ARGUMENTS, f'invalid parent goal facts: {exc}', structural=True, vocabulary=True, arguments=True, effects=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, final_facts=frozenset(state), produced_facts=frozenset(produced))
        if cardinality_contract is not None:
            surrogate_goals = sorted((value.render() for value in required if value.predicate in {'panels_installed_ge_5', 'panels_installed_ge_8'}))
            if surrogate_goals:
                return _result(gate_input, started, BAD_ARGUMENTS, f'surrogate cardinality goal(s) forbidden: {surrogate_goals}', structural=True, vocabulary=True, arguments=False, effects=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, final_facts=frozenset(state), produced_facts=frozenset(produced))
        missing_goal = required - state
        cleared_goal = (required & initial) - state
        if missing_goal:
            reason = CONTRACT_NOT_ENTAILED if cleared_goal else GOAL_INCOMPLETE
            return _result(gate_input, started, reason, f'missing goal fact(s): {sorted((x.render() for x in missing_goal))}', structural=True, vocabulary=True, arguments=True, effects=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, final_facts=frozenset(state), produced_facts=frozenset(produced))
        discharge: Dict[str, str] = dict(cardinality.certificate.goal_discharge) if cardinality is not None and cardinality.certificate is not None else {}
        for goal_fact in required:
            rendered = goal_fact.render()
            if goal_fact in produced and goal_fact in state:
                discharge[rendered] = 'candidate_entailed'
            elif goal_fact in initial:
                if provenance.get(rendered) != 'observed_initial':
                    return _result(gate_input, started, INITIAL_PROVENANCE_MISSING, f'initial goal fact lacks observed_initial provenance: {rendered}', structural=True, vocabulary=True, arguments=True, effects=True, goal=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, discharge=discharge, final_facts=frozenset(state), produced_facts=frozenset(produced))
                discharge[rendered] = 'observed_initial'
        children = build_conjunction(_render_facts(frozenset(state)))
        parent = build_conjunction(_render_facts(required))
        try:
            solver = check_entailment(children, parent, z3_module=gate_input.z3_module, solver_factory=gate_input.solver_factory)
        except Z3BackendError as exc:
            return _result(gate_input, started, SOLVER_ERROR, str(exc), structural=True, vocabulary=True, arguments=True, effects=True, goal=True, agent=True, communication=True, cardinality=cardinality, effect_projection=effect_projection, discharge=discharge, final_facts=frozenset(state), produced_facts=frozenset(produced))
        if not solver.entailment_holds:
            return _result(gate_input, started, CONTRACT_NOT_ENTAILED, 'Z3 did not certify the parent contract', structural=True, vocabulary=True, arguments=True, effects=True, goal=True, agent=True, communication=True, solver=solver, cardinality=cardinality, effect_projection=effect_projection, discharge=discharge, final_facts=frozenset(state), produced_facts=frozenset(produced))
        return GateResult(candidate_id=candidate.candidate_id, accepted=True, reason_codes=(), structural_valid=True, vocabulary_valid=True, arguments_valid=True, effects_valid=True, goal_complete=True, entailment_holds=True, cardinality_valid=cardinality.cardinality_holds if cardinality else None, agent_constraints_valid=True, communication_valid=True, solver_status=solver.status, solver_result=solver.status, solver_latency_s=solver.total_latency_s + (cardinality.certificate.total_latency_s if cardinality is not None and cardinality.certificate is not None else 0.0), gate_latency_s=time.perf_counter() - started, contract_id=str(request.parent_contract.get('contract_id') or '') or None, goal_discharge=discharge, final_facts=frozenset(state), produced_facts=frozenset(produced), cardinality_certificate=cardinality.certificate if cardinality else None, effect_projection_id=effect_projection.projector_id if effect_projection else None, effect_projection_input_state_hash=effect_projection.input_state_hash if effect_projection else None, effect_projection_sources=effect_projection.source_references if effect_projection else (), effect_projected_facts=effect_projection.projected_facts if effect_projection else frozenset(), solver_version=solver.solver_version)

"""E03 observed-world Task validation and fixed-leaf Construction execution.

This explicit assembly version supports two source primitives, not the whole
Construction vocabulary. Frozen cardinality certification remains candidate-only:
cardinality recovery here requires an assembly-before-installation observation.
Named Task goals can be discharged by observed initial facts without being
misreported as new recovery. Transport/circuit construction remains out of scope.
"""
from copy import deepcopy
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from domains.actions.spec import _freeze_identity
from planning.repair import GeneratedCandidate
from planning.repair import RecoveryRequest
from domains.scenarios.construction import CONSTRUCTION_SCENARIO_SPEC
from domains.scenarios.spec import EffectResolutionError
from domains.scenarios.spec import SymbolicEffect
from controller.scope import Scope
from validation.contracts.recovery_gate import GateInput
from validation.contracts.recovery_gate import RecoveryGate
from validation.contracts.recovery_gate import _normalize_facts
from validation.contracts.recovery_gate import normalize_candidate_step
from domains.worlds.construction_execution import ConstructionExecutionWorld
from domains.worlds.construction_execution import SUPPORTED_ACTIONS
from domains.worlds.construction_execution import VERSION
from execution.realization.psr_recovery import BoundStep
_SPEC = replace(CONSTRUCTION_SCENARIO_SPEC, allowed_primitives=SUPPORTED_ACTIONS)

def normalize_plan(plan, default_actor=None):
    normalized = []
    for raw in plan:
        step = normalize_candidate_step(raw)
        if step is None:
            raise ValueError('primitive leaf required')
        if step['agent_id'] is None:
            step['agent_id'] = default_actor
        if default_actor is not None and step['agent_id'] != default_actor:
            raise ValueError('Task actor differs from primitive actor')
        if not isinstance(step['params'], dict):
            raise ValueError('primitive params must be an object')
        normalized.append(BoundStep(step['primitive'], step['agent_id'], step['agent_type'], _freeze_identity(step['params'])))
    return tuple(normalized)

class _Effects:

    def __init__(self, world):
        self.world = deepcopy(world)

    def __call__(self, step, state):
        before = self.world.global_facts()
        if before != state:
            raise EffectResolutionError('BAD_ARGUMENTS', 'effect observation mismatch')
        result = self.world.project_step(step['primitive'], step['agent_id'], step['params'])
        if not result.success:
            raise EffectResolutionError(result.reason_code, result.detail)
        after = self.world.global_facts()
        return SymbolicEffect(produces=after - before, clears=before - after)

@dataclass(frozen=True)
class PreparedConstructionRecovery:
    plan: tuple
    initial_observation: object
    gate: object
    required_goal_facts: frozenset
    cardinality_contract_id: str | None
    recovery_required: bool

def prepare_construction_recovery(world, plan, *, required_goal_facts=(), cardinality_contract_id=None, default_actor=None, scope=Scope.T):
    if not isinstance(world, ConstructionExecutionWorld):
        raise ValueError('assembly execution requires ConstructionExecutionWorld')
    cardinality = None
    if cardinality_contract_id is not None:
        from domains.scenarios.construction import get_construction_cardinality_contract
        cardinality = get_construction_cardinality_contract(cardinality_contract_id)
        if cardinality is None:
            raise ValueError(f'unknown Construction cardinality contract: {cardinality_contract_id}')
    if cardinality_contract_id and world.installed_panel_ids:
        raise ValueError('candidate-cardinality recovery requires assembly-before-installation initial state')
    frozen_plan = normalize_plan(plan, default_actor)
    initial = world.global_facts()
    required = _normalize_facts(required_goal_facts)
    if cardinality is not None:
        required |= _normalize_facts(cardinality.required_observed_initial_facts)
    agents = tuple(({**deepcopy(actor), 'available': actor.get('available', True) and aid not in world.disabled_agents} for aid, actor in world.agents.items()))
    request = RecoveryRequest('construction-assembly', 'construction', {}, {'facts': initial, 'fact_provenance': {f.render(): 'observed_initial' for f in initial}}, available_agents=agents, communication_policy={'state': 'Connected'}, parent_contract={'contract_id': cardinality_contract_id or VERSION, 'required_goal_facts': [f.render() for f in required]})
    candidate = GeneratedCandidate('construction-assembly-candidate', tuple((s.to_mapping() for s in frozen_plan)), 'observed_assembly_candidate', False, 'offline-assembly')
    gate = RecoveryGate().evaluate(GateInput(candidate, scope, request, _SPEC, 'offline_test', contextual_effect_builder=_Effects(world), allow_empty_maintenance=bool(required) and required <= initial))
    return PreparedConstructionRecovery(frozen_plan, _freeze_identity(world.snapshot_state()), gate, required, cardinality_contract_id, bool(cardinality_contract_id or required - initial))

def validate_construction_task(task, chain, prompt_context, runtime_world):
    """Source-backed D_T branch, using observed world rather than declared child posts."""
    from validation.contracts.contracts import D_TValidationResult
    from domains.predicates import parse_predicate
    try:
        precondition = parse_predicate(task.precondition or '')
        if any((a.negated for a in precondition.atomics)):
            return D_TValidationResult(False, 'failed', 'UNRESOLVED_SPEC', ['negative Task preconditions are outside the assembly subset'])
        pre_required = _normalize_facts([str(a) for a in precondition.atomics])
        if task.assigned_agent_id not in runtime_world.agents:
            return D_TValidationResult(False, 'failed', 'AGENT_CONSTRAINT_FAIL', ['unknown Task actor'])
        if not pre_required <= runtime_world.actor_facts(task.assigned_agent_id):
            return D_TValidationResult(False, 'failed', 'CONTRACT_NOT_ENTAILED', ['Task precondition is not observed'])
        parent = parse_predicate(task.postcondition or '')
        if not parent.atomics or any((a.negated for a in parent.atomics)):
            return D_TValidationResult(False, 'failed', 'GOAL_INCOMPLETE', ['Task requires nonempty positive goal atoms'])
        prepared = prepare_construction_recovery(runtime_world, chain, required_goal_facts=[str(a) for a in parent.atomics], default_actor=task.assigned_agent_id)
    except (ValueError, TypeError) as exc:
        return D_TValidationResult(False, 'failed', 'BAD_ARGUMENTS', [str(exc)])
    gate = prepared.gate
    if gate.accepted and (not prepared.recovery_required):
        return D_TValidationResult(False, 'not_applicable', 'NO_RECOVERY_OBLIGATION', ['goal already observed'], goal_discharge=dict(gate.goal_discharge), final_facts=set(gate.final_facts))
    return D_TValidationResult(gate.accepted, 'valid' if gate.accepted else 'failed', gate.reason_codes[0] if gate.reason_codes else '', [gate.detail] if gate.detail else [], goal_discharge=dict(gate.goal_discharge), final_facts=set(gate.final_facts), produced_facts=set(gate.produced_facts))

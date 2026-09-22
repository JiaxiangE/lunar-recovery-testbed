"""Primitives."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from domains.actions.psr.tokens import PSR_PRIMITIVES as _SPECS
from domains.actions.psr.tokens import PrimitiveSpec
_MOVEMENT = {'move_to', 'navigate_to_relay', 'return_to_base'}
_DURATION_S: Dict[str, float] = {'move_to': 60.0, 'navigate_to_relay': 80.0, 'return_to_base': 90.0, 'scan_spectral': 30.0, 'sample_collect': 45.0, 'sample_store': 20.0, 'sample_offload': 40.0, 'dock_with_base': 30.0, 'energy_check': 5.0, 'communicate_status': 10.0, 'communicate_relay': 12.0, 'wait_for_relay': 30.0}
_FAILURE_MODES: Dict[str, List[str]] = {'move_to': ['L-01_wheel_stuck', 'L-02_wheel_slip', 'L-04_path_blocked'], 'navigate_to_relay': ['L-01_wheel_stuck', 'L-02_wheel_slip'], 'return_to_base': ['L-01_wheel_stuck', 'L-02_wheel_slip', 'R-02_low_energy'], 'dock_with_base': ['L-04_path_blocked', 'M-01_gripper_miss'], 'scan_spectral': ['S-01_sensor_fail', 'S-04_no_reading'], 'energy_check': [], 'communicate_status': ['C-01_link_loss', 'C-02_relay_down', 'C-03_jam'], 'communicate_relay': ['C-01_link_loss', 'C-03_jam'], 'wait_for_relay': ['C-02_relay_down'], 'sample_collect': ['M-01_gripper_miss'], 'sample_store': ['M-04_drop_in_transit'], 'sample_offload': ['M-04_drop_in_transit']}

@dataclass
class ExecutionResult:
    success: bool
    primitive_name: str
    agent_id: str
    failure_mode: Optional[str] = None
    elapsed_s: float = 0.0
    energy_consumed_wh: float = 0.0
    facts_added: List[str] = field(default_factory=list)
    detail: str = ''

@dataclass(frozen=True)
class PSRPrimitive:
    name: str
    spec: PrimitiveSpec
    expected_duration_s: float
    failure_modes: tuple
    is_movement: bool

    @property
    def args_schema(self) -> Dict[str, type]:
        return {p: str for p in self.spec.params}

    @property
    def capability_required(self) -> tuple:
        return self.spec.agent_types

    def applies_to(self, agent_type: str) -> bool:
        return agent_type.upper() in {a.upper() for a in self.spec.agent_types}
PSR_PRIMITIVES: Dict[str, PSRPrimitive] = {name: PSRPrimitive(name=name, spec=spec, expected_duration_s=_DURATION_S.get(name, 10.0), failure_modes=tuple(_FAILURE_MODES.get(name, [])), is_movement=name in _MOVEMENT) for name, spec in _SPECS.items()}

def _pre_failure_mode(prim: PSRPrimitive) -> str:
    """A representative failure-mode id when a precondition is unmet."""
    return prim.failure_modes[0] if prim.failure_modes else 'PRE_UNMET'

def execute_primitive(prim: PSRPrimitive, agent_id: str, args: Dict[str, Any], world: Any) -> ExecutionResult:
    """Execute one primitive against the abstract world; mutate world state on success."""
    agent = world.agents[agent_id]
    if not prim.applies_to(agent['agent_type']):
        return ExecutionResult(False, prim.name, agent_id, failure_mode='capability', detail=f"{agent['agent_type']} cannot run {prim.name}")
    missing = set(prim.spec.requires) - set(agent['facts'])
    if missing:
        return ExecutionResult(False, prim.name, agent_id, failure_mode=_pre_failure_mode(prim), detail=f'unmet precondition(s) {sorted(missing)}')
    structured_preflight = getattr(world, 'validate_structured_preconditions', None)
    if callable(structured_preflight):
        structured_failure = structured_preflight(prim.name, agent_id, args)
        if structured_failure is not None:
            failure_mode, detail = structured_failure
            return ExecutionResult(False, prim.name, agent_id, failure_mode=failure_mode, detail=detail)
    injected = world.pop_injected_failure(agent_id, prim.name)
    if injected is not None:
        return ExecutionResult(False, prim.name, agent_id, failure_mode=injected, detail='injected failure')
    energy_cost = 0.0
    elapsed = prim.expected_duration_s
    if prim.is_movement:
        target = world.resolve_target(prim.name, args, agent_id)
        if target is None:
            return ExecutionResult(False, prim.name, agent_id, failure_mode='L-04_path_blocked', detail='target could not be resolved')
        if not world.terrain.is_traversable(target[0], target[1]):
            return ExecutionResult(False, prim.name, agent_id, failure_mode='L-04_path_blocked', detail=f'target {target} not traversable')
        slope = world.terrain.get_slope(target[0], target[1])
        energy_cost = world.energy_model.compute_travel_cost(world.energy_model.euclidean_distance(agent['position'], target), slope)
        if agent['energy_wh'] < energy_cost:
            return ExecutionResult(False, prim.name, agent_id, failure_mode='R-02_low_energy', detail=f"insufficient energy ({agent['energy_wh']:.1f} < {energy_cost:.1f} Wh)")
        agent['position'] = target
    else:
        energy_cost = world.energy_model.compute_task_cost(prim.name)
        if agent['energy_wh'] < energy_cost:
            return ExecutionResult(False, prim.name, agent_id, failure_mode='R-02_low_energy', detail='insufficient energy')
    agent['energy_wh'] -= energy_cost
    from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
    strict = getattr(world, 'semantics_profile', None) == STRICT_PSR_PROFILE
    if strict:
        world.apply_structured_effects(prim.name, agent_id, args)
    agent['facts'].difference_update(prim.spec.clears)
    agent['facts'].update(prim.spec.produces)
    if not strict:
        world.apply_structured_effects(prim.name, agent_id, args)
    world.sim_time_s += elapsed
    if hasattr(world, 'agent_busy_s'):
        world.agent_busy_s[agent_id] = world.agent_busy_s.get(agent_id, 0.0) + elapsed
    return ExecutionResult(True, prim.name, agent_id, elapsed_s=elapsed, energy_consumed_wh=energy_cost, facts_added=sorted(prim.spec.produces))

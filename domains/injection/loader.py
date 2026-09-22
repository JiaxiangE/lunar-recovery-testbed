"""Loader."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from controller.scope import Scope

@dataclass(frozen=True)
class InjectionSpec:
    id: str
    symptom: str
    trigger: Dict[str, Any]
    target: Dict[str, Any]
    parameters: Dict[str, Any]
    expected_cause_level: Scope
PSR_INJECTIONS: Dict[str, InjectionSpec] = {s.id: s for s in [InjectionSpec(id='inj_psr_001', symptom='L-01_wheel_stuck', trigger={'type': 'position', 'agent': 'rover_1', 'x_range': [80, 120], 'y_range': [80, 120]}, target={'agent': 'rover_1', 'primitive_in_progress': 'move_to'}, parameters={'stuck_duration_seconds': 30, 'recovery_possible': True}, expected_cause_level=Scope.P), InjectionSpec(id='inj_psr_002', symptom='C-04_base_unreachable', trigger={'type': 'position', 'agent': 'rover_2', 'x_range': [50, 100], 'y_range': [50, 100]}, target={'agent': 'rover_2'}, parameters={'outage_duration_seconds': 180, 'peer_visibility': True}, expected_cause_level=Scope.T), InjectionSpec(id='inj_psr_003', symptom='G-01_task_unreachable', trigger={'type': 'time', 't_seconds': 600}, target={'scope': 'task', 'task_id': 'task_sample_4', 'sample_id': 'sample_4'}, parameters={'modification': 'make_target_location_unreachable', 'alt_location_provided': True}, expected_cause_level=Scope.T), InjectionSpec(id='inj_psr_004', symptom='R-02_energy_critical', trigger={'type': 'time', 't_seconds': 900}, target={'agent': 'rover_1'}, parameters={'remaining_energy_percent': 8}, expected_cause_level=Scope.M), InjectionSpec(id='inj_psr_005', symptom='G-02_mission_infeasible', trigger={'type': 'time', 't_seconds': 300}, target={'scope': 'mission', 'mission_id': 'mission_collect_5_samples'}, parameters={'modification': 'invalidate_3_of_5_sample_locations', 'invalidate_samples': ['sample_3', 'sample_4', 'sample_5'], 'alt_locations_provided': False}, expected_cause_level=Scope.M)]}
ADVERSARIAL_PSR: Dict[str, InjectionSpec] = {s.id: s for s in [InjectionSpec(id='inj_psr_adv_01', symptom='L-01_wheel_stuck', trigger={'type': 'time', 't_seconds': 300}, target={'agent': 'rover_1'}, parameters={'adversarial': True}, expected_cause_level=Scope.T), InjectionSpec(id='inj_psr_adv_02', symptom='G-01_task_unreachable', trigger={'type': 'time', 't_seconds': 300}, target={'scope': 'task', 'task_id': 'task_x', 'sample_id': 'sample_4'}, parameters={'adversarial': True}, expected_cause_level=Scope.P), InjectionSpec(id='inj_psr_adv_03', symptom='R-02_energy_critical', trigger={'type': 'time', 't_seconds': 300}, target={'agent': 'rover_1'}, parameters={'adversarial': True}, expected_cause_level=Scope.T)]}

class InjectionLoader:

    def load(self, spec_id: str) -> InjectionSpec:
        spec = PSR_INJECTIONS.get(spec_id) or ADVERSARIAL_PSR.get(spec_id)
        if spec is None:
            raise KeyError(f'unknown injection {spec_id!r}; known: {sorted(PSR_INJECTIONS) + sorted(ADVERSARIAL_PSR)}')
        return spec

    def all(self) -> List[InjectionSpec]:
        return list(PSR_INJECTIONS.values())

def trigger_fires(trigger: Dict[str, Any], world: Any, sim_time_s: float) -> bool:
    """True iff the trigger condition holds against the live world at `sim_time_s`."""
    ttype = trigger.get('type')
    if ttype == 'time':
        return sim_time_s >= float(trigger['t_seconds'])
    if ttype == 'position':
        agent = world.agents.get(trigger['agent'])
        if agent is None:
            return False
        x, y = (agent['position'][0], agent['position'][1])
        xr, yr = (trigger['x_range'], trigger['y_range'])
        return xr[0] <= x <= xr[1] and yr[0] <= y <= yr[1]
    raise ValueError(f'unknown trigger type {ttype!r}')

def apply_effect(spec: InjectionSpec, world: Any) -> None:
    """Mutate the world per the injection's symptom. Idempotent (safe to call once)."""
    sym = spec.symptom
    if sym == 'L-01_wheel_stuck':
        world.inject_failure(spec.target['agent'], 'move_to', 'L-01_wheel_stuck')
    elif sym == 'C-04_base_unreachable':
        world.injected_comm_offline.add(spec.target['agent'])
    elif sym == 'G-01_task_unreachable':
        world.injected_unreachable.add(spec.target['sample_id'])
    elif sym == 'R-02_energy_critical':
        ag = world.agents[spec.target.get('agent', 'rover_1')]
        ag['energy_wh'] = ag['initial_energy_wh'] * spec.parameters.get('remaining_energy_percent', 8) / 100.0
    elif sym == 'G-02_mission_infeasible':
        for sid in spec.parameters.get('invalidate_samples', ['sample_3', 'sample_4', 'sample_5']):
            if sid in world.samples:
                world.injected_unreachable.add(sid)
    else:
        raise ValueError(f'no effect mapping for symptom {sym!r}')

class InjectionRunner:
    """Polls a set of injections against the world; fires each at most once when its trigger holds.

    Returns the specs that fired on this poll, so the runner can record the active injection's
    `expected_cause_level` as SDA ground truth for the failure event."""

    def __init__(self, specs: List[InjectionSpec]):
        self.specs = list(specs)
        self.fired: Set[str] = set()

    def poll(self, world: Any, sim_time_s: float) -> List[InjectionSpec]:
        newly: List[InjectionSpec] = []
        for spec in self.specs:
            if spec.id in self.fired:
                continue
            if trigger_fires(spec.trigger, world, sim_time_s):
                apply_effect(spec, world)
                self.fired.add(spec.id)
                newly.append(spec)
        return newly

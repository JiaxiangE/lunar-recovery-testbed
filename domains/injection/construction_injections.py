"""P3-0b — Construction failure injections (scenarios/Construction_L1_Contract_v1.md §S6).

The richest injection set (P/T/M/M/S) and the ONLY Scene-level case in Paper 4: inj_const_005
(G-03 Scene-unsatisfiable → expected σ=S), which validates that the scope selector can escalate to
the top of the lattice. Comm-light scenario: no injection cuts the base link, so the comm-constraint
does not fire (base_reachable stays True) — by design.
"""
from __future__ import annotations
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from controller.scope import Scope
from domains.injection.loader import InjectionSpec
CONSTRUCTION_INJECTIONS: Dict[str, InjectionSpec] = {s.id: s for s in [InjectionSpec(id='inj_const_001', symptom='M-02_joint_limit', trigger={'type': 'time', 't_seconds': 600}, target={'agent': 'manipulator_1', 'primitive_in_progress': 'install_bracket'}, parameters={'joint_limit_reached': True, 'alt_pose_available': True}, expected_cause_level=Scope.P), InjectionSpec(id='inj_const_002', symptom='M-05_assembly_mismatch', trigger={'type': 'task_node', 'task_id': 'task_panel_dock_3'}, target={'agent': 'assembler_1', 'task_id': 'task_panel_dock_3'}, parameters={'position_error_mm': 50, 'retry_can_correct': False}, expected_cause_level=Scope.T), InjectionSpec(id='inj_const_003', symptom='Co-01_deadlock', trigger={'type': 'time', 't_seconds': 1500}, target={'agents': ['manipulator_1', 'assembler_1'], 'agent': 'manipulator_1'}, parameters={'shared_resource': 'panel_slot_5', 'resolution_requires': 'mission_level_replanning'}, expected_cause_level=Scope.M), InjectionSpec(id='inj_const_004', symptom='Co-03_goal_conflict', trigger={'type': 'time', 't_seconds': 1200}, target={'agents': ['manipulator_1', 'transport_1'], 'agent': 'manipulator_1'}, parameters={'conflict_type': 'both_assigned_panel_7'}, expected_cause_level=Scope.M), InjectionSpec(id='inj_const_005', symptom='G-03_scene_unsatisfiable', trigger={'type': 'time', 't_seconds': 2400}, target={'scope': 'scene', 'agent': 'assembler_1'}, parameters={'inject_method': 'destroy_3_panels_and_1_manipulator', 'alt_scene_available': 'install_5_panels_not_12'}, expected_cause_level=Scope.S)]}

def _agent_of(spec: InjectionSpec) -> str:
    t = spec.target
    return t.get('agent') or (t.get('agents') or ['assembler_1'])[0]

def trigger_fires_construction(trigger: Dict[str, Any], world: Any, sim_time_s: float) -> bool:
    ttype = trigger.get('type')
    if ttype == 'time':
        return sim_time_s >= float(trigger['t_seconds'])
    if ttype == 'task_node':
        return getattr(world, 'current_task_id', None) == trigger.get('task_id')
    raise ValueError(f'unknown construction trigger type {ttype!r}')

def apply_effect_construction(spec: InjectionSpec, world: Any) -> None:
    """Mutate ConstructionWorld per the symptom. Idempotent."""
    sym = spec.symptom
    agent = _agent_of(spec)
    if sym == 'M-02_joint_limit':
        world.inject_failure(agent, 'install_bracket', sym)
    elif sym == 'M-05_assembly_mismatch':
        world.inject_failure(agent, 'panel_align_and_dock', sym)
    elif sym == 'Co-01_deadlock':
        for a in spec.target.get('agents', [agent]):
            world.deadlock.add(a)
    elif sym == 'Co-03_goal_conflict':
        for a in spec.target.get('agents', [agent]):
            world.goal_conflict.add(a)
    elif sym == 'G-03_scene_unsatisfiable':
        world.scene_unsatisfiable = True
        world.broken_panels += 3
        world.disabled_agents.add('manipulator_1')
        world.alt_scene_goal = spec.parameters.get('alt_scene_available', 'install_5_panels_not_12')
    else:
        raise ValueError(f'no construction effect mapping for symptom {sym!r}')

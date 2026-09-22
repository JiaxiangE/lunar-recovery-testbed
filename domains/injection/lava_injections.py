"""P3-0a — Lava Tube failure injections (scenarios/LavaTube_L1_Contract_v1.md §S6).

5 injections (inj_lava_001..005) + a lava-aware trigger evaluator (time + tube-depth/section
position) + symptom-keyed effects over LavaWorld's hooks. Reuses the shared `InjectionSpec`
dataclass and `FailureEvent`. `build_failure_context_lava` derives (F, base_reachable, C_edge) for
the scope-selection pipeline — comm-aware (depth-driven) and per-agent C_edge (rover {P,T},
sampler {P,T,M}).

Depth convention: the contract uses negative z (e.g. -45 m); here depth_m is POSITIVE metres into
the tube, so the contract's tube_depth [-45,-50] is encoded as [45,50].
"""
from __future__ import annotations
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from controller.scope import Scope
from domains.injection.loader import InjectionSpec
LAVA_INJECTIONS: Dict[str, InjectionSpec] = {s.id: s for s in [InjectionSpec(id='inj_lava_001', symptom='L-03_tip_over', trigger={'type': 'position', 'agent': 'rover_1', 'tube_section_meters': [30, 50]}, target={'agent': 'rover_1'}, parameters={'tip_angle_degrees': 35, 'recovery_possible': False}, expected_cause_level=Scope.T), InjectionSpec(id='inj_lava_002', symptom='C-02_comm_link_broken', trigger={'type': 'time', 't_seconds': 600}, target={'agent': 'rover_2'}, parameters={'outage_duration_seconds': 600, 'peer_visibility': False}, expected_cause_level=Scope.T), InjectionSpec(id='inj_lava_003', symptom='G-02_mission_infeasible', trigger={'type': 'position', 'agent': 'sampler_1', 'tube_depth_meters': [45, 50]}, target={'scope': 'mission', 'mission_id': 'mission_collect_deep_samples', 'agent': 'sampler_1'}, parameters={'modification': 'spawn_blocker_behind_sampler', 'alt_path_available': True}, expected_cause_level=Scope.M), InjectionSpec(id='inj_lava_004', symptom='C-05_peer_mismatch', trigger={'type': 'time', 't_seconds': 900}, target={'agents': ['rover_1', 'rover_2'], 'agent': 'rover_1'}, parameters={'conflicting_state_info': True, 'ground_truth_with': 'rover_1'}, expected_cause_level=Scope.T), InjectionSpec(id='inj_lava_005', symptom='R-02_energy_critical', trigger={'type': 'time', 't_seconds': 1200}, target={'agents': ['rover_1'], 'agent': 'rover_1'}, parameters={'remaining_energy_percent': 5, 'context_depth_m': 30}, expected_cause_level=Scope.M)]}

def _agent_of(spec: InjectionSpec) -> str:
    t = spec.target
    return t.get('agent') or (t.get('agents') or ['rover_1'])[0]

def trigger_fires_lava(trigger: Dict[str, Any], world: Any, sim_time_s: float) -> bool:
    ttype = trigger.get('type')
    if ttype == 'time':
        return sim_time_s >= float(trigger['t_seconds'])
    if ttype == 'position':
        agent = world.agents.get(trigger['agent'])
        if agent is None:
            return False
        depth = agent.get('depth_m', 0.0)
        rng = trigger.get('tube_section_meters') or trigger.get('tube_depth_meters')
        return bool(rng) and rng[0] <= depth <= rng[1]
    raise ValueError(f'unknown lava trigger type {ttype!r}')

def apply_effect_lava(spec: InjectionSpec, world: Any) -> None:
    """Mutate LavaWorld per the symptom. Idempotent."""
    sym = spec.symptom
    agent = _agent_of(spec)
    if sym == 'L-03_tip_over':
        world.tipped.add(agent)
        world.inject_failure(agent, 'move_to', sym)
        section = spec.trigger.get('tube_section_meters')
        if section and agent in world.agents:
            world.set_depth(agent, (section[0] + section[1]) / 2.0)
    elif sym == 'C-02_comm_link_broken':
        world.injected_comm_offline.add(agent)
        if spec.parameters.get('peer_visibility') is False:
            world.peer_conflict.add(agent)
    elif sym == 'G-02_mission_infeasible':
        world.mission_infeasible = True
        depth = spec.trigger.get('tube_depth_meters', [47, 50])[0]
        if agent in world.agents:
            world.set_depth(agent, max(depth, world.agents[agent]['depth_m']))
    elif sym == 'C-05_peer_mismatch':
        for a in spec.target.get('agents', [agent]):
            world.peer_conflict.add(a)
    elif sym == 'R-02_energy_critical':
        if agent in world.agents:
            ag = world.agents[agent]
            ag['energy_wh'] = ag['initial_energy_wh'] * spec.parameters.get('remaining_energy_percent', 5) / 100.0
            world.set_depth(agent, float(spec.parameters.get('context_depth_m', ag['depth_m'])))
    else:
        raise ValueError(f'no lava effect mapping for symptom {sym!r}')

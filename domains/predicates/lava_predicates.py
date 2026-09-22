"""P3-0a — Lava Tube L1 contract predicates (scenarios/LavaTube_L1_Contract_v1.md §S4/§S7).

The Lava Tube Scene goal: collect the 4 deep samples (depths -10/-25/-40/-50 m) into agent storage
AND return all agents to the entry zone. Hard success (S4): TCR ≥ 70% (≥3/4 samples) + 0 agents
permanently lost + makespan ≤ budget + all agents within 30 m of entry. This module encodes that
contract as a check over the abstract LavaWorld state — the decision-layer analogue of PSR's
`global_facts`/`is_done`, not a physics sim.
"""
from __future__ import annotations
from typing import Any
from typing import Mapping
from typing import Optional
LAVA_PRIMITIVES = ['move_to', 'sample_collect', 'sample_store', 'scan_spectral', 'scan_volatiles', 'energy_check', 'communicate_status', 'communicate_relay', 'navigate_to_relay', 'wait_for_relay', 'return_to_base', 'dock_with_base', 'headlight_toggle', 'low_light_navigation']
TCR_THRESHOLD = 0.7
ENTRY_RETURN_RADIUS_M = 30.0
NOMINAL_MODE = 'nominal'
ABANDONMENT_MODE = 'accepted_s_level_abandonment'

def tcr(world: Any) -> float:
    """Task-completion rate = fraction of the 4 deep samples in agent/entry storage."""
    n = max(len(world.samples), 1)
    collected = sum((1 for s in world.samples.values() if s.get('status') in ('in_rover', 'stored')))
    return collected / n

def _scene_goal_met_v1(world: Any) -> bool:
    """Frozen v1 predicate retained exactly as the nominal behavior."""
    if tcr(world) < TCR_THRESHOLD:
        return False
    return all((abs(a.get('depth_m', 0.0)) <= ENTRY_RETURN_RADIUS_M for a in world.agents.values()))

def _validated_abandonment(world: Any, value: Optional[Mapping[str, Any]]) -> set[str]:
    if not isinstance(value, Mapping):
        raise ValueError('abandonment mode requires S-level candidate/gate lineage')
    required = {'s_candidate_id', 's_candidate_scope', 's_gate_accepted', 's_reason_code', 'declared_lost_agents', 'original_contract_id', 'revised_contract_id', 'contract_semantics_version'}
    missing = required - set(value)
    if missing:
        raise ValueError(f'abandonment lineage missing fields: {sorted(missing)}')
    lost = {str(actor) for actor in value['declared_lost_agents']}
    if not value['s_candidate_id'] or value['s_candidate_scope'] != 'S' or value['s_gate_accepted'] is not True or (not value['s_reason_code']) or (value['contract_semantics_version'] != 'v2') or (not lost) or (value['original_contract_id'] == value['revised_contract_id']):
        raise ValueError('abandonment mode requires an accepted S candidate and revised contract')
    if not lost <= set(world.agents) or not lost <= set(world.tipped):
        raise ValueError('declared lost agents must be observed tipped agents in this world')
    return lost

def scene_goal_met(world: Any, *, mode: str=NOMINAL_MODE, abandonment: Optional[Mapping[str, Any]]=None) -> bool:
    """Evaluate frozen nominal S4 or the PI-authorized v2 abandonment criterion."""
    if mode == NOMINAL_MODE:
        if abandonment is not None:
            raise ValueError('nominal mode does not accept abandonment lineage')
        return _scene_goal_met_v1(world)
    if mode != ABANDONMENT_MODE:
        raise ValueError(f'unknown Lava contract mode: {mode!r}')
    lost = _validated_abandonment(world, abandonment)
    if tcr(world) < TCR_THRESHOLD:
        return False
    return all((actor_id in lost or abs(agent.get('depth_m', 0.0)) <= ENTRY_RETURN_RADIUS_M for actor_id, agent in world.agents.items()))

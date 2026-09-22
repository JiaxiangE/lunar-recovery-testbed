"""Lite simulator."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
DEFAULT_OUTAGE_PENALTY_S = 600.0
PRIMITIVE_DURATIONS_S: Dict[str, float] = {'move_to': 60.0, 'scan_spectral': 30.0, 'sample_collect': 40.0, 'sample_store': 25.0, 'energy_check': 5.0, 'return_to_base': 120.0, 'dock_with_base': 20.0, 'sample_offload': 20.0, 'communicate_status': 10.0, 'communicate_relay': 15.0, 'navigate_to_relay': 60.0, 'wait_for_relay': 30.0, 'scan_volatiles': 30.0, 'headlight_toggle': 5.0, 'low_light_navigation': 80.0, 'peer_communicate': 10.0, 'cargo_pickup': 40.0, 'cargo_drop': 30.0, 'dig_regolith': 120.0, 'place_foundation': 90.0, 'install_bracket': 80.0, 'panel_align_and_dock': 100.0}
_DEFAULT_DURATION_S = 40.0
BASE_CONTACT_PRIMS: Set[str] = {'return_to_base', 'dock_with_base'}
_TRIP_PRIM = 'return_to_base'

@dataclass
class SimResult:
    makespan_s: float
    contacted_base: bool
    n_base_contacts: int
    n_primitives: int
    unknown_primitives: List[str]

def _prim_name(p: Any) -> str:
    """Accept (name, args) tuples, bare name strings, or objects with .primitive_name."""
    if isinstance(p, str):
        return p
    if isinstance(p, (tuple, list)) and p:
        return str(p[0])
    return str(getattr(p, 'primitive_name', '') or '')

class LiteSimulator:
    """Costs a recovery plan → SimResult. Stateless given its tables (one instance reusable)."""

    def __init__(self, durations: Optional[Dict[str, float]]=None, base_contact_prims: Optional[Set[str]]=None, outage_penalty_s: float=DEFAULT_OUTAGE_PENALTY_S):
        self.durations = dict(durations or PRIMITIVE_DURATIONS_S)
        self.base_contact_prims = set(base_contact_prims or BASE_CONTACT_PRIMS)
        self.outage_penalty_s = outage_penalty_s

    def duration_of(self, name: str) -> float:
        return self.durations.get(name, _DEFAULT_DURATION_S)

    def simulate(self, plan: List[Any], base_reachable: bool=True, outage_active: Optional[bool]=None) -> SimResult:
        """Cost an ordered recovery plan.

        makespan = Σ primitive durations + (outage_penalty per base-contacting primitive that runs
        while the base link is down). `outage_active` defaults to `not base_reachable` — a base
        contact during an outage waits out the outage window. Returns the RECOVERY makespan only;
        the caller adds BASE_MAKESPAN_S for the full-sortie total."""
        if outage_active is None:
            outage_active = not base_reachable
        makespan = 0.0
        trips = 0
        contacted = False
        unknown: List[str] = []
        for p in plan:
            name = _prim_name(p)
            if name and name not in self.durations:
                unknown.append(name)
            makespan += self.duration_of(name)
            if name in self.base_contact_prims:
                contacted = True
            if name == _TRIP_PRIM:
                trips += 1
                if outage_active:
                    makespan += self.outage_penalty_s
        return SimResult(makespan_s=makespan, contacted_base=contacted, n_base_contacts=trips, n_primitives=len(plan), unknown_primitives=unknown)

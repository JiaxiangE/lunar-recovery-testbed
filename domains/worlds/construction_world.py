"""P3-0b — Construction (PV-array) abstract world (scenarios/Construction_L1_Contract_v1.md).

The COMM-LIGHT scenario (§S3: agent-to-base 100 m, agent-to-agent 50 m, no outage by design): the
base link is effectively always up, so the D-045 comm-constraint correctly NEVER fires — the BCAR
mechanism is null here, an architectural-consistency check, NOT a failure (Build Spec §4.4 C1). This
is the high-coupling scenario with the only Scene-level case (inj_const_005 → G-03 → σ=S).

Per-agent C_edge (§S3 edge models): TRANSPORT/MANIPULATOR (Qwen-7B) = {P,T}, ASSEMBLER (Qwen-14B)
= {P,T,M}. Decision-layer world (state + injection hooks); manipulation physics is out of scope for
the scope-selection pipeline.
"""
from __future__ import annotations
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from controller.scope import Scope
_INITIAL_ENERGY_WH = 500.0
N_PANELS = 12
AGENT_C_EDGE: Dict[str, set] = {'TRANSPORT': {Scope.P, Scope.T}, 'MANIPULATOR': {Scope.P, Scope.T}, 'ASSEMBLER': {Scope.P, Scope.T, Scope.M}}

class ConstructionWorld:

    def __init__(self, n_panels: int=N_PANELS):
        self.n_panels = n_panels
        self.agents: Dict[str, dict] = {}
        self.sim_time_s: float = 0.0
        self.agent_busy_s: Dict[str, float] = {}
        self.installed_panels: int = 0
        self.circuit_connected: bool = False
        self.wasted_material: int = 0
        self.current_task_id: Optional[str] = None
        self._injected: Dict[Tuple[str, str], str] = {}
        self.injected_comm_offline: set = set()
        self.deadlock: set = set()
        self.goal_conflict: set = set()
        self.disabled_agents: set = set()
        self.broken_panels: int = 0
        self.scene_unsatisfiable: bool = False
        self.alt_scene_goal: Optional[str] = None

    def reset(self, seed: int=0) -> dict:
        random.Random(seed)
        self.sim_time_s = 0.0
        self.installed_panels = 0
        self.circuit_connected = False
        self.wasted_material = 0
        self.current_task_id = None
        self._injected = {}
        self.injected_comm_offline = set()
        self.deadlock = set()
        self.goal_conflict = set()
        self.disabled_agents = set()
        self.broken_panels = 0
        self.scene_unsatisfiable = False
        self.alt_scene_goal = None
        self.agents = {'transport_1': self._mk_agent('transport_1', 'TRANSPORT'), 'manipulator_1': self._mk_agent('manipulator_1', 'MANIPULATOR'), 'assembler_1': self._mk_agent('assembler_1', 'ASSEMBLER')}
        self.agent_busy_s = {aid: 0.0 for aid in self.agents}
        return self.snapshot_state()

    def _mk_agent(self, aid: str, agent_type: str) -> dict:
        return {'id': aid, 'agent_type': agent_type, 'position': (0.0, 30.0, 0.0), 'energy_wh': _INITIAL_ENERGY_WH, 'initial_energy_wh': _INITIAL_ENERGY_WH, 'facts': {'at_base'}, 'cargo': set()}

    def inject_failure(self, agent_id: str, prim_name: str, failure_mode: str) -> None:
        self._injected[agent_id, prim_name] = failure_mode

    def pop_injected_failure(self, agent_id: str, prim_name: str) -> Optional[str]:
        return self._injected.pop((agent_id, prim_name), None)

    def comm_connected_to_base(self, agent_id: str) -> bool:
        """Comm-light by design (§S3): the base link is always up unless an outage is injected
        (none of the 5 construction injections do) — so the comm-constraint never binds here."""
        return agent_id not in self.injected_comm_offline

    def c_edge_for(self, agent_id: str) -> set:
        actor = self.agents.get(agent_id)
        if actor is None:
            return set()
        return set(AGENT_C_EDGE.get(actor.get('agent_type'), set()))

    def scene_goal_met(self) -> bool:
        from domains.predicates.construction_predicates import scene_goal_met
        return scene_goal_met(self)

    def snapshot_state(self) -> dict:
        return {'sim_time_s': self.sim_time_s, 'installed_panels': self.installed_panels, 'circuit_connected': self.circuit_connected, 'broken_panels': self.broken_panels, 'scene_unsatisfiable': self.scene_unsatisfiable, 'agents': {aid: {'type': a['agent_type'], 'energy_wh': a['energy_wh']} for aid, a in self.agents.items()}}

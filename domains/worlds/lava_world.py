"""Lava world."""
from __future__ import annotations
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from controller.scope import Scope
from models.routing import CommunicationState
Pos = Tuple[float, float, float]
_INITIAL_ENERGY_WH = 350.0
COMM_DEPTH_M = 30.0
PEER_RANGE_M = 30.0
ENTRY_POS: Pos = (50.0, 50.0, 0.0)
SAMPLE_DEPTHS_M = [10.0, 25.0, 40.0, 50.0]
AGENT_C_EDGE: Dict[str, set] = {'ROVER': {Scope.P, Scope.T}, 'SAMPLER': {Scope.P, Scope.T, Scope.M}, 'RELAY': {Scope.P}}

class LavaWorld:

    def __init__(self, n_samples: int=4):
        self.n_samples = min(n_samples, len(SAMPLE_DEPTHS_M))
        self.entry_pos: Pos = ENTRY_POS
        self.agents: Dict[str, dict] = {}
        self.samples: Dict[str, dict] = {}
        self.sim_time_s: float = 0.0
        self.agent_busy_s: Dict[str, float] = {}
        self._injected: Dict[Tuple[str, str], str] = {}
        self.injected_comm_offline: set = set()
        self.injected_unreachable: set = set()
        self.peer_conflict: set = set()
        self.tipped: set = set()
        self.mission_infeasible: bool = False

    def reset(self, seed: int=0) -> dict:
        rng = random.Random(seed)
        self.sim_time_s = 0.0
        self._injected = {}
        self.injected_comm_offline = set()
        self.injected_unreachable = set()
        self.peer_conflict = set()
        self.tipped = set()
        self.mission_infeasible = False
        self.agents = {'rover_1': self._mk_agent('rover_1', 'ROVER'), 'rover_2': self._mk_agent('rover_2', 'ROVER'), 'sampler_1': self._mk_agent('sampler_1', 'SAMPLER'), 'relay_1': self._mk_agent('relay_1', 'RELAY', depth_m=-5.0)}
        self.agent_busy_s = {aid: 0.0 for aid in self.agents}
        self.samples = {}
        for k in range(1, self.n_samples + 1):
            depth = SAMPLE_DEPTHS_M[k - 1]
            jitter = rng.uniform(-1.0, 1.0)
            self.samples[f'sample_{k}'] = {'depth_m': depth, 'status': 'field', 'location': (50.0, 50.0, -(depth + jitter))}
        return self.snapshot_state()

    def _mk_agent(self, aid: str, agent_type: str, depth_m: float=0.0) -> dict:
        return {'id': aid, 'agent_type': agent_type, 'position': (self.entry_pos[0], self.entry_pos[1], -depth_m), 'depth_m': depth_m, 'energy_wh': _INITIAL_ENERGY_WH, 'initial_energy_wh': _INITIAL_ENERGY_WH, 'facts': {'at_entry'}, 'cargo': set()}

    def inject_failure(self, agent_id: str, prim_name: str, failure_mode: str) -> None:
        self._injected[agent_id, prim_name] = failure_mode

    def pop_injected_failure(self, agent_id: str, prim_name: str) -> Optional[str]:
        return self._injected.pop((agent_id, prim_name), None)

    def set_depth(self, agent_id: str, depth_m: float) -> None:
        a = self.agents[agent_id]
        a['depth_m'] = depth_m
        a['position'] = (a['position'][0], a['position'][1], -depth_m)

    def comm_connected_to_base(self, agent_id: str) -> bool:
        """Base link iff NOT under an injected outage AND within the entry-zone comm depth.
        The RELAY (deployed just outside the entry) is always connected; it is the bridge that
        would otherwise extend comm — modelling that is a later upgrade."""
        if agent_id in self.injected_comm_offline:
            return False
        a = self.agents[agent_id]
        if a['agent_type'] == 'RELAY':
            return abs(a['depth_m']) <= COMM_DEPTH_M
        return a['depth_m'] <= COMM_DEPTH_M

    def c_edge_for(self, agent_id: str) -> set:
        """The comm-feasible scope set for the agent's edge model (§S3)."""
        actor = self.agents.get(agent_id)
        if actor is None:
            return set()
        return set(AGENT_C_EDGE.get(actor.get('agent_type'), set()))

    def communication_context_for(self, agent_id: str) -> dict:
        """Runtime communication state with the physical depth provenance retained."""
        actor = self.agents.get(agent_id)
        if actor is None:
            raise KeyError(f'unknown Lava actor: {agent_id}')
        connected = self.comm_connected_to_base(agent_id)
        return {'actor_id': agent_id, 'agent_type': actor['agent_type'], 'actor_depth_m': float(actor['depth_m']), 'comm_depth_m': COMM_DEPTH_M, 'communication_state': CommunicationState.CONNECTED.value if connected else CommunicationState.DISCONNECTED.value, 'c_edge': sorted((scope.value for scope in self.c_edge_for(agent_id))), 'injected_outage': agent_id in self.injected_comm_offline, 'provenance': 'LavaWorld.comm_connected_to_base'}

    def scene_goal_met(self, *, mode: str='nominal', abandonment: Optional[dict]=None) -> bool:
        from domains.predicates.lava_predicates import scene_goal_met
        return scene_goal_met(self, mode=mode, abandonment=abandonment)

    def snapshot_state(self) -> dict:
        return {'sim_time_s': self.sim_time_s, 'agents': {aid: {'depth_m': a['depth_m'], 'energy_wh': a['energy_wh'], 'comm': self.comm_connected_to_base(aid)} for aid, a in self.agents.items()}, 'samples': {sid: dict(s) for sid, s in self.samples.items()}}

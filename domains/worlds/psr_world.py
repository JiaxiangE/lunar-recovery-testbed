"""Psr world."""
from __future__ import annotations
import hashlib
import json
import math
from collections.abc import Mapping
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from domains.physics.terrain import TerrainGrid
from domains.physics.energy_model import EnergyModel
from domains.physics.energy_model import EnergyConfig
from domains.predicates import Conjunction
from domains.predicates import parse_predicate
from domains.actions.psr.primitives import PSR_PRIMITIVES
from domains.actions.psr.primitives import ExecutionResult
from domains.actions.psr.primitives import execute_primitive
from controller.scope import Scope
from domains.actions.psr.strict_profile import COLLECTION_TOLERANCE_M
from domains.actions.psr.strict_profile import LEGACY_PSR_PROFILE
from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
from domains.actions.psr.strict_profile import STRICT_OFFLOAD_STATUSES
from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
from domains.actions.psr.strict_profile import runtime_primitive
Pos = Tuple[float, float, float]
_INITIAL_ENERGY_WH = 400.0
AGENT_C_EDGE: Dict[str, set] = {'ROVER': {Scope.P, Scope.T}, 'RELAY': {Scope.P}}

class PSRWorld:

    def __init__(self, dem_path: Optional[str]=None, comm_config: Optional[dict]=None, energy_config: Optional[dict]=None, n_samples: int=5, *, semantics_profile: str=LEGACY_PSR_PROFILE):
        if semantics_profile not in {LEGACY_PSR_PROFILE, STRICT_PSR_PROFILE}:
            raise ValueError(f'unsupported PSR semantics_profile: {semantics_profile}')
        self.semantics_profile = semantics_profile
        self.dem_path = dem_path
        self.terrain = TerrainGrid.from_npz(dem_path) if dem_path else TerrainGrid(width=40, height=40, cell_size=10.0)
        self.energy_model = EnergyModel(EnergyConfig(**energy_config or {}))
        self.comm_config = {'rover_to_relay_m': 50.0, 'relay_to_base_m': 200.0, **(comm_config or {})}
        self.n_samples = n_samples
        self.base_pos: Pos = (0.0, 0.0, 0.0)
        self.relay_pos: Dict[str, Pos] = {'relay_1': (80.0, 80.0, 0.0)}
        self.agents: Dict[str, dict] = {}
        self.samples: Dict[str, dict] = {}
        self.base_storage: set = set()
        self.sim_time_s: float = 0.0
        self.agent_busy_s: Dict[str, float] = {}
        self.acr_events: List[dict] = []
        self._injected: Dict[Tuple[str, str], str] = {}
        self.injected_comm_offline: set = set()
        self.injected_unreachable: set = set()
        self.strict_collect: bool = False

    def reset(self, seed: int=0) -> dict:
        """Deterministic initial state: 2 rovers + 1 relay at base, samples in the field."""
        rng = random.Random(seed)
        self.sim_time_s = 0.0
        self.base_storage = set()
        self.acr_events = []
        self._injected = {}
        self.injected_comm_offline = set()
        self.injected_unreachable = set()
        self.agents = {'rover_1': self._mk_agent('rover_1', 'ROVER'), 'rover_2': self._mk_agent('rover_2', 'ROVER'), 'relay_1': self._mk_agent('relay_1', 'RELAY')}
        self.agent_busy_s = {aid: 0.0 for aid in self.agents}
        self.samples = {}
        for k in range(1, self.n_samples + 1):
            x = 50.0 + rng.uniform(0, 120)
            y = 50.0 + rng.uniform(0, 120)
            self.samples[f'sample_{k}'] = {'location': (round(x, 1), round(y, 1), 0.0), 'status': 'field'}
        return self.snapshot_state()

    def _mk_agent(self, aid: str, agent_type: str) -> dict:
        return {'id': aid, 'agent_type': agent_type, 'position': self.base_pos, 'energy_wh': _INITIAL_ENERGY_WH, 'initial_energy_wh': _INITIAL_ENERGY_WH, 'facts': {'at_base'}, 'cargo': set(), 'docked': True}

    def step(self, primitive: Any, agent: str, args: Optional[dict]=None) -> ExecutionResult:
        """Execute one primitive (by name or PSRPrimitive) for `agent`."""
        if self.semantics_profile == STRICT_PSR_PROFILE:
            name = primitive if isinstance(primitive, str) else primitive.name
            if name not in STRICT_TOKEN_SPECS:
                return ExecutionResult(False, str(name), agent, failure_mode='UNKNOWN_PRIMITIVE')
            if agent not in self.agents:
                return ExecutionResult(False, name, agent, failure_mode='UNKNOWN_ACTOR')
            prim = runtime_primitive(name)
            return execute_primitive(prim, agent, {} if args is None else args, self)
        prim = PSR_PRIMITIVES[primitive] if isinstance(primitive, str) else primitive
        return execute_primitive(prim, agent, args or {}, self)

    def resolve_target(self, prim_name: str, args: dict, agent_id: str) -> Optional[Pos]:
        if prim_name == 'return_to_base':
            return self.base_pos
        if prim_name == 'navigate_to_relay':
            return self.relay_pos.get(args.get('relay', 'relay_1'))
        if prim_name == 'move_to':
            tgt = args.get('target')
            if isinstance(tgt, (list, tuple)) and len(tgt) >= 2:
                return (float(tgt[0]), float(tgt[1]), float(tgt[2]) if len(tgt) > 2 else 0.0)
            if isinstance(tgt, str):
                sid = tgt.replace('.pos', '')
                if sid in self.samples and sid not in self.injected_unreachable:
                    return self.samples[sid]['location']
            return None
        return None

    def pop_injected_failure(self, agent_id: str, prim_name: str) -> Optional[str]:
        return self._injected.pop((agent_id, prim_name), None)

    def inject_failure(self, agent_id: str, prim_name: str, failure_mode: str) -> None:
        """Phase-2 hook: pre-arm a failure for the next (agent, primitive) execution."""
        self._injected[agent_id, prim_name] = failure_mode

    def validate_structured_preconditions(self, prim_name: str, agent_id: str, args: dict) -> Optional[Tuple[str, str]]:
        """Check identity-bearing world preconditions without mutating state."""
        if self.semantics_profile == STRICT_PSR_PROFILE:
            if agent_id not in self.agents or prim_name not in STRICT_TOKEN_SPECS:
                return ('BAD_ARGUMENTS', 'unknown strict-profile actor or primitive')
            if not isinstance(args, Mapping):
                return ('BAD_ARGUMENTS', 'primitive arguments must be an object')
            actor = self.agents[agent_id]
            source = STRICT_TOKEN_SPECS[prim_name]
            if actor.get('available', True) is not True or actor.get('agent_type') not in source.agent_types:
                return ('ACTOR_UNAVAILABLE', 'actor is unavailable or not authorized for this primitive')
            if 'capabilities' in actor and (not isinstance(actor['capabilities'], (set, frozenset, list, tuple)) or prim_name not in actor['capabilities']):
                return ('ACTOR_UNAVAILABLE', 'primitive absent from observed actor capabilities')
            targets = {'base', *self.samples, *self.relay_pos}
            if prim_name in {'move_to', 'return_to_base', 'navigate_to_relay'}:
                try:
                    target = self.resolve_target(prim_name, args, agent_id)
                    valid_target = target is not None and len(target) == 3 and all((math.isfinite(v) for v in target))
                except (ValueError, TypeError, KeyError):
                    valid_target = False
                if not valid_target:
                    return ('BAD_ARGUMENTS', 'unknown, unreachable or invalid movement target')
            if prim_name == 'scan_spectral' and args.get('target') not in targets:
                return ('BAD_ARGUMENTS', 'unknown scan target')
            if prim_name in {'navigate_to_relay', 'communicate_relay'} and args.get('relay') not in self.relay_pos:
                return ('BAD_ARGUMENTS', 'unknown relay')
            if prim_name == 'communicate_status' and args.get('target') not in {'base', *self.agents}:
                return ('BAD_ARGUMENTS', 'unknown recipient')
            if prim_name in {'sample_collect', 'sample_store'}:
                sid = args.get('sample_id')
                if not isinstance(sid, str) or sid not in self.samples:
                    return ('BAD_ARGUMENTS', 'unknown named sample')
            if prim_name == 'sample_collect':
                if sid in self.injected_unreachable:
                    return ('L-04_path_blocked', 'named sample is observed unreachable')
                if not self.at_named_target(agent_id, sid):
                    return ('SAMPLE_POSITION_MISMATCH', 'actor is not within 0.5 m of this named sample')
            if prim_name == 'sample_offload':
                if not args or set(args) - {'base', 'target'} or any((v != 'base' for v in args.values())):
                    return ('BAD_ARGUMENTS', 'offload requires the named base')
        if prim_name != 'sample_store':
            return None
        sample_id = args.get('sample_id')
        if not isinstance(sample_id, str) or sample_id not in self.samples:
            return ('BAD_ARGUMENTS', f'sample_store has invalid sample_id {sample_id!r}')
        agent = self.agents[agent_id]
        if sample_id not in agent['cargo'] or self.samples[sample_id]['status'] != 'in_rover':
            return ('M-04_drop_in_transit', f'sample_store requires {sample_id!r} in actor {agent_id!r} cargo')
        return None

    def apply_structured_effects(self, prim_name: str, agent_id: str, args: dict) -> None:
        if self.semantics_profile == STRICT_PSR_PROFILE:
            failure = self.validate_structured_preconditions(prim_name, agent_id, args)
            if failure is not None:
                raise ValueError(f'{failure[0]}: {failure[1]}')
            missing = STRICT_TOKEN_SPECS[prim_name].requires - set(self.agents[agent_id]['facts'])
            if missing:
                raise ValueError(f'PRECONDITION_UNMET: {sorted(missing)}')
        agent = self.agents[agent_id]
        if prim_name == 'move_to':
            tgt = args.get('target', '')
            sid = tgt.replace('.pos', '') if isinstance(tgt, str) else None
            agent['at_sample'] = sid if sid in self.samples else None
            agent['docked'] = False
        elif prim_name == 'navigate_to_relay':
            agent['docked'] = False
        elif prim_name == 'sample_collect':
            named = args.get('sample_id')
            sid = named if named in self.samples else None if self.strict_collect else agent.get('at_sample')
            self.acr_events.append({'named': named, 'actual': sid if sid in self.samples else None})
            if sid in self.samples:
                self.samples[sid]['status'] = 'in_rover'
                agent['cargo'].add(sid)
        elif prim_name == 'sample_store':
            sid = args.get('sample_id')
            if sid in self.samples and sid in agent['cargo'] and (self.samples[sid]['status'] == 'in_rover'):
                self.samples[sid]['status'] = 'stored'
        elif prim_name == 'return_to_base':
            agent['at_sample'] = None
        elif prim_name == 'dock_with_base':
            agent['docked'] = True
        elif prim_name == 'sample_offload':
            statuses = STRICT_OFFLOAD_STATUSES if self.semantics_profile == STRICT_PSR_PROFILE else {'stored', 'in_rover'}
            for sid in list(agent['cargo']):
                if self.semantics_profile == STRICT_PSR_PROFILE and sid not in self.samples:
                    continue
                if self.samples[sid]['status'] in statuses:
                    self.samples[sid]['status'] = 'in_base'
                    self.base_storage.add(sid)
                    agent['cargo'].discard(sid)

    def at_named_target(self, agent_id: str, target: str) -> bool:
        """Strict observed arrival; identity strings never replace coordinates."""
        targets = {'base': self.base_pos, **self.relay_pos, **{sid: sample['location'] for sid, sample in self.samples.items()}}
        if agent_id not in self.agents or target not in targets:
            return False
        try:
            position, destination = (tuple(self.agents[agent_id]['position']), tuple(targets[target]))
            if len(position) != 3 or len(destination) != 3:
                return False
            return math.dist(position, destination) <= COLLECTION_TOLERANCE_M
        except (TypeError, ValueError):
            return False

    def global_facts(self) -> List[str]:
        return [f'in_base_storage({sid})' for sid in sorted(self.base_storage)] + [f'stored({sid})' for sid, s in self.samples.items() if s['status'] == 'stored']

    def predicate_state(self) -> dict:
        return {'facts': self.global_facts()}

    def is_done(self, scene_goal: Any) -> bool:
        goal = scene_goal if isinstance(scene_goal, Conjunction) else parse_predicate(str(scene_goal))
        return goal.evaluate(self.predicate_state())

    def comm_connected_to_base(self, agent_id: str) -> bool:
        """Simple PSR comm: rover within rover_to_relay_m of a relay that is within
        relay_to_base_m of base (relays at base are trivially connected)."""
        if agent_id in self.injected_comm_offline:
            return False
        a = self.agents[agent_id]
        if a['agent_type'] == 'RELAY':
            return _dist(a['position'], self.base_pos) <= self.comm_config['relay_to_base_m']
        for rid, rpos in self.relay_pos.items():
            relay = self.agents.get(rid)
            rp = relay['position'] if relay else rpos
            if _dist(a['position'], rp) <= self.comm_config['rover_to_relay_m'] and _dist(rp, self.base_pos) <= self.comm_config['relay_to_base_m']:
                return True
        return _dist(a['position'], self.base_pos) <= self.comm_config['relay_to_base_m']

    def c_edge_for(self, agent_id: str) -> set:
        """PSR L1 §S3 capability by actor type; unknown actors fail closed."""
        actor = self.agents.get(agent_id)
        if actor is None:
            return set()
        return set(AGENT_C_EDGE.get(str(actor.get('agent_type', '')), set()))

    def get_snapshot(self, agent_id: str='rover_1', mission_phase_id: str='G3'):
        """SensorSnapshot for the decision layer (reuses Paper 3's populator + world battery/comm)."""
        from domains.physics.sensor_snapshot_populator import SensorSnapshotPopulator
        a = self.agents[agent_id]
        battery_pct = 100.0 * a['energy_wh'] / max(a['initial_energy_wh'], 1e-09)
        snap = SensorSnapshotPopulator().populate_nominal(timestamp=self.sim_time_s, agent_id=agent_id, mission_phase_id=mission_phase_id, battery_remaining_pct=round(battery_pct, 1))
        snap.comm_state = 'A' if self.comm_connected_to_base(agent_id) else 'C'
        return snap

    def snapshot_state(self) -> dict:
        """Plain-dict view of world state (for reset() return + logging/determinism tests)."""
        value = {'sim_time_s': self.sim_time_s, 'agents': {aid: {'position': ag['position'], 'energy_wh': ag['energy_wh'], 'facts': sorted(ag['facts']), 'cargo': sorted(ag['cargo'])} for aid, ag in self.agents.items()}, 'samples': {sid: dict(s) for sid, s in self.samples.items()}, 'base_storage': sorted(self.base_storage)}
        if self.semantics_profile == STRICT_PSR_PROFILE:
            value['semantics_profile'] = self.semantics_profile
            value['collection_tolerance_m'] = COLLECTION_TOLERANCE_M
        return value

    def canonical_execution_state_bytes(self) -> bytes:
        """Versioned, complete mutable-state view for accepted-only audit hashes.

        Static terrain/energy configuration is outside the execution-state
        boundary.  Every mutable PSR field that can affect or evidence a trial is
        represented here so W3 wrong-ID before/after hashes use one production
        serializer rather than test-local approximations.
        """
        payload = {'schema_version': 'psr_execution_state_v2_strict' if self.semantics_profile == STRICT_PSR_PROFILE else 'psr_execution_state_v1', 'n_samples': self.n_samples, 'snapshot': self.snapshot_state(), 'agent_busy_s': dict(sorted(self.agent_busy_s.items())), 'agent_runtime': {actor_id: {'at_sample': actor.get('at_sample'), 'docked': actor.get('docked')} for actor_id, actor in sorted(self.agents.items())}, 'acr_events': list(self.acr_events), 'injected_failures': sorted(([actor_id, primitive, mode] for (actor_id, primitive), mode in self._injected.items())), 'injected_comm_offline': sorted(self.injected_comm_offline), 'injected_unreachable': sorted(self.injected_unreachable), 'strict_collect': self.strict_collect}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

    def execution_state_sha256(self) -> str:
        return hashlib.sha256(self.canonical_execution_state_bytes()).hexdigest()

def _dist(a: Pos, b: Pos) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

"""Source-limited Lava transitions; the historical decision world is unchanged.

Movement coordinates come from named world entities (3-D), timing from the existing
lite simulator table (nominal, not measured). Onboarding move_to retains the 5%
energy precondition. No source defines numeric Lava action consumption: energy is
preserved and every result reports consumption=None, including headlight (frozen F2).
The source-defined 600-second inj_lava_002 outage expires with simulated time;
returning to entry alone does not repair the radio before that time.
Collector and a free storage slot remain source collect preconditions (onboarding
Primitive Library; remediation ruling §3). No Lava source specifies total slots:
storage_capacity is an explicit fixed fixture configuration, default four per
collecting actor, independent of the requested goal and not a hardware measurement.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import math
from domains.worlds.lava_world import LavaWorld
from domains.worlds.lava_world import PEER_RANGE_M
from domains.actions.lava.spec import LAVA_SYMBOLIC_PRIMITIVE_SPECS
from domains.actions.spec import ParameterSchema
from domains.simulator.lite_simulator import PRIMITIVE_DURATIONS_S
SEMANTICS_VERSION = 'lava_source_execution_v1'
SUPPORTED_PRIMITIVES = frozenset(LAVA_SYMBOLIC_PRIMITIVE_SPECS) | {'move_to', 'energy_check'}

@dataclass(frozen=True)
class LavaExecutionResult:
    success: bool
    primitive_name: str
    agent_id: str
    failure_mode: str | None = None
    elapsed_s: float = 0.0
    energy_consumed_wh: float | None = None
    detail: str = ''

def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(v) for v in value))
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    return value

class LavaExecutionWorld(LavaWorld):
    semantics_version = SEMANTICS_VERSION

    def __init__(self, n_samples: int=4, *, storage_capacity: int=4):
        if type(storage_capacity) is not int or storage_capacity < 0:
            raise ValueError('fixture storage_capacity must be a nonnegative integer')
        self.fixture_storage_capacity = storage_capacity
        super().__init__(n_samples=n_samples)

    def reset(self, seed: int=0) -> dict:
        self.comm_outage_until = {}
        self.disabled_agents = set()
        self.low_visibility = True
        self.dispatch_count = 0
        super().reset(seed)
        for actor in self.agents.values():
            actor['has_volatiles_sensor'] = actor['agent_type'] == 'ROVER'
            actor['has_collector'] = actor['agent_type'] in {'ROVER', 'SAMPLER'}
            actor['storage_capacity'] = self.fixture_storage_capacity if actor['has_collector'] else 0
        for sample in self.samples.values():
            sample['depth_m'] = -sample['location'][2]
            sample['owner'] = None
        self._refresh_location_facts()
        return self.snapshot_state()

    def set_depth(self, agent_id: str, depth_m: float) -> None:
        super().set_depth(agent_id, depth_m)
        self._refresh_location_facts()

    def _refresh_location_facts(self):
        for aid, actor in self.agents.items():
            actor['facts'] = {f for f in actor['facts'] if not f.startswith('agent_at(') and f != 'at_entry'}
            for target in ('entry', *self.samples):
                if self.at_target(aid, target):
                    actor['facts'].add(f'agent_at({target})')
            if self.at_entry(aid):
                actor['facts'].add('at_entry')

    def target_position(self, target: str):
        if target == 'entry':
            return self.entry_pos
        return self.samples[target]['location']

    def at_target(self, aid: str, target: str) -> bool:
        return aid in self.agents and target in {'entry', *self.samples} and (math.dist(self.agents[aid]['position'], self.target_position(target)) <= 0.5)

    def at_entry(self, aid: str) -> bool:
        return aid in self.agents and math.dist(self.agents[aid]['position'], self.entry_pos) <= 30.0

    def storage_free(self, aid: str) -> int | None:
        """Available slots derived from trusted capacity and actor-local cargo.

        Stored onboard samples remain in cargo and occupy their slot; no source
        authorizes Lava offload, automatic capacity growth, or custody transfer.
        """
        actor = self.agents.get(aid, {})
        capacity = actor.get('storage_capacity')
        if type(capacity) is not int or capacity < 0:
            return None
        return max(0, capacity - len(actor.get('cargo', ())))

    def comm_connected_to_base(self, agent_id: str) -> bool:
        until = getattr(self, 'comm_outage_until', {}).get(agent_id)
        if until is not None and self.sim_time_s >= until:
            return self.agents[agent_id]['depth_m'] <= 30.0
        return super().comm_connected_to_base(agent_id)

    def peer_blocked(self, aid: str) -> bool:
        until = self.comm_outage_until.get(aid)
        return aid in self.peer_conflict or (until is not None and self.sim_time_s < until)

    def communication_context_for(self, agent_id: str) -> dict:
        context = super().communication_context_for(agent_id)
        until = self.comm_outage_until.get(agent_id)
        if until is not None:
            context['injected_outage'] = self.sim_time_s < until
            context['outage_until_s'] = until
        context['provenance'] = 'LavaExecutionWorld.comm_connected_to_base'
        return context

    def apply_injection(self, spec) -> None:
        from domains.injection.lava_injections import apply_effect_lava
        from domains.injection.lava_injections import trigger_fires_lava
        if not trigger_fires_lava(spec.trigger, self, self.sim_time_s):
            raise ValueError('injection trigger has not fired')
        prior_peer_conflict = set(self.peer_conflict)
        apply_effect_lava(spec, self)
        if spec.id == 'inj_lava_002':
            aid = spec.target['agent']
            self.comm_outage_until.setdefault(aid, self.sim_time_s + spec.parameters['outage_duration_seconds'])
            if aid not in prior_peer_conflict:
                self.peer_conflict.discard(aid)
        self._refresh_location_facts()

    def snapshot_state(self) -> dict:
        return _json_value({'semantics_version': self.semantics_version, 'sim_time_s': self.sim_time_s, 'entry_pos': self.entry_pos, 'n_samples': self.n_samples, 'storage_fixture': {'slots_per_collecting_actor': self.fixture_storage_capacity, 'source': 'fixed fixture configuration; no sourced numerical Lava capacity; not measured hardware'}, 'agents': deepcopy(self.agents), 'samples': deepcopy(self.samples), 'tipped': self.tipped, 'disabled_agents': getattr(self, 'disabled_agents', set()), 'injected_unreachable': self.injected_unreachable, 'injected_comm_offline': self.injected_comm_offline, 'comm_outage_until': getattr(self, 'comm_outage_until', {}), 'peer_conflict': self.peer_conflict, 'low_visibility': getattr(self, 'low_visibility', True), 'mission_infeasible': self.mission_infeasible, 'pending_failures': [[a, p, v] for (a, p), v in sorted(self._injected.items())], 'agent_busy_s': self.agent_busy_s})

    def _transition(self, agent_id: str, primitive_name: str, params: dict, *, runtime: bool):
        fail = lambda code, detail='': LavaExecutionResult(False, primitive_name, agent_id, code, detail=detail)
        if primitive_name not in SUPPORTED_PRIMITIVES:
            return fail('UNSUPPORTED_PRIMITIVE')
        actor = self.agents.get(agent_id)
        if actor is None:
            return fail('UNKNOWN_ACTOR')
        if agent_id in self.tipped or agent_id in self.disabled_agents or actor.get('available', True) is not True:
            return fail('ACTOR_UNAVAILABLE')
        if 'capabilities' in actor:
            capabilities = actor['capabilities']
            if not isinstance(capabilities, (list, tuple, set, frozenset)) or primitive_name not in capabilities:
                return fail('CAPABILITY_NOT_AUTHORIZED')
        spec = LAVA_SYMBOLIC_PRIMITIVE_SPECS.get(primitive_name)
        schema = spec.parameter_schema if spec else ParameterSchema(required={'target': str}) if primitive_name == 'move_to' else ParameterSchema()
        ok, detail = schema.validate(params)
        if not ok:
            return fail('BAD_ARGUMENTS', detail)
        allowed = spec.allowed_agent_types if spec else {'ROVER', 'SAMPLER'} if primitive_name == 'move_to' else {'ROVER'}
        if actor['agent_type'] not in allowed:
            return fail('AGENT_TYPE_MISMATCH')
        target = params.get('target')
        sid = params.get('sample_id')
        if target is not None and target not in {'entry', *self.samples}:
            return fail('UNKNOWN_TARGET')
        if sid is not None and sid not in self.samples:
            return fail('UNKNOWN_SAMPLE')
        if target in self.injected_unreachable or sid in self.injected_unreachable:
            return fail('TARGET_UNREACHABLE')
        if primitive_name == 'low_light_navigation' and (not self.low_visibility):
            return fail('PRECONDITION_UNMET', 'low_visibility')
        if primitive_name == 'scan_volatiles' and actor.get('has_volatiles_sensor') is not True:
            return fail('PRECONDITION_UNMET', 'agent_has_volatiles_sensor is not observed')
        if primitive_name == 'sample_collect':
            if actor.get('has_collector') is not True:
                return fail('PRECONDITION_UNMET', 'has_collector is not observed')
            free = self.storage_free(agent_id)
            if free is None or free < 1:
                return fail('PRECONDITION_UNMET', 'storage_free >= 1 is not observed')
            if not self.at_target(agent_id, sid) or self.samples[sid]['status'] != 'field':
                return fail('PRECONDITION_UNMET', 'actor must be at the field sample')
        if primitive_name == 'sample_store':
            if sid not in actor['cargo'] or self.samples[sid].get('owner') != agent_id or self.samples[sid]['status'] not in {'in_rover', 'stored'}:
                return fail('PRECONDITION_UNMET', 'actor must own the collected sample')
        if primitive_name == 'peer_communicate':
            peer = params['peer_id']
            if peer not in self.agents or peer == agent_id:
                return fail('UNKNOWN_PEER')
            if math.dist(actor['position'], self.agents[peer]['position']) > PEER_RANGE_M or self.peer_blocked(agent_id) or self.peer_blocked(peer):
                return fail('PRECONDITION_UNMET', 'peer range or injected communication outage')
        if runtime:
            injected = self.pop_injected_failure(agent_id, primitive_name)
            if injected:
                return fail(injected, 'injected execution failure')
            if primitive_name == 'move_to' and actor['energy_wh'] < 0.05 * actor['initial_energy_wh']:
                return fail('R-02_energy_critical', 'source move_to energy below 5%; consumption unspecified')
        elapsed = PRIMITIVE_DURATIONS_S[primitive_name]
        if primitive_name in {'move_to', 'low_light_navigation'}:
            actor['position'] = tuple(self.target_position(target))
            actor['depth_m'] = -actor['position'][2]
        elif primitive_name == 'sample_collect':
            actor['cargo'].add(sid)
            self.samples[sid].update(status='in_rover', owner=agent_id)
        elif primitive_name == 'sample_store':
            self.samples[sid]['status'] = 'stored'
        elif primitive_name == 'headlight_toggle':
            actor['facts'].discard('headlight_off')
            actor['facts'].add('headlight_on')
        elif primitive_name == 'scan_volatiles':
            actor['facts'].add(f'volatiles_reading_published({target})')
        elif primitive_name == 'peer_communicate':
            actor['facts'].add(f"peer_received({params['peer_id']})")
        elif primitive_name == 'energy_check':
            actor['facts'].add('energy_checked')
        self.sim_time_s += elapsed
        self.agent_busy_s[agent_id] += elapsed
        self._refresh_location_facts()
        return LavaExecutionResult(True, primitive_name, agent_id, elapsed_s=elapsed, detail='nominal timing; numeric Lava energy consumption is source-undefined; communication tokens are local')

    def step(self, agent_id: str, primitive_name: str, params: dict | None=None):
        self.dispatch_count += 1
        return self._transition(agent_id, primitive_name, {} if params is None else params, runtime=True)

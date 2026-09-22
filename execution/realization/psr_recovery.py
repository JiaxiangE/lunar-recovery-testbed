"""Bind expanded PSR action candidates to an observed state. Symbolic preparation precedes dispatch; each executed prefix is checked against its predicted state. Runtime world failures stop the suffix."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any
from typing import Mapping
from domains.actions.psr.strict_profile import LEGACY_PSR_PROFILE
from domains.actions.psr.strict_profile import require_psr_profile

def _freeze(value):
    if isinstance(value, Mapping):
        return tuple(sorted(((key, _freeze(v)) for key, v in value.items())))
    if isinstance(value, (set, frozenset)):
        return frozenset((_freeze(v) for v in value))
    if isinstance(value, (list, tuple)):
        return tuple((_freeze(v) for v in value))
    return value

@dataclass(frozen=True)
class BoundStep:
    primitive: str
    agent_id: str
    agent_type: str | None
    params: tuple

    def to_mapping(self):
        return {'primitive': self.primitive, 'agent_id': self.agent_id, 'agent_type': self.agent_type, 'params': dict(self.params)}

def _observation(world, include_energy=True):
    agents = world.agents if include_energy else {aid: {key: value for key, value in actor.items() if key not in {'energy_wh', 'initial_energy_wh'}} for aid, actor in world.agents.items()}
    observation = {'agents': agents, 'samples': world.samples, 'base_storage': world.base_storage, 'base_pos': world.base_pos, 'relay_pos': world.relay_pos, 'unreachable': world.injected_unreachable, 'comm_offline': world.injected_comm_offline, 'strict_collect': world.strict_collect}
    if world.semantics_profile != LEGACY_PSR_PROFILE:
        observation['semantics_profile'] = world.semantics_profile
    return _freeze(observation)

"""Token-level PSR action specifications and finite-chain helpers. The common domain adds actor/entity state and the strict collection/offload profile."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Mapping
from typing import Tuple
INCIDENTAL_FACTS: FrozenSet[str] = frozenset({'scanned', 'energy_checked'})

@dataclass(frozen=True)
class PrimitiveSpec:
    name: str
    params: Tuple[str, ...]
    agent_types: Tuple[str, ...]
    requires: FrozenSet[str]
    produces: FrozenSet[str]
    clears: FrozenSet[str] = frozenset()
    description: str = ''

def _p(name, params, agent_types, requires, produces, clears=(), description=''):
    return PrimitiveSpec(name=name, params=tuple(params), agent_types=tuple(agent_types), requires=frozenset(requires), produces=frozenset(produces), clears=frozenset(clears), description=description)
PSR_PRIMITIVES: Dict[str, PrimitiveSpec] = {'move_to': _p('move_to', ['target_xy', 'x', 'y', 'target'], ['ROVER', 'SAMPLER'], requires=[], produces=['at_location'], clears=['at_base', 'docked'], description='Navigate to a target coordinate.'), 'scan_spectral': _p('scan_spectral', ['target'], ['ROVER', 'SAMPLER'], requires=['at_location'], produces=['scanned'], description='Spectral scan of a target (must be on-site).'), 'sample_collect': _p('sample_collect', ['sample_id'], ['ROVER', 'SAMPLER'], requires=['at_location'], produces=['sample_collected'], description='Collect a sample (must be on-site).'), 'sample_store': _p('sample_store', ['sample_id'], ['ROVER', 'SAMPLER'], requires=['sample_collected'], produces=['sample_stored'], description='Stow a collected sample into permanent storage.'), 'energy_check': _p('energy_check', [], ['ROVER', 'SAMPLER', 'RELAY'], requires=[], produces=['energy_checked'], description='Report current energy level (never fails).'), 'communicate_status': _p('communicate_status', ['target'], ['ROVER', 'SAMPLER', 'RELAY'], requires=[], produces=['status_sent'], description='Send a status message to base/peer (direct link).'), 'communicate_relay': _p('communicate_relay', ['relay'], ['ROVER', 'SAMPLER'], requires=['relay_linked'], produces=['status_sent'], description='Send status via an established relay link.'), 'navigate_to_relay': _p('navigate_to_relay', ['relay'], ['ROVER', 'SAMPLER'], requires=[], produces=['at_relay'], clears=['at_base', 'docked'], description="Drive to the relay node's position."), 'wait_for_relay': _p('wait_for_relay', [], ['ROVER', 'SAMPLER'], requires=['at_relay'], produces=['relay_linked'], description='Wait at relay until the relay link is up.'), 'return_to_base': _p('return_to_base', [], ['ROVER', 'SAMPLER'], requires=[], produces=['at_base'], clears=['at_location', 'at_relay'], description='Drive back to base.'), 'dock_with_base': _p('dock_with_base', [], ['ROVER', 'SAMPLER'], requires=['at_base'], produces=['docked'], description='Dock with the base station (must be at base).'), 'sample_offload': _p('sample_offload', ['base', 'target'], ['ROVER', 'SAMPLER'], requires=['at_base', 'sample_stored'], produces=['sample_offloaded'], clears=['sample_stored'], description='Offload stored samples into base storage.')}
PRIMITIVE_NAMES: Tuple[str, ...] = tuple(PSR_PRIMITIVES.keys())
INITIAL_FACTS_FIELD: FrozenSet[str] = frozenset()

def is_known_primitive(name: str) -> bool:
    return name in PSR_PRIMITIVES

def check_step_legal(name: str, params: Mapping[str, object]) -> Tuple[bool, str]:
    """A single step is legal if the name is in-library and params are allowed names."""
    if not is_known_primitive(name):
        return (False, f'unknown primitive {name!r}')
    spec = PSR_PRIMITIVES[name]
    bad = [k for k in params or {} if k not in spec.params]
    if bad:
        return (False, f'{name}: unknown param(s) {bad} (allowed: {list(spec.params)})')
    return (True, '')

def chain_produces(chain: List[Mapping[str, object]]) -> set:
    """MONOTONIC union of every step's produced facts (clears IGNORED).

    Used for GOAL-achievement checking: an achievement (e.g. sample_stored) stays
    achieved even if a later step's `clears` would drop the transient position state.
    This is deliberately different from `simulate_chain_facts` (stateful, with clears),
    which is for precondition/legality checking.
    """
    facts: set = set()
    for step in chain:
        spec = PSR_PRIMITIVES.get(step.get('primitive', ''))
        if spec is not None:
            facts |= spec.produces
    return facts

def goal_facts_of(chain: List[Mapping[str, object]]) -> set:
    """The success predicate a chain is meant to achieve = its produced facts minus
    incidental side-steps (scanning / energy-check). If stripping leaves nothing (the
    task's whole point WAS the incidental step, e.g. a pure energy_check task), keep the
    un-stripped set so the goal is non-empty.
    """
    produced = chain_produces(chain)
    stripped = produced - INCIDENTAL_FACTS
    return stripped if stripped else produced

def chain_legality(chain: List[Mapping[str, object]], initial: FrozenSet[str]=INITIAL_FACTS_FIELD) -> Tuple[bool, str]:
    """Forward-check a chain: every step in-library, valid params, and `requires`
    satisfied by initial facts + prior effects.
    """
    if not chain:
        return (False, 'empty chain')
    facts = set(initial)
    for i, step in enumerate(chain):
        name = step.get('primitive', '')
        ok, reason = check_step_legal(name, step.get('params', {}))
        if not ok:
            return (False, f'step {i} ({name}): {reason}')
        spec = PSR_PRIMITIVES[name]
        missing = spec.requires - facts
        if missing:
            return (False, f'step {i} ({name}): unmet precondition(s) {sorted(missing)}')
        facts -= spec.clears
        facts |= spec.produces
    return (True, '')

"""Spec."""
from __future__ import annotations
from types import MappingProxyType
from typing import Any
from typing import Iterable
from typing import Mapping
from domains.actions.spec import ParameterSchema
from domains.actions.spec import PrimitiveSpec
from domains.actions.spec import ResolutionContext
from domains.actions.spec import fact
from domains.actions.spec import no_facts
_ONBOARDING = 'docs/action_reference.md#lava-sensing-and-navigation'
_L1 = 'docs/action_reference.md#lava-position-and-communication'
_WORLD = 'domains/worlds/lava_world.py:23-27,38-123'
_SIM = 'domains/simulator/lite_simulator.py:43-45'
_DEFAULT_TARGET_IDS = frozenset({'entry', 'sample_1', 'sample_2', 'sample_3', 'sample_4'})
_SAMPLE_IDS = frozenset({'sample_1', 'sample_2', 'sample_3', 'sample_4'})
_SHARED_SOURCES = ('docs/action_reference.md#lava-collection-and-storage', 'docs/action_reference.md#lava-cargo-ownership', 'domains/predicates/lava_predicates.py:28-32', 'domains/actions/psr/tokens.py:67-72', 'docs/action_reference.md#lava-state-effects', 'AR-LAVA-SHARED-EFFECTS-20260902')

def _nonempty_string(value: Any):
    return (isinstance(value, str) and bool(value.strip()), 'must be a non-empty string')

def _target_validator(target_ids: frozenset[str]):

    def validate(value: Any):
        return (isinstance(value, str) and value in target_ids, f'must be a symbolic Lava target ID in {sorted(target_ids)}')
    return validate

def _sample_id(value: Any):
    return (isinstance(value, str) and value in _SAMPLE_IDS, f'must be one of {sorted(_SAMPLE_IDS)}')

def _normalize_named_waypoints(named_waypoints: Iterable[str]) -> frozenset[str]:
    if isinstance(named_waypoints, (str, bytes)):
        raise TypeError('named_waypoints must be an iterable of symbolic waypoint IDs')
    values = tuple(named_waypoints)
    invalid = [value for value in values if not isinstance(value, str) or not value.strip()]
    if invalid:
        raise ValueError(f'named waypoint IDs must be non-empty strings: {invalid!r}')
    return frozenset((value.strip() for value in values))

def _scan_requires(args: Mapping[str, Any]):
    return {fact('agent_has_volatiles_sensor'), fact('target_reachable', args['target'])}

def _scan_produces(args: Mapping[str, Any]):
    return {fact('volatiles_reading_published', args['target'])}

def _navigation_requires(_args: Mapping[str, Any]):
    return {fact('low_visibility')}

def _navigation_produces(args: Mapping[str, Any]):
    return {fact('agent_at', args['target'])}

def _peer_requires(args: Mapping[str, Any]):
    return {fact('peer_in_range', args['peer_id']), fact('communication_not_jammed', args['peer_id'])}

def _peer_produces(args: Mapping[str, Any]):
    return {fact('peer_received', args['peer_id'])}

def build_lava_primitive_specs(*, named_waypoints: Iterable[str]=()) -> dict[str, PrimitiveSpec]:
    """Build the frozen registry, explicitly extending its symbolic target IDs.

    ``named_waypoints`` is deliberately opt-in: an arbitrary string or coordinate
    is never accepted merely because it resembles a location.  Concrete 3-D
    resolution remains the scenario/world adapter's responsibility, while facts
    produced here preserve the supplied symbolic ID.
    """
    target_ids = _DEFAULT_TARGET_IDS | _normalize_named_waypoints(named_waypoints)
    target_schema = ParameterSchema(required={'target': str}, validators={'target': _target_validator(target_ids)})
    return {'scan_volatiles': PrimitiveSpec(name='scan_volatiles', parameter_schema=target_schema, allowed_agent_types=frozenset({'ROVER'}), requires=_scan_requires, produces=_scan_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'headlight_toggle': PrimitiveSpec(name='headlight_toggle', parameter_schema=ParameterSchema(), allowed_agent_types=frozenset({'ROVER', 'SAMPLER'}), requires=no_facts, produces=lambda _args: {fact('headlight_on')}, clears=lambda _args: {fact('headlight_off')}, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'low_light_navigation': PrimitiveSpec(name='low_light_navigation', parameter_schema=target_schema, allowed_agent_types=frozenset({'ROVER'}), requires=_navigation_requires, produces=_navigation_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'peer_communicate': PrimitiveSpec(name='peer_communicate', parameter_schema=ParameterSchema(required={'peer_id': str, 'msg': str}, validators={'peer_id': _nonempty_string, 'msg': _nonempty_string}), allowed_agent_types=frozenset({'ROVER', 'SAMPLER'}), requires=_peer_requires, produces=_peer_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM))}
LAVA_PRIMITIVE_SPECS = build_lava_primitive_specs()

def _collect_requires(args: Mapping[str, Any], _context: ResolutionContext):
    return {fact('agent_at', args['sample_id'])}

def _collect_produces(args: Mapping[str, Any], context: ResolutionContext):
    return {fact('sample_collected', context.actor_id, args['sample_id'])}

def _store_requires(args: Mapping[str, Any], context: ResolutionContext):
    return {fact('sample_collected', context.actor_id, args['sample_id'])}

def _store_produces(args: Mapping[str, Any], _context: ResolutionContext):
    return {fact('in_storage', args['sample_id'])}

def _actor_no_facts(_args: Mapping[str, Any], _context: ResolutionContext):
    return frozenset()
LAVA_SHARED_PRIMITIVE_SPECS = {'sample_collect': PrimitiveSpec(name='sample_collect', parameter_schema=ParameterSchema(required={'sample_id': str}, validators={'sample_id': _sample_id}), allowed_agent_types=frozenset({'ROVER', 'SAMPLER'}), requires=_collect_requires, produces=_collect_produces, clears=_actor_no_facts, source_references=_SHARED_SOURCES, actor_sensitive=True), 'sample_store': PrimitiveSpec(name='sample_store', parameter_schema=ParameterSchema(required={'sample_id': str}, validators={'sample_id': _sample_id}), allowed_agent_types=frozenset({'ROVER', 'SAMPLER'}), requires=_store_requires, produces=_store_produces, clears=_actor_no_facts, source_references=_SHARED_SOURCES, actor_sensitive=True)}
LAVA_SYMBOLIC_PRIMITIVE_SPECS = MappingProxyType({**LAVA_PRIMITIVE_SPECS, **LAVA_SHARED_PRIMITIVE_SPECS})

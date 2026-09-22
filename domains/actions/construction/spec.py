"""Spec."""
from __future__ import annotations
from typing import Any
from typing import Mapping
from domains.actions.spec import ParameterSchema
from domains.actions.spec import PrimitiveSpec
from domains.actions.spec import fact
from domains.actions.spec import no_facts
_ONBOARDING = 'docs/action_reference.md#construction-action-schemas'
_L1 = 'docs/action_reference.md#construction-preparation'
_WORLD = 'domains/worlds/construction_world.py:21-40,68-79'
_INJECTIONS = 'domains/injection/construction_injections.py:23-46,71-79'
_SIM = 'domains/simulator/lite_simulator.py:46-49'
_LOCATION = (str, tuple, list)
_SCALAR = (int, float)
PANEL_IDS = frozenset((f'panel_{index}' for index in range(1, 13)))

def _panel_id(value: Any):
    return (value in PANEL_IDS, f'expected one of panel_1..panel_12, got {value!r}')

def _pickup_requires(args: Mapping[str, Any]):
    return {fact('agent_at_material', args['material_id']), fact('cargo_free_ge_1')}

def _pickup_produces(args: Mapping[str, Any]):
    return {fact('in_cargo', 'TRANSPORT', args['material_id'])}

def _drop_requires(args: Mapping[str, Any]):
    return {fact('cargo_nonempty', 'TRANSPORT'), fact('target_reachable', args['target_location'])}

def _drop_produces(args: Mapping[str, Any]):
    return {fact('cargo_at', args['target_location'])}

def _dig_requires(args: Mapping[str, Any]):
    return {fact('agent_has_digging_tool'), fact('soil_diggable', args['target_location'])}

def _dig_produces(args: Mapping[str, Any]):
    return {fact('regolith_pile_at', args['target_location'])}

def _foundation_requires(args: Mapping[str, Any]):
    return {fact('in_cargo', 'MANIPULATOR', args['bracket_id']), fact('location_prepared', args['location'])}

def _foundation_produces(args: Mapping[str, Any]):
    return {fact('bracket_at_correct_location', args['bracket_id'], args['location'])}

def _bracket_requires(args: Mapping[str, Any]):
    return {fact('bracket_at_correct_location', args['bracket_id']), fact('agent_has_arm')}

def _bracket_produces(args: Mapping[str, Any]):
    return {fact('bracket_secured', args['bracket_id'])}

def _panel_requires(args: Mapping[str, Any]):
    return {fact('in_cargo', 'ASSEMBLER', args['panel_id']), fact('bracket_secured', args['bracket_id']), fact('reachable', args['panel_id']), fact('reachable', args['bracket_id'])}

def _panel_produces(args: Mapping[str, Any]):
    return {fact('panel_docked_to_bracket', args['panel_id'], args['bracket_id']), fact('panel_installed', args['panel_id'])}
CONSTRUCTION_PRIMITIVE_SPECS = {'cargo_pickup': PrimitiveSpec(name='cargo_pickup', parameter_schema=ParameterSchema(required={'material_id': str}), allowed_agent_types=frozenset({'TRANSPORT'}), requires=_pickup_requires, produces=_pickup_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'cargo_drop': PrimitiveSpec(name='cargo_drop', parameter_schema=ParameterSchema(required={'target_location': _LOCATION}), allowed_agent_types=frozenset({'TRANSPORT'}), requires=_drop_requires, produces=_drop_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'dig_regolith': PrimitiveSpec(name='dig_regolith', parameter_schema=ParameterSchema(required={'target_location': _LOCATION, 'depth': _SCALAR}), allowed_agent_types=frozenset({'MANIPULATOR'}), requires=_dig_requires, produces=_dig_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'place_foundation': PrimitiveSpec(name='place_foundation', parameter_schema=ParameterSchema(required={'bracket_id': str, 'location': _LOCATION}), allowed_agent_types=frozenset({'MANIPULATOR'}), requires=_foundation_requires, produces=_foundation_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _SIM)), 'install_bracket': PrimitiveSpec(name='install_bracket', parameter_schema=ParameterSchema(required={'bracket_id': str}), allowed_agent_types=frozenset({'MANIPULATOR'}), requires=_bracket_requires, produces=_bracket_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _INJECTIONS, _SIM)), 'panel_align_and_dock': PrimitiveSpec(name='panel_align_and_dock', parameter_schema=ParameterSchema(required={'panel_id': str, 'bracket_id': str}, validators={'panel_id': _panel_id}), allowed_agent_types=frozenset({'ASSEMBLER'}), requires=_panel_requires, produces=_panel_produces, clears=no_facts, source_references=(_ONBOARDING, _L1, _WORLD, _INJECTIONS, _SIM))}

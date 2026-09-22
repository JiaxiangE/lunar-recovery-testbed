"""Lava ScenarioSpec backed by the frozen EA-W1-03b primitive registry."""
from __future__ import annotations
from domains.predicates.lava_predicates import LAVA_PRIMITIVES
from execution.realization import AbstractLiteSimulatorRealizer
from domains.simulator.lite_simulator import BASE_CONTACT_PRIMS
from domains.scenarios.psr import PSR_SCENARIO_SPEC
from domains.scenarios.spec import ArgumentSchema
from domains.scenarios.spec import ScenarioSpec
from domains.scenarios.spec import argument_schema_from_primitive
from domains.scenarios.spec import observable_contract_builder
from domains.scenarios.spec import required_primitive_goal_checker
from domains.scenarios.spec import symbolic_effect_builder_from_primitive
_ALLOWED = frozenset(LAVA_PRIMITIVES).union({'peer_communicate'})
_SCHEMAS = {name: schema for name, schema in PSR_SCENARIO_SPEC.primitive_argument_schema.items() if name in _ALLOWED}
_EFFECTS = {name: effect for name, effect in PSR_SCENARIO_SPEC.primitive_symbolic_effects.items() if name in _ALLOWED}

def _make_realizer(**kwargs):
    return AbstractLiteSimulatorRealizer(scenario_id='lava', implementation_id='lava_abstract_lite_simulator', allowed_primitives=_ALLOWED, **kwargs)
LAVA_SCENARIO_SPEC = ScenarioSpec(scenario_id='lava', allowed_primitives=_ALLOWED, primitive_argument_schema=_SCHEMAS, primitive_symbolic_effects=_EFFECTS, goal_checker=required_primitive_goal_checker, recovery_contract_builder=observable_contract_builder('lava'), base_contact_primitives=frozenset(BASE_CONTACT_PRIMS).intersection(_ALLOWED), realizer_id='lava_abstract_lite_simulator', realizer_factory=_make_realizer, freeze_notes=('C01-C05 are frozen; Lava targets are symbolic IDs resolved by the Lava 3-D adapter.', 'headlight_on is incidental and low_light_navigation does not require it.', 'peer_communicate is included per doc 15 fixed four-primitive coverage; the older LAVA_PRIMITIVES list omitted it.'))

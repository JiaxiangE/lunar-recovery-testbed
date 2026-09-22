"""Historical token-only PSR ScenarioSpec backed by the 12-primitive library.

Kept for signed v1/v2 runners and existing projector callers. New named P/T/M
validation uses ``validation.psr_named.evaluate_psr_named`` explicitly; importing
this spec does not silently upgrade an old experiment's semantics.
"""
from __future__ import annotations
from typing import Any
from typing import Mapping
from domains.actions.psr.tokens import PSR_PRIMITIVES
from execution.realization import PSRGroundingRealizer
from domains.simulator.lite_simulator import BASE_CONTACT_PRIMS
from domains.scenarios.spec import ArgumentSchema
from domains.scenarios.spec import ScenarioSpec
from domains.scenarios.spec import SymbolicEffect
from domains.scenarios.spec import observable_contract_builder
from domains.scenarios.spec import required_primitive_goal_checker
_REQUIRED = {'scan_spectral': {'target': str}, 'sample_collect': {'sample_id': str}, 'sample_store': {'sample_id': str}, 'communicate_status': {'target': str}, 'communicate_relay': {'relay': str}, 'navigate_to_relay': {'relay': str}}

def _schema(name: str) -> ArgumentSchema:
    params = {param: object for param in PSR_PRIMITIVES[name].params}
    required = dict(_REQUIRED.get(name, {}))
    optional = {key: value for key, value in params.items() if key not in required}
    if name == 'move_to':
        optional.update({'target': object, 'target_xy': object, 'x': (int, float), 'y': (int, float)})
        return ArgumentSchema(optional=optional, required_any_of=(frozenset({'target'}), frozenset({'target_xy'}), frozenset({'x', 'y'})))
    if name == 'sample_offload':
        return ArgumentSchema(optional={'base': str, 'target': str}, required_any_of=(frozenset({'base'}), frozenset({'target'})))
    return ArgumentSchema(required=required, optional=optional)

def _effects(name: str):
    source = PSR_PRIMITIVES[name]

    def build(_args: Mapping[str, Any]) -> SymbolicEffect:
        return SymbolicEffect(source.requires, source.produces, source.clears)
    return build
PSR_SCENARIO_SPEC = ScenarioSpec(scenario_id='psr', allowed_primitives=frozenset(PSR_PRIMITIVES), primitive_argument_schema={name: _schema(name) for name in PSR_PRIMITIVES}, primitive_symbolic_effects={name: _effects(name) for name in PSR_PRIMITIVES}, goal_checker=required_primitive_goal_checker, recovery_contract_builder=observable_contract_builder('psr'), base_contact_primitives=frozenset(BASE_CONTACT_PRIMS).intersection(PSR_PRIMITIVES), realizer_id='psr_grounding_v1', realizer_factory=PSRGroundingRealizer, freeze_notes=('PSR effects reuse experiments.psr_primitive_spec; no simulator rewrite.',))

"""Construction ScenarioSpec backed by the frozen EA-W1-03b primitive registry."""
from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from typing import Tuple
from domains.predicates.construction_predicates import CONSTRUCTION_PRIMITIVES
from domains.predicates.construction_predicates import PANEL_DEGRADED
from domains.predicates.construction_predicates import PANEL_TARGET
from domains.actions.construction import CONSTRUCTION_PRIMITIVE_SPECS
from domains.actions.construction import PANEL_IDS
from execution.realization import AbstractLiteSimulatorRealizer
from domains.simulator.lite_simulator import BASE_CONTACT_PRIMS
from domains.scenarios.psr import PSR_SCENARIO_SPEC
from domains.scenarios.spec import ArgumentSchema
from domains.scenarios.spec import ScenarioSpec
from domains.scenarios.spec import argument_schema_from_primitive
from domains.scenarios.spec import observable_contract_builder
from domains.scenarios.spec import required_primitive_goal_checker
from domains.scenarios.spec import symbolic_effect_builder_from_primitive
CONSTRUCTION_HARD_CONTRACT_ID = 'construction_hard_ge8'
CONSTRUCTION_DEGRADED_CONTRACT_ID = 'construction_degraded_ge5'

@dataclass(frozen=True)
class ConstructionCardinalityContract:
    """Frozen finite-domain portion of one Construction scene contract."""
    contract_id: str
    threshold: int
    panel_domain: Tuple[str, ...]
    required_observed_initial_facts: Tuple[str, ...]
    excluded_facts: Tuple[str, ...]
    source_references: Tuple[str, ...]
_PANEL_DOMAIN = tuple(sorted(PANEL_IDS, key=lambda value: int(value.split('_')[1])))
_CONTRACT_SOURCE = ('docs/action_reference.md#construction-distinct-panel-goal', 'domains/worlds/construction_world.py:22,38-39,57', 'domains/actions/construction/spec.py:84-95', 'docs/action_reference.md#construction-assembly', 'docs/action_reference.md#construction-alternative-goal')
CONSTRUCTION_CARDINALITY_CONTRACTS: Mapping[str, ConstructionCardinalityContract] = MappingProxyType({CONSTRUCTION_HARD_CONTRACT_ID: ConstructionCardinalityContract(contract_id=CONSTRUCTION_HARD_CONTRACT_ID, threshold=PANEL_TARGET, panel_domain=_PANEL_DOMAIN, required_observed_initial_facts=('circuit_connected', 'zero_material_wasted'), excluded_facts=(), source_references=_CONTRACT_SOURCE), CONSTRUCTION_DEGRADED_CONTRACT_ID: ConstructionCardinalityContract(contract_id=CONSTRUCTION_DEGRADED_CONTRACT_ID, threshold=PANEL_DEGRADED, panel_domain=_PANEL_DOMAIN, required_observed_initial_facts=('circuit_connected',), excluded_facts=('zero_material_wasted',), source_references=_CONTRACT_SOURCE)})

def get_construction_cardinality_contract(contract_id: str) -> ConstructionCardinalityContract | None:
    """Return a frozen cardinality contract, or ``None`` when not applicable."""
    return CONSTRUCTION_CARDINALITY_CONTRACTS.get(str(contract_id))
_ALLOWED = frozenset(CONSTRUCTION_PRIMITIVES)
_SCHEMAS = {name: schema for name, schema in PSR_SCENARIO_SPEC.primitive_argument_schema.items() if name in _ALLOWED}
_EFFECTS = {name: effect for name, effect in PSR_SCENARIO_SPEC.primitive_symbolic_effects.items() if name in _ALLOWED}

def _make_realizer(**kwargs):
    return AbstractLiteSimulatorRealizer(scenario_id='construction', implementation_id='construction_abstract_lite_simulator', allowed_primitives=_ALLOWED, **kwargs)
CONSTRUCTION_SCENARIO_SPEC = ScenarioSpec(scenario_id='construction', allowed_primitives=_ALLOWED, primitive_argument_schema=_SCHEMAS, primitive_symbolic_effects=_EFFECTS, goal_checker=required_primitive_goal_checker, recovery_contract_builder=observable_contract_builder('construction'), base_contact_primitives=frozenset(BASE_CONTACT_PRIMS).intersection(_ALLOWED), realizer_id='construction_abstract_lite_simulator', realizer_factory=_make_realizer, freeze_notes=('Six construction-specific primitives only; connector_install is excluded.', 'C06/C07 are frozen; panel effects retain canonical panel IDs.', 'construction_hard_ge8 and construction_degraded_ge5 are distinct finite-domain contracts over canonical panel_1..panel_12 IDs.', 'AR-CONST-F1: construction_degraded_ge5 is AtLeast(5 unique candidate-derived panel IDs) AND observed_initial circuit_connected; waste is excluded.', 'circuit_connected, location_prepared(location), and ASSEMBLER cargo are declared observable initial facts when applicable, never candidate effects.', 'Construction peer_communicate has no §S3-authorized agent and fails closed.'))

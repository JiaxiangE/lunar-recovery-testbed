"""Scenario-aware realization boundary for corrected experiments.

Realization is deliberately an interface, not a claim that every scenario uses
the PSR spatial grounder.  A result always carries the concrete implementation
ID that must be copied into ``ExecutionTrace.realizer_implementation_id``.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Mapping
from typing import Optional
from typing import Protocol
from typing import Sequence
from domains.simulator.lite_simulator import LiteSimulator
from domains.simulator.lite_simulator import SimResult

class RealizationError(RuntimeError):
    """A candidate could not be realized without inventing missing semantics."""

class UnknownPrimitiveError(RealizationError):
    """The abstract simulator has no declared duration/semantics for a primitive."""

@dataclass(frozen=True)
class PrimitiveRealizationProvenance:
    """Auditable, per-step realization decision."""
    step_index: int
    primitive_name: str
    target_input: Any = None
    target_output: Any = None
    target_resolution: str = 'not_applicable'
    soft_grounded: bool = False
    no_op: Optional[bool] = None
    detail: str = ''

@dataclass
class RealizationResult:
    """Canonical result shared by scenario-specific realizer implementations."""
    realizer_implementation_id: str
    realized_plan: List[Any]
    treatment: str
    primitive_provenance: List[PrimitiveRealizationProvenance] = field(default_factory=list)
    component_provenance: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    runtime_controls: Dict[str, Any] = field(default_factory=dict)
    simulation: Optional[SimResult] = None

    def trace_fields(self) -> Dict[str, Any]:
        """Fields safe to attach to the realization/execution portion of a trace."""
        return {'realizer_implementation_id': self.realizer_implementation_id, 'realized_plan': deepcopy(self.realized_plan), 'realization_treatment': self.treatment, 'realization_provenance': [{'step_index': event.step_index, 'primitive_name': event.primitive_name, 'target_input': deepcopy(event.target_input), 'target_output': deepcopy(event.target_output), 'target_resolution': event.target_resolution, 'soft_grounded': event.soft_grounded, 'no_op': event.no_op, 'detail': event.detail} for event in self.primitive_provenance], 'realization_components': deepcopy(self.component_provenance), 'realization_runtime_controls': deepcopy(self.runtime_controls)}

class Realizer(Protocol):
    """Minimal protocol implemented by all corrected-experiment realizers."""
    implementation_id: str

    def realize(self, candidate: Sequence[Any], **kwargs: Any) -> RealizationResult:
        ...

def _primitive_name(step: Any) -> str:
    if isinstance(step, str):
        return step
    if isinstance(step, (tuple, list)) and step:
        return str(step[0])
    if isinstance(step, Mapping):
        return str(step.get('primitive', step.get('primitive_name', step.get('name', ''))) or '')
    return str(getattr(step, 'primitive_name', '') or '')

class AbstractLiteSimulatorRealizer:
    """Scenario-local canonical abstract realizer for Lava/Construction.

    This class has no import of :mod:`validation.grounding`, which makes the
    non-PSR boundary structural rather than a convention.  Scenario factories
    supply distinct implementation IDs even though they share this protocol.
    """

    def __init__(self, *, scenario_id: str, implementation_id: str, allowed_primitives: FrozenSet[str] | set[str] | Sequence[str], simulator: Optional[LiteSimulator]=None) -> None:
        self.scenario_id = scenario_id
        self.implementation_id = implementation_id
        self.allowed_primitives = frozenset(allowed_primitives)
        self.simulator = simulator or LiteSimulator()

    def realize(self, candidate: Sequence[Any], *, base_reachable: bool=True, outage_active: Optional[bool]=None) -> RealizationResult:
        plan = deepcopy(list(candidate))
        names = [_primitive_name(step) for step in plan]
        unknown = [name for name in names if not name or name not in self.allowed_primitives]
        if unknown:
            raise UnknownPrimitiveError(f'{self.scenario_id} abstract realizer rejects undeclared primitive(s): {unknown}')
        simulation = self.simulator.simulate(names, base_reachable=base_reachable, outage_active=outage_active)
        if simulation.unknown_primitives:
            raise UnknownPrimitiveError(f'{self.scenario_id} has no abstract duration for: {simulation.unknown_primitives}')
        provenance = [PrimitiveRealizationProvenance(index, name, detail='abstract_lite_simulator') for index, name in enumerate(names)]
        return RealizationResult(realizer_implementation_id=self.implementation_id, realized_plan=plan, treatment='abstract_lite_simulator', primitive_provenance=provenance, component_provenance={'abstract_lite_simulator': {'status': 'executed', 'calls': 1, 'scenario_id': self.scenario_id}}, simulation=simulation)

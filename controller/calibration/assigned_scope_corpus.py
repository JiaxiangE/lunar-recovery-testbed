"""Truth-isolated assigned-scope calibration corpus definitions (W3-R1).

Runtime cells carry only observable failure data, an assigned repair scope, and
source provenance. Cause-level labels live in a physically separate table and
can be joined only after a trial has completed.
"""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import fields
from dataclasses import is_dataclass
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Sequence
from typing import Tuple
from controller.scope import Scope

class CalibrationRuntimeError(RuntimeError):
    """Calibration runtime invariant failed closed."""
_TRUTH_KEYS = frozenset({'cause_level', 'expected', 'expected_cause_level', 'expected_scope', 'ground_truth', 'scorer', 'scorer_label', 'scorer_labels', 'gate_outcome', 'terminal_outcome'})

def assert_truth_isolated(value: Any, path: str='runtime') -> None:
    """Reject scorer/expected truth anywhere in a runtime object graph."""
    if is_dataclass(value):
        for item in fields(value):
            assert_truth_isolated(getattr(value, item.name), f'{path}.{item.name}')
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower() in _TRUTH_KEYS:
                raise CalibrationRuntimeError(f'truth key leaked into {path}.{key}')
            assert_truth_isolated(child, f'{path}.{key}')
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            assert_truth_isolated(child, f'{path}[{index}]')

@dataclass(frozen=True)
class ScorerLabel:
    trial_id: str
    cause_level: Scope

@dataclass(frozen=True)
class AssignedScopeCell:
    trial_id: str
    scenario_id: str
    fixture_id: str
    seed: int
    assigned_scope: Scope
    communication_state: str
    actor_id: str
    agent_type: str
    c_edge: Tuple[Scope, ...]
    observable_failure: Mapping[str, Any]
    parent_contract_source: str

    def __post_init__(self) -> None:
        assert_truth_isolated(self)

    def to_runtime_dict(self) -> dict:
        return {'trial_id': self.trial_id, 'scenario_id': self.scenario_id, 'fixture_id': self.fixture_id, 'seed': self.seed, 'assigned_scope': self.assigned_scope.value, 'communication_state': self.communication_state, 'actor_id': self.actor_id, 'agent_type': self.agent_type, 'c_edge': [value.value for value in self.c_edge], 'observable_failure': dict(self.observable_failure), 'parent_contract_source': self.parent_contract_source}
_CAUSE_SOURCES = {Scope.P: ('inj_psr_001', 'L-01_wheel_stuck', 'docs/action_reference.md#psr-injected-drive-failure'), Scope.T: ('inj_psr_003', 'G-01_task_unreachable', 'docs/action_reference.md#psr-unreachable-sample'), Scope.M: ('inj_psr_004', 'R-02_energy_critical', 'docs/action_reference.md#psr-energy-depletion'), Scope.S: ('inj_const_005', 'G-03_scene_unsatisfiable', 'docs/action_reference.md#construction-panel-loss')}

def build_pt_assigned_cells(*, repeats: int, seeds: Sequence[int], cause_levels: Sequence[Scope]=(Scope.P, Scope.T, Scope.M, Scope.S)) -> Tuple[list[AssignedScopeCell], list[ScorerLabel]]:
    """Build the P/T x P/T/M/S dry-run matrix with labels kept separate.

    This is a budget/corpus manifest, not executable truth-bearing runtime input.
    An execution adapter must still bind each row to an exact source-backed parent
    contract and terminal goal observer before a real Batch-A run.
    """
    if repeats < 1:
        raise ValueError('repeats must be positive')
    if not seeds:
        raise ValueError('at least one held-out seed is required')
    normalized_causes = tuple((Scope(value) for value in cause_levels))
    if not normalized_causes or len(set(normalized_causes)) != len(normalized_causes):
        raise ValueError('cause_levels must be non-empty and unique')
    cells: list[AssignedScopeCell] = []
    labels: list[ScorerLabel] = []
    for assigned in (Scope.P, Scope.T):
        for cause in normalized_causes:
            source_id, symptom, source = _CAUSE_SOURCES[cause]
            for repeat in range(repeats):
                seed = int(seeds[repeat % len(seeds)])
                trial_id = f'pt-{assigned.value}-{source_id}-r{repeat + 1}-s{seed}'
                cells.append(AssignedScopeCell(trial_id=trial_id, scenario_id='construction' if cause is Scope.S else 'psr', fixture_id=source_id, seed=seed, assigned_scope=assigned, communication_state='Connected', actor_id='manipulator_1' if cause is Scope.S else 'rover_1', agent_type='MANIPULATOR' if cause is Scope.S else 'ROVER', c_edge=(Scope.P, Scope.T), observable_failure={'symptom': symptom, 'source': source}, parent_contract_source='axis_b_psr_tranche1_tree_v1/P-or-T ancestor; exact binding required'))
                labels.append(ScorerLabel(trial_id, cause))
    return (cells, labels)

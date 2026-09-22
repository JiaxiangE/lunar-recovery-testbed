"""Stage-2 utility-weight calibration with paired success/cost metrics.

These definitions are frozen before the one-shot 27-combination grid run.
Scope decision accuracy is intentionally absent: the corrected objective reports
escalation-free success together with subtree, LLM, and actual-base-contact cost.
"""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order
from controller.selector import PFixParams
from controller.selector import ScopeSelector
from controller.selector import UtilityWeights
from controller.selector import _LLM_COST
from controller.selector import _SUBTREE_SIZE
from controller.calibration.selector_config_v2 import STAGE1_FROZEN_P_FIX
from controller.calibration.selector_config_v2 import STAGE1_FIXED_UTILITY_WEIGHTS
W_TCR_GRID = [5.0, 10.0, 20.0]
W_MKS_GRID = [0.02, 0.05, 0.1]
W_RISK_GRID = [0.2, 0.5, 1.0]
W_COST_FIXED = 0.01
TIE_BREAK_RULE_ID = 'pareto_then_min_grid_distance_to_stage1_weights_v1'
TIE_BREAK_RATIONALE = 'Preserve the Stage-1 value policy whenever it remains nondominated; otherwise choose the nearest grid policy. Exact distance ties use declared ascending grid order. This avoids post-result preference among subtree, LLM, and base-contact costs.'

@dataclass(frozen=True)
class CalibrationScenario:
    scenario_id: str
    delta_dist: Mapping[Scope, float]
    base_reachable: bool
    c_edge: frozenset[Scope]
    cause_level: Scope
    agent_type: str
    capability_provenance: str

@dataclass(frozen=True)
class PairedMetrics:
    escalation_free_success_count: int
    escalation_free_success_rate: float
    subtree_cost_total: float
    llm_cost_total: float
    actual_base_contacts_total: int

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class WeightFit:
    weights: UtilityWeights
    metrics: PairedMetrics
    sensitivity: List[Dict[str, Any]] = field(default_factory=list)
    pareto_frontier: List[Dict[str, Any]] = field(default_factory=list)
    tie_break_rule_id: str = TIE_BREAK_RULE_ID
    tie_break_rationale: str = TIE_BREAK_RATIONALE
    previous_weights: UtilityWeights = STAGE1_FIXED_UTILITY_WEIGHTS

def _peaked(level: Scope, hi: float) -> Dict[Scope, float]:
    rest = (1.0 - hi) / 3.0
    return {scope: hi if scope is level else rest for scope in SCOPES}

def default_calibration_scenarios(hi: float=0.85) -> List[CalibrationScenario]:
    """Four Connected diagonals plus three unique constrained capability classes.

    Each constrained case uses the broadest locally supported scope: RELAY/P,
    ROVER/T, and SAMPLER/M. This exercises each unique C_edge once without an
    impossible S-at-edge target or repeated weighting of equivalent agents.
    """
    scenarios = [CalibrationScenario(scenario_id=f'connected_diagonal_{level.value}', delta_dist=_peaked(level, hi), base_reachable=True, c_edge=frozenset({Scope.P, Scope.T}), cause_level=level, agent_type='CONNECTED_GENERIC', capability_provenance='connected_all_scopes_available') for level in SCOPES]
    constrained = (('disconnected_relay_P', 'RELAY', frozenset({Scope.P}), Scope.P, 'PSR/Lava L1 Contract §S3 relay_C_edge'), ('disconnected_rover_T', 'ROVER', frozenset({Scope.P, Scope.T}), Scope.T, 'PSR/Lava L1 Contract §S3 rover_C_edge'), ('disconnected_sampler_M', 'SAMPLER', frozenset({Scope.P, Scope.T, Scope.M}), Scope.M, 'LavaTube L1 Contract §S3 sampler_C_edge'))
    scenarios.extend((CalibrationScenario(scenario_id=scenario_id, delta_dist=_peaked(level, hi), base_reachable=False, c_edge=c_edge, cause_level=level, agent_type=agent_type, capability_provenance=provenance) for scenario_id, agent_type, c_edge, level, provenance in constrained))
    return scenarios

def paired_metrics_for_weights(weights: UtilityWeights, scenarios: Sequence[CalibrationScenario], *, p_fix_params: PFixParams=STAGE1_FROZEN_P_FIX) -> tuple[PairedMetrics, List[Dict[str, Any]]]:
    selector = ScopeSelector(weights, p_fix_params)
    success = 0
    subtree_total = 0.0
    llm_total = 0.0
    base_contacts = 0
    decisions = []
    for scenario in scenarios:
        decision = selector.select(None, dict(scenario.delta_dist), {}, set(scenario.c_edge), scenario.base_reachable)
        sigma = Scope(decision.sigma)
        case_success = scope_order(sigma) >= scope_order(scenario.cause_level)
        success += int(case_success)
        subtree_total += float(_SUBTREE_SIZE[sigma])
        llm_total += float(_LLM_COST[sigma])
        actual_base = int(scenario.base_reachable and sigma is not Scope.P)
        base_contacts += actual_base
        decisions.append({'scenario_id': scenario.scenario_id, 'cause_level': scenario.cause_level.value, 'selected_scope': sigma.value, 'escalation_free_success': case_success, 'subtree_cost': float(_SUBTREE_SIZE[sigma]), 'llm_cost': float(_LLM_COST[sigma]), 'actual_base_contacts': actual_base, 'base_reachable': scenario.base_reachable, 'agent_type': scenario.agent_type, 'c_edge': [scope.value for scope in sorted(scenario.c_edge, key=scope_order)]})
    metrics = PairedMetrics(escalation_free_success_count=success, escalation_free_success_rate=success / len(scenarios), subtree_cost_total=subtree_total, llm_cost_total=llm_total, actual_base_contacts_total=base_contacts)
    return (metrics, decisions)

def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    better_or_equal = left['escalation_free_success_count'] >= right['escalation_free_success_count'] and left['subtree_cost_total'] <= right['subtree_cost_total'] and (left['llm_cost_total'] <= right['llm_cost_total']) and (left['actual_base_contacts_total'] <= right['actual_base_contacts_total'])
    strictly_better = left['escalation_free_success_count'] > right['escalation_free_success_count'] or left['subtree_cost_total'] < right['subtree_cost_total'] or left['llm_cost_total'] < right['llm_cost_total'] or (left['actual_base_contacts_total'] < right['actual_base_contacts_total'])
    return better_or_equal and strictly_better

def pareto_frontier(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(row) for index, row in enumerate(rows) if not any((other_index != index and _dominates(other, row) for other_index, other in enumerate(rows)))]

def _grid_distance(row: Mapping[str, Any], previous: UtilityWeights) -> int:
    pairs = ((W_TCR_GRID.index(float(row['tcr'])), W_TCR_GRID.index(previous.tcr)), (W_MKS_GRID.index(float(row['mks'])), W_MKS_GRID.index(previous.mks)), (W_RISK_GRID.index(float(row['risk'])), W_RISK_GRID.index(previous.risk)))
    return sum((abs(left - right) for left, right in pairs))

def calibrate_weights(scenarios: Optional[List[CalibrationScenario]]=None, *, p_fix_params: PFixParams=STAGE1_FROZEN_P_FIX, previous_weights: UtilityWeights=STAGE1_FIXED_UTILITY_WEIGHTS) -> WeightFit:
    """One-shot 27-grid Pareto calibration under the predeclared tie-break."""
    scenarios = scenarios or default_calibration_scenarios()
    sensitivity: List[Dict[str, Any]] = []
    for tcr in W_TCR_GRID:
        for mks in W_MKS_GRID:
            for risk in W_RISK_GRID:
                weights = UtilityWeights(tcr=tcr, mks=mks, cost=W_COST_FIXED, risk=risk)
                metrics, decisions = paired_metrics_for_weights(weights, scenarios, p_fix_params=p_fix_params)
                sensitivity.append({'tcr': tcr, 'mks': mks, 'cost': W_COST_FIXED, 'risk': risk, **metrics.as_dict(), 'decisions': decisions})
    frontier = pareto_frontier(sensitivity)
    for row in sensitivity:
        row['pareto_nondominated'] = any((all((row[key] == candidate[key] for key in ('tcr', 'mks', 'cost', 'risk'))) for candidate in frontier))
    selected = min(frontier, key=lambda row: (_grid_distance(row, previous_weights), W_TCR_GRID.index(float(row['tcr'])), W_MKS_GRID.index(float(row['mks'])), W_RISK_GRID.index(float(row['risk']))))
    weights = UtilityWeights(tcr=float(selected['tcr']), mks=float(selected['mks']), cost=float(selected['cost']), risk=float(selected['risk']))
    metrics = PairedMetrics(**{key: selected[key] for key in PairedMetrics.__dataclass_fields__})
    return WeightFit(weights=weights, metrics=metrics, sensitivity=sensitivity, pareto_frontier=frontier, previous_weights=previous_weights)

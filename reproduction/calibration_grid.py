"""One-shot Stage-2 freeze and Stage-3 corrected selector reanalysis."""
from __future__ import annotations
from dataclasses import asdict
from controller.calibration.selector_config_v2 import STAGE1_FIXED_UTILITY_WEIGHTS
from controller.calibration.selector_config_v2 import STAGE1_FROZEN_P_FIX
from controller.calibration.weight_grid import TIE_BREAK_RATIONALE
from controller.calibration.weight_grid import calibrate_weights
from controller.calibration.weight_grid import default_calibration_scenarios
from domains.injection import PSR_INJECTIONS
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order
from controller.selector import PFixParams
from controller.selector import ScopeSelector
from controller.selector import _LLM_COST
from controller.selector import _SUBTREE_SIZE
from controller.selector import p_fix
from controller.selector import risk_term

def _label_alignment() -> dict:
    expected = {'inj_psr_001': Scope.P, 'inj_psr_003': Scope.T, 'inj_psr_004': Scope.M}
    physical = {'inj_psr_001': 'primitive move_to failure hook (wheel stuck)', 'inj_psr_003': 'Task target sample_4 made unreachable', 'inj_psr_004': 'Mission resource/actor allocation invalidated by critical energy'}
    return {key: {'expected_cause_level': PSR_INJECTIONS[key].expected_cause_level.value, 'physical_failure_level': value.value, 'aligned': PSR_INJECTIONS[key].expected_cause_level is value, 'physical_effect': physical[key], 'source': 'domains/injection/loader.py PSR_INJECTIONS/apply_effect'} for key, value in expected.items()}

def _p_anchor_robustness(weights) -> dict:
    selector = ScopeSelector(weights, STAGE1_FROZEN_P_FIX)
    posterior = {scope: 0.95 if scope is Scope.P else 0.05 / 3.0 for scope in SCOPES}
    decision = selector.select(None, posterior)
    parameterized_gap = decision.scores[Scope.T] - decision.scores[Scope.P]
    nonparam_gap = -weights.mks * (_SUBTREE_SIZE[Scope.T] - _SUBTREE_SIZE[Scope.P]) - weights.cost * (_LLM_COST[Scope.T] - _LLM_COST[Scope.P]) - weights.risk * 0.5
    return {'parameterized': {'selected_scope': decision.sigma.value, 'u_t_minus_u_p': parameterized_gap, 'lambda_point_estimate': STAGE1_FROZEN_P_FIX.lambda_, 'lambda_ci_95': [1.944, 2.711], 'ci_entirely_above_reachability_threshold': False}, 'nonparametric': {'p_fix_P_given_P': 1.0, 'p_fix_T_given_P': 1.0, 'tcr_term_difference': 0.0, 'u_t_minus_u_p': nonparam_gap, 'direction_agrees_with_parameterized': nonparam_gap < 0 and parameterized_gap < 0, 'model_form_note': 'direct Batch-A rates represent zero overscope gain; analytic p_fix can only approximate zero gain as lambda grows'}}

def build_stage2_payload() -> dict:
    fit = calibrate_weights()
    scenarios = default_calibration_scenarios()
    return {'status': 'STAGE2_WEIGHTS_FROZEN', 'definitions_commit': '4ac8008a', 'stage1_p_fix': asdict(STAGE1_FROZEN_P_FIX), 'old_weights': asdict(STAGE1_FIXED_UTILITY_WEIGHTS), 'new_weights': asdict(fit.weights), 'weights_changed': fit.weights != STAGE1_FIXED_UTILITY_WEIGHTS, 'selected_metrics': asdict(fit.metrics), 'tie_break_rule_id': fit.tie_break_rule_id, 'tie_break_rationale': TIE_BREAK_RATIONALE, 'pareto_frontier_count': len(fit.pareto_frontier), 'pareto_frontier': fit.pareto_frontier, 'sensitivity_27': fit.sensitivity, 'p_anchor_robustness_without_full_distribution': _p_anchor_robustness(fit.weights), 'label_alignment_narrow_check': _label_alignment(), 'scenarios': [{'scenario_id': case.scenario_id, 'posterior': {scope.value: value for scope, value in case.delta_dist.items()}, 'base_reachable': case.base_reachable, 'c_edge': [scope.value for scope in sorted(case.c_edge, key=scope_order)], 'cause_level': case.cause_level.value, 'agent_type': case.agent_type, 'capability_provenance': case.capability_provenance} for case in scenarios]}

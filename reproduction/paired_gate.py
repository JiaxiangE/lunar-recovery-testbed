"""Predeclared per-cell paired summaries; execution outcomes are not formal labels."""
from collections import Counter
import math
import random
COSTS = ('dispatch_count', 'failed_action_count', 'work_time_s', 'makespan_s', 'energy_wh')
COMMON_CATEGORIES = ('schema_vocabulary', 'entity_domain', 'actor_capability', 'resource_authorization', 'communication_policy')
DEGENERATE_LIMIT = 'The empirical paired sample has no variation (or collapsed bootstrap percentiles). This interval does not establish a zero population effect or equivalence of the policies.'

def exact_mcnemar(on_only, off_only):
    n = on_only + off_only
    return 1.0 if n == 0 else min(1.0, 2 * sum((math.comb(n, k) for k in range(min(on_only, off_only) + 1))) / 2 ** n)

def paired_mean_ci(values, resamples=10000, seed=1003):
    """95% percentile paired bootstrap, exploratory; never delete zero differences."""
    if not values:
        return {'n': 0, 'mean': None, 'ci95': None, 'degenerate_ci': False, 'interpretation_limit': 'No observed pairs; no interval estimated.'}
    n = len(values)
    if min(values) == max(values):
        return {'n': n, 'mean': sum(values) / n, 'ci95': [values[0], values[0]], 'degenerate_ci': True, 'interpretation_limit': DEGENERATE_LIMIT}
    rng = random.Random(seed)
    means = sorted((sum((values[rng.randrange(n)] for _ in range(n))) / n for _ in range(resamples)))

    def quantile(q):
        p = (len(means) - 1) * q
        i = int(p)
        return means[i] + (means[min(i + 1, len(means) - 1)] - means[i]) * (p - i)
    interval = [quantile(0.025), quantile(0.975)]
    degenerate = interval[0] == interval[1]
    return {'n': n, 'mean': sum(values) / n, 'ci95': interval, 'degenerate_ci': degenerate, 'interpretation_limit': DEGENERATE_LIMIT if degenerate else 'Exploratory empirical paired bootstrap; not an equivalence test.'}

def _is_common_rejected(row):
    return row.get('common_boundary', {}).get('accepted') is False or any((a.get('status') == 'common_boundary_rejected' for a in row.get('arms', {}).values()))

def _gate_rejected(row):
    return not _is_common_rejected(row) and row.get('arms', {}).get('on', {}).get('gate_accepted') is False

def _selection_counts(group):
    rejected = [r for r in group if _is_common_rejected(r)]
    categories = Counter((r.get('common_boundary', {}).get('category', 'unclassified') for r in rejected))
    adverse = [r for r in group if _gate_rejected(r) and r.get('arms', {}).get('off', {}).get('terminal_goal_met') is True]
    primary = Counter(((r['arms']['on'].get('gate_rejection_reason_codes') or ['unclassified'])[0] for r in adverse))
    return {'common_boundary_rejected_n': len(rejected), 'common_boundary_rejected_by_category': {k: categories[k] for k in COMMON_CATEGORIES}, 'common_boundary_unclassified_n': categories['unclassified'], 'stage_denominators': {'planned_candidates': len(group), 'generation_started': sum((r.get('generation_attempts', 0) > 0 for r in group)), 'parse_failed_n': sum((r.get('generation_status') == 'parse_failed' for r in group)), 'parsed_candidates': sum((r.get('generation_status') == 'parsed' for r in group)), 'common_boundary_evaluated_candidates': sum(('common_boundary' in r or _is_common_rejected(r) for r in group)), 'common_boundary_passed_candidates': sum((r.get('common_boundary', {}).get('accepted') is True for r in group)), 'recovery_eligible_candidates': sum((r.get('eligible') is True for r in group)), 'gate_decisions_observed': sum((not _is_common_rejected(r) and type(r.get('arms', {}).get('on', {}).get('gate_accepted')) is bool for r in group))}, 'denominator_definition': 'candidate counts, never two-arm counts; parse includes JSON/envelope failures; five common categories use first failing stage then first failing step; eligible additionally excludes initial maintenance', 'rejected_but_off_goal_met_n': len(adverse), 'rejected_but_off_goal_met_by_primary_gate_reason': dict(primary), 'adverse_gate_reason_counting': 'first structured gate reason per candidate; missing historical codes remain unclassified, never inferred from prose', 'goal_met_with_illegal_suffix_n': sum((r.get('recovery_required') is True and r['arms']['off'].get('execution_scorer_matches_main_contract') is True and (r['arms']['off'].get('goal_met_with_illegal_suffix') is True) for r in adverse)), 'adverse_initial_maintenance_n': sum((r.get('recovery_required') is False for r in adverse)), 'adverse_suffix_classification_unavailable_n': sum(('goal_met_with_illegal_suffix' not in r['arms']['off'] for r in adverse))}

def _policy_value(row, arm):
    if row.get('pairing_error'):
        return None
    value = row.get('arms', {}).get(arm, {}).get('policy_new_task_completed')
    return value if type(value) is bool else None

def _binary(rows):
    counts = {'both_complete': 0, 'on_only': 0, 'off_only': 0, 'neither_complete': 0}
    complete = []
    marginal = {}
    for arm in ('on', 'off'):
        values = [_policy_value(r, arm) for r in rows]
        successes, missing = (values.count(True), values.count(None))
        marginal[arm] = {'planned_n': len(rows), 'known_n': len(rows) - missing, 'completed': successes, 'unknown': missing, 'completion_fraction_bounds': [successes / len(rows), (successes + missing) / len(rows)] if rows else None}
    for row in rows:
        on, off = (_policy_value(row, 'on'), _policy_value(row, 'off'))
        if on is None or off is None:
            continue
        complete.append(int(on) - int(off))
        key = 'both_complete' if on and off else 'on_only' if on else 'off_only' if off else 'neither_complete'
        counts[key] += 1
    return {'n': len(rows), 'complete_pair_n': len(complete), 'missing_pair_n': len(rows) - len(complete), 'marginal': marginal, 'paired_2x2': counts, 'paired_completion_difference_on_minus_off': paired_mean_ci(complete), 'exact_mcnemar_two_sided_p': exact_mcnemar(counts['on_only'], counts['off_only']) if complete else None, 'inference_scope': 'exploratory complete pairs; missing outcomes shown as bounds, not imputed'}

def analyze_pairs(rows):
    cells = {}
    for cell_id in sorted({r['cell_id'] for r in rows}):
        group = [r for r in rows if r['cell_id'] == cell_id]
        eligible = [r for r in group if r.get('eligible') is True]
        costs = {}
        for metric in COSTS:
            values = []
            for row in eligible:
                if row.get('pairing_error'):
                    continue
                on = row.get('arms', {}).get('on', {}).get(metric)
                off = row.get('arms', {}).get('off', {}).get(metric)
                if on is not None and off is not None:
                    values.append(off - on)
            costs[metric] = paired_mean_ci(values)
        cells[cell_id] = {'all_plan_units': _binary(group), 'eligible': _binary(eligible), 'generation_flow': dict(Counter((r.get('generation_status', 'not_started') for r in group))), 'arm_flow': {arm: dict(Counter((r.get('arms', {}).get(arm, {}).get('status', 'pending') for r in group))) for arm in ('on', 'off')}, 'maintenance_n': sum((r.get('recovery_required') is False for r in group)), 'pairing_error_n': sum((bool(r.get('pairing_error')) for r in group)), 'gate_rejected_n': sum((_gate_rejected(r) for r in group)), **_selection_counts(group), 'accepted_but_world_failed_n': sum((r.get('arms', {}).get('on', {}).get('gate_accepted') is True and r.get('arms', {}).get('on', {}).get('status') == 'execution_failed' for r in group)), 'cost_saved_off_minus_on': costs, 'verification_overhead_s': {key: paired_mean_ci([r['arms']['on'][key] for r in eligible if r.get('arms', {}).get('on', {}).get(key) is not None]) for key in ('gate_treatment_s', 'solver_s')}, 'accepted_only_auxiliary': {'accepted_n': sum((r.get('arms', {}).get('on', {}).get('gate_accepted') is True for r in eligible)), 'executed_goal_met_n': sum((r.get('arms', {}).get('on', {}).get('gate_accepted') is True and r.get('arms', {}).get('on', {}).get('terminal_goal_met') is True for r in eligible)), 'eligible_denominator': len(eligible)}, 'formal_confusion_matrix': None, 'formal_label_limitation': 'No independent formal-compliance labels for generated plans; world failures are not FPR/FNR labels.'}
    return {'cells': cells, 'cross_cell_success_rate': None, 'common_boundary': {key: value for key, value in _selection_counts(rows).items() if key.startswith('common_boundary') or key in {'stage_denominators', 'denominator_definition'}}, 'null_interpretation': 'No difference detected; no equivalence or no-effect conclusion is licensed.', 'difference_conventions': 'completion: on-off; saved execution costs: off-on; preparation/solver overhead separate', 'ci_method': 'exploratory 95% percentile paired bootstrap; 10000 resamples; seed=1003; no multiplicity-adjusted confirmatory claims'}

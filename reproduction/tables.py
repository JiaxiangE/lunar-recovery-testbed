"""Rebuild the paper studies from their explicit source indexes, without inference."""
from collections import Counter
from dataclasses import asdict
import gzip
import json
import statistics
import re
from pathlib import Path
from reproduction.records import record_path, public_locations
from reproduction.accounting import ROOT
from reproduction.accounting import accounting
from reproduction.accounting import process
from reproduction.accounting import count
from reproduction.accounting import mechanism_processes

def read(name):
    path = record_path(name)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding='utf-8'))

def records(name):
    return [json.loads(line) for line in record_path(name).read_text(encoding='utf-8').splitlines() if line.strip()]

def main_comparison():
    index = read('data/indexes/main_comparison.json')
    rows = []
    for spec in index['rows']:
        row = {k: v for k, v in spec.items() if k != 'source_calls'}
        trial = read(spec['source_trial'])
        cost, requests = accounting(spec['source_calls'])
        row.update(cost=cost, **process(row, trial, requests))
        if row['method'] == 'llmp':
            reference = index['original_shared_input_specifications'][f"{row['cell']}:{row['seed']}"]
            assert all((trial[k] == v for k, v in reference['fields'].items()))
            assert [[a['policy'], a['goal']] for a in trial['attempts']] == reference['policy_goals']
        rows.append(row)
    native = []
    for spec in index['native_references']:
        row = dict(spec)
        row.update(cost=accounting([])[0], **process(row, read(row['source_trial']), []))
        native.append(row)
    counts = count(rows)
    assert counts == index['expected_method_counts']
    assert len(rows) == 36 and len(native) == 12
    assert sum((r['tokens'] for r in counts)) == 3088645
    return {'independent_inputs': 6, 'response_seeds_are_not_independent_tasks': True, 'rows': rows, 'method_counts': counts, 'native_references': native, 'known_tokens_lower_bound': sum((r['tokens'] for r in counts))}

def successful_cases():
    index = read('data/indexes/successful_task_cases.json')
    result = []
    for spec in index['rows']:
        trial = read(spec['source_trial'])
        value = dict(spec)
        assert trial['task_goal_met'] is True and trial['parent_goal_met'] == spec['parent']
        for key, expected in spec.items():
            if isinstance(expected, dict) and 'source_files' in expected:
                actual, _ = accounting(expected['source_files'])
                assert actual == expected, (spec['cell'], spec['arm'], key)
                value[key] = actual
        result.append(value)
    assert len(result) == 9
    return {'scope': 'nine configuration-specific examples, not one fixed-cohort success rate', 'rows': result, 'preceding_failed_calls_retained': True}

def scope_subset():
    from reproduction.scope_selection import build
    value = build(ROOT)
    unique = [r for r in value['decisions'] if r['offline_recomputation'] and (not r.get('duplicate_decision_of'))]
    changed = sum((r['offline_recomputation']['communication_arithmetic']['changed'] for r in unique))
    observed = {'native_records': len(value['decisions']), 'unique_decisions': len(unique), 'communication_changed': changed}
    assert observed == read('data/indexes/scope_subset.json')['expected']
    value['paper_denominators'] = observed
    return value

def grounding_and_gate():
    from reproduction.paired_gate import analyze_pairs
    grounding = records('data/grounding/trials.jsonl')
    assert len(grounding) == 120 and len({r['pair_id'] for r in grounding}) == 60
    by_arm = {}
    for arm in sorted({r['arm'] for r in grounding}):
        group = [r for r in grounding if r['arm'] == arm]
        by_arm[arm] = {'realizations': len(group), 'hard_success_n': sum((r['hard_success_met'] for r in group)), 'argument_correct': sum((r['argument_correct_count'] for r in group)), 'argument_observations': sum((r['argument_observation_count'] for r in group))}
    gate = records('data/contract_gate/trials.jsonl')
    assert len(gate) == 30
    analysis = analyze_pairs(gate)
    assert analysis == read('data/contract_gate/summary.json')['analysis']
    return {'grounding': {'candidate_denominator': 60, 'realization_denominator': 120, 'arms': by_arm}, 'gate': {'candidate_denominator': 30, 'realization_denominator': 60, 'analysis': analysis}, 'scope': 'different historical semantics remain separate; no outcome filtering or reinterpretation'}

def calibration_and_primitive():
    from controller.scope import Scope
    from controller.calibration.assigned_scope_corpus import build_pt_assigned_cells
    from controller.calibration.p_fix import calibrate_p_fix_profile
    from reproduction.calibration_grid import build_stage2_payload
    summary = read('data/scope_calibration/repair_benefit/summary.json')
    trials = records('data/scope_calibration/repair_benefit/trials.jsonl')
    cells, labels = build_pt_assigned_cells(repeats=1, seeds=[101], cause_levels=[Scope.P, Scope.T, Scope.M])
    truth = {cell.fixture_id: label.cause_level for cell, label in zip(cells, labels)}
    observations = [(Scope(r['assigned_scope']), truth[r['fixture_id']], r['fixed']) for r in trials if r['fixed'] is not None]
    fit = calibrate_p_fix_profile(observations)
    assert len(observations) == 600 and asdict(fit.params) == summary['fitted_params']
    grid = build_stage2_payload()
    expected = read('data/scope_calibration/utility_weights/stage2_weights.json')['stage2']
    for key in ('new_weights', 'sensitivity_27', 'selected_metrics', 'pareto_frontier'):
        assert grid[key] == expected[key], key
    probes = []
    for suffix in ('', '/v2'):
        rows = records('data/primitive_diagnosis' + suffix + '/trials.jsonl')
        assert len(rows) == 30
        probes.append({'configuration': suffix or 'v1', 'n': len(rows), 'scope_counts': dict(Counter((r['sigma'] for r in rows))), 'by_symptom': {s: dict(Counter((r['sigma'] for r in rows if r['symptom'] == s))) for s in sorted({r['symptom'] for r in rows})}, 'tokens': sum((r['tokens'] for r in rows))})
    return {'P_T_observations': 600, 'fitted_params': asdict(fit.params), 'log_likelihood': fit.log_likelihood, 'weight_grid': grid, 'primitive_probes': probes, 'M_S_fit_observations': 0}

def capability_pruning():
    from reproduction.capability import unit
    saved = read('data/local_repair/capability_screen/summary.json')
    rows = []
    for spec in [saved['historical_control']] + saved['new_units']:
        actual, _ = unit(ROOT, record_path(spec['source_trial']), spec['label'])
        for key in ('logical_requests', 'physical_attempts', 'known_tokens_lower_bound', 'unknown_usage_attempts'):
            assert actual['full_cost'][key] == spec['full_cost'][key]
        rows.append(actual)
    before, after = [r['full_cost']['known_tokens_lower_bound']['total_tokens'] for r in rows]
    assert (before, after) == (67144, 56387)
    return {'rows': rows, 'full_path_token_reduction': 1 - after / before, 'scope': 'matched saved diagnosis; capability-feasible scope screening, not a new general success rate'}

def timing_records():
    saved = read('data/embedded/verification/core_measurement.json')
    for row in saved['records']:
        warm = row['samples'][1:]
        row['recomputed_warm_denominators'] = {}
        for key, expected in row['warm_median_s'].items():
            known = [r[key] for r in warm if r[key] is not None]
            assert (statistics.median(known) if known else None) == expected
            row['recomputed_warm_denominators'][key] = {'total': len(warm), 'observed': len(known), 'unavailable': len(warm) - len(known)}
    return saved

def orin_memory():
    folder = ROOT / 'data/diagnostic_refinement/orin_platform'
    hardware = (folder / 'hardware.txt').read_text(encoding='utf-8')
    telemetry = (folder / 'tegrastats.log').read_text(encoding='utf-8')
    hwm = [int(x) for x in re.findall('VmHWM:\\s+(\\d+)\\s+kB', hardware)]
    ram = [int(x) for x in re.findall('RAM (\\d+)/\\d+MB', telemetry)]
    swap = [int(x) for x in re.findall('SWAP (\\d+)/\\d+MB', telemetry)]
    assert hwm == [9732072] and max(ram) == 10446 and (max(swap) == 1)
    return {'server_high_water_RSS_KiB': hwm[0], 'whole_system_RAM_max_MB_as_reported': max(ram), 'swap_max_MB_as_reported': max(swap), 'RAM_observation_count': len(ram), 'scope': 'original server snapshot and whole-system telemetry overlap; not additive, not new v4 hardware measurements'}

def build():
    from reproduction.embedded import build as embedded_build
    from reproduction.task_views import summarize
    from reproduction.diagnosis import analyze
    from reproduction.communication import build as link_build
    view = summarize(ROOT)
    psr = [r for r in view['rows'] if r['cell'] == 'R_T_psr_collection_position_drift']
    tokens = {r['arm']: r['full_cost']['known_tokens_lower_bound']['total_tokens'] for r in psr}
    assert tokens == {'ours_full': 12077, 'ours_view': 8494, 'flat_full': 11986}
    assert all((r['parent_goal_met'] and r['dispatch_n'] == 3 for r in psr))
    mechanisms = mechanism_processes()
    retry = [r for r in mechanisms if r['study'] == 'feedback_state_clarity_v1']
    assert {r['method']: r['cost']['known_tokens'] for r in retry} == {'ours': 77396, 'flat': 36014}
    diagnosis = analyze(ROOT / 'data/diagnostic_refinement/confirmed/workstation')
    assert diagnosis['arms'] == read('data/diagnostic_refinement/confirmed/workstation/analysis.json')['arms']
    orin = analyze(ROOT / 'data/diagnostic_refinement/confirmed/orin')
    for row in orin['units']:
        assert row['original_goal_met'] and row['dispatch_n'] == 3
    return {'embedded_goal_focused': embedded_build(), 'main': main_comparison(), 'complete_repair_view': view, 'child_retry_and_link_processes': mechanisms, 'Task_cases': successful_cases(), 'scope_S2': scope_subset(), 'grounding_gate': grounding_and_gate(), 'calibration_primitive': calibration_and_primitive(), 'diagnosis': diagnosis, 'Orin': orin, 'Orin_memory': orin_memory(), 'continuous_link': link_build(ROOT), 'capability_pruning': capability_pruning(), 'gate_timing_record': timing_records(), 'scope_sensitivity_record': read('data/embedded/verification/scope_sensitivity.json')}

def write_tables(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    values = public_locations(build())
    for name, value in values.items():
        (folder / (name + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    return values

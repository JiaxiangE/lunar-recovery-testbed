"""Separate A/B/C estimands and complete path accounting from durable records."""
import json
from pathlib import Path
from reproduction.costs import cost
from reproduction.costs import plus

def read(path):
    from reproduction.records import read_record
    return read_record(path)

def calls_for(root, trial):
    path = next(trial.parent.rglob('calls.jsonl'), None)
    return (path, read(path)) if path else (None, {})

def full_cost(root, calls):
    value = cost(calls)
    prefix = calls.get('experimental_prefix_reuse')
    if prefix:
        path = Path(prefix['source_calls'])
        path = path if path.is_absolute() else root / path
        source = read(path)
        value = plus(cost(source, source['requests'][:prefix['used_n']]), value)
    return value

def operation_costs(root, calls):
    source = None
    prefix = calls.get('experimental_prefix_reuse')
    if prefix:
        path = Path(prefix['source_calls'])
        source = read(path if path.is_absolute() else root / path)
    result = {}
    for op in ('diagnosis', 'D_S', 'D_M', 'D_T', 'flat'):
        value = cost(calls, [q for q in calls.get('requests', []) if q['operation'] == op])
        if source:
            value = plus(cost(source, [q for q in source['requests'][:prefix['used_n']] if q['operation'] == op]), value)
        result[op] = value
    return result

def unit(root, path, label):
    result = {'label': label, 'source_trial': path.relative_to(root).as_posix(), 'status': 'pending', 'goal': None}
    cp, calls = calls_for(root, path)
    if cp:
        result.update(source_calls=cp.relative_to(root).as_posix(), new_cost=cost(calls))
    if not path.exists():
        return (result, calls)
    trial = read(path)
    execution = trial.get('execution') or {}
    result.update(status=trial['status'], goal=trial['original_goal_met'], dispatch_n=trial.get('dispatch_n', 0), world_s=execution.get('elapsed_s'), world_energy_wh=execution.get('energy_consumed_wh'), unit_wall_s=trial.get('wall_s'), local_work=trial.get('local_work'), new_cost=cost(calls), full_cost=None if trial['status'] == 'not_applicable' else full_cost(root, calls), full_cost_by_operation=operation_costs(root, calls) if calls else None, binding=trial.get('repair_process'), pruning=trial.get('task_capability_pruning'), communication=trial.get('communication_protocol'), segment_local_work=trial.get('segment_local_work'))
    native = [a.get('native', {}) for a in trial.get('attempts', [])]
    result.update(initial_scope=native[0].get('selected_scope') if native else None, final_scope=native[-1].get('final_scope') if native else None, diagnosis=[n.get('diagnosis_audit') for n in native], generated_actions=trial.get('mechanism_metrics', {}).get('actions'))
    return (result, calls)

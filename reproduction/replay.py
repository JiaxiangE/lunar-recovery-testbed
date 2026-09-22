"""Strict replay of the retained Task configurations, including their failures."""
from pathlib import Path
from reproduction.tables import ROOT
from reproduction.tables import read

def tasks(output):
    from examples.task_requests import evaluate_unit
    from examples.task_requests import SCHEDULE
    from reproduction.grounded_requests import factory
    from reproduction.feedback_requests import prepared_factory
    import reproduction.state_requests as bounded
    from reproduction.state_assessment import extract_chain
    output = Path(output)
    results = []

    def compare(path, row, calls):
        expected = read(path)
        for key in ('status', 'candidate', 'task_goal_met', 'parent_goal_met', 'final_facts', 'dispatch_n'):
            assert row.get(key) == expected.get(key), (str(path), key, row.get(key), expected.get(key))
        assert (row.get('execution') or {}).get('executed_plan') == (expected.get('execution') or {}).get('executed_plan')
        assert calls['actual_api_calls'] == 0 and len(calls['strict_replay_checks']) == 1
        results.append({'source_trial': str(path), 'Task': row['task_goal_met'], 'parent': row['parent_goal_met'], 'status': row['status'], 'strict_request_checks': calls['strict_replay_checks'], 'new_model_calls': 0})
    for cell, arm in SCHEDULE:
        if cell == 'cross_actor' and arm == 'D_T-full':
            continue
        folder = ROOT / 'data/local_repair/task_generation/runs' / cell / arm
        row, calls = evaluate_unit(cell, arm, factory(output / 'grounded' / cell / arm, source=folder / 'calls.jsonl'))
        compare((folder / 'trial.json').relative_to(ROOT), row, calls)
    for cell, arm in [('B05_psr_strict_disconnected', 'flat-full')]:
        folder = ROOT / 'data/local_repair/task_feedback/runs' / cell / arm
        row, calls = evaluate_unit(cell, arm, prepared_factory(output / 'feedback' / cell / arm, cell, arm, replay_source=folder / 'calls.jsonl'))
        compare((folder / 'trial.json').relative_to(ROOT), row, calls)
    for layer in ('L1', 'L2'):
        source = ROOT / bounded.ROOT

        def make(case, view):
            session = bounded.make_session(bounded.ROOT, layer, case, output / layer / 'calls.jsonl', source=source / layer / 'calls.jsonl')
            original = session.request
            session.request = lambda *a, **kw: extract_chain(original(*a, **kw))
            return session
        row, calls = evaluate_unit('cross_actor', 'D_T-full', make)
        compare(bounded.ROOT / layer / 'evaluation/trial.json', row, calls)
    return {'records': results, 'scope': 'nine reported Task configurations and their two declared preceding requests; not a new experiment denominator'}

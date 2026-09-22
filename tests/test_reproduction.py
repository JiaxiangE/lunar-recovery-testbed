from copy import deepcopy
from dataclasses import replace
import json
import pytest
from reproduction.tables import build
from reproduction.replay import tasks
from reproduction.records import ROOT, record_path
from reproduction.identity import historical_context, require_same_request

def test_paper_arithmetic_and_saved_dispatch_replay():
    value = build()
    assert value['main']['known_tokens_lower_bound'] == 3088645
    assert value['scope_S2']['paper_denominators'] == {'native_records': 30, 'unique_decisions': 26, 'communication_changed': 7}
    assert len(value['continuous_link']['rows']) == 6
    assert [r['cost']['known_tokens'] for r in value['embedded_goal_focused']] == [6665, 38435]

def test_all_reported_task_configurations_and_two_precursors_replay(tmp_path):
    value = tasks(tmp_path)
    assert len(value['records']) == 11
    assert sum((r['Task'] for r in value['records'])) == 9
    assert all((r['new_model_calls'] == 0 for r in value['records']))

@pytest.mark.parametrize('change', ['goal', 'state', 'actor', 'effects', 'feedback'])
def test_source_locator_mapping_cannot_hide_changed_request(change):
    source = json.loads((ROOT / 'data/local_repair/task_generation/runs/B05_psr_strict_disconnected/D_T-full/calls.jsonl').read_text())['requests'][0]
    actual = deepcopy(source)
    packet = json.loads(actual['user'])
    if change == 'goal':
        packet['goal']['positive'].append('forged_goal')
    elif change == 'state':
        packet['request_initial_facts'].append('forged_state')
    elif change == 'actor':
        packet['node']['assigned_agent_id'] = 'rover_2'
    elif change == 'effects':
        packet['supporting_context']['content']['forged_action_effect'] = 'in_base_storage(sample_1)'
    else:
        packet['new_feedback'] = 'invented feedback'
    actual['user'] = json.dumps(packet, ensure_ascii=False, separators=(',', ':'))
    with pytest.raises(ValueError, match='strict replay mismatch'):
        require_same_request(source, actual)

def test_source_locator_adapter_preserves_all_action_semantics():
    from examples.recovery_inputs import build_case
    case = build_case('R_T_lava_collection_position_drift')
    changed, info = historical_context(case.nominal, case.original_information)
    assert replace(changed, actions=case.nominal.actions, observation=case.nominal.observation) == case.nominal
    for a, b in zip(changed.actions, case.nominal.actions):
        assert replace(a, source_references=b.source_references) == b

def test_missing_records_and_path_escape_fail_without_network_fallback():
    with pytest.raises(ValueError):
        record_path('../../outside')
    with pytest.raises(FileNotFoundError):
        record_path('data/no_such_record.json').read_bytes()

from copy import deepcopy
import pytest
from controller.api import recover
from examples.recovery_inputs import build_case
from models.generation import response_key
from planning.repair.domain_hierarchy import goal_record, HierarchyLimits
from models.fixtures import ready_scripts

@pytest.mark.parametrize('cell,original,alternative', [('R_T_psr_collection_position_drift', True, None), ('E05_lava_communication_return', True, None), ('R_S_construction_prefix_source_inj005', False, True)])
def test_three_worlds_use_real_recovery_and_execution(tmp_path, cell, original, alternative):
    case = build_case(cell)
    initial = deepcopy(case.world.snapshot_state())
    row, calls = recover(case, tmp_path)
    assert row['status'] == 'executed'
    assert row['original_goal_met'] is original
    assert row['degraded_goal_met'] is alternative
    assert row['dispatch_n'] > 0 and calls['actual_api_calls'] == 0
    assert case.world.snapshot_state() == initial
    assert row['execution']['executed_plan'] == row['execution']['verification']['normalized_plan']

@pytest.mark.parametrize('bad', [{'chain': ['return_to_base']}, {'chain': [None]}, {'chain': [{'primitive': 'return_to_base', 'params': []}]}])
def test_bad_candidates_retry_without_dispatch_or_extra_call(tmp_path, bad):
    case = build_case('B05_psr_strict_disconnected')

    def scripts(c, p):
        values = ready_scripts(c, p)
        key = response_key('D_T', goal_record(p))
        values[key] = [bad, {'chain': [{'primitive': 'return_to_base', 'params': {}}]}]
        return values
    row, calls = recover(case, tmp_path, scripts_builder=scripts, experimental_limits_override=HierarchyLimits(2, 2, 6, 2))
    assert row['status'] == 'executed' and row['logical_model_calls'] == 2 and (row['dispatch_n'] == 1)
    events = row['attempts'][0]['native']['generated_hierarchy']
    assert events[0]['format']['accepted'] is False and events[1]['format']['accepted'] is True
    assert calls['actual_api_calls'] == 0

def test_flat_decline_is_not_budget_exhaustion_or_policy_permission(tmp_path):
    case = build_case('B05_psr_strict_disconnected')

    def scripts(c, p):
        return {response_key('flat', goal_record(p)): [{'decline': True}]}
    row, calls = recover(case, tmp_path, method='flat', scripts_builder=scripts, experimental_limits_override=HierarchyLimits(2, 2, 6, 2))
    assert row['status'] == 'model_declined' and row['logical_model_calls'] == 1
    assert row['dispatch_n'] == 0 and row['degraded_goal_met'] is None

from copy import deepcopy
import pytest
from domains.worlds.psr_world import PSRWorld
from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
from domains.common import build_problem, atom
from execution.binding import verify_plan, execute_plan

def world(n=3):
    w = PSRWorld(n_samples=n, semantics_profile=STRICT_PSR_PROFILE)
    w.reset(0)
    return w

def step(name, actor='rover_1', **params):
    return {'primitive': name, 'agent_id': actor, 'params': params}

@pytest.mark.parametrize('distance,allowed', [(0.0, True), (0.5, True), (0.5001, False)])
def test_collect_location_boundary_matches_world_and_gate(distance, allowed):
    w = world()
    pos = w.samples['sample_1']['location']
    w.agents['rover_1']['position'] = (pos[0] + distance, pos[1], pos[2])
    p = build_problem(w, 'psr', goal_facts={atom('stored', 'sample_1')})
    plan = [step('sample_collect', sample_id='sample_1'), step('sample_store', sample_id='sample_1')]
    assert verify_plan(p, plan)['accepted'] is allowed
    assert w.step('sample_collect', 'rover_1', {'sample_id': 'sample_1'}).success is allowed

def test_mixed_cargo_offload_preserves_other_owner_and_unstored_sample():
    w = world()
    w.agents['rover_1']['cargo'] = {'sample_1', 'sample_2'}
    w.agents['rover_2']['cargo'] = {'sample_3'}
    w.agents['rover_1']['facts'].update({'at_base', 'sample_stored'})
    for sid, status in [('sample_1', 'stored'), ('sample_2', 'in_rover'), ('sample_3', 'stored')]:
        w.samples[sid]['status'] = status
    p = build_problem(w, 'psr', goal_facts={atom('in_base_storage', 'sample_1')})
    result = execute_plan(w, p, [step('sample_offload', base='base')], capture_transitions=True)
    assert result['execution_success'] and result['goal_met']
    assert w.base_storage == {'sample_1'} and w.agents['rover_1']['cargo'] == {'sample_2'}
    assert w.agents['rover_2']['cargo'] == {'sample_3'}

def test_final_goal_includes_later_deletion_and_unknown_entity_does_not_dispatch():
    from examples.recovery_inputs import build_case
    c = build_case('B05_psr_strict_disconnected')
    assert not verify_plan(c.nominal, [step('return_to_base'), step('move_to', target='sample_1')])['accepted']
    before = deepcopy(c.world.snapshot_state())
    result = execute_plan(c.world, c.nominal, [step('move_to', target='not_an_entity')])
    assert result['dispatch_n'] == 0 and c.world.snapshot_state() == before

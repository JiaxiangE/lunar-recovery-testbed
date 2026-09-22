from copy import deepcopy
from examples.recovery_inputs import build_case
from integrations.isaac.candidate_motion import compile_motion, dispatch_motion

def test_actual_candidate_coordinates_and_primitive_sequence_reach_actuator():
    case = build_case('B05_psr_strict_disconnected')
    plan = [{'primitive': 'move_to', 'agent_id': 'rover_1', 'params': {'target': [0.0, 0.0, 0.0]}}, {'primitive': 'return_to_base', 'agent_id': 'rover_1', 'params': {}}]
    compiled = compile_motion(case.world, case.nominal, plan, actor_map={'rover_1': 'husky'})
    seen = []

    def actuator(command):
        seen.append(command)
        return {'received_command': deepcopy(command), 'reached': True, 'frames': [{'x': 2.0, 'y': 2.0}, {'x': 0.1, 'y': 0.0}]}
    result = dispatch_motion(compiled, actuator, world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
    assert result['candidate'] == plan and result['dispatch_n'] == 2
    assert [c['primitive'] for c in seen] == ['move_to', 'return_to_base']
    assert all((c['target_xyz'] == [0.0, 0.0, 0.0] for c in seen))
    assert result['motion_targets_reached'] and result['physical_parent_goal_met'] is None

def test_symbolically_rejected_candidate_never_dispatches():
    case = build_case('B05_psr_strict_disconnected')
    compiled = compile_motion(case.world, case.nominal, [{'primitive': 'return_to_base', 'agent_id': 'relay_1', 'params': {}}], actor_map={'relay_1': 'husky'})
    result = dispatch_motion(compiled, lambda _: (_ for _ in ()).throw(AssertionError('dispatch')), world=case.world, problem=case.nominal, actor_map={'relay_1': 'husky'})
    assert result['status'] == 'symbolic_rejected' and result['dispatch_n'] == 0

def test_legal_nonmotion_plan_stops_before_any_motion_dispatch():
    case = build_case('B02_psr_connected_named_store')
    compiled = compile_motion(case.world, case.nominal, [{'primitive': 'sample_store', 'agent_id': 'rover_1', 'params': {'sample_id': 'sample_1'}}], actor_map={'rover_1': 'husky'})
    assert compiled['symbolic_verification']['accepted']
    result = dispatch_motion(compiled, lambda _: (_ for _ in ()).throw(AssertionError('dispatch')), world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
    assert result['dispatch_n'] == 0 and result['status'] == 'motion_adapter_unsupported_primitive'

def test_changed_or_failed_command_never_dispatches_suffix():
    case = build_case('B05_psr_strict_disconnected')
    plan = [{'primitive': 'return_to_base', 'agent_id': 'rover_1', 'params': {}}] * 2
    compiled = compile_motion(case.world, case.nominal, plan, actor_map={'rover_1': 'husky'})
    for mode in ('changed', 'failed', 'no_frames'):

        def actuator(c):
            echo = deepcopy(c)
            if mode == 'changed':
                echo['target_xyz'] = [9.0, 9.0, 0.0]
            return {'received_command': echo, 'reached': mode != 'failed', 'frames': [] if mode == 'no_frames' else [{'x': 9.0, 'y': 9.0}]}
        result = dispatch_motion(compiled, actuator, world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
        assert result['dispatch_n'] == 1 and (not result['motion_targets_reached'])

def test_mutating_compiled_coordinates_cannot_reuse_old_acceptance():
    case = build_case('B05_psr_strict_disconnected')
    plan = [{'primitive': 'return_to_base', 'agent_id': 'rover_1', 'params': {}}]
    compiled = compile_motion(case.world, case.nominal, plan, actor_map={'rover_1': 'husky'})
    compiled['commands'][0]['target_xyz'] = [9.0, 9.0, 0.0]
    result = dispatch_motion(compiled, lambda _: (_ for _ in ()).throw(AssertionError('dispatch')), world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
    assert result['status'] == 'motion_binding_changed' and result['dispatch_n'] == 0

def test_actuator_exception_keeps_unknown_effects_and_never_dispatches_suffix():
    case = build_case('B05_psr_strict_disconnected')
    plan = [{'primitive': 'return_to_base', 'agent_id': 'rover_1', 'params': {}}] * 2
    compiled = compile_motion(case.world, case.nominal, plan, actor_map={'rover_1': 'husky'})

    def actuator(command):
        raise TimeoutError('response lost after possible dispatch')
    result = dispatch_motion(compiled, actuator, world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
    assert result['actuator_calls_n'] == 1 and result['dispatch_n'] is None
    assert result['status'] == 'actuator_outcome_unknown' and (not result['motion_targets_reached'])

def test_arrival_boolean_cannot_override_actual_pose_and_noop_is_legal():
    case = build_case('B05_psr_strict_disconnected')
    plan = [{'primitive': 'return_to_base', 'agent_id': 'rover_1', 'params': {}}]
    compiled = compile_motion(case.world, case.nominal, plan, actor_map={'rover_1': 'husky'})
    for xy, expected in (([9.0, 9.0], 'motion_target_observation_mismatch'), ([0.1, 0.0], 'motion_commands_completed')):
        result = dispatch_motion(compiled, lambda c: {'received_command': c, 'reached': True, 'frames': [], 'final_xy': xy}, world=case.world, problem=case.nominal, actor_map={'rover_1': 'husky'})
        assert result['status'] == expected

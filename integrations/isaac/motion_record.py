"""One content-bound motion/pose/RGB record; missing physical truth stays unknown."""
from copy import deepcopy
import math
from integrations.isaac.frame_observation import observe_target

def bind_record_content(payload, observed):
    result = {'physical_parent_goal_met': None, 'content_bound': False, 'pose_checks': [], 'rgb_received_n': len(observed.get('images', [])), 'scope': 'command/observation binding, not inferred mission effects'}

    def fail(reason):
        return {**result, 'status': reason}
    if observed.get('candidate') != payload['candidate']:
        return fail('DIFFERENT_CANDIDATE_OBSERVATION')
    if any((picture.get('capture_candidate') != payload['candidate'] for picture in observed.get('images', []))):
        return fail('DIFFERENT_OR_UNBOUND_IMAGE_CANDIDATE')
    count = observed.get('repair_dispatch_n')
    if type(count) is not int:
        return fail('DISPATCH_COUNT_UNKNOWN')
    gate_ok = payload['symbolic_verification']['accepted'] and payload['supported']
    if not gate_ok:
        if count or observed.get('prefix_send_attempt_n', 0) or observed.get('steps'):
            return fail('DISPATCH_WITH_REJECTED_GATE')
        return {**result, 'content_bound': True, 'status': 'REJECTION_ZERO_DISPATCH'}
    commands = payload['commands']
    steps = observed.get('steps', [])
    if count != len(steps) or len(steps) > len(commands):
        return fail('INCOMPLETE_OR_EXCESS_DISPATCH_TRACE')
    for i, step in enumerate(steps):
        command = commands[i]
        if step.get('received_command') != command:
            return fail('ACTOR_TARGET_OR_ORDER_MISMATCH')
        expected = {'target_x': float(command['target_xyz'][0]), 'target_y': float(command['target_xyz'][1]), 'ns': command['namespace']}
        if step.get('sent_goal') != expected:
            return fail('SENT_CONTENT_DIFFERS')
    repair_rgb_n = 0
    for picture in observed.get('images', []):
        if picture.get('capture_candidate') != payload['candidate']:
            return fail('DIFFERENT_OR_UNBOUND_IMAGE_CANDIDATE')
        if picture.get('capture_phase', 'repair') != 'repair':
            continue
        repair_rgb_n += 1
        index = picture.get('candidate_index')
        if type(index) is not int or not 0 <= index < len(steps):
            return fail('IMAGE_OUTSIDE_DISPATCHED_STEP')
        if picture.get('received_command') != commands[index]:
            return fail('IMAGE_COMMAND_MISMATCH')
        frame = picture.get('paired_odom')
        if not frame or not frame.get('child_frame') or (not payload.get('body_frame')):
            return fail('IMAGE_BODY_REFERENCE_MISSING')
        if frame['child_frame'] != payload['body_frame']:
            return fail('IMAGE_BODY_REFERENCE_MISMATCH')
        expected_frame = (payload.get('named_pose_mapping') or {}).get('observation_frame')
        if not frame.get('frame') or (expected_frame and frame['frame'] != expected_frame):
            return fail('IMAGE_POSE_FRAME_MISMATCH')
        image_stamp = picture.get('image_stamp_s')
        odom_stamp = frame.get('odom_stamp_s')
        current_stamp = frame.get('sim_time_s')
        max_age = payload.get('rgb_pose_max_age_s', 0.5)
        if 'receive_monotonic_s' in picture:
            received = picture['receive_monotonic_s']
            max_wait = payload.get('rgb_clock_max_wait_wall_s', 2.0)
            if any((type(v) not in (int, float) or not math.isfinite(v) for v in (received, image_stamp, odom_stamp, max_wait))) or max_wait < 0:
                return fail('IMAGE_CLOCK_REFERENCE_MISSING')
            following = [c for c in observed.get('clock_observations', []) if type(c.get('receive_monotonic_s')) in (int, float) and type(c.get('sim_stamp_s')) in (int, float) and (0 <= c['receive_monotonic_s'] - received <= max_wait) and (c['sim_stamp_s'] >= max(image_stamp, odom_stamp))]
            if not following:
                return fail('IMAGE_CLOCK_REFERENCE_MISSING')
            current_stamp = min(following, key=lambda c: c['receive_monotonic_s'])['sim_stamp_s']
        if any((type(v) not in (int, float) or not math.isfinite(v) for v in (image_stamp, odom_stamp, current_stamp, max_age))) or max_age < 0:
            return fail('IMAGE_POSE_TIME_UNKNOWN')
        if abs(image_stamp - odom_stamp) > max_age or not 0 <= current_stamp - odom_stamp <= max_age or (not 0 <= current_stamp - image_stamp <= max_age):
            return fail('IMAGE_POSE_TIME_MISMATCH')
    mapping = payload.get('named_pose_mapping')
    if mapping and observed.get('final_observation'):
        if not commands or mapping.get('actor') != payload['candidate'][-1]['agent_id'] or mapping['motion_target_xyz'] != commands[-1]['target_xyz']:
            return fail('POSE_MAPPING_NOT_BOUND_TO_SENT_TARGET')
        try:
            pose = observe_target(mapping, observed['final_observation'], body_frame=payload['body_frame'], current_stamp_s=observed['final_observation'].get('sim_time_s'), max_age_s=payload['pose_max_age_s'], tolerance_m=payload['pose_tolerance_m'])
        except (KeyError, ValueError, TypeError) as exc:
            return fail('FRAME_MAPPING_INVALID: ' + str(exc))
        result['pose_checks'].append(pose)
    return {**result, 'content_bound': True, 'status': 'BOUND_COMPLETE_TRACE' if count == len(commands) else 'BOUND_PARTIAL_TRACE', 'pose_evidence_available': any((p['position_within_tolerance'] is not None for p in result['pose_checks'])), 'rgb_evidence_available': repair_rgb_n > 0, 'repair_rgb_n': repair_rgb_n, 'nonrepair_rgb_n': len(observed.get('images', [])) - repair_rgb_n}

def audit_record(world, problem, payload, observed, *, actor_map):
    """Independent current gate/target recomputation outside the ROS recorder."""
    from integrations.isaac.candidate_motion import compile_motion
    from domains.transition import observe
    from domains.transition import binding_observation
    if observe(world, problem.scenario_id) != problem.initial_state or binding_observation(world, problem.scenario_id) != problem.observation.get('world'):
        return {'content_bound': False, 'status': 'SOURCE_STATE_CHANGED', 'physical_parent_goal_met': None}
    fresh = compile_motion(world, problem, payload['candidate'], actor_map=actor_map)
    if fresh['supported'] != payload['supported'] or fresh['commands'] != payload['commands'] or fresh['symbolic_verification']['accepted'] != payload['symbolic_verification']['accepted']:
        return {'content_bound': False, 'status': 'CURRENT_GATE_OR_COMMAND_MISMATCH', 'physical_parent_goal_met': None}
    return bind_record_content(payload, observed)

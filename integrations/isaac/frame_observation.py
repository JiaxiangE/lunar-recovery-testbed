"""Explicit rigid frame/pose observations; never synthesizes mission predicates."""
from copy import deepcopy
import math

def _xyz(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any((type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError('finite xyz required')
    return tuple(value)

def transform_point(point, registration, *, source_frame, target_frame):
    """ROS TF convention: parent(target) <- child(source). No inferred identity."""
    if registration is None:
        raise ValueError('explicit frame registration missing')
    if registration['child_frame'] != source_frame or registration['parent_frame'] != target_frame:
        raise ValueError('frame registration direction mismatch')
    p = _xyz(point)
    t = _xyz(registration['translation_xyz'])
    q = registration['rotation_xyzw']
    if not isinstance(q, (list, tuple)) or len(q) != 4 or any((type(x) not in (float, int) or not math.isfinite(x) for x in q)):
        raise ValueError('invalid quaternion')
    if abs(sum((x * x for x in q)) - 1) > 1e-06:
        raise ValueError('non-unit quaternion')
    x, y, z, w = q
    matrix = ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)), (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)), (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))
    return [sum((row[i] * p[i] for i in range(3))) + t[j] for j, row in enumerate(matrix)]

def map_target(world, step, *, source_frame, observation_frame, registration):
    point = world.resolve_target(step['primitive'], step['params'], step['agent_id'])
    if point is None:
        raise ValueError('unresolved source target')
    mapped = transform_point(point, registration, source_frame=source_frame, target_frame=observation_frame)
    return {'actor': step['agent_id'], 'primitive': step['primitive'], 'params': deepcopy(step['params']), 'source_xyz': list(point), 'motion_target_xyz': mapped, 'source_frame': source_frame, 'observation_frame': observation_frame, 'registration': deepcopy(registration), 'control_projection': 'XY command only; full 3D arrival requires independent body pose observation'}

def observe_target(mapping, observation, *, body_frame, current_stamp_s, max_age_s, tolerance_m):
    result = {'position_within_tolerance': None, 'physical_parent_goal_met': None, 'predicate_scope': 'pose proximity only; no cargo, assembly, radio or mission predicate is inferred'}
    if any((type(v) not in (int, float) or not math.isfinite(v) for v in (max_age_s, tolerance_m, current_stamp_s))) or max_age_s < 0 or tolerance_m <= 0:
        raise ValueError('explicit valid age/tolerance required')
    if observation.get('frame') != mapping['observation_frame'] or observation.get('child_frame') != body_frame:
        return {**result, 'reason': 'FRAME_OR_BODY_REFERENCE_MISSING'}
    stamp = observation.get('odom_stamp_s')
    if type(stamp) not in (int, float) or not 0 <= current_stamp_s - stamp <= max_age_s:
        return {**result, 'reason': 'STALE_OR_FUTURE_OBSERVATION'}
    registration = mapping['registration']
    if registration.get('static') is not True:
        tf_stamp = registration.get('stamp_s')
        if type(tf_stamp) not in (int, float) or not 0 <= current_stamp_s - tf_stamp <= max_age_s:
            return {**result, 'reason': 'STALE_OR_UNTIMED_FRAME_REGISTRATION'}
    target = transform_point(mapping['source_xyz'], mapping['registration'], source_frame=mapping['source_frame'], target_frame=mapping['observation_frame'])
    if target != mapping['motion_target_xyz']:
        return {**result, 'reason': 'TARGET_BINDING_CHANGED'}
    point = _xyz([observation.get(k) for k in ('x', 'y', 'z')])
    distance = math.dist(point, target)
    return {**result, 'position_within_tolerance': distance <= tolerance_m, 'distance_3d_m': distance, 'distance_xy_m': math.dist(point[:2], target[:2]), 'reason': 'POSE_OBSERVED', 'observation': deepcopy(observation)}

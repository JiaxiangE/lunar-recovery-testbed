"""Common-domain candidate to existing Isaac/ROS XY motion interface.

This adapter supports motion only. It does not synthesize sample/cargo effects,
resource readings, regained_comm, relocalized or a physical parent-goal label.
The existing ROS controller's 0.20 m arrival observation is reported separately
from the symbolic contract, whose external predicate grounding remains explicit.
"""
from copy import deepcopy
import math
from execution.binding import verify_plan

def compile_motion(world, problem, plan, *, actor_map):
    """Validate the entire candidate before resolving any actuator commands."""
    candidate = deepcopy(plan)
    verdict = verify_plan(problem, candidate)
    result = {'candidate': candidate, 'symbolic_verification': verdict, 'commands': [], 'supported': False}
    if not verdict['accepted']:
        return {**result, 'reason': 'symbolic_rejected'}
    commands = []
    for index, step in enumerate(candidate):
        if step['primitive'] not in {'move_to', 'return_to_base', 'navigate_to_relay'}:
            return {**result, 'reason': 'motion_adapter_unsupported_primitive', 'unsupported_step': index}
        actor = step['agent_id']
        if actor not in actor_map:
            return {**result, 'reason': 'motion_adapter_unmapped_actor', 'unsupported_step': index}
        target = world.resolve_target(step['primitive'], step['params'], actor)
        if target is None or len(target) != 3 or (not all((math.isfinite(v) for v in target))):
            return {**result, 'reason': 'motion_adapter_unresolved_target', 'unsupported_step': index}
        commands.append({'candidate_index': index, 'primitive': step['primitive'], 'actor': actor, 'namespace': actor_map[actor], 'target_xyz': list(target), 'motion_projection': 'existing ROS MoveTo target_x/target_y; vertical goal not certified'})
    return {**result, 'commands': commands, 'supported': True, 'reason': 'motion_mapping_available'}

def dispatch_motion(compiled, actuator, *, world, problem, actor_map):
    """Actuator returns actual frames/result and echoes the received command.

    A legal but unsupported full plan dispatches nothing. A failed/changed
    command stops its suffix and remains failed; there is no substituted route.
    """
    output = {'candidate': deepcopy(compiled['candidate']), 'symbolic_verification': deepcopy(compiled['symbolic_verification']), 'dispatch_n': 0, 'actuator_calls_n': 0, 'steps': [], 'motion_targets_reached': False, 'physical_parent_goal_met': None, 'physical_parent_goal_note': 'not inferred from symbolic acceptance or the ROS controller arrival flag'}
    if not compiled['supported']:
        return {**output, 'status': compiled['reason']}
    fresh = compile_motion(world, problem, compiled['candidate'], actor_map=actor_map)
    if not fresh['supported'] or fresh['commands'] != compiled['commands']:
        return {**output, 'status': 'motion_binding_changed', 'fresh_validation': fresh}
    for command in compiled['commands']:
        output['actuator_calls_n'] += 1
        try:
            observed = actuator(deepcopy(command))
        except Exception as exc:
            return {**output, 'status': 'actuator_outcome_unknown', 'dispatch_n': None, 'failure': {'type': type(exc).__name__, 'detail': str(exc)}}
        output['dispatch_n'] += 1
        output['steps'].append({'command': deepcopy(command), 'observed': observed})
        if observed.get('received_command') != command:
            return {**output, 'status': 'actuator_content_mismatch'}
        if observed.get('reached') is not True:
            return {**output, 'status': 'motion_execution_failed'}
        frames = observed.get('frames') or []
        final_xy = observed.get('final_xy')
        if final_xy is None and frames:
            final_xy = [frames[-1].get('x'), frames[-1].get('y')]
        if not isinstance(final_xy, (list, tuple)) or len(final_xy) != 2 or any((type(v) not in (float, int) or not math.isfinite(v) for v in final_xy)):
            return {**output, 'status': 'motion_observation_missing'}
        if math.hypot(final_xy[0] - command['target_xyz'][0], final_xy[1] - command['target_xyz'][1]) >= 0.2:
            return {**output, 'status': 'motion_target_observation_mismatch'}
    return {**output, 'status': 'motion_commands_completed', 'motion_targets_reached': True}

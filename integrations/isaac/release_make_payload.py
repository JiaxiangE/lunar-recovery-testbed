"""Build a new, symbolic-navigation-only fixture from measured ROS odometry.

This checks the motion adapter in each physical scene, not that scene's mission.
The target offset is fixed before dispatch; there is no model or target search.
"""
import argparse, json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--observation', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--offset-x', type=float, default=0.6)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError(a.output)
    observed = json.loads(a.observation.read_text(encoding='utf-8'))
    if not observed['MoveTo_ready'] or not all((observed[k] > 0 for k in ['odom_n', 'clock_n', 'rgb_n', 'communication_n'])):
        raise ValueError('all required measured interfaces must be available before dispatch')
    from examples.recovery_inputs import build_case
    from domains.common import build_problem
    from domains.common import atom
    from integrations.isaac.candidate_motion import compile_motion
    case = build_case('B05_psr_strict_disconnected')
    world = case.world
    initial = list(observed['last_odom']['xyz'])
    target = [initial[0] + a.offset_x, initial[1], initial[2]]
    world.agents['rover_1']['position'] = tuple(initial)
    world.relay_pos['release_waypoint'] = tuple(target)
    problem = build_problem(world, 'psr', goal_facts={atom('at_target', 'rover_1', 'release_waypoint')})
    plan = [{'primitive': 'move_to', 'agent_id': 'rover_1', 'params': {'target': target}}]
    result = compile_motion(world, problem, plan, actor_map={'rover_1': 'husky'})
    if not result['supported']:
        raise ValueError(result['reason'])
    result.update(source_trial='release_installation_check/' + a.scene, actor_id='rover_1', initial_actor_xyz=initial, base_xyz=list(world.base_pos), body_frame=observed['last_odom']['body_frame'], release_fixture={'physical_scene': a.scene, 'offset_x_m': a.offset_x, 'coordinate_frame': observed['last_odom']['frame'], 'scope': 'new adapter navigation fixture; release_waypoint is a registered navigation target, not a physical relay/sample/cargo claim; no paper trial reused'}, rgb_pose_max_age_s=0.5, rgb_clock_max_wait_wall_s=2.0)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
if __name__ == '__main__':
    main()

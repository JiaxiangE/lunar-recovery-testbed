"""Execute a prevalidated motion replay in the isolated Isaac ROS domain.

No model client or formal-goal inference. Uses the existing MoveTo action
server; actual odometry frames and its reached boolean are retained.
"""
import argparse
import json
import math
from pathlib import Path
import time

def save_observation(payload, result, output):
    """Persist actual observations even if the offline binding audit fails internally."""
    from integrations.isaac.motion_record import bind_record_content
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    try:
        result['content_binding'] = bind_record_content(payload, result)
    except Exception as exc:
        result['content_binding'] = {'content_bound': False, 'physical_parent_goal_met': None, 'status': 'BINDING_INSTRUMENTATION_FAILED', 'error_type': type(exc).__name__, 'detail': str(exc)}
        output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        raise
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

def validate_payload(payload):
    candidate = payload['candidate']
    commands = payload['commands']
    if not payload['symbolic_verification']['accepted'] or not payload['supported']:
        return []
    if len(candidate) != len(commands):
        raise ValueError('candidate/command length differs')
    for index, (step, command) in enumerate(zip(candidate, commands)):
        if command['candidate_index'] != index or command['primitive'] != step['primitive'] or command['actor'] != step['agent_id']:
            raise ValueError('candidate/actuator identity differs')
        if command['namespace'] != 'husky' or step['agent_id'] != payload['actor_id']:
            raise ValueError('this physical replay has one explicitly mapped Husky actor')
        if step['primitive'] not in ('move_to', 'return_to_base'):
            raise ValueError('physical replay supports these two motion primitives only')
        target = command['target_xyz']
        if len(target) != 3 or any((type(v) not in (float, int) or not math.isfinite(v) for v in target)):
            raise ValueError('invalid actuator target')
        if step['primitive'] == 'move_to' and target != step['params'].get('target'):
            raise ValueError('motion target differs from the candidate numeric target')
        if step['primitive'] == 'return_to_base' and target != payload['base_xyz']:
            raise ValueError('base target differs from declared source coordinate frame')
    return commands

def drain_observations(observer, result):
    """Receive a following clock without issuing further commands or losing errors."""
    observer.active_command = None
    try:
        observer.pump(0.5)
    except Exception as exc:
        result['observation_drain_error'] = {'type': type(exc).__name__, 'detail': str(exc)}
    result['post_dispatch_observation_drain_wall_s'] = 0.5

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rgb-topic', default='/monitoring/psr_chase')
    p.add_argument('--image-interval', type=float, default=5.0)
    a = p.parse_args()
    if not math.isfinite(a.image_interval) or a.image_interval <= 0:
        raise ValueError('positive image interval required')
    payload = json.loads(a.input.read_text())
    commands = validate_payload(payload)
    if a.output.exists():
        raise FileExistsError(a.output)
    if any((a.output.parent / 'images').glob(a.output.stem + '_*.png')):
        raise FileExistsError('preserve previous partial images; use a new output filename')
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Image as RosImage
    from rclpy.qos import qos_profile_sensor_data
    from rosgraph_msgs.msg import Clock
    from std_msgs.msg import Bool
    from tf2_msgs.msg import TFMessage
    from paper4_msgs.action import MoveTo
    from PIL import Image

    class Observer(Node):

        def __init__(self):
            super().__init__('paper4_candidate_motion_replay')
            self.frames = []
            self.sim_stamp = None
            self.radio = None
            self.send_count = 0
            self.accepted_count = 0
            self.images = []
            self.last_image_wall = 0.0
            self.transforms = []
            self.active_command = None
            self.clock_observations = []
            self.create_subscription(Clock, '/clock', self.clock, 10)
            self.create_subscription(Odometry, '/odom', self.odom, 10)
            self.create_subscription(Bool, '/husky/base_reachable', self.comm, 10)
            self.create_subscription(RosImage, a.rgb_topic, self.image, qos_profile_sensor_data)
            from rclpy.qos import QoSProfile
            from rclpy.qos import DurabilityPolicy
            self.create_subscription(TFMessage, '/tf', lambda msg: self.tf(msg, False), 100)
            self.create_subscription(TFMessage, '/tf_static', lambda msg: self.tf(msg, True), QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL))
            self.client = ActionClient(self, MoveTo, 'move_to')

        def clock(self, msg):
            self.sim_stamp = msg.clock.sec + msg.clock.nanosec * 1e-09
            self.clock_observations.append({'sim_stamp_s': self.sim_stamp, 'receive_monotonic_s': time.monotonic()})

        def comm(self, msg):
            self.radio = bool(msg.data)

        def tf(self, msg, static):
            for item in msg.transforms:
                t = item.transform.translation
                q = item.transform.rotation
                self.transforms.append({'parent_frame': item.header.frame_id, 'child_frame': item.child_frame_id, 'stamp_s': item.header.stamp.sec + item.header.stamp.nanosec * 1e-09, 'static': static, 'translation_xyz': [t.x, t.y, t.z], 'rotation_xyzw': [q.x, q.y, q.z, q.w]})

        def image(self, msg):
            now = time.monotonic()
            if now - self.last_image_wall < a.image_interval:
                return
            formats = {'rgb8': ('RGB', 'RGB'), 'bgr8': ('RGB', 'BGR'), 'rgba8': ('RGBA', 'RGBA'), 'bgra8': ('RGBA', 'BGRA')}
            if msg.encoding not in formats:
                return
            mode, raw = formats[msg.encoding]
            image = Image.frombytes(mode, (msg.width, msg.height), bytes(msg.data), 'raw', raw, msg.step)
            directory = a.output.parent / 'images'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{a.output.stem}_{len(self.images):04d}.png'
            image.save(path)
            self.images.append({'file': str(path), 'image_stamp_s': msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-09, 'receive_monotonic_s': now, 'receive_sim_stamp_s': self.sim_stamp, 'frame_id': msg.header.frame_id, 'nearest_received_odom_index': len(self.frames) - 1 if self.frames else None, 'capture_candidate': payload['candidate'], 'candidate_index': self.active_command.get('candidate_index') if self.active_command else None, 'capture_phase': 'repair' if self.active_command and 'candidate_index' in self.active_command else 'initial_positioning' if self.active_command else 'observation', 'received_command': self.active_command, 'paired_odom': dict(self.frames[-1]) if self.frames else None})
            self.last_image_wall = now

        def odom(self, msg):
            point = msg.pose.pose.position
            self.frames.append({'receive_monotonic_s': time.monotonic(), 'sim_time_s': self.sim_stamp, 'odom_stamp_s': msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-09, 'frame': msg.header.frame_id, 'child_frame': msg.child_frame_id, 'x': point.x, 'y': point.y, 'z': point.z, 'radio_observed': self.radio})

        def pump(self, seconds):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                rclpy.spin_once(self, timeout_sec=0.05)

        def move(self, command):
            if self.sim_stamp is None or not self.frames:
                raise RuntimeError('no actual simulation clock/odometry; no dispatch')
            if not self.client.wait_for_server(timeout_sec=10.0):
                raise RuntimeError('MoveTo action server unavailable; no dispatch')
            start = len(self.frames)
            t0 = time.monotonic()
            goal = MoveTo.Goal()
            goal.target_x = float(command['target_xyz'][0])
            goal.target_y = float(command['target_xyz'][1])
            goal.ns = command['namespace']
            sent_goal = {'target_x': goal.target_x, 'target_y': goal.target_y, 'ns': goal.ns}
            self.active_command = dict(command)
            self.send_count += 1
            future = self.client.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, future, timeout_sec=15.0)
            if not future.done():
                raise RuntimeError('goal acknowledgement unknown; no automatic retry')
            handle = future.result()
            if not handle.accepted:
                return {'received_command': command, 'sent_goal': sent_goal, 'reached': False, 'action_accepted': False, 'frames': self.frames[start:]}
            self.accepted_count += 1
            future = handle.get_result_async()
            rclpy.spin_until_future_complete(self, future, timeout_sec=180.0)
            if not future.done():
                handle.cancel_goal_async()
                return {'received_command': command, 'sent_goal': sent_goal, 'reached': False, 'outcome': 'response_timeout', 'frames': self.frames[start:]}
            response = future.result()
            native = response.result
            return {'received_command': command, 'sent_goal': sent_goal, 'reached': bool(native.reached), 'native_action_status': response.status, 'final_xy': [native.final_x, native.final_y], 'wall_s': time.monotonic() - t0, 'frames': self.frames[start:]}
    rclpy.init()
    observer = Observer()
    result = {'source_trial': payload['source_trial'], 'candidate': payload['candidate'], 'prefix': None, 'repair_dispatch_n': 0, 'steps': [], 'physical_parent_goal_met': None, 'scope': 'actual planar motion replay only; no sample/custody/energy or named 3D parent-goal certification'}
    phase = 'observation'
    prefix_send_n = 0
    try:
        observer.pump(5.0)
        if commands:
            prefix = {'namespace': 'husky', 'target_xyz': payload['initial_actor_xyz'], 'primitive': 'source_initial_positioning'}
            phase = 'prefix'
            result['prefix'] = observer.move(prefix)
            prefix_send_n = observer.send_count
            if result['prefix']['reached']:
                phase = 'repair'
                for command in commands:
                    observed = observer.move(command)
                    result['repair_dispatch_n'] += 1
                    result['steps'].append(observed)
                    target = command['target_xyz']
                    observed['target_xy_observation_matches'] = bool(observed['reached'] and math.hypot(observed['final_xy'][0] - target[0], observed['final_xy'][1] - target[1]) < 0.2)
                    if not observed['target_xy_observation_matches']:
                        break
            result['motion_targets_reached'] = bool(result['prefix']['reached'] and len(result['steps']) == len(commands) and all((s.get('target_xy_observation_matches') is True for s in result['steps'])))
        else:
            start = len(observer.frames)
            observer.pump(2.0)
            result.update(motion_targets_reached=False, rejection=payload['reason'], no_dispatch_frames=observer.frames[start:])
    except Exception as exc:
        result['failure'] = {'type': type(exc).__name__, 'detail': str(exc)}
        result['motion_targets_reached'] = False
    finally:
        drain_observations(observer, result)
        if phase == 'prefix':
            prefix_send_n = observer.send_count
        result['prefix_send_attempt_n'] = prefix_send_n
        result['repair_dispatch_n'] = observer.send_count - prefix_send_n
        result['ros_action_accepted_n'] = observer.accepted_count
        result['dispatch_count_scope'] = 'send_goal_async invocations; acceptance, effects and unknown outcomes remain separate'
        result['final_observation'] = observer.frames[-1] if observer.frames else None
        result['tf_observations'] = observer.transforms
        result['clock_observations'] = observer.clock_observations
        result['rgb_topic'] = a.rgb_topic
        result['images'] = observer.images
        result['image_sampling_wall_interval_s'] = a.image_interval
        try:
            save_observation(payload, result, a.output)
        finally:
            observer.destroy_node()
            rclpy.shutdown()
if __name__ == '__main__':
    main()

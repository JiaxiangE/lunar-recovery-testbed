"""Bounded ROS readiness observation; sends no robot command."""
import argparse, json, time, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=12)
    a = p.parse_args()
    import rclpy
    from nav_msgs.msg import Odometry
    from rosgraph_msgs.msg import Clock
    from sensor_msgs.msg import Image
    from std_msgs.msg import Bool
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.action import ActionClient
    from paper4_msgs.action import MoveTo
    import PIL
    rclpy.init()
    node = rclpy.create_node('paper4_release_observe')
    r = {'python': sys.version, 'PIL': PIL.__version__, 'clock_n': 0, 'odom_n': 0, 'rgb_n': 0, 'communication_n': 0}

    def odom(m):
        r['odom_n'] += 1
        r['last_odom'] = {'xyz': [m.pose.pose.position.x, m.pose.pose.position.y, m.pose.pose.position.z], 'frame': m.header.frame_id, 'body_frame': m.child_frame_id, 'stamp_s': m.header.stamp.sec + m.header.stamp.nanosec * 1e-09}

    def clock(m):
        r['clock_n'] += 1
        r['clock_s'] = m.clock.sec + m.clock.nanosec * 1e-09

    def rgb(m):
        r['rgb_n'] += 1
        r['rgb_shape'] = [m.height, m.width]
        r['rgb_encoding'] = m.encoding

    def comm(m):
        r['communication_n'] += 1
        r['base_reachable'] = m.data
    handles = [node.create_subscription(Odometry, '/odom', odom, 10), node.create_subscription(Clock, '/clock', clock, 10), node.create_subscription(Image, '/monitoring/psr_chase', rgb, qos_profile_sensor_data), node.create_subscription(Bool, '/husky/base_reachable', comm, 10)]
    client = ActionClient(node, MoveTo, 'move_to')
    start = time.monotonic()
    while time.monotonic() - start < a.seconds:
        rclpy.spin_once(node, timeout_sec=0.2)
    r['MoveTo_ready'] = client.server_is_ready()
    a.output.write_text(json.dumps(r, indent=2) + '\n')
    node.destroy_node()
    rclpy.shutdown()
if __name__ == '__main__':
    main()

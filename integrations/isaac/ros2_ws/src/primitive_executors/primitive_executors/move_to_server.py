"""move_to ROS2 action server (paper4_msgs/MoveTo).

Closed-loop drive of a namespaced rover to a planar target, with REAL failure
detection: an independent POSE-CHANGE monitor returns reached=False if the rover's
pose (x, y, yaw) stays frozen for STALL_S seconds. Pose-change (not distance) is the
right signal — an in-place turn (yaw moving, distance flat) is activity, NOT a stall;
only a genuinely frozen rover (e.g. injected wheel-lock / physically blocked) stalls.

Injection hook: /<ns>/injected_stuck (Bool). When set, locomotion is disabled (a clean
stand-in for a physical wheel-lock) -> the rover's pose freezes -> the SAME independent
monitor detects the stall. Injection != detection.
"""
import math
import time
import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool
from paper4_msgs.action import MoveTo

class MoveToServer(Node):
    REACH_TOL = 0.2
    TURN_GATE = 0.3
    MAX_LIN = 0.5
    MAX_ANG = 0.6
    TIMEOUT_S = 150.0
    STALL_S = 5.0
    POS_EPS = 0.02
    YAW_EPS = 0.03

    def __init__(self):
        super().__init__('move_to_server')
        self._cb = ReentrantCallbackGroup()
        self.x = self.y = self.yaw = None
        self.injected_stuck = False
        self._pubs = {}
        self.create_subscription(Odometry, '/odom', self._on_odom, 10, callback_group=self._cb)
        self.create_subscription(Bool, '/husky/injected_stuck', self._on_stuck, 10, callback_group=self._cb)
        self._srv = ActionServer(self, MoveTo, 'move_to', self._execute, callback_group=self._cb)
        self.get_logger().info('move_to action server ready (pose-change stall + injection hook)')

    def _pub(self, ns):
        if ns not in self._pubs:
            self._pubs[ns] = self.create_publisher(Twist, f'/{ns}/cmd_vel', 10)
        return self._pubs[ns]

    def _on_odom(self, msg):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.x, self.y = (p.x, p.y)
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny, cosy)

    def _on_stuck(self, msg):
        self.injected_stuck = msg.data

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def _execute(self, goal_handle):
        g = goal_handle.request
        tx, ty, ns = (g.target_x, g.target_y, g.ns or 'husky')
        pub = self._pub(ns)
        self.get_logger().info(f'move_to ({tx:.2f},{ty:.2f}) ns={ns}')
        t0 = time.time()
        last_x = last_y = last_yaw = None
        last_move_t = time.time()
        reached = False
        stuck = False
        while rclpy.ok() and time.time() - t0 < self.TIMEOUT_S:
            if self.x is None:
                time.sleep(0.05)
                continue
            dx, dy = (tx - self.x, ty - self.y)
            dist = math.hypot(dx, dy)
            fb = MoveTo.Feedback()
            fb.distance_remaining = dist
            goal_handle.publish_feedback(fb)
            if dist < self.REACH_TOL:
                reached = True
                break
            if last_x is None:
                last_x, last_y, last_yaw = (self.x, self.y, self.yaw)
                last_move_t = time.time()
            else:
                dpos = math.hypot(self.x - last_x, self.y - last_y)
                dyaw = abs(math.atan2(math.sin(self.yaw - last_yaw), math.cos(self.yaw - last_yaw)))
                if dpos > self.POS_EPS or dyaw > self.YAW_EPS:
                    last_x, last_y, last_yaw = (self.x, self.y, self.yaw)
                    last_move_t = time.time()
                elif time.time() - last_move_t > self.STALL_S:
                    stuck = True
                    break
            if self.injected_stuck:
                pub.publish(Twist())
            else:
                tyaw = math.atan2(dy, dx)
                yerr = math.atan2(math.sin(tyaw - self.yaw), math.cos(tyaw - self.yaw))
                tw = Twist()
                if abs(yerr) > self.TURN_GATE:
                    tw.angular.z = self._clamp(1.2 * yerr, -self.MAX_ANG, self.MAX_ANG)
                else:
                    tw.linear.x = self._clamp(0.6 * dist, 0.05, self.MAX_LIN)
                    tw.angular.z = self._clamp(1.0 * yerr, -self.MAX_ANG, self.MAX_ANG)
                pub.publish(tw)
            time.sleep(0.1)
        pub.publish(Twist())
        goal_handle.succeed()
        r = MoveTo.Result()
        r.reached = reached
        r.final_x = self.x if self.x is not None else 0.0
        r.final_y = self.y if self.y is not None else 0.0
        tag = 'REACHED' if reached else 'STUCK' if stuck else 'TIMEOUT'
        self.get_logger().info(f'{tag} at ({r.final_x:.2f},{r.final_y:.2f})')
        return r

def main():
    rclpy.init()
    node = MoveToServer()
    ex = MultiThreadedExecutor()
    ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
if __name__ == '__main__':
    main()

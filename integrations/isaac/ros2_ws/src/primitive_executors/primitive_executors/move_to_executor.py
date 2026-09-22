"""move_to primitive — closed-loop drive husky to a target (x, y) and stop.

Standalone rclpy node (std msgs only, no custom build needed yet) so it can run
directly in the omnilrs-navigation container during W1 bring-up. This becomes the
core of the proper ROS2 action server (paper4_msgs/MoveTo) in the next slice.

Usage (inside the demo container, ROS2 sourced):
    python3 move_to_executor.py <target_x> <target_y> [--ns husky]
Prints REACHED or TIMEOUT with the final pose. Exit code 0 = reached, 2 = timeout.
"""
import sys
import math
import time
import rclpy
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist

class MoveTo:
    REACH_TOL = 0.2
    TURN_GATE = 0.3
    MAX_LIN = 0.4
    MAX_ANG = 0.6
    TIMEOUT_S = 60.0

    def __init__(self, tx, ty, ns='husky'):
        self.tx, self.ty = (tx, ty)
        self.node = rclpy.create_node('move_to_executor')
        self.pub = self.node.create_publisher(Twist, f'/{ns}/cmd_vel', 10)
        self.node.create_subscription(Odometry, '/odom', self._on_odom, 10)
        self.x = self.y = self.yaw = None

    def _on_odom(self, msg):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.x, self.y = (p.x, p.y)
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny, cosy)

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def run(self):
        reached = False
        t0 = time.time()
        while rclpy.ok() and time.time() - t0 < self.TIMEOUT_S:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            if self.x is None:
                continue
            dx, dy = (self.tx - self.x, self.ty - self.y)
            dist = math.hypot(dx, dy)
            if dist < self.REACH_TOL:
                reached = True
                break
            target_yaw = math.atan2(dy, dx)
            yaw_err = math.atan2(math.sin(target_yaw - self.yaw), math.cos(target_yaw - self.yaw))
            tw = Twist()
            if abs(yaw_err) > self.TURN_GATE:
                tw.angular.z = self._clamp(1.2 * yaw_err, -self.MAX_ANG, self.MAX_ANG)
            else:
                tw.linear.x = self._clamp(0.6 * dist, 0.05, self.MAX_LIN)
                tw.angular.z = self._clamp(1.0 * yaw_err, -self.MAX_ANG, self.MAX_ANG)
            self.pub.publish(tw)
            time.sleep(0.1)
        self.pub.publish(Twist())
        tag = 'REACHED' if reached else 'TIMEOUT'
        print(f'{tag} at ({self.x:.2f},{self.y:.2f}) target ({self.tx:.2f},{self.ty:.2f})', flush=True)
        return 0 if reached else 2

def main():
    if len(sys.argv) < 3:
        print('usage: move_to_executor.py <x> <y> [ns]')
        return 1
    tx, ty = (float(sys.argv[1]), float(sys.argv[2]))
    ns = sys.argv[3] if len(sys.argv) > 3 else 'husky'
    rclpy.init()
    rc = MoveTo(tx, ty, ns).run()
    rclpy.shutdown()
    return rc
if __name__ == '__main__':
    raise SystemExit(main())

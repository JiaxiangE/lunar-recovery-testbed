"""comm_evaluator — geometry-driven base_reachable for the edge_brain.

v1 (this slice): range-from-base. base_reachable = dist(rover, base) <= comm_range.
Publishes std_msgs/Bool on /<ns>/base_reachable at 5 Hz; the edge_brain consumes it
both as the scope-selector input and to gate the base-LLM call. So when the rover
drives out of range (or, later, behind a crater wall / under a tube ceiling), comm
genuinely drops and the repair is forced edge-local.

NEXT iterations (design DL-3 / DL-6): add the relay hop (rover->relay->base) and a
PhysX-raycast / region LOS occlusion term, optionally backed by OmniLRS radio_model.
"""
import math
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool

class CommEvaluator(Node):

    def __init__(self):
        super().__init__('comm_evaluator')
        self.declare_parameter('base_x', 0.0)
        self.declare_parameter('base_y', 0.0)
        self.declare_parameter('comm_range', 4.0)
        self.declare_parameter('occlude_past_y', 1000000000.0)
        self.declare_parameter('ns', 'husky')
        self.bx = float(self.get_parameter('base_x').value)
        self.by = float(self.get_parameter('base_y').value)
        self.comm_range = float(self.get_parameter('comm_range').value)
        self.occlude_past_y = float(self.get_parameter('occlude_past_y').value)
        ns = self.get_parameter('ns').value
        self.pub = self.create_publisher(Bool, f'/{ns}/base_reachable', 10)
        self.create_subscription(Odometry, '/odom', self._on_odom, 10)
        self.create_timer(0.2, self._tick)
        self.x = self.y = None
        self.reachable = None
        self.get_logger().info(f'comm_evaluator: base=({self.bx:.1f},{self.by:.1f}) comm_range={self.comm_range:.1f}m occlude_past_y={self.occlude_past_y:g} ns={ns}')

    def _on_odom(self, msg):
        p = msg.pose.pose.position
        self.x, self.y = (p.x, p.y)

    def _tick(self):
        if self.x is None:
            return
        d = math.hypot(self.x - self.bx, self.y - self.by)
        occluded = self.y > self.occlude_past_y
        r = d <= self.comm_range and (not occluded)
        if r != self.reachable:
            self.get_logger().info(f'base_reachable {self.reachable} -> {r}  (dist={d:.2f}m, range={self.comm_range:.1f}m, occluded={occluded})')
            self.reachable = r
        self.pub.publish(Bool(data=bool(r)))

def main():
    rclpy.init()
    node = CommEvaluator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
if __name__ == '__main__':
    main()

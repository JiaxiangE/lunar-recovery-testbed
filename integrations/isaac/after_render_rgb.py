"""Optional RGB publisher hook AFTER the existing world.step(render=True).

No second renderer/orchestrator step. Construct inside the running Isaac process
only when deployment resumes; importing this module starts no ROS or simulator.
"""
import math
import time

def rgba_payload(array):
    if array is None:
        return None
    import numpy as np
    data = np.asarray(array)
    if data.size == 0:
        return None
    if data.dtype != np.uint8 or data.ndim != 3 or data.shape[2] != 4:
        raise ValueError('camera must supply real uint8 HxWx4 RGBA')
    h, w, _ = data.shape
    return {'width': w, 'height': h, 'encoding': 'rgba8', 'step': 4 * w, 'data': data.tobytes(order='C')}

class AfterRenderRGB:

    def __init__(self, node, camera, *, topic='/monitoring/psr_chase', frame_id='psr_chase', publish_period_s=0.0):
        if not math.isfinite(publish_period_s) or publish_period_s < 0:
            raise ValueError('nonnegative finite RGB wall publication period required')
        from sensor_msgs.msg import Image
        from rosgraph_msgs.msg import Clock
        from rclpy.qos import qos_profile_sensor_data
        self.message_type = Image
        self.camera = camera
        self.frame_id = frame_id
        self.publisher = node.create_publisher(Image, topic, qos_profile_sensor_data)
        self.published = 0
        self.empty_frames = 0
        self.sim_stamp_s = None
        self.last_published_stamp = None
        self.publish_period_s = publish_period_s
        self.next_publish_wall_s = 0.0

        def clock(msg):
            self.sim_stamp_s = msg.clock.sec + msg.clock.nanosec * 1e-09
        self.clock_subscription = node.create_subscription(Clock, '/clock', clock, 10)

    def after_world_step(self, sim_stamp_s=None):
        started = time.monotonic()
        if started < self.next_publish_wall_s:
            return {'published': False, 'reason': 'publication_interval', 'publish_period_wall_s': self.publish_period_s}
        sim_stamp_s = self.sim_stamp_s if sim_stamp_s is None else sim_stamp_s
        if sim_stamp_s is None:
            return {'published': False, 'reason': 'simulation_clock_not_observed'}
        if sim_stamp_s == self.last_published_stamp:
            return {'published': False, 'reason': 'no_new_observed_simulation_stamp'}
        payload = rgba_payload(self.camera.get_rgba())
        if payload is None:
            self.empty_frames += 1
            return {'published': False, 'reason': 'camera_returned_no_frame'}
        message = self.message_type()
        message.header.frame_id = self.frame_id
        message.header.stamp.sec = int(sim_stamp_s)
        message.header.stamp.nanosec = int((sim_stamp_s - int(sim_stamp_s)) * 1000000000.0)
        for key, value in payload.items():
            setattr(message, key, value)
        self.publisher.publish(message)
        self.published += 1
        self.last_published_stamp = sim_stamp_s
        self.next_publish_wall_s = time.monotonic() + self.publish_period_s
        return {'published': True, 'frame_id': self.frame_id, 'sim_stamp_s': sim_stamp_s, 'published_n': self.published, 'publication_wall_s': time.monotonic() - started, 'publish_period_wall_s': self.publish_period_s, 'stamp_scope': 'explicit simulation stamp or latest received /clock; not inferred wall time'}

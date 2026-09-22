import numpy as np
import pytest
from integrations.isaac.frame_observation import transform_point, observe_target
from integrations.isaac.after_render_rgb import rgba_payload
TF = {'parent_frame': 'odom', 'child_frame': 'source', 'translation_xyz': [1.0, 2.0, 3.0], 'rotation_xyzw': [0.0, 0.0, 0.0, 1.0], 'static': True}

def test_explicit_frame_transform_and_three_dimensional_observation():
    point = transform_point([0.0, 0.0, 0.0], TF, source_frame='source', target_frame='odom')
    assert point == [1.0, 2.0, 3.0]
    mapping = {'source_frame': 'source', 'observation_frame': 'odom', 'source_xyz': [0.0, 0.0, 0.0], 'motion_target_xyz': point, 'registration': TF}
    obs = {'frame': 'odom', 'child_frame': 'husky/base_link', 'odom_stamp_s': 10.0, 'x': 1.0, 'y': 2.0, 'z': 5.0}
    result = observe_target(mapping, obs, body_frame='husky/base_link', current_stamp_s=10.1, max_age_s=0.5, tolerance_m=0.5)
    assert result['distance_xy_m'] == 0 and result['position_within_tolerance'] is False
    obs['z'] = 3.0
    result = observe_target(mapping, obs, body_frame='husky/base_link', current_stamp_s=10.1, max_age_s=0.5, tolerance_m=0.5)
    assert result['position_within_tolerance'] and result['physical_parent_goal_met'] is None
    assert observe_target(mapping, obs, body_frame='husky/base_link', current_stamp_s=11.0, max_age_s=0.5, tolerance_m=0.5)['position_within_tolerance'] is None
    del obs['child_frame']
    assert observe_target(mapping, obs, body_frame='husky/base_link', current_stamp_s=10.0, max_age_s=0.5, tolerance_m=0.5)['reason'] == 'FRAME_OR_BODY_REFERENCE_MISSING'

def test_unknown_or_wrong_transform_is_never_assumed_identity():
    with pytest.raises(ValueError):
        transform_point([0.0, 0.0, 0.0], None, source_frame='source', target_frame='odom')
    with pytest.raises(ValueError):
        transform_point([0.0, 0.0, 0.0], TF, source_frame='odom', target_frame='source')

def test_rgb_payload_is_only_actual_supplied_image_bytes():
    assert rgba_payload(None) is None
    assert rgba_payload(np.empty((0, 0, 4), dtype=np.uint8)) is None
    fixture = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    result = rgba_payload(fixture)
    assert result['data'] == fixture.tobytes() and result['step'] == 12
    with pytest.raises(ValueError):
        rgba_payload(fixture.astype(float))

def test_publication_interval_skips_camera_read_and_never_advances_world(monkeypatch):
    from types import SimpleNamespace
    from integrations.isaac import after_render_rgb as module
    now = [10.0]
    reads = []
    published = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    hook = object.__new__(module.AfterRenderRGB)
    hook.publish_period_s = 5.0
    hook.next_publish_wall_s = 0.0
    hook.sim_stamp_s = 1.0
    hook.last_published_stamp = None
    hook.published = 0
    hook.empty_frames = 0
    hook.frame_id = 'camera'

    def camera():
        reads.append(now[0])
        return np.zeros((2, 2, 4), dtype=np.uint8)
    hook.camera = SimpleNamespace(get_rgba=camera)
    hook.publisher = SimpleNamespace(publish=published.append)
    hook.message_type = lambda: SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace()))
    assert hook.after_world_step()['published']
    now[0] = 11.0
    hook.sim_stamp_s = 2.0
    assert hook.after_world_step()['reason'] == 'publication_interval'
    assert len(reads) == len(published) == 1
    now[0] = 15.0
    assert hook.after_world_step()['published'] and len(reads) == len(published) == 2
    assert published[-1].header.stamp.sec == 2

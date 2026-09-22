import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize('reference,expected', [('file:/home/public-user/velodyne-vlp16.usd', 'velodyne-vlp16.usd'), ('omniverse://localhost/NVIDIA/Assets/Isaac/4.0/Isaac/Sensors/rsd455.usd', '../common/rsd455.usd'), ('OmniPBR.mdl', None), ('unrelated_sensor.usd', None)])
def test_asset_relocation_is_limited_to_known_same_assets(reference, expected):
    from integrations.isaac.release_assets import relocated_reference
    assert relocated_reference(reference) == expected

@pytest.mark.parametrize('ready', [True, False])
def test_new_navigation_fixture_requires_measured_interfaces_and_source_gate(monkeypatch, tmp_path, ready):
    from integrations.isaac import release_make_payload as fixture
    source = tmp_path / 'observation.json'
    output = tmp_path / 'payload.json'
    source.write_text(json.dumps({'MoveTo_ready': True, 'clock_n': 1, 'odom_n': 1, 'rgb_n': int(ready), 'communication_n': 1, 'last_odom': {'xyz': [10.0, 20.0, 0.5], 'frame': 'odom', 'body_frame': 'base_link'}}))
    monkeypatch.setattr(sys, 'argv', ['fixture', '--observation', str(source), '--output', str(output), '--scene', 'psr'])
    if not ready:
        with pytest.raises(ValueError, match='measured interfaces'):
            fixture.main()
        assert not output.exists()
    else:
        fixture.main()
        row = json.loads(output.read_text())
        assert row['symbolic_verification']['accepted'] and row['supported']
        assert row['candidate'][0]['params']['target'] == [10.6, 20.0, 0.5]
        assert row['commands'][0]['target_xyz'] == [10.6, 20.0, 0.5]
        assert row['body_frame'] == 'base_link'

def test_capture_uses_observed_ros_clock_not_different_world_epoch(monkeypatch, tmp_path):
    from integrations.isaac.release_capture import after_step
    seen = []
    hook = SimpleNamespace(after_world_step=lambda *args: seen.append(args) or {'published': False})
    manager = SimpleNamespace(_paper4_capture=(object(), hook), world=SimpleNamespace(current_time=500.0))
    monkeypatch.setitem(sys.modules, 'rclpy', SimpleNamespace(spin_once=lambda *a, **kw: None))
    after_step(manager)
    assert seen == [()]

@pytest.mark.parametrize('fails', [False, True])
def test_observation_drain_stops_repair_attribution_and_preserves_failure(fails):
    from integrations.isaac.scripts.ros_motion_worker import drain_observations
    result = {'steps': [{'reached': True}]}
    node = SimpleNamespace(active_command={'target': [1, 0, 0]})

    def pump(seconds):
        assert node.active_command is None and seconds == 0.5
        if fails:
            raise RuntimeError('clock reader unavailable')
    node.pump = pump
    drain_observations(node, result)
    assert result['steps'] == [{'reached': True}]
    assert ('observation_drain_error' in result) == fails

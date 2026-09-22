"""Opt-in release hook called after the existing Isaac render step."""
import json
import os
from pathlib import Path

def after_step(manager):
    if not hasattr(manager, '_paper4_capture'):
        import numpy as np
        import rclpy
        from pxr import Gf
        from pxr import Usd
        from pxr import Ar
        from isaacsim.sensors.camera import Camera
        from integrations.isaac.after_render_rgb import AfterRenderRGB
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        import omni.kit.app, sys
        app = omni.kit.app.get_app()
        kit_version = app.get_build_version() if hasattr(app, 'get_build_version') else None
        robot = stage.GetPrimAtPath('/Robots/husky')
        from pxr import UsdGeom
        xyz = UsdGeom.Xformable(robot).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        eye = Gf.Vec3d(xyz[0] + 5, xyz[1] - 6, xyz[2] + 5)
        target = Gf.Vec3d(xyz[0], xyz[1], xyz[2])
        quat = Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse().ExtractRotationQuat()
        camera = Camera(prim_path='/MonitoringCameras/release_camera', resolution=(256, 144))
        camera.set_world_pose(position=np.array(eye), orientation=np.array([quat.GetReal(), *quat.GetImaginary()]), camera_axes='usd')
        camera.initialize()
        node = rclpy.create_node('paper4_release_capture')
        hook = AfterRenderRGB(node, camera, topic='/monitoring/psr_chase', frame_id='release_camera', publish_period_s=0.2)
        manager._paper4_capture = (node, hook)
        materials = []
        with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
            for prim in stage.Traverse():
                attr = prim.GetAttribute('info:mdl:sourceAsset')
                if attr and attr.HasAuthoredValueOpinion():
                    val = attr.Get()
                    path = getattr(val, 'path', str(val))
                    resolved = getattr(val, 'resolvedPath', '') or str(Ar.GetResolver().Resolve(path))
                    materials.append({'prim': str(prim.GetPath()), 'source': path, 'resolved': resolved})
        out = Path(os.environ['PAPER4_RELEASE_OUT'])
        (out / 'scene_loaded.json').write_text(json.dumps({'robot_xyz': list(xyz), 'camera_xyz': list(eye), 'kit_build': kit_version, 'SDK_python': sys.version, 'preview_resolution': [256, 144], 'RGB_stamp_source': 'latest independently received ROS /clock; no World.current_time epoch assumption', 'materials': materials, 'used_layers': [l.realPath for l in stage.GetUsedLayers()], 'scope': 'live Isaac stage/resolver inspection, no LLM or mission-effect claim'}, indent=2) + '\n')
    import rclpy
    node, hook = manager._paper4_capture
    for _ in range(5):
        rclpy.spin_once(node, timeout_sec=0.0)
    value = hook.after_world_step()
    if value.get('published') and hook.published % 20 == 1:
        (Path(os.environ['PAPER4_RELEASE_OUT']) / 'capture_status.json').write_text(json.dumps(value) + '\n')

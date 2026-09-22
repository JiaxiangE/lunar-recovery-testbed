"""Prepare/start an isolated, parameterized Isaac/ROS release-check environment.

No model client. External source/assets/images must already be provisioned.
Only the explicitly named output directory and owned containers are modified.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
OMNILRS = 'bd05f7fb9499a34076504f76f11cb2165a4edbe3'
WORLD_BUILDERS = '23bd494b1bbcdd54cf314250a4312352d285c712'
SCENES = {'psr': 'lunaryard_psr', 'lava': 'lunaryard_lava', 'construction': 'lunaryard_const'}

def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)

def archive(repo, revision, destination, paths=()):
    data = subprocess.check_output(['git', '-C', str(repo), 'archive', revision, *paths])
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
        bundle.extractall(destination, filter='data')

def prepare(args):
    import yaml
    root = args.output.resolve()
    project = args.project.resolve()
    external = args.assets.resolve()
    if root.exists():
        raise FileExistsError('fresh output required; existing scenes/records remain untouched')
    root.mkdir(parents=True)
    sim = root / 'omnilrs'
    archive(args.omnilrs, OMNILRS, sim, ('run.py', 'pyproject.toml', '__init__.py', 'src', 'cfg'))
    archive(args.omnilrs / 'WorldBuilders', WORLD_BUILDERS, sim / 'WorldBuilders')
    run(['git', '-c', 'core.autocrlf=false', 'apply', project / 'integrations/isaac/installation/omnilrs-runtime.patch'], cwd=sim)
    assets = sim / 'assets'
    assets.mkdir()
    for directory, dirs, files in os.walk(external):
        relative = Path(directory).relative_to(external)
        dest = assets / relative
        dest.mkdir(parents=True, exist_ok=True)
        for name in files:
            (dest / name).symlink_to(Path('/external_assets') / relative / name)
    for folder, destination in [('psr', 'psr'), ('lava_tube', 'lava'), ('construction', 'construction')]:
        for source in (project / 'integrations/isaac/scenarios' / folder / 'assets').glob('*.usda'):
            target = assets / 'USD_Assets' / destination / source.name
            if target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    terrain = args.terrain.resolve()
    for name in ('dem.npy', 'mask.npy'):
        if not (terrain / name).is_file():
            raise FileNotFoundError(terrain / name)
    dem = assets / 'ReleaseTerrains' / args.scene / 'selected'
    dem.mkdir(parents=True)
    for name in ('dem.npy', 'mask.npy'):
        (dem / name).symlink_to(Path('/terrain_input') / name)
    cfg = yaml.safe_load((project / 'integrations/isaac/installation/cfg/environment' / (SCENES[args.scene] + '.yaml')).read_text())
    cfg['terrain_manager']['dems_path'] = 'ReleaseTerrains/' + args.scene
    cfg['lunaryard_settings']['terrain_id'] = 0
    (sim / 'cfg/environment/release_check.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False))
    source = sim / 'src/environments_wrappers/ros2/simulation_manager_ros2.py'
    text = source.read_text()
    needle = '            self.world.step(render=True)'
    before, loop = text.split('    def run_simulation(', 1)
    if loop.count(needle) != 1:
        raise ValueError('unexpected upstream render-loop shape')
    loop = loop.replace(needle, needle + '\n            from integrations.isaac.release_capture import after_step\n            after_step(self)')
    text = before + '    def run_simulation(' + loop
    source.write_text(text)
    overlay = root / 'ros_overlay/src/paper4_msgs'
    overlay.parent.mkdir(parents=True)
    shutil.copytree(project / 'integrations/isaac/ros2_ws/src/paper4_msgs', overlay)
    (root / 'out').mkdir()
    config = {'version': 'release-installation-check-v1', 'scene': args.scene, 'project': str(project), 'assets': str(external), 'terrain': str(terrain), 'sim_image': args.sim_image, 'ros_image': args.ros_image, 'domain': args.domain, 'gpu': args.gpu, 'prefix': args.prefix, 'comm_base_xy': [args.comm_base_x, args.comm_base_y], 'comm_range': args.comm_range, 'spawn_xyz': cfg['robots_settings']['parameters']['pose']['position'], 'source_revisions': {'OmniLRS': OMNILRS, 'WorldBuilders': WORLD_BUILDERS}, 'scope': 'new installation validation; not a historical paper experiment or a new model run'}
    (root / 'environment.json').write_text(json.dumps(config, indent=2) + '\n')
    return config

def config(args):
    return json.loads((args.output / 'environment.json').read_text())

def mounts(args, c):
    root = args.output.resolve()
    return ['--network', 'host', '--ipc', 'host', '-e', f"ROS_DOMAIN_ID={c['domain']}", '-e', 'ROS_LOCALHOST_ONLY=1', '-e', 'PYTHONPATH=/repo', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', c['project'] + ':/repo:ro', '-v', str(root / 'out') + ':/out', '--label', 'paper4.release-check=' + c['prefix']]

def start(args):
    c = config(args)
    root = args.output.resolve()
    prefix = c['prefix']
    names = [prefix + '-sim', prefix + '-ros']
    current = subprocess.check_output(['docker', 'ps', '-a', '--format', '{{.Names}}'], text=True).splitlines()
    if set(names) & set(current):
        raise RuntimeError('owned names already exist; inspect/stop explicitly before a new run')
    common = mounts(args, c)
    run(['docker', 'run', '--rm', *common, '-v', str(root / 'ros_overlay') + ':/ros_overlay', '-w', '/ros_overlay', '--entrypoint', 'bash', c['ros_image'], '-lc', 'source /opt/ros/humble/setup.bash && /usr/bin/python3 -c "import sys,PIL,rclpy; print(sys.version, PIL.__version__)" && colcon build --packages-select paper4_msgs'])
    import sys
    run([sys.executable, '-m', 'integrations.isaac.release_assets', '--root', root / 'omnilrs', '--external-assets', c['assets'], '--output', root / 'out/asset_resolution.json'])
    if not (root / 'out/asset_resolution.json').is_file():
        raise RuntimeError('asset preparation did not produce a result; simulator not started')
    run(['docker', 'run', '-d', '--name', names[0], *common, '--gpus', 'device=' + str(c['gpu']), '-e', 'ACCEPT_EULA=Y', '-e', 'PRIVACY_CONSENT=N', '-e', 'PAPER4_RELEASE_OUT=/out', '-v', str(root / 'omnilrs') + ':/workspace/omnilrs', '-v', c['assets'] + ':/external_assets:ro', '-v', c['terrain'] + ':/terrain_input:ro', '-w', '/workspace/omnilrs', '--entrypoint', 'bash', c['sim_image'], '-lc', '/isaac-sim/python.sh run.py environment=release_check rendering.renderer.headless=true mode.ROS_DOMAIN_ID=' + str(c['domain'])])
    run(['docker', 'run', '-d', '--name', names[1], *common, '-v', str(root / 'ros_overlay') + ':/ros_overlay:ro', '--entrypoint', 'bash', c['ros_image'], '-lc', 'source /opt/ros/humble/setup.bash && source /ros_overlay/install/setup.bash && python3 /repo/integrations/isaac/ros2_ws/src/primitive_executors/primitive_executors/move_to_server.py & source /opt/ros/humble/setup.bash; source /ros_overlay/install/setup.bash; python3 /repo/integrations/isaac/ros2_ws/src/comm_evaluator/comm_evaluator/comm_evaluator_node.py --ros-args -p base_x:=' + str(c['comm_base_xy'][0]) + ' -p base_y:=' + str(c['comm_base_xy'][1]) + ' -p comm_range:=' + str(c['comm_range']) + ' & wait'])

def stop(args):
    c = config(args)
    for name in (c['prefix'] + '-ros', c['prefix'] + '-sim'):
        row = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        if row['Config']['Labels'].get('paper4.release-check') != c['prefix']:
            raise ValueError('ownership label differs')
        run(['docker', 'stop', '-t', '20', name])

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'start', 'stop'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--project', type=Path)
    p.add_argument('--omnilrs', type=Path)
    p.add_argument('--assets', type=Path)
    p.add_argument('--terrain', type=Path)
    p.add_argument('--scene', choices=SCENES)
    p.add_argument('--sim-image', default='isaac-sim-omnilrs:latest')
    p.add_argument('--ros-image', default='omnilrs-navigation:v1.0')
    p.add_argument('--comm-base-x', type=float, default=0.0)
    p.add_argument('--comm-base-y', type=float, default=0.0)
    p.add_argument('--comm-range', type=float, default=4.0)
    p.add_argument('--domain', type=int, default=84)
    p.add_argument('--gpu', type=int, default=0)
    p.add_argument('--prefix', default='paper4-release')
    a = p.parse_args()
    if a.command == 'prepare':
        if any((getattr(a, n) is None for n in ['project', 'omnilrs', 'assets', 'terrain', 'scene'])):
            p.error('prepare requires all source/asset/terrain roots and scene')
        prepare(a)
    elif a.command == 'start':
        start(a)
    else:
        stop(a)
if __name__ == '__main__':
    main()

# Three-scene Isaac/ROS installation

This procedure prepares a scene directory and ROS message overlay, then connects MoveTo commands to clock, odometry, communication and rendered RGB observations. Provision the external images and assets below before starting.

## 1. Provision source and external runtime

Use a Linux host with Docker/NVIDIA Container Toolkit and an available GPU. Install Isaac Sim 5.0 under its vendor terms. The captured build recipes are in `integrations/isaac/installation/build_recipes/`; they identify external base images and packages, not a publicly pullable private image digest. Build your local image tags with those recipes or supply compatible existing tags through `--sim-image` and `--ros-image`. The release checks used the recorded Isaac 5.0 and ROS Humble images; rebuilds against newer package repositories should be recorded as a new runtime configuration.

Acquire OmniLRS at `bd05f7fb9499a34076504f76f11cb2165a4edbe3`, initialize WorldBuilders at `23bd494b1bbcdd54cf314250a4312352d285c712`, and obtain its LFS assets following [upstream installation](https://github.com/OmniLRS/OmniLRS/wiki/Installation). [The ROS source comparison](../data/isaac/installation/ros_source_reconstruction.json) identifies baseline `9d09ff1e6b7ead7d761dda37f2e84c59268b3e91`: all 81 compared tracked files match the captured workspace, so its tracked-file patch is empty. The historical Git HEAD itself was not retained. The new overlay builds only this repository's MIT paper4_msgs; the motion/communication nodes are directly launched Python scripts, not assumed colcon packages.

Host preparation uses Python 3.12+ with:

```bash
python3 -m venv .isaac-tools
.isaac-tools/bin/pip install -r integrations/isaac/requirements-preparation.txt
```

The ROS process uses `/usr/bin/python3` after sourcing ROS Humble. The verified image reports Python 3.10.12 and Pillow 9.0.1; rclpy, action_msgs, geometry_msgs, nav_msgs, sensor_msgs, rosgraph_msgs, tf2_msgs, rosidl generators and colcon must be installed in that image. Install distro packages (including `python3-pil`, `python3-colcon-common-extensions`, `ros-humble-rosidl-default-generators`) when building it. The CPU paper-replay Python 3.14 environment is separate. Isaac uses its bundled Python and USD/renderer libraries.

## 2. Supply terrain and scene assets

The `--assets` directory is the acquired OmniLRS `assets/` root. The preparer creates a directory/symlink overlay; vendor files are read-only. It copies the five project props from the checkout and patches only prepared robot copies: the old absolute LiDAR reference points to the existing `velodyne-vlp16.usd`, and the old local Nucleus RSD455 reference points to the existing `common/rsd455.usd`. It does not replace sensor types. The original files and provenance remain intact.

`--terrain` names a directory containing `dem.npy` and `mask.npy`. For the captured PSR profile use `Terrains/Lunaryard/shackleton_psr`; for Lava use `Terrains/LavaTube/lava_base`. The captured Construction YAML inherits the LavaTube ground, so use `lava_base` with the Construction asset layout. A separately generated Construction pad is a different configuration.

To generate missing terrain, mount your asset-building OmniLRS directory at `/workspace/omnilrs` and this checkout at `/repo` in the provisioned Isaac image. Create `/workspace/omnilrs/tmp`, then run `integrations/isaac/scenarios/psr/make_psr_dem.sh` with an explicit path to the NASA DEM, or `integrations/isaac/scenarios/lava_tube/make_lava_dem.sh`. The scripts specify the generated paths. [ISAAC_ASSETS.md](isaac_assets.md) records the PSR input hash, crop, scale and acquisition URL. The stored simulation runs used the existing terrain files.

## 3. Prepare and launch

Run from the checkout. Choose fresh output directories, unique container prefixes and ROS domain IDs. Example:

```bash
export PYTHONPATH="$PWD"
.isaac-tools/bin/python -m integrations.isaac.release_environment prepare   --project "$PWD" --omnilrs /data/OmniLRS --assets /data/OmniLRS/assets   --terrain /data/OmniLRS/assets/Terrains/Lunaryard/shackleton_psr   --scene psr --output /data/checks/psr --prefix paper4-check-psr   --sim-image isaac-sim-omnilrs:latest --ros-image omnilrs-navigation:v1.0   --gpu 0 --domain 84 --comm-base-x 0 --comm-base-y 0 --comm-range 0.3
.isaac-tools/bin/python -m integrations.isaac.release_environment start --output /data/checks/psr
```

Use `--scene lava` or `--scene construction`, the terrain noted above, and new prefixes/output directories for the other scenes. Run them sequentially if they share GPU0. `prepare` reconstructs source from the exact Git objects, applies the captured patch and installs an after-render hook; `start` builds only paper4_msgs, checks asset preparation, and launches the two owned containers.

The geometric communication beacon is specified **in odom coordinates**, not world-spawn coordinates. The short validation uses origin `(0,0)`, range `0.3m`, and a fixed `+0.6m` odom-x target, so range departure is observable. This is a software range model, not physical RF/LOS certification.

The release preview is 256x144 RGBA at a bounded wall publication rate. Its small DDS messages work with the tested stock socket buffers. RGB is captured after the existing render step and stamped from the independently received ROS `/clock`; `World.current_time` is not assumed to have the same startup epoch. Check real message availability before commanding the rover.

## 4. Observe, compile a bounded fixture and execute

```bash
docker exec paper4-check-psr-ros bash -lc 'source /opt/ros/humble/setup.bash; source /ros_overlay/install/setup.bash; python3 -m integrations.isaac.release_observe --output /out/readiness.json --seconds 8'
```

Inspect `/data/checks/psr/out/readiness.json`: clock, odom, RGB and communication counts must be nonzero and MoveTo must be ready. The scene may require several minutes of initial shader/runtime loading. Logs are available through `docker logs paper4-check-psr-sim` and `...-ros`.

Using the CPU environment with Z3 5.1.0, generate a navigation input from the observed odometry:

```bash
.venv/bin/python -B -m integrations.isaac.release_make_payload   --observation /data/checks/psr/out/readiness.json --scene psr   --output /data/checks/psr/out/input.json

docker exec paper4-check-psr-ros bash -lc 'source /opt/ros/humble/setup.bash; source /ros_overlay/install/setup.bash; python3 -m integrations.isaac.scripts.ros_motion_worker --input /out/input.json --output /out/motion.json --image-interval 0.2'
```

The fixture registers a navigation waypoint in the source action domain, runs the real symbolic gate, then sends the fixed planar target through the motion worker. The worker records actual action acceptance, odometry, images and subsequent clocks. Read `motion_targets_reached`, `content_binding`, the frames and any failure. The task-world parent goal is evaluated by the task-world executor.

## 5. Preserve observations and stop owned containers

Keep the output's readiness, asset-resolution, scene-loaded, motion and images files together with the environment configuration and Docker logs. Every run uses a fresh output filename/directory. Stop only the recorded, ownership-labeled pair:

```bash
.isaac-tools/bin/python -m integrations.isaac.release_environment stop --output /data/checks/psr
```

The three-scene release evidence is indexed in [the installation verification record](../data/isaac/installation/verification.json). The record identifies the saved simulator configuration and its measured observations.

### Observed limits

Construction retains the captured secondary-assembler asset with dynamic triangle meshes; PhysX reports convex-hull fallback. The main Husky short action passed, but the secondary collision model and physical assembly were not validated. The recorder drains 0.5 seconds of observations after dispatch, with repair-image attribution ended, so the last repair frame can obtain a subsequent independently received clock. This does not loosen the 0.5-second pose/clock bound or command tolerance. The CPU fixture builder may run on a different machine; transfer readiness and the generated input explicitly.

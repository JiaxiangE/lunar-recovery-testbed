# Isaac assets and versions

The repository supplies the ROS MoveTo interface, motion/communication nodes, scene definitions, terrain-generation scripts, and five project-authored USDA props. Install the simulator, vendor assets and terrain inputs externally, then follow [the installation procedure](isaac_installation.md).

## Software and scene sources

| Component | Version or source | Supplied material |
|---|---|---|
| OmniLRS | [Upstream project](https://github.com/OmniLRS/OmniLRS) | Runtime patch, build recipe and three scene configurations |
| WorldBuilders | OmniLRS submodule | Source selection in the environment preparer |
| Isaac Sim | `5.0.0-rc.45+release.23960.184afb15.gl` | Kit configuration and captured runtime identifiers |
| Kit | `107.3.1+production.206797.8131b85d.gl` | Recorded loading and material-resolution observations |
| ROS demo baseline | Captured ROS source | Build recipe and [source comparison](../data/isaac/installation/ros_source_reconstruction.json) |
| Project ROS interface | ROS 2 Humble, `paper4_msgs/MoveTo` | Message source plus directly launched motion and communication nodes |

[Runtime provenance](../integrations/isaac/installation/provenance.json) records the simulator environment, mounts and ROS package versions. Build your image tags using the recipes or provide compatible installed images.

Acquire OmniLRS assets using its [installation instructions](https://github.com/OmniLRS/OmniLRS/wiki/Installation). The preparer creates a read-only vendor overlay, copies the project props, and relocates the known LiDAR and RSD455 references to the corresponding existing local files. It uses the existing OmniPBR/OmniGlass materials.

## Terrain

The PSR terrain uses `LDEM_83S_10MPP_ADJ.tiff` as its source elevation map.

[NASA LOLA products](https://pgda.gsfc.nasa.gov/products/90) provide the acquisition reference. The generator crops `(22660, 23150, 20, 20)`, resamples to 800x800 with cubic interpolation, applies vertical scale 0.20, and adds seeded surface relief and a spawn pad. Supply the source file explicitly to `integrations/isaac/scenarios/psr/make_psr_dem.sh`.

The saved PSR configuration uses `Terrains/Lunaryard/shackleton_psr`. Lava uses `Terrains/LavaTube/lava_base`; the supplied Construction YAML uses that same ground with its Construction asset layout. `--terrain` selects a directory containing `dem.npy` and `mask.npy`.

## Saved observations

The paper [candidate input](../data/isaac/paper/positive_rgb5s_input.json), compressed [motion/clock record](../data/isaac/paper/positive_rgb5s_clock_bound.json.gz), and RGB frames support CPU content-binding checks. The [installation index](../data/isaac/installation/verification.json) records loading and short-motion observations for the three scenes. These are simulator measurements; sampling, cargo and assembly outcomes come from the task-world models.

Construction's secondary assembler uses dynamic triangle meshes for which PhysX reports convex-hull fallback. The recorded motion check concerns the primary Husky, not the secondary robot's collision or assembly behavior.

## Licenses

Obtain Isaac Sim under [its terms](https://docs.isaacsim.omniverse.nvidia.com/5.0.0/common/licenses-isaac-sim.html). Upstream notices accompany the OmniLRS patch and ROS build recipes. Project `paper4_msgs` is [MIT-licensed](../integrations/isaac/ros2_ws/src/paper4_msgs/LICENSE), copyright JiaxiangE. Vendor models, textures, DEMs and binaries retain their respective licenses. See [dependency notices](../THIRD_PARTY_NOTICES.md).

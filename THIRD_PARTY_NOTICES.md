# Licenses and external dependencies

Project-owned code is distributed under the root [MIT license](LICENSE). External libraries, model weights, datasets, and simulator assets retain their own licenses.

| Component | Source and distribution |
|---|---|
| Shared task-world code | Project-owned code in `domains/` and related functional modules; existing author notices are preserved. |
| Python dependencies and Z3 | Installed through `pyproject.toml`; their distribution licenses apply. Dependency binaries are not bundled. |
| llama.cpp | External source revision `5266f24da75dc449bd56cbed7addb9c8e4a6a73e`. |
| Model weights | External Qwen model repositories. The study configurations identify the model and serving settings used for each run. |
| Fast Downward | External Docker image pinned in `baselines/llmp/domain_extension.py`. |
| Comparison methods | Project-authored adapters and shared-domain implementations. The repository does not distribute the original authors' implementations of LLM+P or HÃƒÂ¶ller's method. |
| Isaac Sim, CUDA, ROS 2, and OmniLRS assets | Installed externally under their component licenses. Runtime identifiers and asset references are listed in [docs/isaac_assets.md](docs/isaac_assets.md). |

The project-owned `paper4_msgs` interface has its own [MIT license](integrations/isaac/ros2_ws/src/paper4_msgs/LICENSE), copyright 2026 JiaxiangE.

Captured OmniLRS modifications retain the upstream [BSD-3-Clause notice](integrations/isaac/installation/OMNILRS_LICENSE). The ROS build recipes include [the upstream notice](integrations/isaac/installation/build_recipes/ros_demo/LICENSE). Four external ROS package manifests captured in `provenance.json` have unresolved license fields; their source trees are not distributed here.

Model weights, container images, Isaac/Kit binaries, and external USD, texture, and DEM files are obtained from their respective providers. The recorded hashes identify files and do not grant redistribution rights.

AI-assisted tools were used in implementation and documentation. The project authors retain responsibility for the software, scientific definitions, and reported results.

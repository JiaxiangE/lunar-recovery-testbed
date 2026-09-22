# Lunar Recovery Testbed

Task worlds and recovery software for lunar sampling, lava-tube operations, and construction. The testbed combines actor-aware action models, scoped planning, contract verification, resource forecasts, and execution records. Packaged data and CPU programs reproduce the accompanying paper's results.

## Install

The tested CPU environment is Python 3.14.3 with Z3 5.1.0. The package requires Python 3.12 or later, excluding 3.14.1 because of its pinned NetworkX dependency. Other interpreter versions have not been run in this validation.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv/Scripts/Activate.ps1
python -m pip install ".[test]"
```

## Run a recovery

```bash
lunar-recover --case psr-position --output runs/psr
lunar-recover --case lava-return --output runs/lava
lunar-recover --case construction --output runs/construction
```

These examples use deterministic candidate responses with the recovery controller, child/parent validation, resource forecast, and task-world executor. The PSR example approaches, collects and stores a sample. Construction reports attainment of the permitted alternative goal and the unmet original goal.

Each output directory contains `trial.json` with the adopted plan, actual execution, goal results and local work, plus `calls.json` with the candidate responses. Choose `--method flat`, `--method htn` or `--method bt` for another implementation. The [API guide](docs/api.md) covers custom inputs, generators and executors; the [action reference](docs/action_reference.md) lists parameters and state effects.

## Reproduce the paper

```bash
lunar-reproduce --output runs/paper
python -m pytest -q
```

The CPU command rebuilds tables, replays saved Task requests and checks recorded Isaac motion bindings. Use a fresh output directory; `--tables-only` omits Task-request replay. [Paper reproduction](docs/reproduction.md) maps each result to its inputs and denominators, including required predecessor requests and cumulative costs.

## Generate with a local model

To generate a new plan with a local 7B model, start the service and edit [its profile](configs/local_service.json):

```bash
lunar-generate --service-profile configs/local_service.json --cell B05_psr_strict_disconnected --arm D_T-full --output runs/new_generation
```

This entry makes a new model request and evaluates the returned Task and parent goals. The routed-session API also supports configured base and edge services.

## Connect Isaac Sim

The [Isaac interface](docs/isaac_installation.md) prepares three scene configurations, sends validated rover-motion commands through ROS, and records odometry, clock and RGB observations. Task-world models evaluate cargo, sampling and assembly goals. Isaac Sim, ROS 2, model assets and terrain are installed externally; [asset sources and versions](docs/isaac_assets.md) are listed with the setup instructions.

## Modules

| Directory | Responsibility |
|---|---|
| `controller/` | Recovery inputs, diagnosis, scope selection and execution workflow |
| `planning/` | Task networks, decomposition, bounded repair and resource search |
| `validation/` | Grounding, symbolic contracts and resource prediction |
| `domains/` | PSR, Lava and Construction worlds and actions |
| `models/` | Request construction, schemas, routing, metering and clients |
| `execution/` | State/candidate binding, dispatch and continuous-link handling |
| `baselines/` | HTN, behavior-tree and LLM+P adapters |
| `integrations/isaac/` | ROS motion/observation interface and scene installation |
| `examples/` | Configured deterministic and local-model entrypoints |
| `data/`, `reproduction/` | Paper records and result reconstruction |

Maintainer: JiaxiangE, jiaxiang.e@mail.mcgill.ca. See [citation](CITATION.cff), [license](LICENSE), and [dependency notices](THIRD_PARTY_NOTICES.md).

# Recovery API and extension

`controller.problem.RecoveryCase` is the input record. It contains a current world, nominal `DomainProblem`, optional policy-authorized alternative, original decomposition tree, failed node, `FailureEvent`, executed prefix, public observations, and communication state. `controller.api.recover` returns a trial and its request records. For a supplied Task and parent, `controller.task_recovery.evaluate_task` provides the single-Task workflow.

```python
from controller.api import recover
from examples.recovery_inputs import build_case

case = build_case("R_T_psr_collection_position_drift")
trial, calls = recover(case, "runs/custom", method="ours", generator="fixture")
print(trial["parent_goal_results"])
print(trial["execution"]["executed_plan"])
```

For a custom input, construct `RecoveryCase` with your own world, task tree, goal, observed failure and public information. `planning/networks.py` builds task networks; `models/fixtures.py` turns caller-supplied plans into deterministic response tables. `examples/task_inputs.py` shows prefix execution and observation capture. Fixture `script_groups` are only responses for deterministic examples; they are excluded from model input and native-planner contexts.

For model generation, pass `generator="session"` and `session_factory`. Its signature is `factory(case, method, trial_id, logical_cap, tier)`. Return a `models.local_routing.RoutedHierarchySession`, configured with explicit `Service` objects, a `LinkState`, and `client_factory(service, ledger)`. Use the shared ledger and a counter for the complete request. `models.local_client.build_client` and `models.grounded_client.build_schema_client` provide the local-server adapters. The client reports unknown provider counts as an unavailable measurement. The session records original replies, schema results, feedback, route, consumption, and retries.

Generation operations are `D_S`, `D_M`, `D_T`, `flat`, and `translation`. `planning.repair.output_contract.generation_schema` and `parse_generation` define their native envelopes. Actors, goals and remaining allowances come from the same problem used by validation. A decline is a model refusal; it neither proves impossibility nor changes the goal policy.

## World and action extension

The common representation is `domains.transition.DomainProblem` with immutable `GroundAction` and `ConditionalEffect` records. Action applicability reads preconditions and negative preconditions; conditional effects read the pre-state. Goal checks include negative literals and distinct-entity cardinality. Use `domains.common.build_problem` for the supplied strict PSR, Lava and Construction worlds.

To add an action, declare its argument/actor schema in `domains/actions/`, implement its transition in the world, and add the corresponding ground actions in `domains/transition.py` (or the strict profile in `domains/common.py`). Update the domain's observation projection and test equality between symbolic transition and world execution. Supply measured resource numbers only where a model exists. An added entity can instead be placed directly in the world's registry before building its problem; grounding preserves its identity.

To add a scenario, implement the world observation and `step` interfaces, register its primitive schema and goal policy, and extend `domains.common.build_problem`, `domains.transition.observe`, and `execution.binding`. Include valid and invalid transition cases, missing actor/entity cases, goal-deletion cases, and stale-state rejection. Custom goals use `with_goal`; cardinality counts distinct facts, not repeated action calls.

## Execution and communication

`execution.binding.execute_plan` validates against the supplied current world and binds candidate content to the authorization context. It records real dispatches and final parent obligations. Independent forecasts operate on copies. Completed actions remain execution history when recovery re-enters.

`execution.continuous_link.ContinuousLink` consumes independent events across request, response, validation and dispatch boundaries. `models.local_routing.LinkState.update` increments the revision; a reconnect does not make a reply from an earlier revision current.

An external executor can use `integrations.isaac.candidate_motion.compile_motion` and `dispatch_motion`. The actuator must echo the received command and report observed motion. Unsupported actions stop before dispatch. The supplied adapter supports planar rover movement; physical cargo, assembly and parent mission outcomes remain unknown.

Resource prediction is an execution aid rather than an added formal energy guarantee. PSR forecasts use the simulator's energy model. Lava and Construction numeric energy remains unknown. Original and alternative goals, partial execution, unknown measurements and failed attempts are separate result fields.

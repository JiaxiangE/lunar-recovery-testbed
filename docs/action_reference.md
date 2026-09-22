# Action reference

Entity IDs are strings from the input registry; coordinate arrays and numeric choices must match a listed binding. Actions are grounded against the current actor/entity registry by `domains.common.build_problem`. Every row has an actor, complete parameter binding, preconditions, add/delete effects, and optional conditional effects. All actions require an available actor; an actor's explicit capability list and unavailable-primitive constraints can further remove rows.

<a id="shared-action-bindings"></a>
Use the bindings actually present in `problem.actions`. The tables describe the supplied schemas; they do not form independent lists from which to combine arbitrary actors and parameters. Conditional effects inspect the state before the action. Validation checks the terminal goal after all deletion effects.

## PSR

The sampling and navigation schemas allow `ROVER` and `SAMPLER`; the supplied PSR world instantiates rovers and a relay. `RELAY` additionally supports the energy/status actions listed below. The current strict profile is defined in `domains/actions/psr/strict_profile.py` and applied by `domains/common.py`.

| Action | Required parameters; actor types | Preconditions and state effects |
|---|---|---|
| `move_to` | `target`: one listed named-sample or coordinate target; ROVER/SAMPLER | Updates the actor's position and matching `at_target` facts; adds `at_location`; clears `at_base` and `docked`. Coordinate bindings use the full listed vector. |
| `scan_spectral` | `target`: registered sample, base or relay ID; ROVER/SAMPLER | Requires actor `at_location`; adds `scanned`. This token does not collect a sample. |
| `sample_collect` | `sample_id`: registered sample ID; ROVER/SAMPLER | Requires the actor within **0.5 m in 3-D** of that sample and the sample not marked unreachable. Adds actor/sample cargo, status `in_rover`, and the collection token; replaces that sample's prior status and clears its `stored` fact. |
| `sample_store` | `sample_id`: registered sample ID; ROVER/SAMPLER | Requires the actor's collection token, ownership in its cargo, and that sample's `in_rover` status. Sets `stored` status and fact plus the actor's storage token. |
| `energy_check` | none; ROVER/SAMPLER/RELAY | Adds `energy_checked`; it does not recharge the actor. |
| `communicate_status` | `target`: `base` or registered actor ID; ROVER/SAMPLER/RELAY | Requires the corresponding base/peer communication permission; adds `status_sent`. Actual runtime communication failures are reported by the world. |
| `communicate_relay` | `relay`: registered relay ID; ROVER/SAMPLER | Requires `relay_linked` and peer permission; adds `status_sent`. This action describes the immediate relay hop. |
| `navigate_to_relay` | `relay`: registered relay ID; ROVER/SAMPLER | Moves to that relay, replaces position/target facts, adds `at_relay`, and clears `at_base`/`docked`. |
| `wait_for_relay` | none; ROVER/SAMPLER | Requires `at_relay`; adds `relay_linked`. |
| `return_to_base` | none; ROVER/SAMPLER | Moves to base, adds `at_base`, and clears `at_location`/`at_relay`. |
| `dock_with_base` | none; ROVER/SAMPLER | Requires `at_base`; adds `docked`. |
| `sample_offload` | `base: "base"`; ROVER/SAMPLER | Requires `at_base` and the actor's storage token. For each sample in **this actor's cargo with status `stored`**, removes its cargo/storage facts, sets status `in_base`, and adds `in_base_storage`. |

Movement and return have different effects even when their coordinates coincide: the common model's base-bound movement row still clears `at_base`, while `return_to_base` establishes it. Choose the primitive by its effects and the exact supplied binding. The named adapter can resolve additional movement spellings; a finite common-domain candidate must match an available action row.

Docking is optional for offload. Mixed cargo stays mixed until the operation: an `in_rover` sample remains aboard, and another actor's cargo is untouched. An offload can succeed while the full parent goal still has missing deliveries. Failed or undispatched actions are evaluated from their actual state changes.

<a id="psr-injected-drive-failure"></a>
<a id="psr-unreachable-sample"></a>
<a id="psr-energy-depletion"></a>
The PSR injection definitions cover drive failures, unavailable targets, and depleted energy. They alter the observed world before recovery; action availability and execution must be checked again. See `domains/injection/loader.py` and `domains/worlds/psr_world.py`. PSR resource forecasts use the configured world energy model; numeric energy is checked during execution rather than certified by the symbolic gate.

## Lava

<a id="lava-sensing-and-navigation"></a>
<a id="lava-position-and-communication"></a>

Targets are registered symbolic IDs such as `entry` and sample IDs. `LavaExecutionWorld` supports the eight actions below. The supplied world has `ROVER` and `SAMPLER` actors; their sensors, tools, capacity and loss state further constrain execution.

| Action | Required parameters; actor types | Preconditions and state effects |
|---|---|---|
| `move_to` | `target`: listed destination ID; ROVER/SAMPLER | Target must be reachable. Updates position and co-located `agent_at` facts, clears prior location facts, and derives `at_entry` within the configured 30 m entry radius. |
| `low_light_navigation` | `target`: listed destination ID; ROVER | Also requires `low_visibility`; applies the same location update. |
| `scan_volatiles` | `target`: listed destination ID; ROVER | Requires a volatiles sensor and reachable target; adds the actor/target reading fact. |
| `headlight_toggle` | none; ROVER/SAMPLER | The declared action turns the headlight on: adds `headlight_on`, removes `headlight_off`. |
| `energy_check` | none; ROVER | Adds `energy_checked`; no numeric energy consumption or recharge is inferred. |
| `peer_communicate` | `peer_id`, nonempty `msg`; ROVER/SAMPLER | Requires peer permission, a distinct registered peer within 30 m, and no outage/conflict at either endpoint. Adds `peer_received(actor,peer)` for the matched position variant. |
| `sample_collect` | `sample_id`: registered sample ID; ROVER/SAMPLER | Requires co-location, status `field`, collection capability and spare actor capacity. Adds actor cargo/ownership, changes status to `in_rover`, and increments cargo count. |
| `sample_store` | `sample_id`: registered sample ID; ROVER/SAMPLER | Requires this actor's cargo and ownership, with status `in_rover` or `stored`. Adds `in_storage`, sets `stored`, and removes `in_rover`. |

<a id="lava-collection-and-storage"></a>
<a id="lava-cargo-ownership"></a>
<a id="lava-state-effects"></a>
Storage retains the cargo ownership represented by this domain. Return is expressed by movement to `entry`; there is no Lava `return_to_base` action. An abandonment goal is supplied by the common scenario policy after declared agent loss, not by a generated action. Outage-clock effects progress with nominal action durations. The world also rejects `move_to` below 5% of the actor's initial energy. Numeric Lava energy consumption remains unspecified. The definitions are in `domains/actions/lava/spec.py`, `domains/transition.py`, and `domains/worlds/lava_execution_world.py`.

## Construction

<a id="construction-action-schemas"></a>
<a id="construction-preparation"></a>

The registry defines six schemas. The shipped `ConstructionExecutionWorld` executes **`install_bracket` and `panel_align_and_dock`**; its supplied inputs observe material/circuit preparation. The four preparation schemas describe the broader registry and require a world adapter implementing those transitions before dispatch.

| Action | Required parameters; actor type | Declared preconditions and effects |
|---|---|---|
| `cargo_pickup` | `material_id`; TRANSPORT | Actor at the material with free cargo capacity; adds actor/material cargo. The common local-domain encoding updates cargo count and free capacity conditionally. |
| `cargo_drop` | `target_location`; TRANSPORT | Nonempty cargo and reachable location; adds `cargo_at(location)`. The registry declares no cargo-deletion effect. |
| `dig_regolith` | `target_location`, numeric `depth`; MANIPULATOR | Digging tool and diggable soil at the location; adds `regolith_pile_at`. Location and depth must match registered options. |
| `place_foundation` | `bracket_id`, `location`; MANIPULATOR | Actor has the bracket and the location is prepared; establishes its location. The common encoding replaces prior placement facts and enables installation only at that bracket's configured foundation. |
| `install_bracket` | `bracket_id`; MANIPULATOR | Bracket at its configured foundation and actor has an arm; adds `bracket_secured`. |
| `panel_align_and_dock` | `panel_id`, `bracket_id`; ASSEMBLER | This actor has the panel, bracket is secured, both entities are reachable, and panel is undamaged; adds `panel_docked_to_bracket` and `panel_installed`. |

<a id="construction-assembly"></a>
Assembly actions preserve allocated cargo in this representation; they do not model gripper release. Actor availability and coordination blockage also apply. The circuit is a separate required state fact. See `domains/actions/construction/spec.py` and `domains/worlds/construction_execution.py`.

<a id="construction-distinct-panel-goal"></a>
<a id="construction-alternative-goal"></a>
<a id="construction-panel-loss"></a>
Panel cardinality counts distinct installed IDs, not repeated docking calls. The supplied panel-loss recovery policy can permit a circuit plus five installed panels instead of the original twelve; its eligibility is observed independently of model proposals. Preparation and action durations are specified by the task-world model. Numeric Construction energy remains unknown.

## Inspect the complete model

```python
from examples.recovery_inputs import build_case
from domains.common import describe_problem

case = build_case("R_T_psr_collection_position_drift")
domain = describe_problem(case.nominal, include_actor_types=True)
print(domain["actor_types"])
for action in case.nominal.actions:
    if action.primitive == "sample_offload":
        print(action.to_step())
        print(action.preconditions, action.negative_preconditions)
        print(action.add_effects, action.delete_effects)
        print(action.conditional_effects)
```

This exposes the exact parameter bindings and state transitions for the selected input. Historical source identifiers used by saved requests are mapped centrally in `data/source_references.json`.

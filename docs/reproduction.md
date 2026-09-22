# Paper reproduction

Run `lunar-reproduce --output runs/paper` from any working directory after installation. Source records are packaged under `data/`; Current analyses use public study paths. `data/source_index.json` resolves historical identifiers found inside saved records at the reading boundary. Model messages, replies, observations and recorded costs retain their original values. New analyses go only to the requested output directory.

| Paper location | Inputs | Output table and expected result |
|---|---|---|
| Main comparison; Supplement S3 | `data/main_comparison/`, `data/indexes/main_comparison.json` | `main.json`: 36 model runs; 12 native references; each model method 8 original and 4 authorized alternative goals; 54/16/16 logical requests; 3,088,645 known tokens, with three unknown-usage attempts |
| Grounding and verification; S4 | `data/grounding/`, `data/contract_gate/` | `grounding_gate.json`: 60 grounding candidates, 120 arms; 5/60 and 60/60 reported delivery outcomes; separate 30-candidate, 60-arm gate study |
| Complete Task view; S5.1 | `data/local_repair/complete_task_view/` | `complete_repair_view.json`: same PSR parent, three actions, 125 simulated seconds, 10.5 Wh; 12,077/8,494/11,986 tokens; 29.7% within-method reduction |
| Task examples; S5.1 | `data/indexes/successful_task_cases.json`, Task generation/feedback/state-assistance records | `Task_cases.json`: nine configuration-specific successful Task requests; parent attainment reported separately; declared two-request costs 31,633 and 30,221 |
| Controlled child retry; S5.2 | `data/local_repair/child_retry/`, `child_retry_source/` | Additional Ours/flat tokens 15,494/19,192; full paths 77,396/36,014; 19.3% applies only to additional retry cost |
| Capability screening; S5.2 | `data/local_repair/capability_screen/`, saved scope inputs | `capability_pruning.json`: 67,144 versus 56,387 full-path tokens |
| Diagnostic policies; S5.3 | `data/diagnostic_refinement/confirmed/workstation/` | `diagnosis.json`: 18 units; each policy 6/6 parents; 64,018/78,760/42,496 tokens |
| Primitive diagnostic specifications; S5.3 | `data/primitive_diagnosis/` | `calibration_primitive.json`: separate 30-response specifications, Primitive selected 0/30 and 28/30 |
| Scope formulation/calibration; S2 | `data/scope_calibration/`, `data/scope_selection/`, indexed real inputs | 600 P/T fit observations; lambda 2.295, p_lucky 0.26; 27 weight combinations; S2 recomputed from 30 records, 26 unique decisions, 7 communication-induced changes |
| Continuous communication; S6 | `data/communication/` | `continuous_link.json`: six paired runs, 13 requests, 309,398 tokens, three stale replies; independent saved dispatch/state reconstruction |
| Embedded measurements; S8 | `data/embedded/`, `data/diagnostic_refinement/confirmed/orin/` | Goal-focused returns 6,665/38,435 tokens; diagnostic runs 61.971/57.030 seconds; saved full-gate warm median 2.015 ms; recorded memory scopes separate |
| Simulation integration; S9 | `data/isaac/paper/`, `integrations/isaac/` | Saved candidate/motion/clock/RGB binding; task-world goals are separate from physical motion |

## Denominators and missing values

Response seeds repeat model responses within fixed inputs; they do not create independent tasks. The nine successful Task examples have different declared request configurations and are not a uniform 9/9 success-rate experiment. Their two reported predecessor requests remain available. Unreported prompt explorations and installation-debug failures are omitted.

Full-path accounting deduplicates reused logical/physical request identifiers. Unknown usage remains unknown and known-token totals are lower bounds where appropriate. A gate rejection, model decline, failed execution or stale response is not reclassified as recovery success. Runtime energy failures in the original gate study remain outside its symbolic contract. Null findings are not equivalence evidence.

## Strict replay and provenance

The CPU replay reconstructs requests through the generation interfaces, compares complete system/user/operation fields, verifies schema, route, sampling and output limits, then reuses the saved reply. An explicit source-reference map restores historical locator strings before lossless context sharing. This restores recorded input spelling; it does not remove fields or change actor, goal, action, observation or feedback checks. Missing source records fail locally, with no model fallback.

Scope scores are computed from saved posteriors, communication and actor/operation permissions using the declared fixed parameters. `data/scope_selection/implementation.json` contains capability/configuration information and historical implementation correspondence. Historical source trees and private Git history are not runtime dependencies. These are later arithmetic reconstructions, distinguished from originally recorded scores.

Fast Downward requires the image pinned in `baselines/llmp/domain_extension.py`; native re-execution is optional. Default table accounting uses the saved native records. Historical hardware times and memory are read as measurements, not recreated by CPU replay on a new machine.

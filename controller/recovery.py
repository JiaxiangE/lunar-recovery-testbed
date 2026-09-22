"""Recovery."""
from copy import deepcopy
from dataclasses import asdict
from dataclasses import is_dataclass
import json
from pathlib import Path
import time
from planning.repair.domain_hierarchy import run_hierarchical
from planning.repair.domain_hierarchy import run_flat
from planning.repair.domain_hierarchy import HierarchyLimits
from planning.repair.domain_hierarchy import IMPLEMENTATION_ID as OURS
from planning.repair.domain_hierarchy import FLAT_IMPLEMENTATION_ID as FLAT
from planning.repair.domain_hierarchy import leaves
from planning.repair.domain_hierarchy import goal_record
from domains.common import observe
from domains.common import describe_problem
from execution.binding import verify_plan
from execution.binding import execute_plan
from baselines.common.limited_worker import run_limited
from baselines.llmp.domain_extension import solve_fd
from baselines.llmp.domain_extension import IMPLEMENTATION_ID as FD
from baselines.htn_repair_holler.domain_repair import REPAIR_IMPLEMENTATION_ID as HTN
from baselines.bt.network_repair import IMPLEMENTATION_ID as BT
from controller.scope import Scope
from models.generation import HierarchyModelSession
from models.generation import PROMPT_CAP
from models.generation import COMPLETION_CAP
METHODS = (OURS, FLAT, HTN, BT, FD)
AXIS_B = METHODS[:5]

def jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((jsonable(v) for v in value), key=repr)
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    return value

def execute_method(case, method, workdir, *, limits=HierarchyLimits(), scripts_builder=None, session_options=None, trial_id=None, resource_support=None, session_factory=None, recomposition_controller=None, candidate_hook=None, diagnosis_mode='legacy_offline', public_preflight=False, task_capability_pruning=False):
    from models.fixtures import make_scripts
    from baselines.common.task_context import make_native_context
    world = deepcopy(case.world)
    continuous = getattr(world, 'continuous_control', None)
    if continuous is not None:
        continuous.enter_world(world)
    trial_id = trial_id or f'{case.cell_id}-{method}-s{case.seed}'
    started = time.perf_counter()
    if method not in AXIS_B:
        raise ValueError('unsupported recovery method: ' + method)
    scripts = {}

    def prepare_scripts():
        for _, p in case.goals():
            scripts.update((scripts_builder or make_scripts)(case, p))
    if not public_preflight:
        prepare_scripts()
    session = None

    def ensure_session():
        nonlocal session
        if session is not None:
            return
        if method in (OURS, FLAT, FD):
            if not scripts:
                prepare_scripts()
            from copy import copy
            factory_case = copy(case)
            factory_case.world = world
            cap = limits.max_model_logical if method in (OURS, FLAT) else len(case.goals())
            tier = 'local' if case.communication != 'Connected' else 'base'
            session = session_factory(case=factory_case, method=method, trial_id=trial_id, logical_cap=cap, tier=tier) if session_factory is not None else HierarchyModelSession(trial_id, scripts, logical_cap=cap, tier=tier, **session_options or {})
    if type(public_preflight) is not bool:
        raise TypeError('public_preflight is a boolean opt-in')
    if public_preflight and candidate_hook is not None:
        raise ValueError('candidate-stimulus and preflight are separate experimental treatments')
    preflight = None
    if public_preflight:
        from planning.repair.shared_preflight import SharedPreflight
        preflight = SharedPreflight(world, case.nominal.scenario_id, resource_support)
    else:
        ensure_session()
    native_invoked = False
    task_pruner = None
    if type(task_capability_pruning) is not bool:
        raise TypeError('task_capability_pruning must be explicit boolean')
    if task_capability_pruning:
        if method != OURS:
            raise ValueError('Task pruning is an explicit hierarchical T configuration')
        from planning.repair.task_capability_pruning import TaskCapabilityPruner
        task_pruner = TaskCapabilityPruner(world, case.nominal.scenario_id, resource_support)
    attempts, execution, chosen, status = ([], None, None, 'no_plan')
    if candidate_hook is not None:
        if method not in (OURS, FLAT):
            raise ValueError('candidate stimulus is defined only for Ours/flat')
        candidate_hook.attach(session, world)
    for policy_name, problem in case.goals():
        record = {'policy': policy_name, 'goal': goal_record(problem)}
        if method == OURS:
            record['scope_decision'] = {'version': 'scope_decision_record_v1', 'status': 'not_invoked', 'selector_invoked': False, 'reason': 'before_hierarchical_call'}
        attempts.append(record)
        try:
            nominal_with_fallback = policy_name == 'nominal' and case.fallback is not None
            call_ceiling = limits.nominal_policy_call_cap if nominal_with_fallback else limits.max_model_logical
            preflight_hit = False
            if preflight is not None:
                probe = preflight.source(case, problem)
                record['source_preflight'] = probe
                if probe['hit']:
                    if method == OURS:
                        record['scope_decision']['reason'] = 'public_preflight_hit'
                    execution = execute_plan(world, problem, probe['candidate'], capture_transitions=True)
                    status = 'executed' if execution.get('execution_success') else 'execution_failed'
                    preflight_hit = True
            if not preflight_hit:
                ensure_session()
                if method in (OURS, FLAT):
                    if method == OURS:
                        native = run_hierarchical(world, problem, case.original_tree, case.failed_node_id, session, failure=case.failure, executed_prefix_ids=case.executed_prefix_ids, limits=limits, permitted_scopes={Scope.S} if policy_name != 'nominal' else None, call_ceiling=call_ceiling, original_information=case.original_information, resource_support=resource_support, candidate_hook=candidate_hook, diagnosis_mode=diagnosis_mode, public_preflight=preflight, task_pruner=task_pruner, decision_audit=record['scope_decision'])
                        record.pop('scope_decision')
                    else:
                        native = run_flat(world, problem, session, original_information=case.original_information, limits=limits, call_ceiling=call_ceiling, resource_support=resource_support, candidate_hook=candidate_hook)
                    record['native'] = native
                    execution = native.get('execution')
                    status = native['status']
                elif method in (HTN, BT):
                    native_invoked = True
                    context = make_native_context(case, problem)
                    module, function = ('baselines.htn_repair_holler.domain_repair', 'solve_htn_repair') if method == HTN else ('baselines.bt.network_repair', 'solve_bt_repair')
                    isolated = run_limited(module, function, (problem,), {'context': context}, timeout_s=30.0, memory_mb=2048)
                    record['native_process'] = isolated.to_dict()
                    native = isolated.result
                    status = isolated.status
                    if isolated.status == 'COMPLETED':
                        status = native.status
                        if native.solved:
                            if resource_support is not None:
                                check = resource_support.verify(problem, native.plan)
                                record['common_symbolic_validation'] = check
                                prediction = resource_support.forecast(world, problem, native.plan) if check['accepted'] else None
                                record['common_resource_prediction'] = prediction
                                if prediction is not None and (not resource_support.may_execute(prediction)):
                                    from baselines.common.resource_replan import replan_native_parent
                                    alternative = replan_native_parent('htn' if method == HTN else 'bt', problem, context=context, scope='M', prediction=prediction, allow_mission_regrouping=case.communication == 'Connected', mandatory_network_obligations=case.original_information.get('mandatory_network_obligations', ()))
                                    record['native_resource_replan'] = alternative
                                    alternative_plan = alternative.get('plan')
                                    if alternative_plan is not None:
                                        alternative_check = resource_support.verify(problem, alternative_plan)
                                        record['native_resource_replan_validation'] = alternative_check
                                        if alternative_check['accepted']:
                                            prediction = resource_support.forecast(world, problem, alternative_plan)
                                            record['native_resource_replan_prediction'] = prediction
                                            if resource_support.may_execute(prediction):
                                                execution = execute_plan(world, problem, alternative_plan, capture_transitions=True)
                                    if execution is None:
                                        status = 'resource_no_native_alternative'
                                        continue
                            if execution is None:
                                execution = execute_plan(world, problem, native.plan, capture_transitions=True)
                else:
                    raw = session.request('translation', problem, None, '', case.original_information)
                    record['raw_translation_response'] = raw
                    record['translation_format'] = {'accepted': None}
                    try:
                        if getattr(session, 'output_contract_version', None) is not None:
                            from planning.repair.output_contract import parse_generation
                            translated = parse_generation(raw, 'translation')
                        else:
                            translated = json.loads(raw)
                        if not isinstance(translated, dict) or set(translated) != {'goal_facts', 'negative_goal_facts', 'goal_cardinality'}:
                            raise ValueError('translation must supply the three explicit goal fields')
                        if any((not isinstance(translated[k], list) for k in translated)):
                            raise ValueError('translation goal fields must be arrays')
                        record['parsed_translation'] = translated
                        record['translation_format'] = {'accepted': True}
                    except (ValueError, TypeError) as exc:
                        record['translation_format'] = {'accepted': False, 'detail': str(exc)}
                        record['translation_rejection'] = str(exc)
                        status = 'translation_schema_invalid'
                        continue
                    native_invoked = True
                    native = solve_fd(problem, Path(workdir) / 'fd', translated_goal=translated, translation_source='instrumented_request_script_non_evidence' if session_factory is None else 'caller_owned_instrumented_session', timeout_s=30.0, hard_wall_s=30.0, memory_mb=2048)
                    record['native'] = native.to_dict()
                    status = native.native_status
                    if native.native_success:
                        if resource_support is not None:
                            check = resource_support.verify(problem, native.canonical_plan)
                            record['common_symbolic_validation'] = check
                            prediction = resource_support.forecast(world, problem, native.canonical_plan) if check['accepted'] else None
                            record['common_resource_prediction'] = prediction
                            if prediction is not None and (not resource_support.may_execute(prediction)):
                                status = 'resource_no_native_alternative'
                                continue
                        execution = execute_plan(world, problem, native.canonical_plan, capture_transitions=True)
                if recomposition_controller is not None and method in (OURS, FLAT):
                    from planning.repair.recomposition_runtime import try_recomposition
                    reconstructed = try_recomposition(case, problem, world, native, method=method, policy_name=policy_name, controller=recomposition_controller)
                    record['prefix_recomposition'] = reconstructed
                    if reconstructed.get('execution') is not None:
                        execution = reconstructed['execution']
                        record['adopted_candidate'] = deepcopy(reconstructed['adopted_candidate'])
                        status = reconstructed['status']
            if execution is not None:
                record['execution'] = execution
                chosen = policy_name
                status = 'execution_failed' if execution.get('accepted') and (not execution.get('execution_success')) else 'wrapper_rejected' if not execution.get('accepted') else 'executed'
                if execution.get('dispatch_n', 0) or execution.get('execution_success'):
                    break
        except Exception as exc:
            status = getattr(exc, 'failure_category', 'instrumentation_failed')
            record['failure'] = {'type': type(exc).__name__, 'detail': str(exc)}
            break
    final = observe(world, case.nominal.scenario_id)
    radio = getattr(world, 'comm_connected_to_base', None)
    physical_radio = {aid: bool(radio(aid)) for aid in world.agents} if callable(radio) else None
    original_remaining = [s for nid, s in leaves(case.original_tree) if nid not in set(case.executed_prefix_ids)]
    unchanged = verify_plan(case.nominal, original_remaining)
    session_record = session.records() if session else None
    row = {'trial_id': trial_id, 'method': method, 'cell_id': case.cell_id, 'seed': case.seed, 'domain_version': case.nominal.domain_version, 'semantics_profile': getattr(world, 'semantics_profile', world.semantics_version if hasattr(world, 'semantics_version') else None), 'status': status, 'representation_supported': True, 'attempts': attempts, 'execution': execution, 'selected_policy': chosen, 'original_goal_met': case.nominal.goal_met(final), 'degraded_goal_met': case.fallback.goal_met(final) if case.fallback else None, 'original_goal_initially_met': case.nominal.goal_met(case.nominal.initial_state), 'original_suffix_still_valid': unchanged['accepted'], 'original_suffix_validity_scope': 'symbolic/authorization obligations only; numeric execution energy is not certified', 'repair_required_by_observed_network': not unchanged['accepted'], 'original_information': case.original_information, 'final_observation': world.snapshot_state(), 'final_facts': sorted(final), 'final_radio_observation': {'connected_by_actor': physical_radio, 'source': type(world).__name__ + '.comm_connected_to_base' if physical_radio is not None else 'source radio model unavailable', 'supervisory_policy': case.communication, 'interpretation': 'physical source-world radio after execution; reaching a return goal does not itself restore communication'}, 'dispatch_n': execution.get('dispatch_n', 0) if execution else 0, 'logical_model_calls': session.logical_n if session else 0, 'model_logical_cap': limits.max_model_logical if method in (OURS, FLAT) else len(case.goals()) if method == FD else 0, 'stage_observations': {'native_kernel_attempted': any(('native' in a or 'native_process' in a for a in attempts)), 'model_transport_attempts': len(session.ledger.physical_attempts) if session else 0, 'common_execution_boundary_reached': execution is not None}, 'actual_model_api_calls': session_record.get('actual_api_calls') if session_record is not None else 0, 'actual_provider_tokens': session_record.get('actual_provider_tokens') if session_record is not None else 0, 'wall_s': time.perf_counter() - started, 'scope': 'configured task-world recovery; generator provenance recorded separately', 'policy_selection_credit': 'shared authorized goal policy' if chosen not in (None, 'nominal') else None, 'runtime_cost_scope': 'PSR actual simulator energy; Construction/Lava numeric energy unknown; durations nominal', 'resource_accounting_scope': 'host hard wall30s per solver goal; Windows Job committed memory2GiB vs Docker cgroup2GiB, distinct peak counters'}
    if resource_support is not None:
        row['resource_assistance'] = resource_support.report()
        row['resource_input'] = problem.observation.get('shared_resource_information')
        row['local_work'] = row['resource_assistance']['metrics']
        row['local_work']['total_wall_s'] = row['wall_s']
    from controller.process_record import describe_process
    from domains.offload_audit import remaining_goal
    row['parent_goal_results'] = {name: remaining_goal(p, final) for name, p in case.goals()}
    if case.nominal.scenario_id == 'psr' and execution is None:
        from domains.offload_audit import custody_state
        before, after = (custody_state(case.world), custody_state(world))
        row['execution_not_started'] = {'reason': status, 'initial_custody': before, 'final_custody': after, 'state_changed': before != after, 'offload_effects_required': False}
    if preflight is not None:
        row['public_preflight'] = preflight.report()
        row['native_invoked'] = native_invoked
        row['adoption_origin'] = 'public_source_plan' if attempts[-1].get('source_preflight', {}).get('hit') else 'original_method_path'
    if task_pruner is not None:
        row['task_capability_pruning'] = task_pruner.report()
    row['repair_process'] = describe_process(row, dict(case.goals()).get(chosen, case.nominal))
    if candidate_hook is not None:
        row['candidate_stimulus'] = candidate_hook.report()
    return (jsonable(row), jsonable(session_record) if session_record is not None else None)

"""Resource recovery."""
from copy import deepcopy
from dataclasses import asdict
import time
import controller.recovery as baseline
from planning.resources import attach_resources
from models.fixtures import ready_scripts
from planning.repair.resource_support import ResourceSupport
from planning.repair.resource_support import AssistanceLimits
from planning.repair.resource_search import ResourceSearchLimits
from planning.repair.hierarchy_budget import limits_for_case
from planning.repair.hierarchy_budget import case_call_bounds
VERSION = 'resource_integrated_comparison_v2'

def _native_wall(attempts):
    total = 0.0
    for a in attempts:
        if 'native_process' in a:
            total += a['native_process']['metrics']['wall_s']
        elif a.get('native', {}).get('native_artifact'):
            artifact = a['native']['native_artifact']
            total += artifact.get('resources', {}).get('host_wall_s_including_setup_cleanup', artifact.get('planner_wall_s', 0.0))
        if a.get('native_resource_replan', {}).get('native_process'):
            total += a['native_resource_replan']['native_process']['metrics']['wall_s']
    return total

def execute_integrated(case, method, workdir, *, model_seed=None, session_factory=None, assistance_limits=AssistanceLimits(), search_limits=ResourceSearchLimits(), prefix_recomposition=False, scripts_builder=None, candidate_hook=None, diagnosis_mode='legacy_offline', experimental_limits_override=None, public_preflight=False, task_capability_pruning=False):
    """Recover a supplied task using independent resource assistance and its declared goals.

    session_factory(case,method,trial_id,logical_cap,tier) returns the existing
    request/records/ledger session protocol. Its observed API/usage fields are
    propagated rather than labelled zero. Real session construction is external.
    """
    started = time.perf_counter()
    if prefix_recomposition and method not in (baseline.OURS, baseline.FLAT):
        raise ValueError('prefix recomposition is defined only for Ours/flat')
    case = attach_resources(deepcopy(case))
    case.original_information['resource_assistance_contract'] = {'permitted_regrouping_scopes': ['M', 'S'] if case.communication == 'Connected' else [], 'parent_obligations': 'preserve all named delivery, custody and explicit source duties; Task-only keeps its return/delivery and actor', 'prediction': 'public observed PSR simulator rollout; same ResourceSupport.forecast interface for all five methods', 'limits': asdict(assistance_limits), 'search_limits': asdict(search_limits), 'method_aid': 'Ours and flat independently search the same declared delivery family; HTN/BT replan with their own kernel; FD retains its own planner'}
    if model_seed is not None:
        case.seed = model_seed
    aid = ResourceSupport(assistance_limits, search_limits=search_limits) if method in baseline.AXIS_B else None
    continuous = getattr(case.world, 'continuous_control', None)
    if continuous is not None:
        if getattr(continuous, 'resource_support', None) is None:
            continuous.resource_support = aid

            def guarded(operation, label):

                def call(*args, **kwargs):
                    revision = continuous.checkpoint(label, expected=continuous.entry_revision)
                    result = operation(*args, **kwargs)
                    continuous.checkpoint(label + '_complete', expected=revision)
                    return result
                return call
            aid.verify = guarded(aid.verify, 'candidate_validation')
            aid.forecast = guarded(aid.forecast, 'resource_prediction')
        else:
            aid = continuous.resource_support
    from planning.repair.recomposition_controller import RecompositionController
    controller = RecompositionController(allow_adoption=True, resource_support=aid) if prefix_recomposition and method in (baseline.OURS, baseline.FLAT) else None
    method_label = dict(zip(baseline.METHODS, ('ours', 'flat', 'htn', 'bt', 'fd')))[method]
    trial_id = f'{case.cell_id}-{method_label}-s{case.seed}'
    limits = limits_for_case(case)
    if diagnosis_mode != 'legacy_offline':
        if method != baseline.OURS or diagnosis_mode not in {'offline_v2', 'connected_refinement', 'edge_diagnostic_refinement'}:
            raise ValueError('diagnosis modes are explicit Ours-only configurations')
        from planning.repair.domain_hierarchy import HierarchyLimits
        from planning.repair.hierarchy_budget import derive_call_bounds
        upper = derive_call_bounds(policy_goals=len(case.goals()))['trial_model_upper'] + len(case.goals())
        limits = HierarchyLimits(upper, 2, 6, upper)
    if experimental_limits_override is not None:
        from planning.repair.domain_hierarchy import HierarchyLimits
        if not isinstance(experimental_limits_override, HierarchyLimits):
            raise TypeError('explicit HierarchyLimits required')
        if method not in (baseline.OURS, baseline.FLAT):
            raise ValueError('generation limits override is Ours/flat only')
        limits = experimental_limits_override
    row, calls = baseline.execute_method(case, method, workdir, limits=limits, scripts_builder=scripts_builder or ready_scripts, session_options={'request_profile': 'recovery_ready_v2'}, resource_support=aid, session_factory=session_factory, trial_id=trial_id, recomposition_controller=controller, candidate_hook=candidate_hook, diagnosis_mode=diagnosis_mode, public_preflight=public_preflight, task_capability_pruning=task_capability_pruning)
    row['prefix_recomposition_enabled'] = bool(prefix_recomposition)
    if experimental_limits_override is not None:
        row['experimental_limits_override'] = asdict(limits)
    row['legacy_resource_search_setting'] = {'assistance_limits': asdict(assistance_limits), 'search_limits': asdict(search_limits)}
    if prefix_recomposition:
        row['operator_version'] = 'scene_prefix_recomposition_integrated_v1'
    if row.get('execution') is not None:
        row['adopted_candidate'] = deepcopy((row['execution'].get('verification') or {}).get('normalized_plan'))
    row.update(task_id=case.cell_id, model_seed=case.seed, implementation_profile=VERSION + '_scene_prefix_v1' if prefix_recomposition else VERSION, policy_eligible=case.fallback is not None, model_source='declared deterministic fixture' if session_factory is None else 'caller-owned model session', method_improvement='symmetric independently invoked resource search for Ours/flat; independent native kernels use common forecast' if aid else None)
    if row.get('adoption_origin') == 'public_source_plan' and row['logical_model_calls'] == 0:
        row['model_source'] = 'not_invoked_public_source_preflight'
    if aid is not None:
        m = row['local_work']
        m.update(native_wall_s=_native_wall(row['attempts']), execution_wall_s=(row.get('execution') or {}).get('wrapper_wall_s', 0.0), total_wall_s=time.perf_counter() - started)
        m['native_replan_calls'] = sum((a.get('native_resource_replan', {}).get('native_replan_calls', 0) for a in row['attempts']))
        row['model_tokens'] = row['actual_provider_tokens']
        if row.get('execution') is None:
            row['metrics'] = {'real_dispatch_n': 0, 'execution_elapsed_s': 0, 'energy_consumed_wh': 0 if case.nominal.scenario_id == 'psr' else None}
        steps = (row.get('execution') or {}).get('executed_plan', [])
        row['communication'] = {'attempts': sum(('communicat' in s['primitive'] for s in steps)), 'delivered': None, 'source': 'local source tokens are not measured radio delivery'}
        row['cost_accounting'] = {'total_wall_s': 'observed inclusive arm wall time, not a sum of nested components', 'prediction_wall_s': 'forecast compute including the subset inside local_search_wall_s', 'execution_wall_s': 'actual execution boundary, including its mandatory revalidation and binding', 'native_wall_s': 'includes native parsing/mapping/verification inside the native worker', 'proposal_validation_wall_s': 'outer candidate and rebuilt-child/parent checks; excludes native/internal execution checks', 'task_preparation': "shared fixture/witness preparation is recorded per task, not charged as each arm's search"}
        selected_problem = dict(case.goals()).get(row.get('selected_policy'), case.nominal)
        row['selected_goal_initially_met'] = selected_problem.goal_met(selected_problem.initial_state)
        row['new_recovery_success'] = not row['selected_goal_initially_met'] and row['repair_required_by_observed_network'] and (row['original_goal_met'] or row['degraded_goal_met'] is True) and (row['status'] == 'executed')
        row['resource_reorganization_used'] = any((e['kind'] == 'resource_search' for e in aid.events))
    from controller.metrics import measure
    row['mechanism_metrics'] = measure(case, row, calls)
    return (baseline.jsonable(row), calls)

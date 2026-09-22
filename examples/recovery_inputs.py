"""Recovery inputs and independently executable witnesses for the repair batch.

Selection is by predeclared failed obligation/transition, never method outcome.
The old cells remain continuation and resource controls. New cells are developer
fixtures, even where they execute an existing source injection. No frequencies
or independent task replication are inferred from model seeds.
"""
from copy import deepcopy
from dataclasses import asdict
from controller.diagnosis import FailureEvent
from domains.common import build_problem
from domains.common import with_goal
from domains.common import atom
from execution.binding import verify_plan
from planning.repair.domain_hierarchy import leaves
from planning.repair.domain_hierarchy import goal_record
from planning.repair.domain_hierarchy import problem_for_node
from planning.schema.decomposition import Task
from examples.task_inputs import CELLS as CONTROL_CELLS
from examples.task_inputs import RecoveryCase
from examples.task_inputs import build_case as build_control
from planning.networks import build_tree
from examples.task_inputs import _dispatch
from examples.task_inputs import _source_injection
from examples.task_inputs import step
from examples.task_inputs import task
from examples.task_inputs import delivery
from models.fixtures import make_scripts
from baselines.common.task_context import make_native_context
RECOVERY_CELLS = ('R_T_psr_collection_position_drift', 'R_T_lava_collection_position_drift', 'R_M_psr_collector_authorization_loss', 'R_M_lava_source_tip_task_reassignment', 'R_S_construction_prefix_source_inj005')
CELLS = CONTROL_CELLS + RECOVERY_CELLS

def _assemble(cell_id, seed, world, scenario, groups, goals, cardinality, prefix_n, deviate, repairs, scope, *, policy_id=None, fallback_cardinality=(), policy_source=None, source_injection=None):
    original = build_problem(world, scenario, goal_facts=goals, goal_cardinality=cardinality, communication_policy={'state': 'Connected'})
    tree = build_tree(original, groups)
    original_leaves = leaves(tree)
    before = deepcopy(world.snapshot_state())
    prefix = []
    for node_id, action in original_leaves[:prefix_n]:
        result = _dispatch(world, scenario, action)
        if not result.success:
            raise RuntimeError(f'physical original prefix failed: {node_id}: {result}')
        prefix.append({'node_id': node_id, 'step': deepcopy(action), 'result': asdict(result)})
    pre_deviation = deepcopy(world.snapshot_state())
    deviation = deviate(world)
    nominal = build_problem(world, scenario, goal_facts=goals, goal_cardinality=cardinality, communication_policy={'state': 'Connected'})
    fallback = with_goal(nominal, facts=goals, cardinality=fallback_cardinality) if policy_id else None
    eligible = bool(policy_id and world.broken_panels == 3 and ('manipulator_1' in world.disabled_agents) and (world.alt_scene_goal == 'install_5_panels_not_12'))
    failed_id = original_leaves[prefix_n][0]
    info = {'tree': tree.model_dump(mode='json'), 'original_plan': [deepcopy(a) for _, a in original_leaves], 'original_observation': before, 'pre_deviation_observation': pre_deviation, 'executed_prefix': prefix, 'executed_prefix_ids': [r['node_id'] for r in prefix], 'observed_deviation': deepcopy(deviation), 'post_deviation_observation': deepcopy(world.snapshot_state()), 'public_policy': {'id': policy_id, 'eligible': eligible, 'source': policy_source, 'nominal_goal': goal_record(nominal), 'fallback_goal': goal_record(fallback) if fallback else None}}
    provenance = {'evidence_class': 'author_added_development_recovery_fixture_non_evidence', 'selection_basis': 'failed obligation and source/observed transition, fixed before method execution', 'source_injection': source_injection, 'scope_stratum': scope, 'coordinate_seed': 0, 'model_seed_is_task_replication': False, 'policy_source': policy_source, 'communication_interpretation': 'shared supervisory request context; no claim of restored robot radio'}
    scripts = {'nominal': deepcopy(groups)} if policy_id else {'nominal': repairs}
    if policy_id:
        scripts[policy_id] = repairs
    failure = FailureEvent(symptom=deviation['symptom'], agent_id=deviation['agent'], node_id=failed_id, context={'scenario': scenario, 'observed_deviation': deepcopy(deviation)})
    return RecoveryCase(cell_id, seed, world, nominal, fallback, policy_id, eligible, original, tree, failed_id, failure, tuple((r['node_id'] for r in prefix)), info['original_plan'], info, provenance, 'Connected', scope, scripts, build_tree(fallback, repairs) if fallback else None)

def build_case(cell_id, seed=1):
    if cell_id in CONTROL_CELLS:
        return build_control(cell_id, seed)
    if cell_id not in RECOVERY_CELLS:
        raise ValueError(cell_id)
    policy_id = policy_source = injection_id = None
    cardinality = fallback_cardinality = ()
    if 'psr' in cell_id:
        from domains.worlds.psr_world import PSRWorld
        from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
        from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
        world = PSRWorld(n_samples=2, semantics_profile=STRICT_PSR_PROFILE)
        world.reset(0)
        for i, sample in enumerate(world.samples.values(), 1):
            sample['location'] = (20.0 * i, 20.0, 0.0)
        scenario = 'psr'
        if cell_id.startswith('R_T'):
            goals = [atom('stored', 'sample_1')]
            groups = [[task('store_sample_1', 'rover_1', goals, delivery('sample_1')[:3])]]
            repairs, scope = (deepcopy(groups), 'T')

            def deviate(w):
                before = tuple(w.agents['rover_1']['position'])
                w.agents['rover_1']['position'] = (before[0] + 1.0, before[1], before[2])
                attempt = step('sample_collect', sample_id='sample_1')
                outcome = _dispatch(w, scenario, attempt)
                if outcome.success:
                    raise RuntimeError('strict location counterexample no longer fails')
                return {'kind': 'author_added_observed_actor_displacement', 'symptom': outcome.failure_mode, 'agent': 'rover_1', 'position_before': before, 'position_after': w.agents['rover_1']['position'], 'attempt': attempt, 'result': asdict(outcome)}
        else:
            goals = [atom('in_base_storage', s) for s in world.samples]
            groups = [[task(f'deliver_{s}', 'rover_1', [atom('in_base_storage', s)], delivery(s)) for s in world.samples]]
            repairs = [[task(f'deliver_{s}', 'rover_2', [atom('in_base_storage', s)], delivery(s, 'rover_2')) for s in world.samples]]
            scope = 'M'

            def deviate(w):
                allowed = {n for n, spec in STRICT_TOKEN_SPECS.items() if 'ROVER' in spec.agent_types}
                w.agents['rover_1']['capabilities'] = sorted(allowed - {'sample_collect'})
                attempt = step('sample_collect', sample_id='sample_1')
                outcome = _dispatch(w, scenario, attempt)
                if outcome.success:
                    raise RuntimeError('observed authorization loss no longer fails')
                return {'kind': 'author_added_observed_authorization_loss', 'symptom': outcome.failure_mode, 'agent': 'rover_1', 'removed_capability': 'sample_collect', 'hardware_damage_claim': False, 'attempt': attempt, 'result': asdict(outcome)}
    elif 'lava' in cell_id:
        from domains.worlds.lava_execution_world import LavaExecutionWorld
        from domains.injection.lava_injections import LAVA_INJECTIONS
        world = LavaExecutionWorld(n_samples=4, storage_capacity=4)
        world.reset(0)
        scenario, sid = ('lava', 'sample_3')
        goals = [atom('in_storage', sid)]
        chain = lambda actor: [step('move_to', actor, target=sid), step('sample_collect', actor, sample_id=sid), step('sample_store', actor, sample_id=sid)]
        groups = [[task('store_sample_3', 'rover_1', goals, chain('rover_1'))]]
        if cell_id.startswith('R_T'):
            scope, repairs = ('T', deepcopy(groups))

            def deviate(w):
                before = tuple(w.agents['rover_1']['position'])
                w.agents['rover_1']['position'] = (before[0] + 1.0, before[1], before[2])
                w._refresh_location_facts()
                attempt = step('sample_collect', sample_id=sid)
                outcome = _dispatch(w, scenario, attempt)
                if outcome.success:
                    raise RuntimeError('Lava location counterexample no longer fails')
                return {'kind': 'author_added_observed_actor_displacement', 'symptom': outcome.failure_mode, 'agent': 'rover_1', 'position_before': before, 'position_after': w.agents['rover_1']['position'], 'attempt': attempt, 'result': asdict(outcome)}
        else:
            scope, injection_id = ('M', 'inj_lava_001')
            repairs = [[task('store_sample_3', 'rover_2', goals, chain('rover_2'))]]
            spec = LAVA_INJECTIONS[injection_id]

            def deviate(w):
                w.apply_injection(spec)
                attempt = step('sample_collect', sample_id=sid)
                outcome = _dispatch(w, scenario, attempt)
                if outcome.success:
                    raise RuntimeError('source tipped-actor attempt unexpectedly succeeded')
                return {**_source_injection(spec), 'kind': 'source_injection_in_author_added_task_context', 'agent': 'rover_1', 'attempt': attempt, 'result': asdict(outcome)}
    else:
        from domains.worlds.construction_execution import assembly_fixture
        from domains.worlds.construction_execution import PANEL_ORDER
        from domains.injection.construction_injections import CONSTRUCTION_INJECTIONS
        world = assembly_fixture(brackets_secured=True, time_s=2300)
        scenario, scope = ('construction', 'S')
        goals = [atom('circuit_connected')]
        atoms = frozenset((atom('panel_installed', p) for p in PANEL_ORDER))
        cardinality, fallback_cardinality = (((12, atoms),), ((5, atoms),))
        install = lambda p: task(f'install_{p}', 'assembler_1', [atom('panel_installed', p)], [step('panel_align_and_dock', 'assembler_1', panel_id=p, bracket_id='bracket_' + p.split('_')[-1])])
        ordered = ['panel_12'] + [p for p in PANEL_ORDER if p != 'panel_12']
        tasks = [install(p) for p in ordered]
        groups = [tasks[:6], tasks[6:]]
        repairs = [[install(f'panel_{i}') for i in (4, 5)], [install(f'panel_{i}') for i in (6, 7)]]
        policy_id, policy_source = ('common_pi_const_nominal12_to_ge5', 'existing PI nominal12 to ge5, same eligible input and alternatives for all methods')
        injection_id = 'inj_const_005'
        spec = CONSTRUCTION_INJECTIONS[injection_id]

        def deviate(w):
            if not w.apply_injection(spec):
                raise RuntimeError('actual prefix failed to reach source injection trigger')
            return {**_source_injection(spec), 'kind': 'source_injection_after_actual_prefix', 'agent': 'assembler_1', 'initial_time_note': 't=2300 initialization plus one actual 100s prefix action'}
    case = _assemble(cell_id, seed, world, scenario, groups, goals, cardinality, 1, deviate, repairs, scope, policy_id=policy_id, fallback_cardinality=fallback_cardinality, policy_source=policy_source, source_injection=injection_id)
    if injection_id == 'inj_lava_001':
        case.provenance['scope_note'] = 'source labels T; fixed Task actor requires Mission reassignment in this comparison representation'
    return case

def audit_case(case):
    """No solver answer or gate label is used as a physical witness oracle."""
    prefix_n = len(case.executed_prefix_ids)
    suffix = deepcopy(case.original_plan[prefix_n:])
    checked = verify_plan(case.nominal, suffix)
    first = checked.get('step_index')
    witnesses = []
    for name, problem in case.goals():
        plan = [deepcopy(s) for group in case.script_groups[name] for t in group for s in t['chain']]
        trial_world = deepcopy(case.world)
        records = []
        for action in plan:
            result = _dispatch(trial_world, problem.scenario_id, action)
            records.append({'step': action, 'result': asdict(result)})
            if not result.success:
                break
        from domains.common import observe
        witnesses.append({'policy': name, 'plan': plan, 'symbolic': verify_plan(problem, plan), 'physical_steps': records, 'execution_success': len(records) == len(plan) and all((r['result']['success'] for r in records)), 'actual_goal_met': problem.goal_met(observe(trial_world, problem.scenario_id)), 'final_observation': trial_world.snapshot_state(), 'given_to_methods_as_answer': False})
    deviation = case.original_information['observed_deviation']
    no_fault = deviation.get('kind') == 'none_nominal_developer_case'
    category = 'goal_degradation_allowed' if case.fallback is not None else 'repair_required' if not checked['accepted'] else 'normal_continuation' if no_fault else 'fault_original_suffix_still_valid'
    remaining_tasks = []
    executed_ids = set(case.executed_prefix_ids)
    for node in case.original_tree.nodes.values():
        if isinstance(node, Task):
            task_leaves = leaves(case.original_tree, node)
            pending = [node_id for node_id, _ in task_leaves if node_id not in executed_ids]
            remaining_tasks.append({'task_id': node.id, 'assigned_actor': node.assigned_agent_id, 'dependencies': list(node.dependencies), 'remaining_leaf_ids': pending, 'executed_leaf_ids': [node_id for node_id, _ in task_leaves if node_id in executed_ids], 'goal': goal_record(problem_for_node(case.nominal, node))})
    return {'cell_id': case.cell_id, 'scope_stratum': case.scope, 'scenario': case.nominal.scenario_id, 'category': category, 'category_scope': 'symbolic obligations and actor authorization; numerical execution energy remains a separate observation', 'resource_stress_control': 'original_seed0' in case.cell_id, 'actual_prefix_n': prefix_n, 'observed_deviation': deepcopy(deviation), 'original_suffix': suffix, 'suffix_symbolic': checked, 'first_invalid_original_step': suffix[first] if first is not None else None, 'first_invalid_original_node': leaves(case.original_tree)[prefix_n + first][0] if first is not None else None, 'remaining_obligations': goal_record(case.nominal), 'original_task_obligations': remaining_tasks, 'public_policy': deepcopy(case.original_information['public_policy']), 'witnesses': witnesses, 'provenance': deepcopy(case.provenance), 'same_method_input_fields': ['world', 'original_observation', 'original_tree', 'original_plan', 'executed_prefix', 'observed_deviation', 'remaining_obligations', 'public_policy'], 'model_seed_is_independent_task': False}

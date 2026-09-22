"""Task inputs."""
from controller.problem import RecoveryCase
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import asdict
from planning.schema.decomposition import Scene
from planning.schema.decomposition import Mission
from planning.schema.decomposition import Task
from planning.schema.decomposition import Primitive
from planning.schema.decomposition import DecompositionTree
from controller.diagnosis import FailureEvent
from domains.common import build_problem
from domains.common import with_goal
from domains.common import observe
from domains.common import atom
from planning.repair.domain_hierarchy import goal_record
from planning.repair.domain_hierarchy import set_node_goal
from planning.repair.domain_hierarchy import leaves
from planning.repair.domain_hierarchy import problem_for_node
from planning.networks import build_tree
from models.fixtures import make_scripts
from baselines.common.task_context import make_native_context
CELLS = ('B01_psr_connected_named_delivery', 'B02_psr_connected_named_store', 'B03_lava_connected_symbolic', 'B04_construction_connected_symbolic', 'B05_psr_strict_disconnected', 'E07_psr_named_navigation', 'E07_psr_five_named_delivery', 'E04_construction_inj005_degraded5', 'E06_lava_abandonment', 'E05_lava_communication_return', 'E07_psr_original_seed0_five_delivery', 'E09_psr_compound_capability_loss')

def step(primitive, actor='rover_1', **params):
    return {'primitive': primitive, 'agent_id': actor, 'params': params}

def delivery(sample, actor='rover_1'):
    return [step('move_to', actor, target=sample), step('sample_collect', actor, sample_id=sample), step('sample_store', actor, sample_id=sample), step('return_to_base', actor), step('dock_with_base', actor), step('sample_offload', actor, base='base')]

def task(name, actor, goals, chain):
    return {'id': name, 'actor': actor, 'goals': list(goals), 'chain': deepcopy(chain)}

def _dispatch(world, scenario, s):
    return world.step(s['agent_id'], s['primitive'], s['params']) if scenario == 'lava' else world.step(s['primitive'], s['agent_id'], s['params'])

def _source_injection(spec):
    return {'id': spec.id, 'symptom': spec.symptom, 'trigger': deepcopy(spec.trigger), 'target': deepcopy(spec.target), 'parameters': deepcopy(spec.parameters)}

def build_case(cell_id, seed=1):
    if cell_id not in CELLS:
        raise ValueError(cell_id)
    communication, scope, prefix_n, policy_id = ('Connected', 'T', 0, None)
    provenance = {'evidence_class': 'development_fixture_non_evidence', 'coordinate_seed': seed}
    injection_fn = None
    fallback_goals, fallback_cardinality, fallback_groups = (None, (), None)
    failure_symptom, failure_actor = ('pending_obligation', 'rover_1')
    if 'psr' in cell_id:
        from domains.worlds.psr_world import PSRWorld
        from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
        from domains.actions.psr.strict_profile import STRICT_TOKEN_SPECS
        original_positions = cell_id == 'E07_psr_original_seed0_five_delivery'
        compound = cell_id == 'E09_psr_compound_capability_loss'
        many = 'five' in cell_id or compound
        if original_positions:
            seed = 0
        world = PSRWorld(n_samples=5 if many else 2, semantics_profile=STRICT_PSR_PROFILE)
        world.reset(seed)
        if not original_positions:
            scale = 2.0 if compound else 20.0
            for i, sample in enumerate(world.samples.values(), 1):
                sample['location'] = (scale * i, scale, 0.0)
            provenance['coordinates'] = 'fixed compact 2m diagnostic spacing' if compound else 'same prior developer 20m spacing'
        else:
            provenance.update(coordinates='unchanged PSRWorld.reset(seed=0) positions', coordinate_seed=0)
        cardinality = ()
        if cell_id.startswith('B02'):
            goals = [atom('stored', 'sample_1')]
            groups = [[task('store_sample_1', 'rover_1', goals, delivery('sample_1')[:3])]]
            repair_groups = [[task('store_sample_1', 'rover_1', goals, [step('sample_store', sample_id='sample_1')])]]
            prefix_n = 2
        elif cell_id.startswith('B05'):
            world.agents['rover_1']['position'] = (20.0, 20.0, 0.0)
            world.agents['rover_1']['facts'].discard('at_base')
            goals = [atom('at_base', 'rover_1')]
            groups = [[task('return_rover_1', 'rover_1', goals, [step('return_to_base')])]]
            repair_groups = deepcopy(groups)
            communication = 'Disconnected'

            def injection_fn(w):
                w.injected_comm_offline.add('rover_1')
                return {'kind': 'existing_PSR_radio_hook', 'agent': 'rover_1', 'author_added_fixture_target': True, 'source': 'PSRWorld.injected_comm_offline; preserves prior B05 developer actor/position, not canonical inj_psr_002'}
            failure_symptom = 'C-04_base_unreachable'
        elif 'navigation' in cell_id:
            goals = [atom('at_target', 'rover_1', 'sample_1')]
            groups = [[task('navigate_sample_1', 'rover_1', goals, [step('move_to', target='sample_1')])]]
            repair_groups, scope = (deepcopy(groups), 'P')
        else:
            ids = list(world.samples) if many else ['sample_1']
            goals = [atom('in_base_storage', sid) for sid in ids]
            group = [task(f'deliver_{sid}', 'rover_1' if i % 2 == 0 else 'rover_2', [atom('in_base_storage', sid)], delivery(sid, 'rover_1' if i % 2 == 0 else 'rover_2')) for i, sid in enumerate(ids)]
            groups, repair_groups = ([group], [deepcopy(group)])
            scope = 'M' if many else 'T'
            if compound:
                prefix_n = 1
                failure_symptom = 'L-01_wheel_stuck'

                def injection_fn(w):
                    w.inject_failure('rover_1', 'move_to', 'L-01_wheel_stuck')
                    attempted = step('move_to', target='sample_1')
                    outcome = _dispatch(w, 'psr', attempted)
                    if outcome.success:
                        raise RuntimeError("compound fixture's actual injected failure did not fire")
                    allowed = {name for name, spec in STRICT_TOKEN_SPECS.items() if 'ROVER' in spec.agent_types}
                    w.agents['rover_2']['capabilities'] = sorted(allowed - {'sample_collect'})
                    return {'kind': 'author_added_compound_deviation', 'attempt': attempted, 'result': asdict(outcome), 'capability_loss': {'agent': 'rover_2', 'removed': 'sample_collect', 'semantics': 'authorization loss, not hardware damage'}}
                repair_groups = [[task(f'deliver_{sid}', 'rover_1', [atom('in_base_storage', sid)], delivery(sid)) for sid in ids]]
        scenario = 'psr'
    elif 'construction' in cell_id:
        from domains.worlds.construction_execution import assembly_fixture
        from domains.worlds.construction_execution import PANEL_ORDER
        from domains.injection.construction_injections import CONSTRUCTION_INJECTIONS
        world = assembly_fixture(brackets_secured=not cell_id.startswith('B04'), time_s=2400 if cell_id.startswith('E04') else 0)
        scenario, failure_actor = ('construction', 'assembler_1')
        if cell_id.startswith('B04'):
            goals, cardinality = ([atom('bracket_secured', 'bracket_1')], ())
            groups = [[task('secure_bracket_1', 'manipulator_1', goals, [step('install_bracket', 'manipulator_1', bracket_id='bracket_1')])]]
            repair_groups = deepcopy(groups)
        else:
            scope, goals = ('S', [atom('circuit_connected')])
            atoms = frozenset((atom('panel_installed', p) for p in PANEL_ORDER))
            cardinality, fallback_cardinality = (((12, atoms),), ((5, atoms),))

            def installation(ids):
                return [task(f'install_{p}', 'assembler_1', [atom('panel_installed', p)], [step('panel_align_and_dock', 'assembler_1', panel_id=p, bracket_id='bracket_' + p.split('_')[-1])]) for p in ids]
            original_tasks = installation(PANEL_ORDER)
            groups = [original_tasks[:6], original_tasks[6:]]
            repair_groups = deepcopy(groups)
            chosen = installation([f'panel_{i}' for i in range(4, 9)])
            fallback_groups = [chosen[:3], chosen[3:]]
            fallback_goals, policy_id = (goals, 'common_pi_const_nominal12_to_ge5')
            spec = CONSTRUCTION_INJECTIONS['inj_const_005']

            def injection_fn(w):
                if not w.apply_injection(spec):
                    raise RuntimeError('Construction source trigger did not fire')
                return _source_injection(spec)
            failure_symptom = spec.symptom
            provenance.update(initial_time='t=2400 fixture, not an executed 2400s prefix', policy_source='PI common nominal12->ge5; Hard8 separately observed', damage_mapping='fixed panel1/2/3 before candidates')
    else:
        from domains.worlds.lava_execution_world import LavaExecutionWorld
        from domains.injection.lava_injections import LAVA_INJECTIONS
        scenario = 'lava'
        world = LavaExecutionWorld(n_samples=2 if cell_id.startswith('B03') else 4, storage_capacity=4)
        world.reset(0 if cell_id.startswith('E06') else seed)
        if cell_id.startswith('B03'):
            goals, cardinality = ([atom('headlight_on', 'rover_1')], ())
            groups = [[task('headlight_rover_1', 'rover_1', goals, [step('headlight_toggle')])]]
            repair_groups = deepcopy(groups)
        elif cell_id.startswith('E05'):
            world.sim_time_s = 600.0
            world.set_depth('rover_2', 40.0)
            goals, cardinality = ([atom('at_entry', 'rover_2')], ())
            groups = [[task('return_rover_2', 'rover_2', goals, [step('move_to', 'rover_2', target='entry')])]]
            repair_groups = deepcopy(groups)
            spec = LAVA_INJECTIONS['inj_lava_002']

            def injection_fn(w):
                w.apply_injection(spec)
                radio = step('peer_communicate', 'rover_2', peer_id='relay_1', msg='request return route')
                return {**_source_injection(spec), 'radio_attempt': radio, 'radio_result': asdict(_dispatch(w, 'lava', radio))}
            communication, failure_symptom, failure_actor = ('Disconnected', spec.symptom, 'rover_2')
            provenance['initial_time'] = 't=600 depth40 fixture'
        else:
            scope = 'S'
            initial_moves = [step('move_to', 'sampler_1', target='sample_4'), step('move_to', 'rover_2', target='sample_4'), step('move_to', 'rover_1', target='sample_3')]
            deployment = [task(f'deployment_{i}', s['agent_id'], [atom('agent_at', s['agent_id'], s['params']['target'])], [s]) for i, s in enumerate(initial_moves)]

            def samples(ids):
                return [task(f'store_{sid}', 'rover_2', [atom('in_storage', sid)], [step('move_to', 'rover_2', target=sid), step('sample_collect', 'rover_2', sample_id=sid), step('sample_store', 'rover_2', sample_id=sid)]) for sid in ids]

            def returns(actors):
                return [task(f'return_{a}', a, [atom('at_entry', a)], [step('move_to', a, target='entry')]) for a in actors if a != 'relay_1']
            goals = [atom('at_entry', a) for a in world.agents]
            atoms = frozenset((atom('in_storage', sid) for sid in world.samples))
            cardinality, fallback_cardinality = (((4, atoms),), ((3, atoms),))
            groups = [deployment, samples(world.samples), returns(world.agents)]
            repair_groups = [samples(world.samples), returns(world.agents)]
            fallback_groups = [samples(('sample_1', 'sample_2', 'sample_3')), returns(('rover_2', 'sampler_1'))]
            fallback_goals = [atom('at_entry', a) for a in world.agents if a != 'rover_1']
            policy_id, prefix_n = ('common_pi_lava_abandonment_3_of_4', 3)
            spec = LAVA_INJECTIONS['inj_lava_001']

            def injection_fn(w):
                w.apply_injection(spec)
                return _source_injection(spec)
            failure_symptom = spec.symptom
            provenance.update(policy_source='author-added PI common abandonment; no natural S or disconnected-rover S capability claim', decision_channel='supervisory Scene policy context; rover connectivity remains observed separately', coordinate_seed=0)
    original_problem = build_problem(world, scenario, goal_facts=goals, goal_cardinality=cardinality, communication_policy={'state': 'Connected'})
    original_tree = build_tree(original_problem, groups)
    original_leaves = leaves(original_tree)
    original_plan = [s for _, s in original_leaves]
    before = deepcopy(world.snapshot_state())
    prefix_records = []
    for node_id, s in original_leaves[:prefix_n]:
        result = _dispatch(world, scenario, s)
        if not result.success:
            raise RuntimeError(f'original physical prefix failed: {node_id}: {result}')
        prefix_records.append({'node_id': node_id, 'step': deepcopy(s), 'result': asdict(result)})
    before_deviation = deepcopy(world.snapshot_state())
    deviation = injection_fn(world) if injection_fn else {'kind': 'none_nominal_developer_case'}
    nominal = build_problem(world, scenario, goal_facts=goals, goal_cardinality=cardinality, communication_policy={'state': communication})
    fallback = with_goal(nominal, facts=fallback_goals, cardinality=fallback_cardinality) if fallback_goals is not None else None
    eligible = False
    if scenario == 'construction' and fallback is not None:
        eligible = world.broken_panels == 3 and 'manipulator_1' in world.disabled_agents and (world.alt_scene_goal == 'install_5_panels_not_12')
    elif scenario == 'lava' and fallback is not None:
        from domains.abandonment import _original_scene
        from domains.abandonment import build_revised_scene_contract
        from domains.abandonment import SceneAbandonmentCandidate
        from domains.abandonment import SceneAbandonmentGate
        from domains.abandonment import CASE_ID
        from domains.abandonment import REASON_CODE
        old = _original_scene()
        revised = build_revised_scene_contract(old, declared_lost_agents=tuple(sorted(world.tipped)))
        candidate = SceneAbandonmentCandidate(CASE_ID, 'S', REASON_CODE, tuple(sorted(world.tipped)), old.context['contract_id'], revised.context['contract_id'])
        eligible = SceneAbandonmentGate().evaluate(candidate, world=world, original=old, revised=revised).accepted
    failed_node_id = original_leaves[prefix_n][0]
    failure = FailureEvent(symptom=failure_symptom, agent_id=failure_actor, node_id=failed_node_id, context={'scenario': scenario, 'observed_deviation': deepcopy(deviation)})
    information = {'tree': original_tree.model_dump(mode='json'), 'original_plan': deepcopy(original_plan), 'original_observation': before, 'pre_deviation_observation': before_deviation, 'executed_prefix': prefix_records, 'executed_prefix_ids': [r['node_id'] for r in prefix_records], 'observed_deviation': deepcopy(deviation), 'post_deviation_observation': deepcopy(world.snapshot_state()), 'public_policy': {'id': policy_id, 'eligible': eligible, 'source': provenance.get('policy_source'), 'nominal_goal': goal_record(nominal), 'fallback_goal': goal_record(fallback) if fallback else None}}
    scripts = {'nominal': repair_groups}
    if policy_id:
        scripts[policy_id] = fallback_groups
    return RecoveryCase(cell_id, seed, world, nominal, fallback, policy_id, eligible, original_problem, original_tree, failed_node_id, failure, tuple((r['node_id'] for r in prefix_records)), original_plan, information, provenance, communication, scope, scripts, build_tree(fallback, fallback_groups) if fallback else None)

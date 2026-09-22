"""EA-W1-10 Stage 1: canonical, frozen PSR/Lava real-tree fixtures.

The fixtures in this module exercise the real ``D_S``/``D_M``/``D_T`` parse and
validation paths with offline mock responses.  They are plumbing evidence only:
``axis_b_real_tree_anchor_v1`` and
``offline_test/non_performance/non_manuscript_evidence``.

The frozen representation deliberately stores canonical JSON rather than a mutable
``DecompositionTree`` object.  Every access reconstructs and revalidates a fresh tree,
so downstream binding/candidate code cannot mutate the pre-failure revision in place.
"""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
import hashlib
import json
from typing import Callable
from typing import Iterable
from typing import Optional
from typing import Sequence
from typing import Tuple
from planning.decomposers import D_M_with_validation
from planning.decomposers import D_S_with_validation
from planning.decomposers import D_T
from models.clients import MockBaseLLMClient
from domains.scenarios import PromptContext
from domains.scenarios import get_scenario_spec
from planning.schema.decomposition import DecompositionTree
from planning.schema.decomposition import Mission
from planning.schema.decomposition import Node
from planning.schema.decomposition import Primitive
from planning.schema.decomposition import Scene
from planning.schema.decomposition import Task
from planning.schema.decomposition import layer_below
from validation.contracts import D_TValidationResult
from validation.contracts import validate_D_M_output
from validation.contracts import validate_D_S_output
from validation.contracts import validate_D_T_output
IMPLEMENTATION_ID = 'axis_b_real_tree_anchor_v1'
EVIDENCE_CLASS = 'offline_test/non_performance/non_manuscript_evidence'
MOCK_FIXTURE_CLASS = 'offline_mock_fixture/non_llm_gated/non_manuscript_evidence'
INITIAL_ANCHOR_BOUNDARY = 'pre_binding_pre_candidate'
STRUCTURE_FIXTURE_EVIDENCE_CLASS = 'offline_test/structure_fixture/non_anchor/non_executable'
STRUCTURE_FIXTURE_BOUNDARY = 'post_dispatch_structure_fixture/non_anchor/non_executable'
LAVA_SCENE_RESIDUAL = 'all_agents_at_entry'
LAVA_SCENE_RESIDUAL_STATUS = 'UNRESOLVED_FOR_CONCRETE_SCENE'

class CanonicalTreeError(ValueError):
    """A tree/decomposer result violates the canonical hierarchy contract."""

@dataclass(frozen=True)
class DecomposerProvenance:
    """Immutable prompt/response/validation record for one decomposition call."""
    decomposer_id: str
    parent_node_id: str
    parent_layer: str
    scenario_id: str
    system_prompt: str
    user_prompt: str
    raw_response: str
    validator_id: str
    validator_passed: bool
    validator_status: str
    validator_reasons: Tuple[str, ...]
    output_node_ids: Tuple[str, ...]
    fixture_class: str = MOCK_FIXTURE_CLASS

@dataclass(frozen=True)
class UnresolvedBranch:
    """A canonical branch deliberately left unexpanded for lack of sourced effects."""
    node_id: str
    layer: str
    required_fact: str
    status: str
    reason: str
    source: str

@dataclass(frozen=True)
class FrozenRealTree:
    """Deep-frozen, integrity-bound canonical tree revision.

    ``canonical_tree_json`` is immutable.  ``tree`` returns a newly parsed and
    structurally validated copy on each access; callers therefore cannot mutate the
    frozen revision used later for failure binding or parent-contract extraction.
    """
    tree_id: str
    tree_revision: int
    tree_hash: str
    canonical_tree_json: str
    scenario_id: str
    implementation_id: str
    evidence_class: str
    provenance: Tuple[DecomposerProvenance, ...]
    frozen_at_boundary: str = INITIAL_ANCHOR_BOUNDARY
    lineage_parent_tree_hash: Optional[str] = None
    scene_residual_predicate: Optional[str] = None
    scene_residual_status: Optional[str] = None
    unresolved_branches: Tuple[UnresolvedBranch, ...] = ()

    @property
    def tree(self) -> DecompositionTree:
        return DecompositionTree.model_validate_json(self.canonical_tree_json)

    def assert_integrity(self) -> None:
        expected = _tree_digest(self.tree_id, self.tree_revision, self.canonical_tree_json, scenario_id=self.scenario_id, implementation_id=self.implementation_id, evidence_class=self.evidence_class, provenance=self.provenance, frozen_at_boundary=self.frozen_at_boundary, lineage_parent_tree_hash=self.lineage_parent_tree_hash, scene_residual_predicate=self.scene_residual_predicate, scene_residual_status=self.scene_residual_status, unresolved_branches=self.unresolved_branches)
        if expected != self.tree_hash:
            raise CanonicalTreeError(f'tree hash mismatch for {self.tree_id!r} revision {self.tree_revision}')

    def assert_initial_anchor(self) -> None:
        """Reject post-dispatch structure fixtures at the pre-binding anchor boundary."""
        self.assert_integrity()
        if self.evidence_class != EVIDENCE_CLASS or self.frozen_at_boundary != INITIAL_ANCHOR_BOUNDARY or self.lineage_parent_tree_hash is not None or (not self.provenance):
            raise CanonicalTreeError('tree revision is an offline structure fixture, not an initial anchor')

def _canonical_tree_json(tree: DecompositionTree) -> str:
    return json.dumps(tree.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def _tree_digest(tree_id: str, revision: int, canonical_tree_json: str, *, scenario_id: str, implementation_id: str, evidence_class: str, provenance: Sequence[DecomposerProvenance], frozen_at_boundary: str, lineage_parent_tree_hash: Optional[str], scene_residual_predicate: Optional[str], scene_residual_status: Optional[str], unresolved_branches: Sequence[UnresolvedBranch]) -> str:
    payload = json.dumps({'tree_id': tree_id, 'tree_revision': revision, 'tree': json.loads(canonical_tree_json), 'scenario_id': scenario_id, 'implementation_id': implementation_id, 'evidence_class': evidence_class, 'provenance': [asdict(item) for item in provenance], 'frozen_at_boundary': frozen_at_boundary, 'lineage_parent_tree_hash': lineage_parent_tree_hash, 'scene_residual_predicate': scene_residual_predicate, 'scene_residual_status': scene_residual_status, 'unresolved_branches': [asdict(item) for item in unresolved_branches]}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def assemble_tree(root_id: str, nodes: Iterable[Node]) -> DecompositionTree:
    """Assemble a strict id-keyed tree and reject ambiguity/orphans loudly.

    ``dict`` construction would silently overwrite duplicate ids, so duplicate checks
    happen before the mapping exists.  Pydantic's canonical schema then checks dangling
    child references, parent links, dependency links, and layer skips.  The additional
    reachability pass disallows unreferenced/orphan nodes.
    """
    copied: list[Node] = []
    seen: set[str] = set()
    for raw in nodes:
        node = type(raw).model_validate(raw.model_dump(mode='json'))
        if not node.id:
            raise CanonicalTreeError('node id must be non-empty')
        if node.id in seen:
            raise CanonicalTreeError(f'duplicate node id {node.id!r}')
        if len(node.children_ids) != len(set(node.children_ids)):
            raise CanonicalTreeError(f'node {node.id!r} lists a child more than once')
        seen.add(node.id)
        copied.append(node)
    try:
        tree = DecompositionTree(root_id=root_id, nodes={node.id: node for node in copied})
    except Exception as exc:
        raise CanonicalTreeError(str(exc)) from exc
    for node in tree.nodes.values():
        if node.id == root_id:
            continue
        if node.parent_id not in tree.nodes:
            raise CanonicalTreeError(f'node {node.id!r} has dangling parent {node.parent_id!r}')
        parent = tree.nodes[node.parent_id]
        if parent.children_ids.count(node.id) != 1:
            raise CanonicalTreeError(f'node {node.id!r} is not referenced exactly once by parent {parent.id!r}')
    reachable: set[str] = set()
    pending = [root_id]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            raise CanonicalTreeError(f'node {node_id!r} is reachable more than once')
        reachable.add(node_id)
        pending.extend(tree.nodes[node_id].children_ids)
    orphaned = sorted(set(tree.nodes) - reachable)
    if orphaned:
        raise CanonicalTreeError(f'orphan node(s): {orphaned}')
    return tree

def freeze_tree(tree: DecompositionTree, *, tree_id: str, tree_revision: int=1, scenario_id: str, provenance: Sequence[DecomposerProvenance]=(), evidence_class: str=EVIDENCE_CLASS, frozen_at_boundary: str=INITIAL_ANCHOR_BOUNDARY, lineage_parent_tree_hash: Optional[str]=None, scene_residual_predicate: Optional[str]=None, scene_residual_status: Optional[str]=None, unresolved_branches: Sequence[UnresolvedBranch]=()) -> FrozenRealTree:
    """Validate and freeze a tree before any binding/candidate operation."""
    if not tree_id:
        raise CanonicalTreeError('tree_id must be non-empty')
    if tree_revision < 1:
        raise CanonicalTreeError('tree_revision must be >= 1')
    allowed_boundaries = {EVIDENCE_CLASS: INITIAL_ANCHOR_BOUNDARY, STRUCTURE_FIXTURE_EVIDENCE_CLASS: STRUCTURE_FIXTURE_BOUNDARY}
    if allowed_boundaries.get(evidence_class) != frozen_at_boundary:
        raise CanonicalTreeError('evidence_class/frozen_at_boundary is not an approved Stage-1 combination')
    if evidence_class == EVIDENCE_CLASS and lineage_parent_tree_hash is not None:
        raise CanonicalTreeError('an initial anchor cannot declare a predecessor revision')
    if evidence_class == STRUCTURE_FIXTURE_EVIDENCE_CLASS and (not lineage_parent_tree_hash):
        raise CanonicalTreeError('a structure fixture must bind its predecessor tree hash')
    validated = assemble_tree(tree.root_id, tree.nodes.values())
    canonical = _canonical_tree_json(validated)
    immutable_provenance = tuple(provenance)
    immutable_unresolved = tuple(unresolved_branches)
    digest = _tree_digest(tree_id, tree_revision, canonical, scenario_id=scenario_id, implementation_id=IMPLEMENTATION_ID, evidence_class=evidence_class, provenance=immutable_provenance, frozen_at_boundary=frozen_at_boundary, lineage_parent_tree_hash=lineage_parent_tree_hash, scene_residual_predicate=scene_residual_predicate, scene_residual_status=scene_residual_status, unresolved_branches=immutable_unresolved)
    frozen = FrozenRealTree(tree_id=tree_id, tree_revision=tree_revision, tree_hash=digest, canonical_tree_json=canonical, scenario_id=scenario_id, implementation_id=IMPLEMENTATION_ID, evidence_class=evidence_class, provenance=immutable_provenance, frozen_at_boundary=frozen_at_boundary, lineage_parent_tree_hash=lineage_parent_tree_hash, scene_residual_predicate=scene_residual_predicate, scene_residual_status=scene_residual_status, unresolved_branches=immutable_unresolved)
    frozen.assert_integrity()
    return frozen

def _provenance(*, decomposer_id: str, parent: Node, scenario_id: str, client: MockBaseLLMClient, raw_response: str, validator_id: str, validator_passed: bool, validator_status: str, validator_reasons: Sequence[str], output_nodes: Sequence[Node]) -> DecomposerProvenance:
    if len(client.calls) != 1:
        raise CanonicalTreeError(f'{decomposer_id} fixture expected one call, observed {len(client.calls)}')
    call = client.calls[0]
    return DecomposerProvenance(decomposer_id=decomposer_id, parent_node_id=parent.id, parent_layer=parent.layer, scenario_id=scenario_id, system_prompt=call['system'], user_prompt=call['user'], raw_response=raw_response, validator_id=validator_id, validator_passed=validator_passed, validator_status=validator_status, validator_reasons=tuple(validator_reasons), output_node_ids=tuple((node.id for node in output_nodes)))

def _with_children(node: Node, children: Sequence[Node]) -> Node:
    return node.model_copy(update={'children_ids': [child.id for child in children]}, deep=True)

def _build_lava_fixture() -> FrozenRealTree:
    scenario_id = 'lava'
    storage_contract = ' & '.join((f'in_storage(sample_{i})' for i in range(1, 5)))
    scene_contract = f'{storage_contract} & all_agents_at_entry'
    scene = Scene(id='lava_scene_tranche1', goal_nl='Store all four deep samples and return all agents to entry', formal_postcondition=scene_contract, context={'artifact_class': EVIDENCE_CLASS, 'canonical_scene_contract': scene_contract, 'source_backed_storage_tranche': storage_contract, 'scene_residual_predicate': LAVA_SCENE_RESIDUAL, 'scene_residual_status': LAVA_SCENE_RESIDUAL_STATUS, 'concrete_world_execution_bridge': 'ABSENT'})
    environment = {'initial_state': 'low_visibility', 'time_budget_s': 1800}
    agents = [{'id': 'rover_1', 'type': 'ROVER', 'capabilities': ['low_light_navigation', 'sample_collect', 'sample_store']}, {'id': 'rover_2', 'type': 'ROVER', 'capabilities': ['low_light_navigation', 'sample_collect', 'sample_store']}, {'id': 'sampler_1', 'type': 'SAMPLER', 'capabilities': ['headlight_toggle']}, {'id': 'relay_1', 'type': 'RELAY', 'capabilities': ['peer_communicate']}]
    ds_raw = json.dumps({'missions': [{'id': 'lava_mission_store_samples', 'goal_nl': 'Collect and store all four deep samples onboard', 'precondition': 'low_visibility', 'postcondition': storage_contract, 'required_agent_types': ['ROVER'], 'dependencies': [], 'estimated_duration_s': 720}, {'id': 'lava_mission_return_agents', 'goal_nl': 'Return all agents to the entry zone', 'precondition': storage_contract, 'postcondition': 'all_agents_at_entry', 'required_agent_types': ['ROVER'], 'dependencies': ['lava_mission_store_samples'], 'estimated_duration_s': 0}]})
    ds_client = MockBaseLLMClient(ds_raw)
    missions = D_S_with_validation(scene, environment, agents, base_llm_client=ds_client, max_retries=0, scenario='lava')
    ds_verdict = validate_D_S_output(missions, scene, environment, agents)
    if not ds_verdict.passed:
        raise CanonicalTreeError(f'Lava D_S fixture failed validation: {ds_verdict.reasons}')
    provenance: list[DecomposerProvenance] = [_provenance(decomposer_id='D_S_with_validation', parent=scene, scenario_id=scenario_id, client=ds_client, raw_response=ds_raw, validator_id='validate_D_S_output', validator_passed=ds_verdict.passed, validator_status='valid', validator_reasons=ds_verdict.reasons, output_nodes=missions)]
    mission = missions[0]
    residual_mission = missions[1].model_copy(update={'context': {'decomposition_status': LAVA_SCENE_RESIDUAL_STATUS, 'reason': 'no source-backed primitive effect discharges all_agents_at_entry', 'concrete_world_execution_bridge': 'ABSENT'}}, deep=True)
    state = {'facts_predicate': 'low_visibility'}
    task_specs = []
    for sample_index in range(1, 5):
        sample_id = f'sample_{sample_index}'
        task_specs.append({'id': f'lava_task_store_{sample_id}', 'goal_nl': f'Navigate in low light, collect, and store {sample_id}', 'assigned_agent_id': 'rover_1', 'precondition': 'low_visibility', 'postcondition': f'in_storage({sample_id})', 'required_primitives': ['low_light_navigation', 'sample_collect', 'sample_store'], 'dependencies': [] if sample_index == 1 else [f'lava_task_store_sample_{sample_index - 1}'], 'context': {'params': {'sample_id': sample_id}}})
    dm_raw = json.dumps({'tasks': task_specs})
    dm_client = MockBaseLLMClient(dm_raw)
    tasks = D_M_with_validation(mission, state, agents, base_llm_client=dm_client, max_retries=0, scenario='lava', scene_goal=scene)
    dm_verdict = validate_D_M_output(tasks, mission, state, agents, scene_goal=scene)
    if not dm_verdict.passed:
        raise CanonicalTreeError(f'Lava D_M fixture failed validation: {dm_verdict.reasons}')
    provenance.append(_provenance(decomposer_id='D_M_with_validation', parent=mission, scenario_id=scenario_id, client=dm_client, raw_response=dm_raw, validator_id='validate_D_M_output', validator_passed=dm_verdict.passed, validator_status='valid', validator_reasons=dm_verdict.reasons, output_nodes=tasks))
    residual_state = {'facts_predicate': storage_contract}
    residual_dm_raw = json.dumps({'tasks': [{'id': 'lava_task_return_agents_residual', 'goal_nl': 'Return all agents to the entry zone', 'assigned_agent_id': 'rover_1', 'precondition': storage_contract, 'postcondition': 'all_agents_at_entry', 'required_primitives': [], 'dependencies': [], 'context': {'decomposition_status': LAVA_SCENE_RESIDUAL_STATUS, 'd_t_status': 'NOT_RUN_NO_SOURCE_BACKED_EFFECT'}}]})
    residual_dm_client = MockBaseLLMClient(residual_dm_raw)
    residual_tasks = D_M_with_validation(residual_mission, residual_state, agents, base_llm_client=residual_dm_client, max_retries=0, scenario='lava', scene_goal=scene)
    residual_dm_verdict = validate_D_M_output(residual_tasks, residual_mission, residual_state, agents, scene_goal=scene)
    if not residual_dm_verdict.passed:
        raise CanonicalTreeError(f'Lava residual D_M fixture failed validation: {residual_dm_verdict.reasons}')
    residual_task = residual_tasks[0]
    provenance.append(_provenance(decomposer_id='D_M_with_validation', parent=residual_mission, scenario_id=scenario_id, client=residual_dm_client, raw_response=residual_dm_raw, validator_id='validate_D_M_output', validator_passed=residual_dm_verdict.passed, validator_status=LAVA_SCENE_RESIDUAL_STATUS, validator_reasons=('D_M C1/C2/C3 valid; D_T intentionally not run because no source-backed effect discharges all_agents_at_entry',), output_nodes=residual_tasks))
    completed_tasks: list[Task] = []
    all_primitives: list[Primitive] = []
    for task in tasks:
        sample_id = task.id.removeprefix('lava_task_store_')
        chain = [{'primitive': 'low_light_navigation', 'params': {'target': sample_id}}, {'primitive': 'sample_collect', 'params': {'sample_id': sample_id}}, {'primitive': 'sample_store', 'params': {'sample_id': sample_id}}]
        dt_raw = json.dumps({'chain': chain})
        dt_client = MockBaseLLMClient(dt_raw)
        prompt_context = PromptContext(observable_failure={'required_primitives': list(task.required_primitives)}, observable_state={'facts': ['low_visibility'], 'fact_provenance': {'low_visibility': 'observed_initial'}}, agents=tuple(agents), communication_state='Disconnected', params={'sample_id': sample_id})
        primitives = D_T(task, edge_llm_client=dt_client, is_failure_recovery=True, max_retries=0, scenario_spec=get_scenario_spec('lava'), prompt_context=prompt_context, execution_path='corrected')
        dt_verdict: D_TValidationResult = validate_D_T_output(task, chain, scenario='lava', prompt_context=prompt_context)
        if not dt_verdict.passed:
            raise CanonicalTreeError(f'Lava D_T fixture for {task.id} failed validation: {dt_verdict.reasons}')
        provenance.append(_provenance(decomposer_id='D_T_option_c_corrected', parent=task, scenario_id=scenario_id, client=dt_client, raw_response=dt_raw, validator_id='validate_D_T_output', validator_passed=dt_verdict.passed, validator_status=dt_verdict.status, validator_reasons=dt_verdict.reasons, output_nodes=primitives))
        completed_tasks.append(_with_children(task, primitives))
        all_primitives.extend(primitives)
    mission = _with_children(mission, completed_tasks)
    residual_mission = _with_children(residual_mission, [residual_task])
    scene = _with_children(scene, [mission, residual_mission])
    tree = assemble_tree(scene.id, [scene, mission, residual_mission, residual_task, *completed_tasks, *all_primitives])
    return freeze_tree(tree, tree_id='axis_b_lava_tranche1_tree_v1', scenario_id=scenario_id, provenance=provenance, scene_residual_predicate=LAVA_SCENE_RESIDUAL, scene_residual_status=LAVA_SCENE_RESIDUAL_STATUS, unresolved_branches=(UnresolvedBranch(node_id=residual_task.id, layer=residual_task.layer, required_fact=LAVA_SCENE_RESIDUAL, status=LAVA_SCENE_RESIDUAL_STATUS, reason='no source-backed primitive effect discharges all_agents_at_entry', source='AR-LAVA-SHARED-EFFECTS-20260902/all_agents_at_entry boundary'),))

def build_lava_real_tree() -> FrozenRealTree:
    """Build and freeze the offline Lava Stage-1 storage tranche tree."""
    return _build_lava_fixture()

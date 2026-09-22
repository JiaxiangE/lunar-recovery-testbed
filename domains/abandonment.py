"""Author-added Lava S-level abandonment/Scene-goal redefinition case (zero API)."""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any
from typing import Iterable
from planning.source_tree import build_lava_real_tree
from planning.schema.decomposition import Scene
from validation.contracts import TRUE_CONTRACT_GATE_KIND
from validation.contracts import require_true_contract_gate
from domains.worlds.lava_world import LavaWorld
CASE_ID = 'inj_lava_revision_s01_scene_abandonment'
REPAIR_TYPE = 'SCENE_ABANDONMENT_GOAL_REDEFINITION'
REASON_CODE = 'UNRECOVERABLE_AGENT_DECLARED_LOST'

@dataclass(frozen=True)
class SceneAbandonmentCandidate:
    candidate_id: str
    scope: str
    reason_code: str
    declared_lost_agents: tuple[str, ...]
    original_contract_id: str
    revised_contract_id: str
    source_injection_id: str = 'inj_lava_001'

@dataclass(frozen=True)
class SceneAbandonmentGateVerdict:
    candidate_id: str
    accepted: bool
    reason_codes: tuple[str, ...]
    gate_kind: str
    contract_semantics_version: str

class SceneAbandonmentGate:
    gate_kind = TRUE_CONTRACT_GATE_KIND
    formal_profile_attested = True
    run_profile = 'offline_test'

    def evaluate(self, candidate: SceneAbandonmentCandidate, *, world: LavaWorld, original: Scene, revised: Scene) -> SceneAbandonmentGateVerdict:
        require_true_contract_gate(self, self.run_profile)
        lost = set(candidate.declared_lost_agents)
        from domains.predicates import parse_predicate

        def atoms(value):
            try:
                return {(a.name, tuple(a.args), a.negated) for a in parse_predicate(value).atomics}
            except (ValueError, TypeError):
                return set()
        original_atoms = {('in_storage', (f'sample_{i}',), False) for i in range(1, 5)} | {('all_agents_at_entry', (), False)}
        revised_atoms = {('tcr_at_least_0_70', (), False), ('all_non_lost_agents_at_entry', (), False)}
        accepted = candidate.scope == 'S' and candidate.reason_code == REASON_CODE and (candidate.source_injection_id == 'inj_lava_001') and bool(lost) and (lost == set(world.tipped)) and (lost <= set(world.agents)) and (set(world.samples) == {f'sample_{i}' for i in range(1, 5)}) and (candidate.original_contract_id == original.context.get('contract_id')) and (candidate.revised_contract_id == revised.context.get('contract_id')) and (candidate.original_contract_id == 'lava_scene_s4_original_v1') and (candidate.revised_contract_id == 'lava_scene_s4_abandonment_v2') and (revised.context.get('original_contract_id') == candidate.original_contract_id) and (set(revised.context.get('declared_lost_agents', ())) == lost) and (revised.context.get('repair_type') == REPAIR_TYPE) and (atoms(original.formal_postcondition) == original_atoms) and (atoms(revised.formal_postcondition) == revised_atoms)
        return SceneAbandonmentGateVerdict(candidate_id=candidate.candidate_id, accepted=accepted, reason_codes=(REASON_CODE if accepted else 'S_LEVEL_ABANDONMENT_GATE_REJECTED',), gate_kind=self.gate_kind, contract_semantics_version='v2')

def _original_scene() -> Scene:
    original = build_lava_real_tree().tree.get('lava_scene_tranche1')
    if not isinstance(original, Scene):
        raise RuntimeError('source-backed Lava Scene is absent')
    return original.model_copy(update={'context': {**original.context, 'contract_id': 'lava_scene_s4_original_v1', 'contract_semantics_version': 'v1'}}, deep=True)

def build_revised_scene_contract(original: Scene, *, declared_lost_agents: Iterable[str]) -> Scene:
    lost = tuple(sorted({str(value) for value in declared_lost_agents}))
    if not lost:
        raise ValueError('revised Scene contract requires declared_lost_agents')
    return Scene(id='lava_scene_tranche1_abandonment_v2', goal_nl='Meet the 70% sample threshold and return all non-lost agents to entry', formal_postcondition='tcr_at_least_0_70 & all_non_lost_agents_at_entry', context={'contract_id': 'lava_scene_s4_abandonment_v2', 'contract_semantics_version': 'v2', 'original_contract_id': original.context['contract_id'], 'declared_lost_agents': list(lost), 'repair_type': REPAIR_TYPE})

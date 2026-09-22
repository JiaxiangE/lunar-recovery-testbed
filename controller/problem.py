"""Public recovery input: current world, goals, task network and observations."""
from dataclasses import dataclass
from controller.diagnosis import FailureEvent
from planning.schema.decomposition import DecompositionTree

@dataclass
class RecoveryCase:
    cell_id: str
    seed: int
    world: object
    nominal: object
    fallback: object | None
    policy_id: str | None
    policy_eligible: bool
    original_problem: object
    original_tree: DecompositionTree
    failed_node_id: str
    failure: FailureEvent
    executed_prefix_ids: tuple
    original_plan: list
    original_information: dict
    provenance: dict
    communication: str
    scope: str
    script_groups: dict
    policy_tree: DecompositionTree | None = None

    def goals(self):
        return [('nominal', self.nominal)] + ([(self.policy_id, self.fallback)] if self.fallback is not None else [])

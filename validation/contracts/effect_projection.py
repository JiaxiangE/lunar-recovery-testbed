"""Auditable candidate-effect projection hook for the one true RecoveryGate.

The hook is intentionally narrow.  A projector may add source-backed facts that the
canonical scenario registry cannot express at the required parameter granularity, but
it cannot inspect or rewrite the parent contract.  Projected facts remain candidate
effects and are recorded in the gate certificate; they are never treated as observed
initial state.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import FrozenSet
from typing import Protocol
from typing import Tuple
from typing import runtime_checkable
from domains.actions.spec import Fact
SOURCE_BACKED_PROJECTOR_KIND = 'source_backed_candidate_effect_projector_v1'

class EffectProjectorCapabilityError(TypeError):
    """An untrusted object attempted to inject facts into the true gate."""

@dataclass(frozen=True)
class CandidateEffectProjection:
    projector_id: str
    scenario_id: str
    candidate_id: str
    input_state_hash: str
    projected_facts: FrozenSet[Fact]
    source_references: Tuple[str, ...]

    def validate(self) -> None:
        for name in ('projector_id', 'scenario_id', 'candidate_id', 'input_state_hash'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a non-empty string')
        if not isinstance(self.projected_facts, frozenset) or not all((isinstance(value, Fact) for value in self.projected_facts)):
            raise ValueError('projected_facts must be a frozenset of Fact')
        if not isinstance(self.source_references, tuple) or not self.source_references or (not all((isinstance(value, str) and value.strip() for value in self.source_references))):
            raise ValueError('source_references must be non-empty source strings')

@runtime_checkable
class CandidateEffectProjector(Protocol):
    """Contract-blind source-backed projection consumed by RecoveryGate."""
    projector_id: str
    scenario_id: str
    projector_kind: str
    formal_profile_attested: bool

    def project(self, candidate: object) -> CandidateEffectProjection:
        ...

def require_source_backed_effect_projector(projector: object, run_profile: str) -> CandidateEffectProjector:
    """Strong use-site identity guard for facts added outside the base registry."""
    if not isinstance(projector, CandidateEffectProjector):
        raise EffectProjectorCapabilityError('effect projector does not implement CandidateEffectProjector')
    if getattr(projector, 'projector_kind', None) != SOURCE_BACKED_PROJECTOR_KIND:
        raise EffectProjectorCapabilityError('effect projector lacks source-backed capability')
    if run_profile in {'formal', 'offline_test'} and getattr(projector, 'formal_profile_attested', None) is not True:
        raise EffectProjectorCapabilityError('formal/offline effect projector lacks formal_profile_attested')
    return projector

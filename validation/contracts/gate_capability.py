"""Single use-site guard for formal true-contract gates."""
from __future__ import annotations
from typing import Protocol
from typing import runtime_checkable
TRUE_CONTRACT_GATE_KIND = 'true_contract_gate_v1'

class FormalGateCapabilityError(RuntimeError):
    """A formal execution path received an uncertified or mismatched gate."""

@runtime_checkable
class TrueContractGateCapability(Protocol):
    gate_kind: str
    formal_profile_attested: bool
    run_profile: str

def require_true_contract_gate(gate: object, run_profile: str) -> None:
    """Validate capabilities at the point where a gate is about to be used.

    Attribute capability checks are intentional: formal callers may wrap the
    gate, but the wrapper must preserve the explicit attestation protocol.
    """
    if getattr(gate, 'gate_kind', None) != TRUE_CONTRACT_GATE_KIND:
        raise FormalGateCapabilityError('formal execution requires gate_kind=true_contract_gate_v1')
    if getattr(gate, 'formal_profile_attested', None) is not True:
        raise FormalGateCapabilityError('formal gate lacks profile attestation')
    if getattr(gate, 'run_profile', None) != run_profile:
        raise FormalGateCapabilityError(f"gate profile {getattr(gate, 'run_profile', None)!r} does not match run profile {run_profile!r}")

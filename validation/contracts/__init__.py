"""Per-layer validation infrastructure (Build Spec §5; D-021; A2 Design §1.6/§1.7).

Contract-entailment checks for D_S / D_M outputs + LLM-readable feedback rendering.
"""
from validation.contracts.contracts import D_TValidationResult
from validation.contracts.contracts import ValidationResult
from validation.contracts.contracts import validate_D_S_output
from validation.contracts.contracts import validate_D_M_output
from validation.contracts.contracts import render_feedback_for_llm
from validation.contracts.contracts import validate_D_T_output
from validation.contracts.gate_capability import FormalGateCapabilityError
from validation.contracts.gate_capability import TRUE_CONTRACT_GATE_KIND
from validation.contracts.gate_capability import TrueContractGateCapability
from validation.contracts.gate_capability import require_true_contract_gate

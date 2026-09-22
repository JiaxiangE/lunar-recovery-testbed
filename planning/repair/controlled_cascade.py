"""Axis-A controlled recovery cascade without synthetic tree splicing.

Each attempted scope performs exactly generation then gate evaluation.  Only a
gate-accepted candidate can be handed to the optional execution callback.
Expected/scorer metadata is rejected at the RecoveryRequest boundary.
"""
from __future__ import annotations
import math
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from controller.scope import SCOPES
from controller.scope import Scope
from controller.scope import scope_order
METRIC_SEMANTICS_VERSION = 'corrected_accepted_execution_v2'
_FORBIDDEN_REQUEST_KEYS = frozenset({'expected', 'expected_cause_level', 'expected_scope', 'ground_truth', 'answer_label', 'experiment_arm', 'experimental_arm', 'scorer_label', 'scorer_labels', 'evaluation_metadata'})

class CascadeError(RuntimeError):
    pass

def _reject_answer_metadata(value: Any, path: str='recovery_request') -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower() in _FORBIDDEN_REQUEST_KEYS:
                raise CascadeError(f'answer/evaluation metadata forbidden at {path}.{key}')
            _reject_answer_metadata(child, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_answer_metadata(child, f'{path}[{index}]')

@dataclass(frozen=True)
class RecoveryRequest:
    request_id: str
    scenario_id: str
    observable_failure: Mapping[str, Any]
    symbolic_state: Mapping[str, Any]
    available_agents: Tuple[Mapping[str, Any], ...] = ()
    resource_constraints: Mapping[str, Any] = field(default_factory=dict)
    communication_policy: Mapping[str, Any] = field(default_factory=dict)
    parent_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise CascadeError('request_id must be non-empty')
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise CascadeError('scenario_id must be non-empty')
        _reject_answer_metadata({'observable_failure': self.observable_failure, 'symbolic_state': self.symbolic_state, 'available_agents': self.available_agents, 'resource_constraints': self.resource_constraints, 'communication_policy': self.communication_policy, 'parent_contract': self.parent_contract})

@dataclass(frozen=True)
class GeneratedCandidate:
    candidate_id: str
    plan: Tuple[Any, ...]
    generation_source: str
    fallback: bool
    generator_draw_id: str
    generation_latency_s: Optional[float] = None
    generator_cache_hit: bool = False

    def validate(self) -> None:
        for name in ('candidate_id', 'generation_source', 'generator_draw_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise CascadeError(f'{name} must be non-empty')
        if not isinstance(self.plan, tuple):
            raise CascadeError('candidate plan must be a tuple')
        if not isinstance(self.fallback, bool):
            raise CascadeError('fallback must be bool')
        if self.fallback != (self.generation_source == 'fallback_template'):
            raise CascadeError('fallback must be true exactly for generation_source=fallback_template')
        if self.generation_latency_s is not None:
            _nonnegative_finite(self.generation_latency_s, 'generation_latency_s')
        if not isinstance(self.generator_cache_hit, bool):
            raise CascadeError('generator_cache_hit must be bool')

@dataclass(frozen=True)
class GateVerdict:
    accepted: bool
    reason_codes: Tuple[str, ...] = ()
    gate_latency_s: Optional[float] = None
    certificate: Optional[Mapping[str, Any]] = None

    def validate(self) -> None:
        if not isinstance(self.accepted, bool):
            raise CascadeError('gate accepted must be bool')
        if not isinstance(self.reason_codes, tuple) or not all((isinstance(code, str) and code for code in self.reason_codes)):
            raise CascadeError('gate reason_codes must be non-empty strings')
        if self.gate_latency_s is not None:
            _nonnegative_finite(self.gate_latency_s, 'gate_latency_s')
        if self.certificate is not None and (not isinstance(self.certificate, Mapping)):
            raise CascadeError('gate certificate must be a mapping when supplied')

@dataclass(frozen=True)
class CascadeAttempt:
    attempt_id: str
    ordinal: int
    attempted_scope: Scope
    candidate: GeneratedCandidate
    gate_verdict: GateVerdict
    generation_latency_s: float
    gate_latency_s: float

@dataclass(frozen=True)
class AcceptedExecution:
    accepted_candidate_id: str
    result: Any
    execution_latency_s: float
    execution_makespan_s: float

    def validate(self) -> None:
        if not isinstance(self.accepted_candidate_id, str) or not self.accepted_candidate_id:
            raise CascadeError('accepted_candidate_id must be non-empty')
        _nonnegative_finite(self.execution_latency_s, 'execution_latency_s')
        _nonnegative_finite(self.execution_makespan_s, 'execution_makespan_s')

@dataclass(frozen=True)
class CascadeResult:
    accepted: bool
    abandoned: bool
    initial_scope: Scope
    final_scope: Scope
    attempts: Tuple[CascadeAttempt, ...]
    accepted_candidate: Optional[GeneratedCandidate]
    execution: Optional[AcceptedExecution]
    failure_reason: str
    generation_latency_s: float
    gate_latency_s: float
    execution_makespan_s: float
    metric_semantics_version: str = METRIC_SEMANTICS_VERSION

def _nonnegative_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not math.isfinite(value)) or (value < 0):
        raise CascadeError(f'{name} must be a non-negative finite number')
    return float(value)

def _record(trace: Any, method: str, value: Any) -> None:
    if trace is None:
        return
    recorder = getattr(trace, method, None)
    if not callable(recorder):
        raise CascadeError(f'trace sink lacks {method}()')
    recorder(value)

class ControlledRecoveryCascade:
    """Controlled Axis-A scope escalation: generate -> gate -> reject/escalate."""

    def run(self, selected_scope: Scope | str, recovery_request: RecoveryRequest, candidate_generator: Callable[[Scope, RecoveryRequest], GeneratedCandidate], gate: Callable[[GeneratedCandidate, RecoveryRequest, Scope], GateVerdict], trace: Any=None, execute_accepted: Optional[Callable[[GeneratedCandidate, RecoveryRequest], AcceptedExecution]]=None, run_profile: str='offline_test', require_certified_gate: bool=False, preassigned_scope: bool=False, max_semantic_retries: int=0) -> CascadeResult:
        if not isinstance(recovery_request, RecoveryRequest):
            raise CascadeError('controlled cascade requires a RecoveryRequest')
        if run_profile in {'formal', 'offline_test'} or require_certified_gate:
            from validation.contracts.gate_capability import require_true_contract_gate
            require_true_contract_gate(gate, run_profile)
        initial_scope = Scope(selected_scope)
        if type(preassigned_scope) is not bool or type(max_semantic_retries) is not int or max_semantic_retries < 0:
            raise CascadeError('preassigned_scope must be bool and max_semantic_retries a nonnegative integer')
        attempts: list[CascadeAttempt] = []
        candidate_ids: set[str] = set()
        scopes = (initial_scope,) if preassigned_scope else SCOPES[scope_order(initial_scope):]
        attempt_scopes = tuple((scope for scope in scopes for _ in range(max_semantic_retries + 1)))
        for ordinal, attempted_scope in enumerate(attempt_scopes, start=1):
            generation_start = time.perf_counter()
            candidate = candidate_generator(attempted_scope, recovery_request)
            measured_generation = time.perf_counter() - generation_start
            if not isinstance(candidate, GeneratedCandidate):
                raise CascadeError('candidate_generator must return GeneratedCandidate')
            candidate.validate()
            if candidate.candidate_id in candidate_ids:
                raise CascadeError(f'candidate_id reused across attempts: {candidate.candidate_id}')
            candidate_ids.add(candidate.candidate_id)
            generation_latency = measured_generation if candidate.generation_latency_s is None else float(candidate.generation_latency_s)
            gate_start = time.perf_counter()
            verdict = gate(candidate, recovery_request, attempted_scope)
            measured_gate = time.perf_counter() - gate_start
            if not isinstance(verdict, GateVerdict):
                raise CascadeError('gate must return GateVerdict')
            verdict.validate()
            gate_latency = measured_gate if verdict.gate_latency_s is None else float(verdict.gate_latency_s)
            attempt = CascadeAttempt(attempt_id=f'{recovery_request.request_id}:attempt:{ordinal}', ordinal=ordinal, attempted_scope=attempted_scope, candidate=candidate, gate_verdict=verdict, generation_latency_s=generation_latency, gate_latency_s=gate_latency)
            attempts.append(attempt)
            _record(trace, 'record_attempt', attempt)
            if not verdict.accepted:
                continue
            execution = None
            if execute_accepted is not None:
                execution = execute_accepted(candidate, recovery_request)
                if not isinstance(execution, AcceptedExecution):
                    raise CascadeError('execute_accepted must return AcceptedExecution')
                execution.validate()
                if execution.accepted_candidate_id != candidate.candidate_id:
                    raise CascadeError('execution candidate ID does not match accepted candidate')
                _record(trace, 'record_execution', execution)
            return CascadeResult(accepted=True, abandoned=False, initial_scope=initial_scope, final_scope=attempted_scope, attempts=tuple(attempts), accepted_candidate=candidate, execution=execution, failure_reason='', generation_latency_s=sum((item.generation_latency_s for item in attempts)), gate_latency_s=sum((item.gate_latency_s for item in attempts)), execution_makespan_s=execution.execution_makespan_s if execution else 0.0)
        return CascadeResult(accepted=False, abandoned=True, initial_scope=initial_scope, final_scope=initial_scope if preassigned_scope else Scope.S, attempts=tuple(attempts), accepted_candidate=None, execution=None, failure_reason='gate rejected all candidates at the preassigned scope' if preassigned_scope else 'gate rejected every candidate through Scene', generation_latency_s=sum((item.generation_latency_s for item in attempts)), gate_latency_s=sum((item.gate_latency_s for item in attempts)), execution_makespan_s=0.0)

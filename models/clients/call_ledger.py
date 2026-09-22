"""Call ledger."""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
import hashlib
import ipaddress
import json
import math
import re
from threading import Lock
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Optional
from urllib.parse import urlsplit
from uuid import uuid4
UNAVAILABLE = 'unavailable'
USAGE_PROVENANCE = frozenset({'actual', 'synthetic', 'estimated_for_budget', UNAVAILABLE})
MEASUREMENT_PROVENANCE = frozenset({'actual', 'synthetic', 'estimated_for_budget'})
TRANSPORT_ATTESTATIONS = frozenset({'openai_sdk_max_retries_0', 'local_replay_single_attempt', 'test_only_injected_transport_non_evidence'})
RETRY_POLICY_IDS = frozenset({'bounded_transport_v1_408_409_429_5xx'})
STAGES = frozenset({'diagnosis', 'generation'})
PARSE_OUTCOMES = frozenset({'valid', 'empty_response', 'malformed_json', 'schema_invalid', 'off_vocab', 'bad_arguments', 'goal_incomplete', 'gate_rejected', 'not_attempted_transport_error'})
PHYSICAL_OUTCOMES = frozenset({'pending', 'success', 'retryable_error', 'fatal_error'})
LOGICAL_OUTCOMES = frozenset({'pending', 'transport_succeeded', 'transport_failed'})
_SAFE_CODE = re.compile('^[A-Za-z0-9_.:-]{1,96}$')
_SAFE_METADATA = re.compile('^[A-Za-z0-9._/-]{1,256}$')
_UTC_TIMESTAMP = re.compile('^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z$')
_PUBLIC_ENDPOINT_ALLOWLIST = frozenset({'dashscope.aliyuncs.com', 'api.openai.com', 'api.example.com'})

class CallLedgerError(RuntimeError):
    """The call ledger is incomplete, contradictory, or unsafe to summarize."""

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def endpoint_hostname(endpoint: str) -> str:
    """Return only the lower-case hostname, excluding credentials, port and path."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise CallLedgerError('endpoint must be a non-empty string')
    value = endpoint.strip()
    parsed = urlsplit(value if '://' in value else f'//{value}')
    if not parsed.hostname:
        raise CallLedgerError('endpoint does not contain a hostname')
    hostname = parsed.hostname.lower().rstrip('.')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if (hostname == 'localhost' or '.' not in hostname or hostname.endswith(('.local', '.internal', '.lan', '.localhost')) or (address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved))) or hostname not in _PUBLIC_ENDPOINT_ALLOWLIST:
        return 'private-endpoint-redacted'
    return hostname

def prompt_sha256(system_prompt: str, user_prompt: str) -> str:
    if not isinstance(system_prompt, str) or not isinstance(user_prompt, str):
        raise CallLedgerError('prompts must be strings')
    payload = json.dumps({'system': system_prompt, 'user': user_prompt}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def _safe_code(value: Any, field_name: str) -> str:
    text = str(value or '')
    if not _SAFE_CODE.fullmatch(text):
        raise CallLedgerError(f'{field_name} must be a short machine-readable code')
    return text

def _text_or_unavailable(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    text = str(value).strip()
    lowered = text.lower()
    if not _SAFE_METADATA.fullmatch(text) or any((marker in lowered for marker in ('secret', 'api_key', 'authorization', 'bearer'))) or lowered.startswith('sk-'):
        return UNAVAILABLE
    return text

def _nonnegative_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not math.isfinite(value)) or (value < 0):
        raise CallLedgerError(f'{field_name} must be a non-negative finite number')
    return float(value)

def _token_value(value: Any, field_name: str) -> int | str:
    if value == UNAVAILABLE:
        return UNAVAILABLE
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CallLedgerError(f'{field_name} must be a non-negative integer or unavailable')
    return value

@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | str = UNAVAILABLE
    completion_tokens: int | str = UNAVAILABLE
    total_tokens: int | str = UNAVAILABLE
    provenance: str = UNAVAILABLE
    reasoning_tokens: int | str = UNAVAILABLE

    def validate(self) -> None:
        values = {'prompt_tokens': _token_value(self.prompt_tokens, 'prompt_tokens'), 'completion_tokens': _token_value(self.completion_tokens, 'completion_tokens'), 'total_tokens': _token_value(self.total_tokens, 'total_tokens')}
        if self.provenance not in USAGE_PROVENANCE:
            raise CallLedgerError(f'unknown usage provenance: {self.provenance!r}')
        numeric = [isinstance(value, int) for value in values.values()]
        if self.provenance == UNAVAILABLE and any(numeric):
            raise CallLedgerError('unavailable usage provenance cannot contain token counts')
        if self.provenance != UNAVAILABLE and (not all(numeric)):
            raise CallLedgerError('actual/synthetic/estimated usage requires all three token counts')
        if all(numeric) and self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise CallLedgerError('total_tokens must equal prompt_tokens + completion_tokens')
        reasoning = _token_value(self.reasoning_tokens, 'reasoning_tokens')
        if isinstance(reasoning, int):
            if self.provenance == UNAVAILABLE:
                raise CallLedgerError('unavailable usage provenance cannot contain token counts')
            completion = values['completion_tokens']
            if isinstance(completion, int) and reasoning > completion:
                raise CallLedgerError('reasoning_tokens cannot exceed completion_tokens under reasoning-plus-answer billing')

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

@dataclass
class PhysicalAttemptRecord:
    physical_attempt_id: str
    logical_call_id: str
    ordinal: int
    stage: str
    model: str
    endpoint_hostname: str
    prompt_sha256: str
    prompt_version: str
    temperature: float
    max_tokens: int
    prompt_token_cap: int
    prompt_token_upper_bound: int | str
    seed: int | str
    measurement_provenance: str
    transport_attestation: str
    sdk_max_retries: int
    retry_policy_id: str
    started_at_utc: str
    ended_at_utc: str = ''
    elapsed_s: Optional[float] = None
    outcome: str = 'pending'
    http_status: int | str = UNAVAILABLE
    api_outcome: str = 'pending'
    error_type: str = UNAVAILABLE
    usage: TokenUsage = field(default_factory=TokenUsage)
    response_model: str = UNAVAILABLE
    response_request_id: str = UNAVAILABLE

    def validate(self, *, complete: bool=True) -> None:
        for name in ('physical_attempt_id', 'logical_call_id', 'stage', 'model', 'endpoint_hostname', 'prompt_sha256', 'prompt_version', 'started_at_utc'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise CallLedgerError(f'physical attempt {name} must be non-empty')
        if self.stage not in STAGES:
            raise CallLedgerError(f'unknown stage: {self.stage!r}')
        _safe_code(self.physical_attempt_id, 'physical_attempt_id')
        _safe_code(self.logical_call_id, 'logical_call_id')
        _safe_code(self.prompt_version, 'prompt_version')
        if not _UTC_TIMESTAMP.fullmatch(self.started_at_utc):
            raise CallLedgerError('physical attempt started_at_utc is not a UTC timestamp')
        if endpoint_hostname(self.endpoint_hostname) != self.endpoint_hostname:
            raise CallLedgerError('physical attempt endpoint hostname is not sanitized')
        if _text_or_unavailable(self.model) != self.model:
            raise CallLedgerError('physical attempt model metadata is unsafe')
        if self.measurement_provenance not in MEASUREMENT_PROVENANCE:
            raise CallLedgerError(f'unknown measurement provenance: {self.measurement_provenance!r}')
        if self.transport_attestation not in TRANSPORT_ATTESTATIONS:
            raise CallLedgerError('physical attempt transport attestation is invalid')
        if self.sdk_max_retries != 0:
            raise CallLedgerError('physical attempt SDK retry value must be zero')
        if self.retry_policy_id not in RETRY_POLICY_IDS:
            raise CallLedgerError('physical attempt retry policy is invalid')
        expected_measurement = {'openai_sdk_max_retries_0': {'actual'}, 'local_replay_single_attempt': {'synthetic'}, 'test_only_injected_transport_non_evidence': {'synthetic', 'estimated_for_budget'}}[self.transport_attestation]
        if self.measurement_provenance not in expected_measurement:
            raise CallLedgerError('physical attempt transport/measurement provenance mismatch')
        if len(self.prompt_sha256) != 64 or any((c not in '0123456789abcdef' for c in self.prompt_sha256)):
            raise CallLedgerError('physical attempt prompt_sha256 must be lowercase SHA-256')
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise CallLedgerError('physical attempt ordinal must be a positive integer')
        _nonnegative_number(self.temperature, 'temperature')
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens < 1:
            raise CallLedgerError('max_tokens must be a positive integer')
        if isinstance(self.prompt_token_cap, bool) or not isinstance(self.prompt_token_cap, int) or self.prompt_token_cap < 1:
            raise CallLedgerError('prompt_token_cap must be a positive integer')
        unknown_offline_bound = self.prompt_token_upper_bound == UNAVAILABLE and self.measurement_provenance == 'synthetic' and (self.transport_attestation in {'local_replay_single_attempt', 'test_only_injected_transport_non_evidence'})
        if not unknown_offline_bound and (isinstance(self.prompt_token_upper_bound, bool) or not isinstance(self.prompt_token_upper_bound, int) or self.prompt_token_upper_bound < 0 or (self.prompt_token_upper_bound > self.prompt_token_cap)):
            raise CallLedgerError('prompt token upper bound exceeds its cap')
        if self.seed != UNAVAILABLE and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise CallLedgerError('seed must be an integer or unavailable')
        if self.outcome not in PHYSICAL_OUTCOMES:
            raise CallLedgerError(f'unknown physical attempt outcome: {self.outcome!r}')
        if complete and self.outcome == 'pending':
            raise CallLedgerError('physical attempt is still pending')
        if complete:
            if not self.ended_at_utc or self.elapsed_s is None:
                raise CallLedgerError('physical attempt lacks terminal timing')
            if not _UTC_TIMESTAMP.fullmatch(self.ended_at_utc):
                raise CallLedgerError('physical attempt ended_at_utc is not a UTC timestamp')
            _nonnegative_number(self.elapsed_s, 'physical attempt elapsed_s')
        if self.http_status != UNAVAILABLE and (isinstance(self.http_status, bool) or not isinstance(self.http_status, int) or (not 100 <= self.http_status <= 599)):
            raise CallLedgerError('http_status must be 100..599 or unavailable')
        if self.api_outcome != 'pending':
            _safe_code(self.api_outcome, 'api_outcome')
        if self.error_type != UNAVAILABLE:
            _safe_code(self.error_type, 'error_type')
        if self.outcome == 'success' and self.error_type != UNAVAILABLE:
            raise CallLedgerError('successful physical attempt cannot contain an error_type')
        if self.outcome.endswith('error') and self.error_type == UNAVAILABLE:
            raise CallLedgerError('failed physical attempt requires error_type')
        self.usage.validate()
        for name in ('response_model', 'response_request_id'):
            value = getattr(self, name)
            if value != UNAVAILABLE and _text_or_unavailable(value) != value:
                raise CallLedgerError(f'physical attempt {name} is not sanitized')

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload['usage'] = self.usage.to_dict()
        return payload

@dataclass
class LogicalCallRecord:
    logical_call_id: str
    ordinal: int
    fixture_id: str
    stage: str
    model: str
    endpoint_hostname: str
    prompt_sha256: str
    prompt_version: str
    temperature: float
    max_tokens: int
    prompt_token_cap: int
    prompt_token_upper_bound: int | str
    seed: int | str
    measurement_provenance: str
    transport_attestation: str
    sdk_max_retries: int
    retry_policy_id: str
    max_physical_attempts: int
    started_at_utc: str
    ended_at_utc: str = ''
    elapsed_s: Optional[float] = None
    outcome: str = 'pending'
    parse_outcome: str = 'pending'
    physical_attempt_ids: list[str] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    response_model: str = UNAVAILABLE
    response_request_id: str = UNAVAILABLE

    def validate(self, *, complete: bool=True, require_parse_outcome: bool=True) -> None:
        for name in ('logical_call_id', 'fixture_id', 'stage', 'model', 'endpoint_hostname', 'prompt_sha256', 'prompt_version', 'started_at_utc'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise CallLedgerError(f'logical call {name} must be non-empty')
        _safe_code(self.fixture_id, 'fixture_id')
        _safe_code(self.logical_call_id, 'logical_call_id')
        _safe_code(self.prompt_version, 'prompt_version')
        if not _UTC_TIMESTAMP.fullmatch(self.started_at_utc):
            raise CallLedgerError('logical call started_at_utc is not a UTC timestamp')
        if self.stage not in STAGES:
            raise CallLedgerError(f'unknown stage: {self.stage!r}')
        if endpoint_hostname(self.endpoint_hostname) != self.endpoint_hostname:
            raise CallLedgerError('logical call endpoint hostname is not sanitized')
        if _text_or_unavailable(self.model) != self.model:
            raise CallLedgerError('logical call model metadata is unsafe')
        if self.measurement_provenance not in MEASUREMENT_PROVENANCE:
            raise CallLedgerError(f'unknown measurement provenance: {self.measurement_provenance!r}')
        if self.transport_attestation not in TRANSPORT_ATTESTATIONS:
            raise CallLedgerError('logical call transport attestation is invalid')
        if self.sdk_max_retries != 0:
            raise CallLedgerError('logical call SDK retry value must be zero')
        if self.retry_policy_id not in RETRY_POLICY_IDS:
            raise CallLedgerError('logical call retry policy is invalid')
        expected_measurement = {'openai_sdk_max_retries_0': {'actual'}, 'local_replay_single_attempt': {'synthetic'}, 'test_only_injected_transport_non_evidence': {'synthetic', 'estimated_for_budget'}}[self.transport_attestation]
        if self.measurement_provenance not in expected_measurement:
            raise CallLedgerError('logical call transport/measurement provenance mismatch')
        if len(self.prompt_sha256) != 64 or any((c not in '0123456789abcdef' for c in self.prompt_sha256)):
            raise CallLedgerError('logical call prompt_sha256 must be lowercase SHA-256')
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise CallLedgerError('logical call ordinal must be a positive integer')
        if isinstance(self.max_physical_attempts, bool) or not isinstance(self.max_physical_attempts, int) or self.max_physical_attempts < 1:
            raise CallLedgerError('max_physical_attempts must be a positive integer')
        _nonnegative_number(self.temperature, 'temperature')
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens < 1:
            raise CallLedgerError('max_tokens must be a positive integer')
        if isinstance(self.prompt_token_cap, bool) or not isinstance(self.prompt_token_cap, int) or self.prompt_token_cap < 1:
            raise CallLedgerError('prompt_token_cap must be a positive integer')
        unknown_offline_bound = self.prompt_token_upper_bound == UNAVAILABLE and self.measurement_provenance == 'synthetic' and (self.transport_attestation in {'local_replay_single_attempt', 'test_only_injected_transport_non_evidence'})
        if not unknown_offline_bound and (isinstance(self.prompt_token_upper_bound, bool) or not isinstance(self.prompt_token_upper_bound, int) or self.prompt_token_upper_bound < 0 or (self.prompt_token_upper_bound > self.prompt_token_cap)):
            raise CallLedgerError('prompt token upper bound exceeds its cap')
        if self.seed != UNAVAILABLE and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise CallLedgerError('seed must be an integer or unavailable')
        if self.outcome not in LOGICAL_OUTCOMES:
            raise CallLedgerError(f'unknown logical outcome: {self.outcome!r}')
        if complete and self.outcome == 'pending':
            raise CallLedgerError('logical call is still pending')
        if complete:
            if not self.ended_at_utc or self.elapsed_s is None:
                raise CallLedgerError('logical call lacks terminal timing')
            if not _UTC_TIMESTAMP.fullmatch(self.ended_at_utc):
                raise CallLedgerError('logical call ended_at_utc is not a UTC timestamp')
            _nonnegative_number(self.elapsed_s, 'logical call elapsed_s')
        if not isinstance(self.physical_attempt_ids, list) or not self.physical_attempt_ids:
            raise CallLedgerError('logical call has no physical attempts')
        if len(self.physical_attempt_ids) != len(set(self.physical_attempt_ids)):
            raise CallLedgerError('logical call contains duplicate physical attempt IDs')
        if len(self.physical_attempt_ids) > self.max_physical_attempts:
            raise CallLedgerError('logical call exceeded its physical-attempt budget')
        if require_parse_outcome and self.parse_outcome == 'pending':
            raise CallLedgerError('logical call lacks parser outcome')
        if self.parse_outcome != 'pending' and self.parse_outcome not in PARSE_OUTCOMES:
            raise CallLedgerError(f'unknown parse_outcome: {self.parse_outcome!r}')
        if self.outcome == 'transport_failed' and self.parse_outcome != 'not_attempted_transport_error':
            raise CallLedgerError('failed transport must mark parse as not_attempted_transport_error')
        self.usage.validate()
        for name in ('response_model', 'response_request_id'):
            value = getattr(self, name)
            if value != UNAVAILABLE and _text_or_unavailable(value) != value:
                raise CallLedgerError(f'logical call {name} is not sanitized')

    def to_dict(self, *, require_parse_outcome: bool=True) -> Dict[str, Any]:
        self.validate(require_parse_outcome=require_parse_outcome)
        payload = asdict(self)
        payload['usage'] = self.usage.to_dict()
        return payload

class RunCallBudget:
    """Thread-safe run-wide reservation shared by every stage/client ledger."""

    def __init__(self, *, max_logical_calls: int, max_physical_attempts: int, max_total_tokens: int, progress_guard: Any=None) -> None:
        for name, value in (('max_logical_calls', max_logical_calls), ('max_physical_attempts', max_physical_attempts), ('max_total_tokens', max_total_tokens)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'{name} must be a non-negative integer')
        self.max_logical_calls = max_logical_calls
        self.max_physical_attempts = max_physical_attempts
        self.max_total_tokens = max_total_tokens
        self.logical_calls_reserved = 0
        self.physical_attempt_capacity_reserved = 0
        self.token_capacity_reserved = 0
        self.physical_attempts_started = 0
        self.observed_tokens = 0
        if progress_guard is not None and any((not callable(getattr(progress_guard, method_name, None)) for method_name in ('bind_run_budget', 'before_logical_call', 'before_physical_attempt'))):
            raise TypeError('progress_guard must expose bind_run_budget(), before_logical_call(), and before_physical_attempt()')
        self.progress_guard = progress_guard
        self._lock = Lock()
        if self.progress_guard is not None:
            self.progress_guard.bind_run_budget(self, self._summary_unlocked())

    def _summary_unlocked(self) -> Dict[str, int]:
        return {'max_logical_calls': self.max_logical_calls, 'max_physical_attempts': self.max_physical_attempts, 'max_total_tokens': self.max_total_tokens, 'logical_calls_reserved': self.logical_calls_reserved, 'physical_attempt_capacity_reserved': self.physical_attempt_capacity_reserved, 'token_capacity_reserved': self.token_capacity_reserved, 'physical_attempts_started': self.physical_attempts_started, 'observed_tokens': self.observed_tokens}

    def reserve_logical(self, *, max_physical_attempts: int, token_ceiling: int) -> None:
        with self._lock:
            if self.progress_guard is not None:
                self.progress_guard.before_logical_call(self, self._summary_unlocked())
            if self.logical_calls_reserved + 1 > self.max_logical_calls:
                raise CallLedgerError('run-wide logical-call budget exhausted')
            if self.physical_attempt_capacity_reserved + max_physical_attempts > self.max_physical_attempts:
                raise CallLedgerError('run-wide physical-attempt budget cannot reserve call')
            if self.token_capacity_reserved + token_ceiling > self.max_total_tokens:
                raise CallLedgerError('run-wide token budget cannot reserve call')
            self.logical_calls_reserved += 1
            self.physical_attempt_capacity_reserved += max_physical_attempts
            self.token_capacity_reserved += token_ceiling

    def record_physical_attempt(self) -> None:
        with self._lock:
            if self.progress_guard is not None:
                self.progress_guard.before_physical_attempt(self, self._summary_unlocked())
            if self.physical_attempts_started + 1 > self.max_physical_attempts:
                raise CallLedgerError('run-wide physical-attempt budget exhausted')
            self.physical_attempts_started += 1

    def record_usage(self, usage: TokenUsage) -> None:
        value = usage.total_tokens
        if not isinstance(value, int):
            return
        with self._lock:
            if self.observed_tokens + value > self.max_total_tokens:
                raise CallLedgerError('run-wide observed token budget exceeded')
            self.observed_tokens += value

    def summary(self) -> Dict[str, int]:
        with self._lock:
            return self._summary_unlocked()

class CallLedger:
    """Mutable collector with a strict, fail-closed serialization boundary."""
    schema_version = 'w3_llm_call_ledger_v1'

    def __init__(self, *, expected_logical_calls: Optional[int]=None, max_logical_calls_total: Optional[int]=None, max_physical_attempts_total: Optional[int]=None, max_total_tokens: Optional[int]=None, run_budget: Optional[RunCallBudget]=None) -> None:
        if expected_logical_calls is not None and (isinstance(expected_logical_calls, bool) or not isinstance(expected_logical_calls, int) or expected_logical_calls < 0):
            raise ValueError('expected_logical_calls must be a non-negative integer or None')
        if max_physical_attempts_total is not None and (isinstance(max_physical_attempts_total, bool) or not isinstance(max_physical_attempts_total, int) or max_physical_attempts_total < 0):
            raise ValueError('max_physical_attempts_total must be a non-negative integer or None')
        if max_logical_calls_total is not None and (isinstance(max_logical_calls_total, bool) or not isinstance(max_logical_calls_total, int) or max_logical_calls_total < 0):
            raise ValueError('max_logical_calls_total must be a non-negative integer or None')
        if max_total_tokens is not None and (isinstance(max_total_tokens, bool) or not isinstance(max_total_tokens, int) or max_total_tokens < 0):
            raise ValueError('max_total_tokens must be a non-negative integer or None')
        self.expected_logical_calls = expected_logical_calls
        self.max_logical_calls_total = max_logical_calls_total
        self.max_physical_attempts_total = max_physical_attempts_total
        self.max_total_tokens = max_total_tokens
        if run_budget is not None and (not isinstance(run_budget, RunCallBudget)):
            raise TypeError('run_budget must be RunCallBudget')
        self.run_budget = run_budget
        self.logical_calls: list[LogicalCallRecord] = []
        self.physical_attempts: list[PhysicalAttemptRecord] = []
        self._logical_by_id: dict[str, LogicalCallRecord] = {}
        self._physical_by_id: dict[str, PhysicalAttemptRecord] = {}

    @property
    def last_logical_call_id(self) -> Optional[str]:
        return self.logical_calls[-1].logical_call_id if self.logical_calls else None

    def begin_logical_call(self, *, fixture_id: str, stage: str, model: str, endpoint: str, system_prompt: str, user_prompt: str, prompt_version: str | int, temperature: float, max_tokens: int, prompt_token_cap: int, prompt_token_upper_bound: int | str, seed: Optional[int], max_physical_attempts: int, measurement_provenance: str='actual', transport_attestation: str='openai_sdk_max_retries_0', sdk_max_retries: int=0, retry_policy_id: str='bounded_transport_v1_408_409_429_5xx') -> LogicalCallRecord:
        if stage not in STAGES:
            raise CallLedgerError(f'unknown stage: {stage!r}')
        if measurement_provenance not in MEASUREMENT_PROVENANCE:
            raise CallLedgerError(f'unknown measurement provenance: {measurement_provenance!r}')
        _nonnegative_number(temperature, 'temperature')
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise CallLedgerError('max_tokens must be a positive integer')
        if isinstance(max_physical_attempts, bool) or not isinstance(max_physical_attempts, int) or max_physical_attempts < 1:
            raise CallLedgerError('max_physical_attempts must be a positive integer')
        if self.expected_logical_calls is not None and len(self.logical_calls) >= self.expected_logical_calls:
            raise CallLedgerError('logical-call denominator exhausted')
        if self.max_logical_calls_total is not None and len(self.logical_calls) >= self.max_logical_calls_total:
            raise CallLedgerError('run logical-call budget exhausted')
        if self.max_physical_attempts_total is not None and len(self.physical_attempts) + max_physical_attempts > self.max_physical_attempts_total:
            raise CallLedgerError('run physical-attempt budget cannot reserve this logical call')
        declared_token_ceiling = max_physical_attempts * (prompt_token_cap + max_tokens)
        prior_declared_tokens = sum((item.max_physical_attempts * (item.prompt_token_cap + item.max_tokens) for item in self.logical_calls))
        if self.max_total_tokens is not None and prior_declared_tokens + declared_token_ceiling > self.max_total_tokens:
            raise CallLedgerError('run token budget cannot reserve this logical call')
        ordinal = len(self.logical_calls) + 1
        record = LogicalCallRecord(logical_call_id=f'logical-{ordinal}-{uuid4().hex}', ordinal=ordinal, fixture_id=_safe_code(fixture_id, 'fixture_id'), stage=stage, model=_text_or_unavailable(model), endpoint_hostname=endpoint_hostname(endpoint), prompt_sha256=prompt_sha256(system_prompt, user_prompt), prompt_version=_text_or_unavailable(prompt_version), temperature=float(temperature), max_tokens=max_tokens, prompt_token_cap=prompt_token_cap, prompt_token_upper_bound=prompt_token_upper_bound, seed=seed if seed is not None else UNAVAILABLE, measurement_provenance=measurement_provenance, transport_attestation=transport_attestation, sdk_max_retries=sdk_max_retries, retry_policy_id=retry_policy_id, max_physical_attempts=max_physical_attempts, started_at_utc=utc_now())
        if self.run_budget is not None:
            self.run_budget.reserve_logical(max_physical_attempts=max_physical_attempts, token_ceiling=declared_token_ceiling)
        if record.logical_call_id in self._logical_by_id:
            raise CallLedgerError('duplicate logical_call_id')
        self.logical_calls.append(record)
        self._logical_by_id[record.logical_call_id] = record
        return record

    def begin_physical_attempt(self, logical_call_id: str) -> PhysicalAttemptRecord:
        logical = self._require_logical(logical_call_id)
        if logical.outcome != 'pending':
            raise CallLedgerError('cannot add an attempt to a terminal logical call')
        ordinal = len(logical.physical_attempt_ids) + 1
        if ordinal > logical.max_physical_attempts:
            raise CallLedgerError('physical-attempt budget exhausted')
        if self.max_physical_attempts_total is not None and len(self.physical_attempts) >= self.max_physical_attempts_total:
            raise CallLedgerError('run physical-attempt budget exhausted')
        if self.run_budget is not None:
            self.run_budget.record_physical_attempt()
        attempt = PhysicalAttemptRecord(physical_attempt_id=f'physical-{len(self.physical_attempts) + 1}-{uuid4().hex}', logical_call_id=logical.logical_call_id, ordinal=ordinal, stage=logical.stage, model=logical.model, endpoint_hostname=logical.endpoint_hostname, prompt_sha256=logical.prompt_sha256, prompt_version=logical.prompt_version, temperature=logical.temperature, max_tokens=logical.max_tokens, prompt_token_cap=logical.prompt_token_cap, prompt_token_upper_bound=logical.prompt_token_upper_bound, seed=logical.seed, measurement_provenance=logical.measurement_provenance, transport_attestation=logical.transport_attestation, sdk_max_retries=logical.sdk_max_retries, retry_policy_id=logical.retry_policy_id, started_at_utc=utc_now())
        if attempt.physical_attempt_id in self._physical_by_id:
            raise CallLedgerError('duplicate physical_attempt_id')
        logical.physical_attempt_ids.append(attempt.physical_attempt_id)
        self.physical_attempts.append(attempt)
        self._physical_by_id[attempt.physical_attempt_id] = attempt
        return attempt

    def finish_physical_attempt(self, physical_attempt_id: str, *, elapsed_s: float, outcome: str, http_status: int | str=UNAVAILABLE, api_outcome: str, error_type: str=UNAVAILABLE, usage: Optional[TokenUsage]=None, response_model: Any=None, response_request_id: Any=None) -> None:
        attempt = self._require_physical(physical_attempt_id)
        if attempt.outcome != 'pending':
            raise CallLedgerError('physical attempt already finalized')
        attempt.ended_at_utc = utc_now()
        attempt.elapsed_s = _nonnegative_number(elapsed_s, 'physical attempt elapsed_s')
        attempt.outcome = outcome
        attempt.http_status = http_status
        attempt.api_outcome = _safe_code(api_outcome, 'api_outcome')
        attempt.error_type = UNAVAILABLE if error_type == UNAVAILABLE else _safe_code(error_type, 'error_type')
        attempt.usage = usage or TokenUsage()
        attempt.response_model = _text_or_unavailable(response_model)
        attempt.response_request_id = _text_or_unavailable(response_request_id)
        attempt.validate()
        if self.run_budget is not None:
            self.run_budget.record_usage(attempt.usage)

    def finish_logical_success(self, logical_call_id: str, physical_attempt_id: str, *, elapsed_s: float) -> None:
        logical = self._require_logical(logical_call_id)
        attempt = self._require_physical(physical_attempt_id)
        if logical.outcome != 'pending' or attempt.outcome != 'success':
            raise CallLedgerError('logical success requires one finalized successful attempt')
        if attempt.logical_call_id != logical.logical_call_id:
            raise CallLedgerError('successful attempt belongs to another logical call')
        logical.ended_at_utc = utc_now()
        logical.elapsed_s = _nonnegative_number(elapsed_s, 'logical call elapsed_s')
        logical.outcome = 'transport_succeeded'
        logical.usage = attempt.usage
        logical.response_model = attempt.response_model
        logical.response_request_id = attempt.response_request_id

    def finish_logical_failure(self, logical_call_id: str, *, elapsed_s: float) -> None:
        logical = self._require_logical(logical_call_id)
        if logical.outcome != 'pending':
            raise CallLedgerError('logical call already finalized')
        logical.ended_at_utc = utc_now()
        logical.elapsed_s = _nonnegative_number(elapsed_s, 'logical call elapsed_s')
        logical.outcome = 'transport_failed'
        logical.parse_outcome = 'not_attempted_transport_error'

    def record_parse_outcome(self, logical_call_id: str, outcome: str) -> None:
        logical = self._require_logical(logical_call_id)
        if logical.outcome != 'transport_succeeded':
            raise CallLedgerError('parser outcome requires a transport-succeeded logical call')
        if logical.parse_outcome != 'pending':
            raise CallLedgerError('parser outcome already recorded')
        if outcome not in PARSE_OUTCOMES.difference({'not_attempted_transport_error'}):
            raise CallLedgerError(f'unknown parser outcome: {outcome!r}')
        logical.parse_outcome = outcome

    def validate(self) -> None:
        if self.expected_logical_calls is not None and len(self.logical_calls) != self.expected_logical_calls:
            raise CallLedgerError(f'logical-call denominator mismatch: expected={self.expected_logical_calls}, observed={len(self.logical_calls)}')
        if self.max_physical_attempts_total is not None and len(self.physical_attempts) > self.max_physical_attempts_total:
            raise CallLedgerError('run physical-attempt budget exceeded')
        if not self.logical_calls:
            if self.expected_logical_calls == 0 and (not self.physical_attempts):
                return
            raise CallLedgerError('complete call ledger requires at least one logical call')
        if len(self._logical_by_id) != len(self.logical_calls):
            raise CallLedgerError('logical-call index is inconsistent')
        if len(self._physical_by_id) != len(self.physical_attempts):
            raise CallLedgerError('physical-attempt index is inconsistent')
        if [item.ordinal for item in self.logical_calls] != list(range(1, len(self.logical_calls) + 1)):
            raise CallLedgerError('logical-call ordinals are not contiguous')
        referenced: list[str] = []
        for logical in self.logical_calls:
            logical.validate(require_parse_outcome=True)
            attempts = [self._require_physical(value) for value in logical.physical_attempt_ids]
            referenced.extend(logical.physical_attempt_ids)
            if [item.ordinal for item in attempts] != list(range(1, len(attempts) + 1)):
                raise CallLedgerError('physical-attempt ordinals are not contiguous within logical call')
            if any((item.logical_call_id != logical.logical_call_id for item in attempts)):
                raise CallLedgerError('physical attempt is linked to the wrong logical call')
            for attempt in attempts:
                attempt.validate()
                if attempt.outcome == 'success':
                    required_usage = {'actual': 'actual', 'synthetic': 'synthetic', 'estimated_for_budget': 'estimated_for_budget'}[attempt.measurement_provenance]
                    if attempt.usage.provenance != required_usage:
                        raise CallLedgerError(f'successful physical attempt lacks complete usage matching its measurement provenance: expected {required_usage!r}')
                    if not isinstance(attempt.usage.prompt_tokens, int) or attempt.usage.prompt_tokens > attempt.prompt_token_cap:
                        raise CallLedgerError('observed prompt tokens exceeded the declared cap')
                    if not isinstance(attempt.usage.completion_tokens, int) or attempt.usage.completion_tokens > attempt.max_tokens:
                        raise CallLedgerError('observed completion tokens exceeded the declared cap')
                for name in ('stage', 'model', 'endpoint_hostname', 'prompt_sha256', 'prompt_version', 'temperature', 'max_tokens', 'prompt_token_cap', 'prompt_token_upper_bound', 'seed', 'measurement_provenance', 'transport_attestation', 'sdk_max_retries', 'retry_policy_id'):
                    if getattr(attempt, name) != getattr(logical, name):
                        raise CallLedgerError(f'attempt/logical metadata mismatch: {name}')
            successes = [item for item in attempts if item.outcome == 'success']
            if logical.outcome == 'transport_succeeded':
                if len(successes) != 1 or successes[0] is not attempts[-1]:
                    raise CallLedgerError('successful logical call needs one final successful attempt')
                if any((item.outcome != 'retryable_error' for item in attempts[:-1])):
                    raise CallLedgerError('only retryable errors may precede a successful retry')
                success = successes[0]
                if logical.usage != success.usage or logical.response_model != success.response_model or logical.response_request_id != success.response_request_id:
                    raise CallLedgerError('logical response metadata differs from successful attempt')
            elif successes:
                raise CallLedgerError('failed logical call cannot contain a successful attempt')
            elif attempts[-1].outcome == 'fatal_error':
                if any((item.outcome != 'retryable_error' for item in attempts[:-1])):
                    raise CallLedgerError('only retryable errors may precede a fatal terminal attempt')
            elif all((item.outcome == 'retryable_error' for item in attempts)):
                if len(attempts) != logical.max_physical_attempts:
                    raise CallLedgerError('retryable-only failure must exhaust the physical-attempt budget')
            else:
                raise CallLedgerError('failed logical call has an invalid attempt sequence')
            physical_elapsed = sum((float(item.elapsed_s or 0.0) for item in attempts))
            if float(logical.elapsed_s or 0.0) + 1e-09 < physical_elapsed:
                raise CallLedgerError('logical elapsed time is smaller than its physical-attempt total')
        physical_ids = [item.physical_attempt_id for item in self.physical_attempts]
        if len(physical_ids) != len(set(physical_ids)):
            raise CallLedgerError('duplicate physical attempt IDs')
        if set(referenced) != set(physical_ids) or len(referenced) != len(physical_ids):
            raise CallLedgerError('physical attempts cannot be explained by logical calls')
        logical_ids = [item.logical_call_id for item in self.logical_calls]
        if len(logical_ids) != len(set(logical_ids)):
            raise CallLedgerError('duplicate logical call IDs')
        if set(logical_ids).intersection(physical_ids):
            raise CallLedgerError('logical and physical IDs must be distinct')
        actual_total_tokens = sum((int(item.usage.total_tokens) for item in self.physical_attempts if isinstance(item.usage.total_tokens, int)))
        if self.max_total_tokens is not None and actual_total_tokens > self.max_total_tokens:
            raise CallLedgerError('observed token use exceeded the run token budget')

    def summary(self) -> Dict[str, Any]:
        self.validate()
        totals: Dict[str, Dict[str, int]] = {'actual': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'synthetic': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'estimated_for_budget': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
        usage_observations = {name: 0 for name in totals}
        unavailable_attempts = 0
        for attempt in self.physical_attempts:
            if attempt.usage.provenance == UNAVAILABLE:
                unavailable_attempts += 1
                continue
            usage_observations[attempt.usage.provenance] += 1
            target = totals[attempt.usage.provenance]
            for name in target:
                value = getattr(attempt.usage, name)
                if isinstance(value, int):
                    target[name] += value
        return {'schema_version': self.schema_version, 'status': 'complete', 'expected_logical_calls': self.expected_logical_calls, 'max_physical_attempts_total': self.max_physical_attempts_total, 'n_logical_calls': len(self.logical_calls), 'n_physical_attempts': len(self.physical_attempts), 'max_physical_attempts_declared': sum((item.max_physical_attempts for item in self.logical_calls)), 'max_logical_calls_total': self.max_logical_calls_total, 'max_physical_attempts_total': self.max_physical_attempts_total, 'max_total_tokens': self.max_total_tokens, 'declared_token_ceiling': sum((item.max_physical_attempts * (item.prompt_token_cap + item.max_tokens) for item in self.logical_calls)), 'run_budget': self.run_budget.summary() if self.run_budget is not None else None, 'token_totals_by_provenance': {name: value if usage_observations[name] else UNAVAILABLE for name, value in totals.items()}, 'usage_observations_by_provenance': usage_observations, 'n_attempts_with_unavailable_usage': unavailable_attempts, 'logical_calls': [item.to_dict(require_parse_outcome=True) for item in self.logical_calls], 'physical_attempts': [item.to_dict() for item in self.physical_attempts]}

    def _require_logical(self, logical_call_id: str) -> LogicalCallRecord:
        try:
            return self._logical_by_id[logical_call_id]
        except KeyError as exc:
            raise CallLedgerError(f'unknown logical_call_id: {logical_call_id}') from exc

    def _require_physical(self, physical_attempt_id: str) -> PhysicalAttemptRecord:
        try:
            return self._physical_by_id[physical_attempt_id]
        except KeyError as exc:
            raise CallLedgerError(f'unknown physical_attempt_id: {physical_attempt_id}') from exc

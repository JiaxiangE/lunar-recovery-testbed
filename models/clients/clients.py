"""LLM clients (Build Spec §6).

`BaseLLMClient` = OpenAI-compatible chat client for the base model (Qwen-Max-class) reached
via the gateway configured in .env (N1N_API_KEY / N1N_BASE_URL). temp=0 for reproducibility
(D-041). `MockBaseLLMClient` returns canned responses (sequence or callable) for offline
tests. Both expose `generate(system_prompt, user_prompt, ...) -> str`.
"""
from __future__ import annotations
import os
import time
import math
import re
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Union
from models.clients.call_ledger import CallLedger
from models.clients.call_ledger import CallLedgerError
from models.clients.call_ledger import TokenUsage
from models.clients.call_ledger import UNAVAILABLE
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_MESSAGE_OVERHEAD_BYTES = 256
RETRY_POLICY_ID = 'bounded_transport_v1_408_409_429_5xx'

def load_dotenv(path: Optional[Path]=None) -> Dict[str, str]:
    """Parse repo `.env` (KEY=VALUE lines). The `.env` file is AUTHORITATIVE: it OVERRIDES
    any pre-existing os.environ value (a stale OS-level DASHSCOPE_API_KEY otherwise shadows
    the .env key and causes a 401). Returns the parsed dict."""
    env_path = path or _REPO_ROOT / '.env'
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = (k.strip(), v.strip().strip('\'"'))
        out[k] = v
        os.environ[k] = v
    return out

def get_base_config() -> Dict[str, str]:
    """Resolve base-LLM api_key / base_url / model from .env + environment.

    Prefers DashScope (direct, OpenAI-compatible) — the n1n gateway was dropped after its
    tokens 401'd (2026-05-31). qwen-max is live on DashScope.
    """
    load_dotenv()
    api_key = os.environ.get('DASHSCOPE_API_KEY') or os.environ.get('OPENAI_API_KEY') or os.environ.get('N1N_API_KEY') or ''
    base_url = os.environ.get('DASHSCOPE_BASE_URL') or os.environ.get('OPENAI_BASE_URL') or os.environ.get('N1N_BASE_URL') or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    model = os.environ.get('BASE_LLM_MODEL', 'qwen3-max')
    return {'api_key': api_key, 'base_url': base_url, 'model': model}

class BaseLLMClient:
    """OpenAI-compatible chat client for the base model. Lazy-imports `openai`."""

    def __init__(self, model: Optional[str]=None, api_key: Optional[str]=None, base_url: Optional[str]=None, temperature: float=0.0, max_tokens: int=2048, timeout_s: float=120.0, max_retries: int=4, seed: Optional[int]=None):
        needs_config = model is None or api_key is None or base_url is None
        cfg = get_base_config() if needs_config else {}
        self.model = model if model is not None else cfg['model']
        self.api_key = api_key if api_key is not None else cfg['api_key']
        self.base_url = base_url if base_url is not None else cfg['base_url']
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.seed = seed
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI
            if not self.api_key:
                raise RuntimeError('No base-LLM API key found (set DASHSCOPE_API_KEY in .env)')
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_s, max_retries=self.max_retries)
        return self._client

    def generate(self, system_prompt: str, user_prompt: str, temperature: Optional[float]=None, max_tokens: Optional[int]=None) -> str:
        client = self._ensure_client()
        kwargs: Dict[str, Any] = dict(model=self.model, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}], temperature=self.temperature if temperature is None else temperature, max_tokens=self.max_tokens if max_tokens is None else max_tokens)
        if self.seed is not None:
            kwargs['seed'] = self.seed
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ''

def _response_field(value: Any, field_name: str, default: Any=None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name, default)
    return getattr(value, field_name, default)

def _response_text(response: Any) -> str:
    choices = _response_field(response, 'choices', ()) or ()
    if not choices:
        return ''
    first = choices[0]
    message = _response_field(first, 'message', {}) or {}
    content = _response_field(message, 'content', '')
    return str(content or '')

def _response_usage(response: Any) -> TokenUsage:
    usage = _response_field(response, 'usage')
    values: Dict[str, int | str] = {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        raw = _response_field(usage, name) if usage is not None else None
        values[name] = raw if isinstance(raw, int) and (not isinstance(raw, bool)) and (raw >= 0) else UNAVAILABLE
    if not all((isinstance(value, int) for value in values.values())):
        return TokenUsage()
    declared = _response_field(response, 'usage_provenance')
    if declared in {'actual', 'synthetic', 'estimated_for_budget'}:
        provenance = str(declared)
    elif any((isinstance(value, int) for value in values.values())):
        provenance = 'actual'
    else:
        provenance = UNAVAILABLE
    reasoning: int | str = UNAVAILABLE
    if usage is not None:
        details = _response_field(usage, 'completion_tokens_details')
        raw_reasoning = _response_field(details, 'reasoning_tokens') if details is not None else None
        if raw_reasoning is None:
            raw_reasoning = _response_field(usage, 'reasoning_tokens')
        if isinstance(raw_reasoning, int) and (not isinstance(raw_reasoning, bool)) and (raw_reasoning >= 0):
            reasoning = raw_reasoning
    return TokenUsage(provenance=provenance, reasoning_tokens=reasoning, **values)

def _retryable_transport_error(exc: BaseException) -> bool:
    status = getattr(exc, 'status_code', None)
    if isinstance(status, int) and (status in {408, 409, 429} or status >= 500):
        return True
    if isinstance(status, int):
        return False
    declared = getattr(exc, 'retryable', None)
    if isinstance(declared, bool):
        return declared
    name = type(exc).__name__.lower()
    return any((token in name for token in ('timeout', 'connection', 'ratelimit', 'internalserver')))

def _error_type_code(exc: BaseException) -> str:
    status = getattr(exc, 'status_code', None)
    if status == 429:
        return 'RateLimitError'
    if status == 408:
        return 'TimeoutError'
    if isinstance(status, int) and status >= 500:
        return 'ServerError'
    fallback = type(exc).__name__
    return fallback if re.fullmatch('[A-Za-z0-9_.:-]{1,96}', fallback) else 'TransportError'

class InstrumentedBaseLLMClient(BaseLLMClient):
    """Formal OpenAI-compatible client with fully observable explicit retries.

    The underlying OpenAI SDK retry budget is always zero.  ``max_transport_retries``
    is the sole retry controller and every physical attempt is written to
    ``call_ledger``.  ``generate`` intentionally retains the legacy string return.
    """

    def __init__(self, model: Optional[str]=None, api_key: Optional[str]=None, base_url: Optional[str]=None, temperature: float=0.0, max_tokens: int=2048, prompt_token_cap: int=8192, timeout_s: float=120.0, max_retries: int=0, seed: Optional[int]=None, *, call_ledger: Optional[CallLedger]=None, stage: str='generation', prompt_version: str | int='unversioned', fixture_id: str='runtime', max_transport_retries: int=0, transport: Any=None, retry_predicate: Optional[Callable[[BaseException], bool]]=None, extra_body: Optional[Dict[str, Any]]=None, _allow_test_transport: bool=False, request_metering: Any=None, total_completion_token_cap: Optional[int]=None, completion_token_overrun_allowance: int=0) -> None:
        if max_retries != 0:
            raise ValueError('InstrumentedBaseLLMClient fixes OpenAI SDK max_retries=0; use max_transport_retries for observable retries')
        if isinstance(max_transport_retries, bool) or not isinstance(max_transport_retries, int) or max_transport_retries < 0:
            raise ValueError('max_transport_retries must be a non-negative integer')
        if isinstance(prompt_token_cap, bool) or not isinstance(prompt_token_cap, int) or prompt_token_cap < 1:
            raise ValueError('prompt_token_cap must be a positive integer')
        if retry_predicate is not None and (not _allow_test_transport):
            raise ValueError('custom retry predicates are test-only and non-evidence')
        super().__init__(model=model, api_key=api_key, base_url=base_url, temperature=temperature, max_tokens=max_tokens, timeout_s=timeout_s, max_retries=0, seed=seed)
        self.call_ledger = call_ledger or CallLedger()
        self.prompt_token_cap = prompt_token_cap
        self.request_metering = request_metering
        if total_completion_token_cap is not None:
            if type(total_completion_token_cap) is not int or total_completion_token_cap < 1:
                raise ValueError('total_completion_token_cap must be a positive integer')
            if request_metering is None:
                raise ValueError('total completion accounting requires full-request metering')
        if type(completion_token_overrun_allowance) is not int or completion_token_overrun_allowance < 0:
            raise ValueError('completion_token_overrun_allowance must be a non-negative integer')
        if total_completion_token_cap is None and completion_token_overrun_allowance:
            raise ValueError('completion overrun allowance requires total_completion_token_cap')
        self.total_completion_token_cap = total_completion_token_cap
        self.completion_token_overrun_allowance = completion_token_overrun_allowance
        self.last_request_measurement = None
        self.request_measurements = {}
        self.stage = stage
        self.prompt_version = prompt_version
        self.fixture_id = fixture_id
        self.max_transport_retries = max_transport_retries
        self.retry_predicate = retry_predicate or _retryable_transport_error
        if extra_body is not None and (not isinstance(extra_body, dict)):
            raise ValueError('extra_body must be a dict of provider request fields')
        self.extra_body = dict(extra_body) if extra_body else None
        if total_completion_token_cap is not None and any((key in (self.extra_body or {}) for key in ('max_tokens', 'max_completion_tokens'))):
            raise ValueError('completion limits must not be overridden through extra_body')
        self.retry_policy_id = RETRY_POLICY_ID
        self.last_logical_call_id: Optional[str] = None
        self.sdk_max_retries = 0
        if transport is not None:
            from models.clients.replay_transport import ReplayTransport
            is_replay = isinstance(transport, ReplayTransport)
            if not is_replay and (not _allow_test_transport):
                raise ValueError('only ReplayTransport may be injected in evidence mode; arbitrary duck-typed transports can hide retries and are forbidden')
            if not is_replay and getattr(transport, 'single_physical_attempt_per_create', False) is not True:
                raise ValueError('test-only transport lacks single-attempt test contract')
            self.transport_attestation = 'local_replay_single_attempt' if is_replay else 'test_only_injected_transport_non_evidence'
            self._measurement_provenance = 'synthetic'
            self._client = transport
        else:
            self.transport_attestation = 'openai_sdk_max_retries_0'
            self._measurement_provenance = 'actual'

    def _ensure_client(self):
        if self.max_retries != 0 or self.sdk_max_retries != 0:
            raise CallLedgerError('instrumented client SDK retry invariant was modified')
        client = super()._ensure_client()
        if self.transport_attestation == 'openai_sdk_max_retries_0':
            observed = getattr(client, 'max_retries', None)
            if observed != 0:
                raise CallLedgerError('constructed OpenAI-compatible client does not attest max_retries=0')
        return client

    def generate(self, system_prompt: str, user_prompt: str, temperature: Optional[float]=None, max_tokens: Optional[int]=None, *, fixture_id: Optional[str]=None, stage: Optional[str]=None, prompt_version: Optional[str | int]=None, tools: Any=None, response_format: Any=None) -> str:
        effective_temperature = self.temperature if temperature is None else temperature
        effective_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        if self.total_completion_token_cap is not None:
            if max_tokens is not None:
                raise ValueError('max_tokens override conflicts with total_completion_token_cap')
            effective_max_tokens = self.total_completion_token_cap + self.completion_token_overrun_allowance
        if isinstance(effective_temperature, bool) or not isinstance(effective_temperature, (int, float)) or (not math.isfinite(effective_temperature)) or (effective_temperature < 0):
            raise ValueError('instrumented temperature must be a non-negative finite number')
        if isinstance(effective_max_tokens, bool) or not isinstance(effective_max_tokens, int) or effective_max_tokens < 1:
            raise ValueError('instrumented max_tokens must be a positive integer')
        prompt_token_upper_bound = len(system_prompt.encode('utf-8')) + len(user_prompt.encode('utf-8')) + _PROMPT_MESSAGE_OVERHEAD_BYTES
        if self.request_metering is None and prompt_token_upper_bound > self.prompt_token_cap:
            raise CallLedgerError('conservative UTF-8 prompt-token upper bound exceeds prompt_token_cap')
        if self.request_metering is None and (tools is not None or response_format is not None):
            raise CallLedgerError('schema/tools requests require explicit full-request metering')
        kwargs: Dict[str, Any] = {'model': self.model, 'messages': [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}], 'temperature': float(effective_temperature), 'max_tokens': effective_max_tokens}
        if self.seed is not None:
            kwargs['seed'] = self.seed
        if self.total_completion_token_cap is not None:
            del kwargs['max_tokens']
            kwargs['max_completion_tokens'] = self.total_completion_token_cap
        if self.extra_body:
            kwargs['extra_body'] = dict(self.extra_body)
        if tools is not None:
            kwargs['tools'] = tools
        if response_format is not None:
            kwargs['response_format'] = response_format
        if self.request_metering is not None:
            measurement = self.request_metering.measure(kwargs, prompt_cap=self.prompt_token_cap, completion_cap=effective_max_tokens)
            measurement['completion_accounting'] = {'wire_parameter': 'max_completion_tokens' if self.total_completion_token_cap is not None else 'max_tokens', 'wire_limit': self.total_completion_token_cap if self.total_completion_token_cap is not None else effective_max_tokens, 'overrun_allowance': self.completion_token_overrun_allowance, 'ledger_reserved_completion_tokens': effective_max_tokens, 'includes_reasoning': self.total_completion_token_cap is not None}
            self.last_request_measurement = measurement
            if not measurement['bytes_fit']:
                raise CallLedgerError('full request plus feedback exceeds bytes hard cap')
            offline = self.transport_attestation in {'local_replay_single_attempt', 'test_only_injected_transport_non_evidence'}
            if measurement['provider_fits'] is False or (measurement['provider_fits'] is not True and (not offline)):
                raise CallLedgerError('provider token/template fit is unknown or exceeds limits; real transport is blocked')
            upper = measurement['provider_input_tokens_upper_bound']
            if upper is not None and upper > self.prompt_token_cap:
                raise CallLedgerError('measured provider prompt upper bound exceeds cap')
            prompt_token_upper_bound = UNAVAILABLE if upper is None else upper
        client = self._ensure_client()
        logical_started = time.perf_counter()
        logical = self.call_ledger.begin_logical_call(fixture_id=fixture_id or self.fixture_id, stage=stage or self.stage, model=self.model, endpoint=self.base_url, system_prompt=system_prompt, user_prompt=user_prompt, prompt_version=self.prompt_version if prompt_version is None else prompt_version, temperature=float(effective_temperature), max_tokens=effective_max_tokens, prompt_token_cap=self.prompt_token_cap, prompt_token_upper_bound=prompt_token_upper_bound, seed=self.seed, max_physical_attempts=self.max_transport_retries + 1, measurement_provenance=self._measurement_provenance, transport_attestation=self.transport_attestation, sdk_max_retries=self.sdk_max_retries, retry_policy_id=self.retry_policy_id)
        self.last_logical_call_id = logical.logical_call_id
        if self.last_request_measurement is not None:
            self.request_measurements[logical.logical_call_id] = self.last_request_measurement
        last_error: Optional[BaseException] = None
        for physical_ordinal in range(1, self.max_transport_retries + 2):
            attempt = self.call_ledger.begin_physical_attempt(logical.logical_call_id)
            attempt_started = time.perf_counter()
            try:
                setter = getattr(client, 'set_replay_context', None)
                if callable(setter):
                    setter(fixture_id=logical.fixture_id, logical_ordinal=logical.ordinal, physical_ordinal=physical_ordinal)
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:
                elapsed = time.perf_counter() - attempt_started
                retryable = bool(self.retry_predicate(exc))
                error_type = _error_type_code(exc)
                status = getattr(exc, 'status_code', None)
                self.call_ledger.finish_physical_attempt(attempt.physical_attempt_id, elapsed_s=elapsed, outcome='retryable_error' if retryable else 'fatal_error', http_status=status if isinstance(status, int) else UNAVAILABLE, api_outcome='transport_error', error_type=error_type)
                last_error = exc
                if not retryable or physical_ordinal > self.max_transport_retries:
                    self.call_ledger.finish_logical_failure(logical.logical_call_id, elapsed_s=time.perf_counter() - logical_started)
                    raise
                continue
            elapsed = time.perf_counter() - attempt_started
            usage = _response_usage(response)
            if self._measurement_provenance == 'synthetic' and usage.provenance == 'actual':
                usage = TokenUsage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens, total_tokens=usage.total_tokens, provenance='synthetic')
            self.call_ledger.finish_physical_attempt(attempt.physical_attempt_id, elapsed_s=elapsed, outcome='success', api_outcome='success', usage=usage, response_model=_response_field(response, 'model'), response_request_id=_response_field(response, 'id'))
            self.call_ledger.finish_logical_success(logical.logical_call_id, attempt.physical_attempt_id, elapsed_s=time.perf_counter() - logical_started)
            return _response_text(response)
        raise CallLedgerError(f'transport retry controller exhausted without outcome: {last_error}')

    def record_parse_outcome(self, outcome: str, *, logical_call_id: Optional[str]=None) -> str:
        selected = logical_call_id or self.last_logical_call_id
        if selected is None:
            raise CallLedgerError('no logical call is available for parser annotation')
        self.call_ledger.record_parse_outcome(selected, outcome)
        return selected

class MockBaseLLMClient:
    """Offline base-LLM stub. `responses` is a list (consumed in order) or a callable
    (system, user) -> str. Records call count for assertions."""

    def __init__(self, responses: Union[List[str], Callable[[str, str], str], str]):
        self._responses = responses
        self._i = 0
        self.calls: List[Dict[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str, temperature: Optional[float]=None, max_tokens: Optional[int]=None) -> str:
        self.calls.append({'system': system_prompt, 'user': user_prompt})
        if callable(self._responses):
            return self._responses(system_prompt, user_prompt)
        if isinstance(self._responses, str):
            return self._responses
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r

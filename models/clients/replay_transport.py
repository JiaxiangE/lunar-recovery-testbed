"""Deterministic, local OpenAI-compatible replay transport.

Responses are selected only by ``(fixture_id, logical_ordinal,
physical_ordinal)`` from a corpus copied and hashed at construction.  Prompt
content, method names, scorer labels, expected truth and observed outcomes can
never participate in selection.
"""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
_FORBIDDEN_SELECTOR_KEYS = frozenset({'expected', 'expected_cause_level', 'expected_scope', 'ground_truth', 'answer_label', 'oracle', 'scorer', 'scorer_label', 'scorer_labels', 'method', 'method_id', 'implementation_id', 'outcome', 'expected_outcome', 'gate_outcome', 'result', 'verdict', 'success', 'score', 'reward', 'metric', 'label', 'evaluation_metadata', 'experiment_arm', 'arm'})
_SAFE_ID = re.compile('^[A-Za-z0-9_.:-]{1,96}$')
_SAFE_RESPONSE_METADATA = re.compile('^[A-Za-z0-9._/-]{1,256}$')

class ReplayCorpusError(ValueError):
    """Replay corpus is malformed, mutable-by-reference, or truth-conditioned."""

class ReplayLookupError(RuntimeError):
    """No frozen response exists for the requested replay coordinates."""

class ReplayTransportError(RuntimeError):
    """A deterministic transport failure requested by the replay corpus."""

    def __init__(self, error_type: str, *, retryable: bool, status_code: int | None=None) -> None:
        super().__init__(error_type)
        self.error_type = error_type
        self.retryable = retryable
        self.status_code = status_code

def _safe_id(value: Any, field_name: str) -> str:
    text = str(value or '')
    if not _SAFE_ID.fullmatch(text):
        raise ReplayCorpusError(f'{field_name} must be a short machine-readable identifier')
    return text

def _assert_no_forbidden_keys(value: Any, path: str='corpus') -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_SELECTOR_KEYS:
                raise ReplayCorpusError(f'truth/method-conditioned replay selector forbidden at {path}.{key}')
            _assert_no_forbidden_keys(child, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_forbidden_keys(child, f'{path}[{index}]')

def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple((_deep_freeze(item) for item in value))
    return value

@dataclass(frozen=True)
class ReplayUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReplayCorpusError(f'replay usage {name} must be a non-negative integer')
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ReplayCorpusError('replay total_tokens must equal prompt_tokens + completion_tokens')

@dataclass(frozen=True)
class ReplayMessage:
    content: str
    role: str = 'assistant'

@dataclass(frozen=True)
class ReplayChoice:
    index: int
    message: ReplayMessage
    finish_reason: str = 'stop'

@dataclass(frozen=True)
class ReplayChatCompletion:
    id: str
    model: str
    choices: Tuple[ReplayChoice, ...]
    usage: ReplayUsage
    created: int = 0
    object: str = 'chat.completion'
    usage_provenance: str = 'synthetic'

    def model_dump(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload['choices'] = [asdict(item) for item in self.choices]
        return payload

@dataclass(frozen=True)
class _FrozenReplayEntry:
    fixture_id: str
    logical_ordinal: int
    physical_ordinal: int
    response: Optional[Mapping[str, Any]] = None
    error: Optional[Mapping[str, Any]] = None

    @property
    def key(self) -> Tuple[str, int, int]:
        return (self.fixture_id, self.logical_ordinal, self.physical_ordinal)

class ReplayCorpus:
    """Immutable-by-copy replay corpus with a canonical content hash."""
    schema_version = 'w3_api_replay_corpus_v1'

    def __init__(self, entries: Sequence[Mapping[str, Any]] | Mapping[str, Any]):
        raw_entries: Any = entries.get('entries') if isinstance(entries, Mapping) else entries
        if isinstance(entries, Mapping):
            extra = set(entries).difference({'schema_version', 'entries'})
            if extra:
                raise ReplayCorpusError(f'unknown replay corpus field(s): {sorted(extra)}')
            version = entries.get('schema_version', self.schema_version)
            if version != self.schema_version:
                raise ReplayCorpusError(f'unsupported replay corpus schema: {version!r}')
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
            raise ReplayCorpusError('replay corpus entries must be a sequence')
        try:
            detached = json.loads(json.dumps(raw_entries, ensure_ascii=False, sort_keys=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ReplayCorpusError(f'replay corpus is not JSON-safe: {exc}') from exc
        _assert_no_forbidden_keys(detached)
        if not detached:
            raise ReplayCorpusError('replay corpus must contain at least one entry')
        frozen: list[_FrozenReplayEntry] = []
        index: dict[Tuple[str, int, int], _FrozenReplayEntry] = {}
        for number, item in enumerate(detached, 1):
            if not isinstance(item, dict):
                raise ReplayCorpusError(f'entry {number} must be an object')
            allowed = {'fixture_id', 'logical_ordinal', 'physical_ordinal', 'response', 'error'}
            extra = set(item).difference(allowed)
            if extra:
                raise ReplayCorpusError(f'entry {number} has unknown field(s): {sorted(extra)}')
            fixture_id = _safe_id(item.get('fixture_id'), 'fixture_id')
            logical_ordinal = self._positive_int(item.get('logical_ordinal'), 'logical_ordinal')
            physical_ordinal = self._positive_int(item.get('physical_ordinal'), 'physical_ordinal')
            response, error = (item.get('response'), item.get('error'))
            if (response is None) == (error is None):
                raise ReplayCorpusError(f'entry {number} must contain exactly one of response or error')
            if response is not None:
                response = _deep_freeze(self._validate_response(response, number))
            if error is not None:
                error = _deep_freeze(self._validate_error(error, number))
            entry = _FrozenReplayEntry(fixture_id, logical_ordinal, physical_ordinal, response=response, error=error)
            if entry.key in index:
                raise ReplayCorpusError(f'duplicate replay coordinates: {entry.key}')
            frozen.append(entry)
            index[entry.key] = entry
        scripts: dict[Tuple[str, int], list[int]] = {}
        for entry in frozen:
            scripts.setdefault((entry.fixture_id, entry.logical_ordinal), []).append(entry.physical_ordinal)
        for coordinates, ordinals in scripts.items():
            ordered = sorted(ordinals)
            if ordered != list(range(1, len(ordered) + 1)):
                raise ReplayCorpusError(f'physical ordinals must be contiguous from 1 for {coordinates}')
        canonical = json.dumps({'schema_version': self.schema_version, 'entries': detached}, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self._entries = tuple(frozen)
        self._index = index
        self.sha256 = hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReplayCorpusError(f'{field_name} must be a positive integer')
        return value

    @staticmethod
    def _validate_response(value: Any, number: int) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ReplayCorpusError(f'entry {number} response must be an object')
        allowed = {'content', 'model', 'request_id', 'usage', 'finish_reason'}
        extra = set(value).difference(allowed)
        if extra:
            raise ReplayCorpusError(f'entry {number} response has unknown field(s): {sorted(extra)}')
        if not isinstance(value.get('content'), str):
            raise ReplayCorpusError(f'entry {number} response.content must be a string')
        for field_name in ('model', 'request_id'):
            if not isinstance(value.get(field_name), str) or not _SAFE_RESPONSE_METADATA.fullmatch(value[field_name]):
                raise ReplayCorpusError(f'entry {number} response.{field_name} is required')
        finish = value.get('finish_reason', 'stop')
        if not isinstance(finish, str) or not finish:
            raise ReplayCorpusError(f'entry {number} finish_reason must be non-empty')
        usage = value.get('usage')
        if not isinstance(usage, dict):
            raise ReplayCorpusError(f'entry {number} response.usage is required')
        usage_extra = set(usage).difference({'prompt_tokens', 'completion_tokens', 'total_tokens'})
        if usage_extra:
            raise ReplayCorpusError(f'entry {number} response.usage has unknown field(s): {sorted(usage_extra)}')
        ReplayUsage(prompt_tokens=usage.get('prompt_tokens'), completion_tokens=usage.get('completion_tokens'), total_tokens=usage.get('total_tokens'))
        return value

    @staticmethod
    def _validate_error(value: Any, number: int) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ReplayCorpusError(f'entry {number} error must be an object')
        allowed = {'type', 'retryable', 'status_code'}
        extra = set(value).difference(allowed)
        if extra:
            raise ReplayCorpusError(f'entry {number} error has unknown field(s): {sorted(extra)}')
        _safe_id(value.get('type'), 'error.type')
        if not isinstance(value.get('retryable'), bool):
            raise ReplayCorpusError(f'entry {number} error.retryable must be bool')
        status = value.get('status_code')
        if status is not None and (isinstance(status, bool) or not isinstance(status, int) or (not 100 <= status <= 599)):
            raise ReplayCorpusError(f'entry {number} error.status_code is invalid')
        if isinstance(status, int):
            policy_retryable = status in {408, 409, 429} or status >= 500
            if value['retryable'] is not policy_retryable:
                raise ReplayCorpusError(f'entry {number} retryable flag conflicts with frozen transport policy')
        elif value['retryable'] and str(value['type']).lower() not in {'timeouterror', 'connectionerror'}:
            raise ReplayCorpusError(f'entry {number} retryable error without status is outside the frozen policy')
        return value

    def lookup(self, fixture_id: str, logical_ordinal: int, physical_ordinal: int) -> _FrozenReplayEntry:
        key = (_safe_id(fixture_id, 'fixture_id'), self._positive_int(logical_ordinal, 'logical_ordinal'), self._positive_int(physical_ordinal, 'physical_ordinal'))
        try:
            return self._index[key]
        except KeyError as exc:
            raise ReplayLookupError(f'no frozen replay entry for {key}') from exc

class _ReplayCompletions:

    def __init__(self, owner: 'ReplayTransport') -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> ReplayChatCompletion:
        return self._owner._create(**kwargs)

class ReplayTransport:
    """Local object exposing ``chat.completions.create`` like the OpenAI client."""
    is_replay_transport = True
    single_physical_attempt_per_create = True

    def __init__(self, corpus: ReplayCorpus | Sequence[Mapping[str, Any]] | Mapping[str, Any]):
        self.corpus = corpus if isinstance(corpus, ReplayCorpus) else ReplayCorpus(corpus)
        self.chat = SimpleNamespace(completions=_ReplayCompletions(self))
        self._context: Optional[Tuple[str, int, int]] = None
        self._consumed: set[Tuple[str, int, int]] = set()
        self.request_fingerprints: list[Dict[str, Any]] = []

    def set_replay_context(self, *, fixture_id: str, logical_ordinal: int, physical_ordinal: int) -> None:
        if self._context is not None:
            raise ReplayLookupError('prior replay context was not consumed')
        self._context = (_safe_id(fixture_id, 'fixture_id'), ReplayCorpus._positive_int(logical_ordinal, 'logical_ordinal'), ReplayCorpus._positive_int(physical_ordinal, 'physical_ordinal'))

    def _create(self, **kwargs: Any) -> ReplayChatCompletion:
        if self._context is None:
            raise ReplayLookupError('set_replay_context() is required before create()')
        context = self._context
        self._context = None
        model = kwargs.get('model')
        messages = kwargs.get('messages')
        if not isinstance(model, str) or not model:
            raise ReplayLookupError('OpenAI-compatible create() requires model')
        if not isinstance(messages, list) or not messages:
            raise ReplayLookupError('OpenAI-compatible create() requires messages')
        request_text = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.request_fingerprints.append({'fixture_id': context[0], 'logical_ordinal': context[1], 'physical_ordinal': context[2], 'model': model, 'messages_sha256': hashlib.sha256(request_text.encode('utf-8')).hexdigest(), 'temperature': kwargs.get('temperature'), 'max_tokens': kwargs.get('max_tokens'), 'seed': kwargs.get('seed')})
        if context in self._consumed:
            raise ReplayLookupError(f'replay coordinates already consumed: {context}')
        entry = self.corpus.lookup(*context)
        self._consumed.add(context)
        if entry.error is not None:
            raise ReplayTransportError(str(entry.error['type']), retryable=bool(entry.error['retryable']), status_code=entry.error.get('status_code'))
        response = dict(entry.response or {})
        usage = dict(response['usage'])
        return ReplayChatCompletion(id=response['request_id'], model=response['model'], choices=(ReplayChoice(index=0, message=ReplayMessage(response['content']), finish_reason=response.get('finish_reason', 'stop')),), usage=ReplayUsage(prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'], total_tokens=usage['total_tokens']))

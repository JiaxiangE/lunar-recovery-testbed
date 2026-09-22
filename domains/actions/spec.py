"""Scenario-neutral symbolic primitive specifications and chain legality.

The PSR primitive model predates the scenario-aware recovery substrate and remains
unchanged.  This module is the identity-preserving model used by the dedicated Lava
and Construction primitive registries: facts such as
``panel_installed(panel_7)`` remain distinct from ``panel_installed(panel_8)``.

All incomplete or malformed specifications fail closed.  In particular, a resolver
exception is returned as a stable rejection code; it never makes a candidate legal.
"""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType
from typing import Any
from typing import Callable
from typing import FrozenSet
from typing import Hashable
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
TypeSpec = type | Tuple[type, ...]
ParameterValidator = Callable[[Any], bool | Tuple[bool, str]]
FactResolver = Callable[[Mapping[str, Any]], Iterable['Fact']]
EMPTY_CANDIDATE = 'EMPTY_CANDIDATE'
OFF_VOCAB = 'OFF_VOCAB'
BAD_ARGUMENTS = 'BAD_ARGUMENTS'
AGENT_TYPE_REQUIRED = 'AGENT_TYPE_REQUIRED'
AGENT_TYPE_MISMATCH = 'AGENT_TYPE_MISMATCH'
AGENT_ID_REQUIRED = 'AGENT_ID_REQUIRED'
AGENT_BINDING_MISMATCH = 'AGENT_BINDING_MISMATCH'
PRECONDITION_UNMET = 'PRECONDITION_UNMET'
UNRESOLVED_SPEC = 'UNRESOLVED_SPEC'
SPEC_RESOLUTION_ERROR = 'SPEC_RESOLUTION_ERROR'
INVALID_INITIAL_FACTS = 'INVALID_INITIAL_FACTS'

def _freeze_identity(value: Any) -> Hashable:
    """Convert structured identity values to immutable, deterministic values."""
    if isinstance(value, Mapping):
        items = ((_freeze_identity(key), _freeze_identity(child)) for key, child in value.items())
        return tuple(sorted(items, key=repr))
    if isinstance(value, (list, tuple)):
        return tuple((_freeze_identity(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return frozenset((_freeze_identity(item) for item in value))
    try:
        hash(value)
    except TypeError as exc:
        raise TypeError(f'fact argument is not identity-preserving/hashable: {value!r}') from exc
    return value

def _render_identity(value: Hashable) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, tuple):
        return '[' + ', '.join((_render_identity(item) for item in value)) + ']'
    if isinstance(value, frozenset):
        return '{' + ', '.join(sorted((_render_identity(item) for item in value))) + '}'
    return str(value)

@dataclass(frozen=True)
class Fact:
    """A predicate atom whose positional arguments preserve entity identity."""
    predicate: str
    args: Tuple[Hashable, ...] = ()

    def __post_init__(self) -> None:
        predicate = str(self.predicate).strip()
        if not predicate:
            raise ValueError('fact predicate must be non-empty')
        raw_args: Any = self.args
        if isinstance(raw_args, tuple):
            values = raw_args
        elif isinstance(raw_args, list):
            values = tuple(raw_args)
        else:
            values = (raw_args,)
        object.__setattr__(self, 'predicate', predicate)
        object.__setattr__(self, 'args', tuple((_freeze_identity(value) for value in values)))

    @property
    def arguments(self) -> Tuple[Hashable, ...]:
        """Readable alias for callers that prefer ``arguments`` over ``args``."""
        return self.args

    def render(self) -> str:
        if not self.args:
            return self.predicate
        return f"{self.predicate}({', '.join((_render_identity(value) for value in self.args))})"

    def __str__(self) -> str:
        return self.render()

def fact(predicate: str, *args: Hashable) -> Fact:
    """Concise constructor for an identity-preserving :class:`Fact`."""
    return Fact(predicate, tuple(args))

@dataclass(frozen=True)
class ResolutionContext:
    """Trusted execution identity supplied outside public primitive params."""
    actor_id: str
    agent_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ValueError('resolution actor_id must be non-empty')
        if not isinstance(self.agent_type, str) or not self.agent_type.strip():
            raise ValueError('resolution agent_type must be non-empty')
        object.__setattr__(self, 'actor_id', self.actor_id.strip())
        object.__setattr__(self, 'agent_type', self.agent_type.strip().upper())

class ActorBindingError(ValueError):

    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        super().__init__(detail)

def trusted_agent_types_by_id(available_agents: Iterable[Any]) -> Mapping[str, str]:
    """Create a duplicate-safe trusted actor map from the runtime roster."""
    result: dict[str, str] = {}
    for agent in available_agents:
        if isinstance(agent, Mapping):
            actor_id = agent.get('id')
            agent_type = agent.get('type', agent.get('agent_type'))
        else:
            actor_id = getattr(agent, 'id', None)
            agent_type = getattr(agent, 'type', getattr(agent, 'agent_type', None))
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ActorBindingError(AGENT_BINDING_MISMATCH, 'available agent has no valid id')
        normalized_id = actor_id.strip()
        if normalized_id in result:
            raise ActorBindingError(AGENT_BINDING_MISMATCH, f'duplicate available agent id: {normalized_id}')
        if not isinstance(agent_type, str) or not agent_type.strip():
            raise ActorBindingError(AGENT_BINDING_MISMATCH, f'available agent {normalized_id!r} has no valid type')
        result[normalized_id] = agent_type.strip().upper()
    return MappingProxyType(result)

def bind_resolution_context(step: Mapping[str, Any], agent_types_by_id: Mapping[str, str]) -> ResolutionContext:
    """Bind actor identity from top-level step metadata, never from params."""
    actor_id = step.get('agent_id')
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ActorBindingError(AGENT_ID_REQUIRED, 'actor-sensitive step requires agent_id')
    normalized_id = actor_id.strip()
    if normalized_id not in agent_types_by_id:
        raise ActorBindingError(AGENT_BINDING_MISMATCH, f'unknown actor_id: {normalized_id!r}')
    trusted_type = str(agent_types_by_id[normalized_id]).upper()
    claimed_type = step.get('agent_type')
    if claimed_type is not None and str(claimed_type).upper() != trusted_type:
        raise ActorBindingError(AGENT_BINDING_MISMATCH, f'actor type claim for {normalized_id!r} conflicts with trusted roster')
    return ResolutionContext(normalized_id, trusted_type)

@dataclass(frozen=True)
class ParameterSchema:
    """Strict primitive-argument schema with optional finite-domain validators."""
    required: Mapping[str, TypeSpec] = field(default_factory=dict)
    optional: Mapping[str, TypeSpec] = field(default_factory=dict)
    required_any_of: Tuple[FrozenSet[str], ...] = ()
    validators: Mapping[str, ParameterValidator] = field(default_factory=dict)
    unresolved_reason: Optional[str] = None

    def validate(self, args: Any) -> Tuple[bool, str]:
        if self.unresolved_reason:
            return (False, f'schema unresolved: {self.unresolved_reason}')
        if not isinstance(args, Mapping):
            return (False, 'arguments must be an object')
        allowed = set(self.required) | set(self.optional)
        extras = set(args) - allowed
        if extras:
            return (False, f'unknown argument(s): {sorted(extras)}')
        missing = set(self.required) - set(args)
        if missing:
            return (False, f'missing argument(s): {sorted(missing)}')
        if self.required_any_of and (not any((option <= set(args) for option in self.required_any_of))):
            choices = [sorted(option) for option in self.required_any_of]
            return (False, f'arguments must include one of {choices}')
        for name, value in args.items():
            expected = self.required.get(name, self.optional.get(name, object))
            if expected is not object:
                if isinstance(value, bool) and _type_spec_accepts_numeric(expected):
                    return (False, f'argument {name!r} has wrong type')
                if not isinstance(value, expected):
                    return (False, f'argument {name!r} has wrong type')
            validator = self.validators.get(name)
            if validator is None:
                continue
            try:
                verdict = validator(value)
                if isinstance(verdict, tuple):
                    if len(verdict) != 2:
                        raise ValueError('validator tuple must contain (accepted, detail)')
                    valid, detail = verdict
                else:
                    valid, detail = (bool(verdict), 'outside the allowed domain')
            except Exception as exc:
                return (False, f'argument {name!r} validator failed: {type(exc).__name__}: {exc}')
            if not valid:
                return (False, f'argument {name!r} invalid: {detail}')
        return (True, '')

def _type_spec_accepts_numeric(expected: TypeSpec) -> bool:
    values = expected if isinstance(expected, tuple) else (expected,)
    return any((item in (int, float) for item in values))

def no_facts(_args: Mapping[str, Any]) -> FrozenSet[Fact]:
    """Reusable resolver for an intentionally empty fact set."""
    return frozenset()

@dataclass(frozen=True)
class PrimitiveSpec:
    """Auditable symbolic semantics for one primitive.

    ``requires``, ``produces``, and ``clears`` receive validated primitive
    arguments.  Dynamic resolvers are what retain identities such as a concrete
    ``panel_id`` in the resulting facts.
    """
    name: str
    parameter_schema: ParameterSchema
    allowed_agent_types: FrozenSet[str]
    requires: FactResolver
    produces: FactResolver
    clears: FactResolver
    source_references: Tuple[str, ...]
    unresolved_reason: Optional[str] = None
    actor_sensitive: bool = False

    def __post_init__(self) -> None:
        raw_agents: Iterable[Any]
        if isinstance(self.allowed_agent_types, str):
            raw_agents = (self.allowed_agent_types,)
        else:
            raw_agents = self.allowed_agent_types
        raw_references: Iterable[Any]
        if isinstance(self.source_references, str):
            raw_references = (self.source_references,)
        else:
            raw_references = self.source_references
        object.__setattr__(self, 'name', str(self.name).strip())
        object.__setattr__(self, 'allowed_agent_types', frozenset((str(agent).strip().upper() for agent in raw_agents if str(agent).strip())))
        object.__setattr__(self, 'source_references', tuple((str(reference).strip() for reference in raw_references if str(reference).strip())))

    @classmethod
    def unresolved(cls, name: str, reason: str, *, source_references: Sequence[str]=()) -> 'PrimitiveSpec':
        """Declare a known but unresolved primitive that always fails closed."""
        return cls(name=name, parameter_schema=ParameterSchema(unresolved_reason=reason), allowed_agent_types=frozenset(), requires=no_facts, produces=no_facts, clears=no_facts, source_references=tuple(source_references), unresolved_reason=reason)

    def resolution_blocker(self) -> Optional[str]:
        if self.unresolved_reason:
            return self.unresolved_reason
        if not self.name:
            return 'primitive name is empty'
        if not isinstance(self.parameter_schema, ParameterSchema):
            return 'parameter schema is missing or invalid'
        if self.parameter_schema.unresolved_reason:
            return f'parameter schema unresolved: {self.parameter_schema.unresolved_reason}'
        if not self.allowed_agent_types:
            return 'allowed agent types are empty'
        if not self.source_references:
            return 'source references are empty'
        for label, resolver in (('requires', self.requires), ('produces', self.produces), ('clears', self.clears)):
            if not callable(resolver):
                return f'{label} resolver is missing'
        return None

    def resolve_requires(self, args: Mapping[str, Any], resolution_context: Optional[ResolutionContext]=None) -> FrozenSet[Fact]:
        return _resolve_fact_set(self.requires, args, self.name, 'requires', self.actor_sensitive, resolution_context)

    def resolve_produces(self, args: Mapping[str, Any], resolution_context: Optional[ResolutionContext]=None) -> FrozenSet[Fact]:
        return _resolve_fact_set(self.produces, args, self.name, 'produces', self.actor_sensitive, resolution_context)

    def resolve_clears(self, args: Mapping[str, Any], resolution_context: Optional[ResolutionContext]=None) -> FrozenSet[Fact]:
        return _resolve_fact_set(self.clears, args, self.name, 'clears', self.actor_sensitive, resolution_context)

def _resolve_fact_set(resolver: FactResolver, args: Mapping[str, Any], primitive_name: str, field_name: str, actor_sensitive: bool, resolution_context: Optional[ResolutionContext]) -> FrozenSet[Fact]:
    if actor_sensitive:
        if resolution_context is None:
            raise ActorBindingError(AGENT_ID_REQUIRED, f'{primitive_name}.{field_name} requires trusted actor context')
        resolved = resolver(args, resolution_context)
    else:
        resolved = resolver(args)
    if resolved is None or isinstance(resolved, (str, bytes, Fact)):
        raise TypeError(f'{primitive_name}.{field_name} must return an iterable of Fact')
    facts = frozenset(resolved)
    invalid = [value for value in facts if not isinstance(value, Fact)]
    if invalid:
        raise TypeError(f'{primitive_name}.{field_name} returned non-Fact value(s): {invalid!r}')
    return facts

@dataclass(frozen=True)
class ResolvedPrimitive:
    name: str
    params: Mapping[str, Any]
    agent_type: str
    requires: FrozenSet[Fact]
    produces: FrozenSet[Fact]
    clears: FrozenSet[Fact]
    actor_id: Optional[str] = None

@dataclass(frozen=True)
class PrimitiveResolution:
    accepted: bool
    reason_code: str = ''
    detail: str = ''
    resolved: Optional[ResolvedPrimitive] = None

@dataclass(frozen=True)
class LegalityResult:
    accepted: bool
    reason_code: str = ''
    detail: str = ''
    failed_step_index: Optional[int] = None
    final_facts: FrozenSet[Fact] = frozenset()
    produced_facts: FrozenSet[Fact] = frozenset()
    resolved_steps: Tuple[ResolvedPrimitive, ...] = ()

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return (self.reason_code,) if self.reason_code else ()

def resolve_primitive(spec: PrimitiveSpec, params: Any, agent_type: Optional[str]=None, *, resolution_context: Optional[ResolutionContext]=None) -> PrimitiveResolution:
    """Validate and resolve one primitive without applying it to a fact state."""
    try:
        blocker = spec.resolution_blocker()
    except Exception as exc:
        return PrimitiveResolution(False, UNRESOLVED_SPEC, f'primitive specification is malformed: {type(exc).__name__}: {exc}')
    if blocker:
        return PrimitiveResolution(False, UNRESOLVED_SPEC, blocker)
    try:
        ok, detail = spec.parameter_schema.validate(params)
    except Exception as exc:
        return PrimitiveResolution(False, UNRESOLVED_SPEC, f'parameter schema is malformed: {type(exc).__name__}: {exc}')
    if not ok:
        code = UNRESOLVED_SPEC if detail.startswith('schema unresolved:') else BAD_ARGUMENTS
        return PrimitiveResolution(False, code, detail)
    if spec.actor_sensitive:
        if resolution_context is None:
            return PrimitiveResolution(False, AGENT_ID_REQUIRED, 'actor-sensitive primitive requires trusted actor context')
        if agent_type is not None and str(agent_type).strip().upper() != resolution_context.agent_type:
            return PrimitiveResolution(False, AGENT_BINDING_MISMATCH, 'agent_type conflicts with resolution context')
        agent_type = resolution_context.agent_type
    if agent_type is None or not str(agent_type).strip():
        return PrimitiveResolution(False, AGENT_TYPE_REQUIRED, 'agent type is required')
    normalized_agent = str(agent_type).strip().upper()
    if normalized_agent not in spec.allowed_agent_types:
        return PrimitiveResolution(False, AGENT_TYPE_MISMATCH, f'agent type {normalized_agent!r} not allowed for {spec.name}; allowed: {sorted(spec.allowed_agent_types)}')
    normalized_params = dict(params)
    try:
        requires = spec.resolve_requires(normalized_params, resolution_context)
        produces = spec.resolve_produces(normalized_params, resolution_context)
        clears = spec.resolve_clears(normalized_params, resolution_context)
    except ActorBindingError as exc:
        return PrimitiveResolution(False, exc.reason_code, str(exc))
    except Exception as exc:
        return PrimitiveResolution(False, SPEC_RESOLUTION_ERROR, f'{spec.name} effect resolution failed: {type(exc).__name__}: {exc}')
    return PrimitiveResolution(True, resolved=ResolvedPrimitive(name=spec.name, params=normalized_params, agent_type=normalized_agent, requires=requires, produces=produces, clears=clears, actor_id=resolution_context.actor_id if resolution_context is not None else None))

def _agent_type_of(step: Mapping[str, Any], default_agent_type: Optional[str]) -> Optional[str]:
    direct = step.get('agent_type')
    if direct is not None:
        return str(direct)
    agent = step.get('agent')
    if isinstance(agent, Mapping):
        nested = agent.get('agent_type', agent.get('type'))
        return None if nested is None else str(nested)
    return default_agent_type

def _params_of(step: Mapping[str, Any]) -> Tuple[bool, Any, str]:
    has_params = 'params' in step
    has_arguments = 'arguments' in step
    if has_params and has_arguments:
        return (False, None, "step cannot contain both 'params' and 'arguments'")
    return (True, step.get('params', step.get('arguments', {})), '')

def _normalize_initial_facts(initial_facts: Iterable[Fact]) -> Tuple[bool, FrozenSet[Fact], str]:
    if isinstance(initial_facts, (str, bytes, Fact)):
        return (False, frozenset(), 'initial_facts must be an iterable of Fact')
    try:
        facts = frozenset(initial_facts)
    except Exception as exc:
        return (False, frozenset(), f'could not read initial facts: {type(exc).__name__}: {exc}')
    invalid = [value for value in facts if not isinstance(value, Fact)]
    if invalid:
        return (False, frozenset(), f'initial facts contain non-Fact value(s): {invalid!r}')
    return (True, facts, '')

def forward_chain_legality(chain: Sequence[Mapping[str, Any]], primitive_specs: Mapping[str, PrimitiveSpec], *, initial_facts: Iterable[Fact]=(), default_agent_type: Optional[str]=None, agent_types_by_id: Optional[Mapping[str, str]]=None) -> LegalityResult:
    """Resolve and apply a candidate chain in order, failing closed at every boundary."""
    ok, facts, detail = _normalize_initial_facts(initial_facts)
    if not ok:
        return LegalityResult(False, INVALID_INITIAL_FACTS, detail)
    if not chain:
        return LegalityResult(False, EMPTY_CANDIDATE, 'candidate is empty', final_facts=facts)
    state = set(facts)
    produced: set[Fact] = set()
    resolved_steps: list[ResolvedPrimitive] = []
    for index, step in enumerate(chain):
        if not isinstance(step, Mapping):
            return _chain_failure(BAD_ARGUMENTS, f'step {index} must be an object', index, state, produced, resolved_steps)
        name = step.get('primitive', '')
        if not isinstance(name, str) or not name:
            return _chain_failure(OFF_VOCAB, f'step {index} has no primitive name', index, state, produced, resolved_steps)
        spec = primitive_specs.get(name)
        if spec is None:
            return _chain_failure(OFF_VOCAB, f'step {index}: unknown primitive {name!r}', index, state, produced, resolved_steps)
        if not isinstance(spec, PrimitiveSpec):
            return _chain_failure(UNRESOLVED_SPEC, f'registry entry {name!r} is not a PrimitiveSpec', index, state, produced, resolved_steps)
        if spec.name != name:
            return _chain_failure(UNRESOLVED_SPEC, f'registry key {name!r} does not match spec name {spec.name!r}', index, state, produced, resolved_steps)
        params_ok, params, params_detail = _params_of(step)
        if not params_ok:
            return _chain_failure(BAD_ARGUMENTS, f'step {index}: {params_detail}', index, state, produced, resolved_steps)
        resolution_context = None
        actor_type = _agent_type_of(step, default_agent_type)
        if spec.actor_sensitive or step.get('agent_id') is not None:
            try:
                resolution_context = bind_resolution_context(step, agent_types_by_id or {})
            except ActorBindingError as exc:
                return _chain_failure(exc.reason_code, f'step {index} ({name}): {exc}', index, state, produced, resolved_steps)
            actor_type = resolution_context.agent_type
        resolution = resolve_primitive(spec, params, actor_type, resolution_context=resolution_context)
        if not resolution.accepted or resolution.resolved is None:
            return _chain_failure(resolution.reason_code or UNRESOLVED_SPEC, f'step {index} ({name}): {resolution.detail}', index, state, produced, resolved_steps)
        resolved = resolution.resolved
        missing = resolved.requires - state
        if missing:
            rendered = sorted((item.render() for item in missing))
            return _chain_failure(PRECONDITION_UNMET, f'step {index} ({name}): unmet precondition(s) {rendered}', index, state, produced, resolved_steps)
        state.difference_update(resolved.clears)
        state.update(resolved.produces)
        produced.update(resolved.produces)
        resolved_steps.append(resolved)
    return LegalityResult(True, final_facts=frozenset(state), produced_facts=frozenset(produced), resolved_steps=tuple(resolved_steps))

def _chain_failure(reason_code: str, detail: str, index: int, state: Iterable[Fact], produced: Iterable[Fact], resolved_steps: Sequence[ResolvedPrimitive]) -> LegalityResult:
    return LegalityResult(False, reason_code, detail, index, frozenset(state), frozenset(produced), tuple(resolved_steps))

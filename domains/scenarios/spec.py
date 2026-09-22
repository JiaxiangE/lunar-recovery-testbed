"""Shared interfaces for scenario-aware generation, validation, and realization."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import FrozenSet
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple

class PromptIsolationError(ValueError):
    """Evaluation-only metadata was placed in a generation prompt context."""
_FORBIDDEN_PROMPT_KEYS = frozenset({'expected_cause_level', 'expected_scope', 'expected', 'experiment_arm', 'experimental_arm', 'arm', 'scorer_label', 'scorer_labels', 'evaluation_metadata', 'ground_truth', 'answer_label'})

def _assert_prompt_safe(value: Any, path: str='prompt_context') -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_PROMPT_KEYS:
                raise PromptIsolationError(f'evaluation-only field at {path}.{key}')
            _assert_prompt_safe(child, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_prompt_safe(child, f'{path}[{index}]')

@dataclass(frozen=True)
class PromptContext:
    """Only runtime-observable information that generation may consume."""
    observable_failure: Mapping[str, Any] = field(default_factory=dict)
    observable_state: Mapping[str, Any] = field(default_factory=dict)
    agents: Tuple[Mapping[str, Any], ...] = ()
    communication_state: str = 'Connected'
    params: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        payload = {'observable_failure': self.observable_failure, 'observable_state': self.observable_state, 'agents': self.agents, 'communication_state': self.communication_state, 'params': self.params}
        _assert_prompt_safe(payload)

    def to_prompt_payload(self, scenario: 'ScenarioSpec') -> Dict[str, Any]:
        self.validate()
        return {'scenario_id': scenario.scenario_id, 'observable_failure': dict(self.observable_failure), 'observable_state': dict(self.observable_state), 'agents': [dict(agent) for agent in self.agents], 'communication_state': self.communication_state, 'params': dict(self.params), 'allowed_actions': scenario.render_prompt_vocabulary()}

    def template_context(self) -> Dict[str, Any]:
        self.validate()
        return {'params': dict(self.params)}

    @classmethod
    def from_legacy(cls, context: Optional[Mapping[str, Any]]) -> 'PromptContext':
        """Compatibility adapter, explicitly excluding evaluation metadata."""
        raw = dict(context or {})
        _assert_prompt_safe(raw, 'legacy_context')
        failure = raw.pop('failure', {})
        agents = raw.pop('agents', ())
        communication_state = raw.pop('communication_state', 'Connected')
        params = raw.pop('params', {})
        if not isinstance(failure, Mapping):
            failure = {'description': failure}
        if not isinstance(agents, (list, tuple)):
            agents = ()
        return cls(observable_failure=dict(failure), observable_state=raw, agents=tuple((dict(agent) for agent in agents if isinstance(agent, Mapping))), communication_state=str(communication_state), params=dict(params) if isinstance(params, Mapping) else {})

@dataclass(frozen=True)
class EvaluationMetadata:
    """Post-trial scorer inputs.  This type is never accepted by D_T."""
    expected_cause_level: Optional[str] = None
    expected_scope: Optional[str] = None
    experimental_arm: Optional[str] = None
    scorer_labels: Mapping[str, Any] = field(default_factory=dict)
TypeSpec = type | Tuple[type, ...]

@dataclass(frozen=True)
class ArgumentSchema:
    required: Mapping[str, TypeSpec] = field(default_factory=dict)
    optional: Mapping[str, TypeSpec] = field(default_factory=dict)
    required_any_of: Tuple[FrozenSet[str], ...] = ()
    validators: Mapping[str, Callable[[Any], Any]] = field(default_factory=dict)
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
            rendered = [sorted(option) for option in self.required_any_of]
            return (False, f'arguments must include one of {rendered}')
        for name, value in args.items():
            expected = self.required.get(name, self.optional.get(name, object))
            if expected is object:
                continue
            if isinstance(value, bool) and expected in ((int, float), int, float):
                return (False, f'argument {name!r} has wrong type')
            if not isinstance(value, expected):
                return (False, f'argument {name!r} has wrong type')
            validator = self.validators.get(name)
            if validator is not None:
                try:
                    verdict = validator(value)
                    if isinstance(verdict, tuple):
                        valid, validator_detail = verdict
                    else:
                        valid, validator_detail = (bool(verdict), 'outside allowed domain')
                except Exception as exc:
                    return (False, f'argument {name!r} validator failed: {type(exc).__name__}: {exc}')
                if not valid:
                    return (False, f'argument {name!r} invalid: {validator_detail}')
        return (True, '')

@dataclass(frozen=True)
class SymbolicEffect:
    requires: FrozenSet[Any] = frozenset()
    produces: FrozenSet[Any] = frozenset()
    clears: FrozenSet[Any] = frozenset()

@dataclass(frozen=True)
class RecoveryContract:
    contract_id: str
    required_primitives: FrozenSet[str] = frozenset()
    required_goal_facts: FrozenSet[str] = frozenset()

@dataclass(frozen=True)
class CandidateValidation:
    accepted: bool
    reason_codes: Tuple[str, ...] = ()
    detail: str = ''
EffectBuilder = Callable[[Mapping[str, Any]], SymbolicEffect]
ContextualEffectBuilder = Callable[[Mapping[str, Any], FrozenSet[Any]], SymbolicEffect]

class EffectResolutionError(ValueError):

    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        super().__init__(detail)
GoalChecker = Callable[[Sequence[Mapping[str, Any]], RecoveryContract], bool]
ContractBuilder = Callable[[PromptContext], RecoveryContract]

@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    allowed_primitives: FrozenSet[str]
    primitive_argument_schema: Mapping[str, ArgumentSchema]
    primitive_symbolic_effects: Mapping[str, EffectBuilder]
    goal_checker: GoalChecker
    recovery_contract_builder: ContractBuilder
    base_contact_primitives: FrozenSet[str]
    realizer_id: str
    realizer_factory: Optional[Callable[..., Any]] = None
    prompt_vocabulary_renderer: Optional[Callable[['ScenarioSpec'], Sequence[str]]] = None
    freeze_notes: Tuple[str, ...] = ()

    def render_prompt_vocabulary(self) -> list[str]:
        if self.prompt_vocabulary_renderer is not None:
            return list(self.prompt_vocabulary_renderer(self))
        rendered = []
        for name in sorted(self.allowed_primitives):
            schema = self.primitive_argument_schema[name]
            args = sorted(set(schema.required) | set(schema.optional))
            rendered.append(f"{name}({', '.join(args)})")
        return rendered

    def build_recovery_contract(self, context: PromptContext) -> RecoveryContract:
        context.validate()
        return self.recovery_contract_builder(context)

    def validate_candidate(self, candidate: Sequence[Mapping[str, Any]], contract: Optional[RecoveryContract]=None) -> CandidateValidation:
        if not candidate:
            return CandidateValidation(False, ('EMPTY_CANDIDATE',), 'candidate is empty')
        names: list[str] = []
        for index, step in enumerate(candidate):
            if not isinstance(step, Mapping):
                return CandidateValidation(False, ('BAD_ARGUMENTS',), f'step {index} must be an object')
            name = step.get('primitive', '')
            if name not in self.allowed_primitives:
                return CandidateValidation(False, ('OFF_VOCAB',), f'step {index}: {name!r} not in {self.scenario_id}')
            schema = self.primitive_argument_schema.get(name)
            if schema is None:
                return CandidateValidation(False, ('BAD_ARGUMENTS',), f'step {index}: no argument schema for {name}')
            ok, detail = schema.validate(step.get('params', {}))
            if not ok:
                reason = 'UNRESOLVED_SPEC' if detail.startswith('schema unresolved:') else 'BAD_ARGUMENTS'
                return CandidateValidation(False, (reason,), f'step {index}: {detail}')
            names.append(str(name))
        if contract is not None:
            missing = set(contract.required_primitives) - set(names)
            if missing or not self.goal_checker(candidate, contract):
                return CandidateValidation(False, ('GOAL_INCOMPLETE',), f'candidate misses required goal step(s): {sorted(missing)}')
        return CandidateValidation(True)

def required_primitive_goal_checker(candidate: Sequence[Mapping[str, Any]], contract: RecoveryContract) -> bool:
    names = {str(step.get('primitive', '')) for step in candidate}
    return set(contract.required_primitives) <= names

def argument_schema_from_primitive(primitive_spec: Any) -> ArgumentSchema:
    """Thin adapter so ScenarioSpec and the authoritative primitive registry cannot drift."""
    schema = primitive_spec.parameter_schema
    return ArgumentSchema(required=dict(schema.required), optional=dict(schema.optional), required_any_of=tuple(schema.required_any_of), validators=dict(schema.validators), unresolved_reason=schema.unresolved_reason or primitive_spec.unresolved_reason)

def symbolic_effect_builder_from_primitive(primitive_spec: Any) -> EffectBuilder:

    def build(args: Mapping[str, Any]) -> SymbolicEffect:
        return SymbolicEffect(requires=primitive_spec.resolve_requires(args), produces=primitive_spec.resolve_produces(args), clears=primitive_spec.resolve_clears(args))
    return build

def observable_contract_builder(scenario_id: str) -> ContractBuilder:
    """Build a contract only from an observable recovery request, never expected truth."""

    def build(context: PromptContext) -> RecoveryContract:
        requested = context.observable_failure.get('required_primitives', ())
        if isinstance(requested, str):
            requested = (requested,)
        if not isinstance(requested, (list, tuple, set, frozenset)):
            requested = ()
        return RecoveryContract(contract_id=f'{scenario_id}_observable_recovery', required_primitives=frozenset((str(item) for item in requested)))
    return build

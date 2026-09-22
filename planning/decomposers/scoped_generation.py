"""Scoped generation."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from models.routing.policy import ClientTier
from models.routing.policy import CommunicationState
from controller.scope import Scope
from planning.decomposers.d_m import D_M_with_validation
from planning.decomposers.d_s import D_S_with_validation
from planning.decomposers.d_s import DecompositionFailure
from planning.decomposers.d_t import D_T
LAYER_NATIVE_KEY: Mapping[Scope, str] = {Scope.P: 'chain', Scope.T: 'chain', Scope.M: 'tasks', Scope.S: 'missions'}
REALIZABLE_SCOPES_BY_SCENARIO: Mapping[str, frozenset[Scope]] = {'psr': frozenset({Scope.P, Scope.T, Scope.M}), 'lava': frozenset({Scope.P, Scope.T}), 'construction': frozenset({Scope.P, Scope.T})}
OUTCOME_GENERATED = 'generated'
OUTCOME_PARSE_INVALID = 'parse_invalid'
OUTCOME_GOAL_INCOMPLETE = 'goal_incomplete'
OUTCOME_GATE_REJECTED = 'gate_rejected'

class ScopedGenerationError(RuntimeError):
    """The dispatcher was asked for a layer it cannot serve with the inputs given."""

    def __init__(self, message: str, *, reason_code: str='SCOPED_GENERATION_ERROR') -> None:
        super().__init__(f'{reason_code}: {message}')
        self.reason_code = reason_code

def native_key_for_scope(scope: Scope) -> str:
    try:
        return LAYER_NATIVE_KEY[Scope(scope)]
    except (KeyError, ValueError) as exc:
        raise ScopedGenerationError(f'unknown recovery scope: {scope!r}') from exc

def has_realizer_for_scope(scenario_id: str, scope: Scope) -> bool:
    try:
        registered = REALIZABLE_SCOPES_BY_SCENARIO[str(scenario_id)]
    except KeyError:
        return False
    return Scope(scope) in registered

@dataclass(frozen=True)
class ScopedCandidate:
    """One recovery candidate, tagged with the layer that produced it."""
    scope: Scope
    native_key: str
    native_artifact: Tuple[Any, ...]
    chain: Tuple[Dict[str, Any], ...]
    realizable: bool
    outcome: str
    selected_tier: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def symbolic_only(self) -> bool:
        """True when no realizer exists, so no concrete execution may be claimed."""
        return not self.realizable

    @property
    def accepted(self) -> bool:
        return self.outcome == OUTCOME_GENERATED

    def to_dict(self) -> Dict[str, Any]:
        return {'scope': Scope(self.scope).value, 'native_key': self.native_key, 'native_artifact_size': len(self.native_artifact), 'chain': [dict(step) for step in self.chain], 'realizable': self.realizable, 'symbolic_only': self.symbolic_only, 'outcome': self.outcome, 'selected_tier': self.selected_tier, 'diagnostics': dict(self.diagnostics)}

@dataclass(frozen=True)
class ScopedGenerationRequest:
    """Layer inputs. Only the fields the chosen scope needs must be populated."""
    scope: Scope
    scenario: str = 'psr'
    task: Any = None
    mission: Any = None
    scene_goal: Any = None
    environment: Mapping[str, Any] = field(default_factory=dict)
    constraints: Optional[Mapping[str, Any]] = None
    current_state: Mapping[str, Any] = field(default_factory=dict)
    agents: Sequence[Any] = ()
    failed_primitive: Optional[Mapping[str, Any]] = None
    replacement_primitive: Optional[Mapping[str, Any]] = None

def _failure_outcome(exc: DecompositionFailure) -> Tuple[str, Dict[str, Any]]:
    """Separate 'the model emitted nothing parseable' from 'it parsed but missed the goal'."""
    reasons: list[str] = []
    for attempt in exc.history or ():
        reasons.extend((str(item) for item in attempt.get('reasons') or ()))
    parse_failed = any(('did not parse' in reason for reason in reasons))
    outcome = OUTCOME_PARSE_INVALID if parse_failed else OUTCOME_GOAL_INCOMPLETE
    return (outcome, {'layer': exc.layer, 'attempts': len(exc.history or ()), 'reasons': reasons})

def _primitive_chain(request: ScopedGenerationRequest) -> Tuple[Dict[str, Any], ...]:
    """P scope is retry-or-replace of one primitive; it never calls a decomposer."""
    chosen = request.replacement_primitive or request.failed_primitive
    if not chosen:
        raise ScopedGenerationError('P-scope recovery needs failed_primitive (retry) or replacement_primitive')
    name = chosen.get('primitive') or chosen.get('primitive_name')
    if not name:
        raise ScopedGenerationError('P-scope primitive record has no primitive name')
    return ({'primitive': str(name), 'params': dict(chosen.get('params') or chosen.get('primitive_args') or {})},)

def _select_tier_client(scope: Scope, communication_state: CommunicationState | str, c_edge: Optional[Sequence[Scope]], *, base_client: Any, edge_client: Any) -> Tuple[str, Any]:
    """Choose exactly one declared generation tier; never silently fall back.

    P is a local retry/replacement and therefore needs no model.  Connected
    generation is base-tier by definition for this dispatcher.  Degraded and
    Disconnected generation must both be within the actor's explicit C_edge and
    use the local edge client.
    """
    if scope is Scope.P:
        return (ClientTier.LOCAL_RULE.value, None)
    try:
        state = communication_state if isinstance(communication_state, CommunicationState) else CommunicationState(communication_state)
    except (TypeError, ValueError) as exc:
        raise ScopedGenerationError(f'unknown communication state {communication_state!r}', reason_code='COMMUNICATION_STATE_INVALID') from exc
    if state is CommunicationState.CONNECTED:
        if base_client is None:
            raise ScopedGenerationError(f'base client is unavailable for connected {scope.value}-scope generation', reason_code='GENERATION_TIER_UNAVAILABLE')
        return (ClientTier.BASE.value, base_client)
    supported = {Scope(value) for value in c_edge or ()}
    if scope not in supported:
        raise ScopedGenerationError(f'{scope.value} is outside the actor edge capability set', reason_code='SCOPE_NOT_EDGE_CAPABLE')
    if edge_client is None:
        raise ScopedGenerationError(f'local edge client is unavailable for {state.value} {scope.value}-scope generation', reason_code='GENERATION_TIER_UNAVAILABLE')
    return (ClientTier.LOCAL_EDGE.value, edge_client)

def generate_for_scope(request: ScopedGenerationRequest, *, base_client: Any=None, edge_client: Any=None, communication_state: CommunicationState | str=CommunicationState.CONNECTED, agent_type: Optional[str]=None, c_edge: Optional[Sequence[Scope]]=None, gate: Optional[Callable[[ScopedCandidate], Tuple[bool, Sequence[str]]]]=None, d_t: Callable[..., Sequence[Any]]=D_T, d_m: Callable[..., Sequence[Any]]=D_M_with_validation, d_s: Callable[..., Sequence[Any]]=D_S_with_validation, **layer_kwargs: Any) -> ScopedCandidate:
    """Route one recovery request to the decomposer for its layer.

    ``gate``, when supplied, is applied at the candidate's *actual* layer; a rejection is
    recorded as an outcome rather than raising, so adverse results are preserved.
    """
    scope = Scope(request.scope)
    native_key = native_key_for_scope(scope)
    realizable = has_realizer_for_scope(request.scenario, scope)
    selected_tier, selected_client = _select_tier_client(scope, communication_state, c_edge, base_client=base_client, edge_client=edge_client)
    artifact: Tuple[Any, ...] = ()
    chain: Tuple[Dict[str, Any], ...] = ()
    outcome = OUTCOME_GENERATED
    diagnostics: Dict[str, Any] = {'selected_tier': selected_tier, 'communication_state': communication_state.value if isinstance(communication_state, CommunicationState) else str(communication_state), 'agent_type': agent_type, 'c_edge': sorted((Scope(value).value for value in c_edge or ()))}
    try:
        if scope is Scope.P:
            chain = _primitive_chain(request)
            artifact = chain
            diagnostics['p_mode'] = 'replacement' if request.replacement_primitive else 'retry'
        elif scope is Scope.T:
            if request.task is None:
                raise ScopedGenerationError('T-scope recovery needs a task')
            primitives = tuple(d_t(request.task, edge_llm_client=selected_client, **layer_kwargs) or ())
            artifact = primitives
            chain = tuple(({'primitive': getattr(item, 'primitive_name', None), 'params': dict(getattr(item, 'primitive_args', {}) or {})} for item in primitives))
        elif scope is Scope.M:
            if request.mission is None:
                raise ScopedGenerationError('M-scope recovery needs a mission')
            artifact = tuple(d_m(request.mission, dict(request.current_state), list(request.agents), base_llm_client=selected_client, scenario=request.scenario, **layer_kwargs) or ())
        elif scope is Scope.S:
            if request.scene_goal is None:
                raise ScopedGenerationError('S-scope recovery needs a scene goal')
            artifact = tuple(d_s(request.scene_goal, dict(request.environment), list(request.agents), dict(request.constraints) if request.constraints else None, base_llm_client=selected_client, scenario=request.scenario, **layer_kwargs) or ())
        else:
            raise ScopedGenerationError(f'unhandled scope: {scope!r}')
    except DecompositionFailure as exc:
        outcome, failure_diagnostics = _failure_outcome(exc)
        diagnostics.update(failure_diagnostics)
    if outcome == OUTCOME_GENERATED and (not artifact):
        outcome = OUTCOME_PARSE_INVALID
        diagnostics.setdefault('reasons', [f'empty {native_key}'])
    candidate = ScopedCandidate(scope=scope, native_key=native_key, native_artifact=artifact, chain=chain, realizable=realizable, outcome=outcome, selected_tier=selected_tier, diagnostics=diagnostics)
    if gate is not None and candidate.accepted:
        accepted, reason_codes = gate(candidate)
        if not accepted:
            candidate = ScopedCandidate(scope=candidate.scope, native_key=candidate.native_key, native_artifact=candidate.native_artifact, chain=candidate.chain, realizable=candidate.realizable, outcome=OUTCOME_GATE_REJECTED, selected_tier=candidate.selected_tier, diagnostics={**candidate.diagnostics, 'gate_reason_codes': [str(code) for code in reason_codes]})
    return candidate

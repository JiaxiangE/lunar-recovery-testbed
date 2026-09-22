"""Fail-closed routing policy for Connected/Degraded/Disconnected operation."""
from __future__ import annotations
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Callable
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Optional

class CommunicationState(str, Enum):
    CONNECTED = 'Connected'
    DEGRADED = 'Degraded'
    DISCONNECTED = 'Disconnected'

class PipelineStage(str, Enum):
    DIAGNOSIS = 'diagnosis'
    GENERATION = 'generation'
    VERIFICATION = 'verification'
    EXECUTION_COMMUNICATION = 'execution_communication'

class ClientTier(str, Enum):
    BASE = 'base'
    LOCAL_EDGE = 'local_edge'
    LOCAL_RULE = 'local_rule'
    LOCAL_TEMPLATE = 'local_template'
    LOCAL_SOLVER = 'local_solver'
    PEER = 'peer'
    LOCAL_EXECUTION = 'local_execution'

class PolicyAction(str, Enum):
    PROCEED = 'proceed'
    WAIT_FOR_LINK = 'wait-for-link'
    LOCAL_DEGRADE = 'local-degrade'

class CommunicationPolicyError(RuntimeError):
    reason_code = 'COMM_POLICY_FAIL'
_ALLOWED: Dict[CommunicationState, Dict[PipelineStage, FrozenSet[ClientTier]]] = {CommunicationState.CONNECTED: {PipelineStage.DIAGNOSIS: frozenset({ClientTier.BASE, ClientTier.LOCAL_EDGE, ClientTier.LOCAL_RULE}), PipelineStage.GENERATION: frozenset({ClientTier.BASE, ClientTier.LOCAL_EDGE, ClientTier.LOCAL_TEMPLATE}), PipelineStage.VERIFICATION: frozenset({ClientTier.LOCAL_SOLVER}), PipelineStage.EXECUTION_COMMUNICATION: frozenset({ClientTier.BASE, ClientTier.PEER, ClientTier.LOCAL_EXECUTION})}, CommunicationState.DEGRADED: {PipelineStage.DIAGNOSIS: frozenset({ClientTier.LOCAL_EDGE, ClientTier.LOCAL_RULE}), PipelineStage.GENERATION: frozenset({ClientTier.LOCAL_EDGE, ClientTier.LOCAL_TEMPLATE}), PipelineStage.VERIFICATION: frozenset({ClientTier.LOCAL_SOLVER}), PipelineStage.EXECUTION_COMMUNICATION: frozenset({ClientTier.PEER, ClientTier.LOCAL_EXECUTION})}, CommunicationState.DISCONNECTED: {PipelineStage.DIAGNOSIS: frozenset({ClientTier.LOCAL_RULE}), PipelineStage.GENERATION: frozenset({ClientTier.LOCAL_EDGE, ClientTier.LOCAL_TEMPLATE}), PipelineStage.VERIFICATION: frozenset({ClientTier.LOCAL_SOLVER}), PipelineStage.EXECUTION_COMMUNICATION: frozenset({ClientTier.LOCAL_EXECUTION})}}

def _state(value: CommunicationState | str) -> CommunicationState:
    try:
        return value if isinstance(value, CommunicationState) else CommunicationState(value)
    except (TypeError, ValueError) as exc:
        raise CommunicationPolicyError(f'unknown communication state: {value!r}') from exc

@dataclass
class CallCounter:
    actual_base_calls: int = 0
    actual_base_bytes: int = 0

@dataclass(frozen=True)
class RoutingEvent:
    stage: str
    communication_state: str
    action: str
    selected_tier: Optional[str]
    reason: str

@dataclass
class RoutingTrace:
    events: List[RoutingEvent] = field(default_factory=list)
    calls: CallCounter = field(default_factory=CallCounter)

    def record(self, stage: PipelineStage, state: CommunicationState, action: PolicyAction, selected_tier: Optional[ClientTier], reason: str) -> None:
        self.events.append(RoutingEvent(stage=stage.value, communication_state=state.value, action=action.value, selected_tier=selected_tier.value if selected_tier is not None else None, reason=reason))

    def to_dict(self) -> Dict[str, Any]:
        return {'events': [asdict(event) for event in self.events], 'actual_base_calls': self.calls.actual_base_calls, 'actual_base_bytes': self.calls.actual_base_bytes}

class RoutingPolicy:
    """Central authorization point; it never infers state from a base_reachable boolean."""

    def allowed_tiers(self, state: CommunicationState | str, stage: PipelineStage) -> FrozenSet[ClientTier]:
        return _ALLOWED[_state(state)][stage]

    def authorize(self, state: CommunicationState | str, stage: PipelineStage, tier: ClientTier) -> None:
        normalized = _state(state)
        if tier not in self.allowed_tiers(normalized, stage):
            raise CommunicationPolicyError(f'COMM_POLICY_FAIL: {tier.value} is forbidden for {stage.value} while {normalized.value}')

class RoutedClient:
    """Authorize before calling and measure actual base contacts from the client boundary."""

    def __init__(self, client: Any, tier: ClientTier, state: CommunicationState | str, stage: PipelineStage, policy: Optional[RoutingPolicy]=None, counter: Optional[CallCounter]=None):
        self.client = client
        self.tier = tier
        self.state = _state(state)
        self.stage = stage
        self.policy = policy or RoutingPolicy()
        self.counter = counter or CallCounter()

    def generate(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        self.policy.authorize(self.state, self.stage, self.tier)
        if self.tier is ClientTier.BASE:
            self.counter.actual_base_calls += 1
            self.counter.actual_base_bytes += len(system_prompt.encode('utf-8'))
            self.counter.actual_base_bytes += len(user_prompt.encode('utf-8'))
        response = self.client.generate(system_prompt, user_prompt, **kwargs)
        if self.tier is ClientTier.BASE:
            self.counter.actual_base_bytes += len(str(response).encode('utf-8'))
        return response

def run_diagnosis_with_policy(diagnoser: Any, failure: Any, state: CommunicationState | str, *, base_client: Any=None, local_edge_client: Any=None, policy: Optional[RoutingPolicy]=None, trace: Optional[RoutingTrace]=None) -> Any:
    """Run diagnosis without ever exposing a forbidden base client to DiagnosisModule."""
    normalized = _state(state)
    policy = policy or RoutingPolicy()
    trace = trace or RoutingTrace()
    client = None
    tier = ClientTier.LOCAL_RULE
    if normalized is CommunicationState.CONNECTED and base_client is not None:
        tier = ClientTier.BASE
        client = RoutedClient(base_client, tier, normalized, PipelineStage.DIAGNOSIS, policy, trace.calls)
    elif normalized is CommunicationState.DEGRADED and local_edge_client is not None:
        tier = ClientTier.LOCAL_EDGE
        client = RoutedClient(local_edge_client, tier, normalized, PipelineStage.DIAGNOSIS, policy, trace.calls)
    policy.authorize(normalized, PipelineStage.DIAGNOSIS, tier)
    trace.record(PipelineStage.DIAGNOSIS, normalized, PolicyAction.PROCEED, tier, 'authorized diagnosis route')
    return diagnoser.diagnose(failure, base_llm_client=client)

@dataclass(frozen=True)
class GenerationResult:
    output: Optional[str]
    generation_source: Optional[str]
    policy_action: str
    selected_tier: Optional[str]

def generate_with_policy(system_prompt: str, user_prompt: str, state: CommunicationState | str, *, base_client: Any=None, local_edge_client: Any=None, fallback_template: Optional[Callable[[], str]]=None, policy: Optional[RoutingPolicy]=None, trace: Optional[RoutingTrace]=None) -> GenerationResult:
    """Select an authorized generator or emit an explicit wait/degrade result."""
    normalized = _state(state)
    policy = policy or RoutingPolicy()
    trace = trace or RoutingTrace()
    selected: Optional[tuple[ClientTier, Any]] = None
    if normalized is CommunicationState.CONNECTED and base_client is not None:
        selected = (ClientTier.BASE, base_client)
    elif normalized is not CommunicationState.DISCONNECTED and local_edge_client is not None:
        selected = (ClientTier.LOCAL_EDGE, local_edge_client)
    elif normalized is CommunicationState.DISCONNECTED and local_edge_client is not None:
        selected = (ClientTier.LOCAL_EDGE, local_edge_client)
    if selected is not None:
        tier, client = selected
        routed = RoutedClient(client, tier, normalized, PipelineStage.GENERATION, policy, trace.calls)
        output = routed.generate(system_prompt, user_prompt)
        trace.record(PipelineStage.GENERATION, normalized, PolicyAction.PROCEED, tier, 'authorized generator route')
        return GenerationResult(output, 'llm', PolicyAction.PROCEED.value, tier.value)
    if fallback_template is not None:
        policy.authorize(normalized, PipelineStage.GENERATION, ClientTier.LOCAL_TEMPLATE)
        output = fallback_template()
        trace.record(PipelineStage.GENERATION, normalized, PolicyAction.LOCAL_DEGRADE, ClientTier.LOCAL_TEMPLATE, 'no authorized model client; explicit template fallback')
        return GenerationResult(output, 'fallback_template', PolicyAction.LOCAL_DEGRADE.value, ClientTier.LOCAL_TEMPLATE.value)
    trace.record(PipelineStage.GENERATION, normalized, PolicyAction.WAIT_FOR_LINK, None, 'no authorized local generator')
    return GenerationResult(None, None, PolicyAction.WAIT_FOR_LINK.value, None)

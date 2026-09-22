"""Local routing."""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import asdict
from models.generation import HierarchyModelSession
from models.generation import generation_messages_v3
from models.context_encoding import render_messages
from planning.repair.output_contract import VERSION
from domains.transition import binding_observation

class RoutingRejected(RuntimeError):
    failure_category = 'communication_rejected'

class StaleResponse(RuntimeError):
    failure_category = 'stale_response'

class ServiceNotReady(RoutingRejected):
    failure_category = 'service_unavailable'

class ContextCapacityError(RuntimeError):
    failure_category = 'context_capacity_exceeded'

class LinkState:
    """Radio/event adapters call update; revision detects disconnect/reconnect."""

    def __init__(self, state):
        self.state, self.revision = (state, 0)

    def update(self, state):
        if state != self.state:
            self.state, self.revision = (state, self.revision + 1)

    def __call__(self):
        return {'state': self.state, 'revision': self.revision}

@dataclass(frozen=True)
class Service:
    role: str
    model: str
    host: str
    endpoint: str
    weights: str
    quantization: str
    backend: str
    context_tokens: int
    output_tokens: int
    observed_ready: bool = False
    placement: str = 'workstation'

    def __post_init__(self):
        if self.role not in {'edge', 'base'}:
            raise ValueError('service role must be explicit edge or base')
        if type(self.context_tokens) is not int or type(self.output_tokens) is not int or (not 0 < self.output_tokens < self.context_tokens):
            raise ValueError('positive output reservation must fit inside the configured context')

def route(*, scenario, actor, operation, repair_scope, communication, edge_scopes, services, actor_type=None):
    if any((key not in {'edge', 'base'} or service.role != key for key, service in services.items())):
        raise RoutingRejected('service registry role mismatch; endpoint location cannot substitute a role')

    def configured(role):
        if role not in services:
            raise RoutingRejected(f'no configured {role} service; substitution forbidden')
        return services[role]
    if operation == 'diagnosis':
        if communication != 'Connected':
            raise RoutingRejected('base diagnosis prohibited without Connected link')
        return configured('base')
    if operation == 'edge_diagnosis':
        if scenario != 'psr' or actor_type != 'ROVER' or 'T' not in edge_scopes:
            raise RoutingRejected('edge diagnosis is enabled only for the audited PSR rover interface')
        return configured('edge')
    if repair_scope in {'M', 'S'} and communication != 'Connected':
        raise RoutingRejected('base-supervised repair scope no longer authorized')
    actor_type = actor_type or {'rover_1': 'ROVER', 'rover_2': 'ROVER', 'transport_1': 'TRANSPORT', 'manipulator_1': 'MANIPULATOR'}.get(actor)
    seven_b_actor = scenario in {'psr', 'lava'} and actor_type == 'ROVER' or (scenario == 'construction' and actor_type in {'TRANSPORT', 'MANIPULATOR'})
    if operation == 'D_T' and 'T' in edge_scopes and seven_b_actor:
        return configured('edge')
    if operation in {'flat', 'translation'} and repair_scope in {'P', 'T'} and ('T' in edge_scopes) and seven_b_actor:
        return configured('edge')
    if communication != 'Connected':
        raise RoutingRejected('no verified local service for this actor/scope; base substitution forbidden')
    return configured('base')

class RoutedHierarchySession(HierarchyModelSession):
    """client_factory(service, ledger) must use the shared ledger and exact meter.

    The callback is the only generation transport. It is supplied by the expert;
    offline tests use the existing instrumented client with a stub transport.
    """

    def __init__(self, case, trial_id, logical_cap, *, services, client_factory, communication, records_path=None, share_context=True, execution_host=None, message_builder=generation_messages_v3, task_action_view='view'):
        super().__init__(trial_id, {}, logical_cap=logical_cap, request_profile='recovery_ready_v2', output_contract_version=VERSION, records_path=records_path)
        self.case, self.services, self.client_factory = (case, services, client_factory)
        del self.client, self.transport
        self.communication, self.share_context = (communication, share_context)
        self.execution_host = execution_host
        self.message_builder = message_builder
        self.interface_version = getattr(message_builder, 'interface_version', None)
        if task_action_view not in {'view', 'full'}:
            raise ValueError('unknown Task view')
        self.task_action_view = task_action_view
        self.goal_alignment_enabled = getattr(message_builder, 'goal_alignment_enabled', False)
        self.prompt_cap = max((s.context_tokens - s.output_tokens for s in services.values()))
        self.completion_cap = max((s.output_tokens for s in services.values()))
        from models.clients import RunCallBudget
        from models.clients import CallLedger
        total = logical_cap * 2 * (self.prompt_cap + self.completion_cap)
        self.run_budget = RunCallBudget(max_logical_calls=logical_cap, max_physical_attempts=logical_cap * 2, max_total_tokens=total)
        self.ledger = CallLedger(max_logical_calls_total=logical_cap, max_physical_attempts_total=logical_cap * 2, max_total_tokens=total, run_budget=self.run_budget)
        self.clients = {}
        self.routes = []
        self.repair_context = {}

    def set_repair_context(self, scope, actor):
        self.repair_context = {'repair_scope': scope, 'repair_actor': actor}

    def request(self, operation, problem, node, feedback, original_information, *, constraints=None):
        if self.logical_n >= self.logical_cap:
            raise RuntimeError('trial model ceiling exhausted')
        from planning.repair.domain_hierarchy import goal_record

        def current_request():
            return {'goal': goal_record(problem), 'node': node.model_dump(mode='json') if node else None, 'original_information': original_information, 'repair_context': self.repair_context, 'problem_contract': problem, 'case_policy_problems': list(self.case.goals())}
        frozen_request = deepcopy(current_request())
        before = binding_observation(self.case.world, problem.scenario_id)
        info = self.repair_context
        scope = info.get('repair_scope')
        link = deepcopy(self.communication())
        if not isinstance(link, dict) or 'state' not in link or 'revision' not in link:
            raise RoutingRejected('communication observer must provide state and revision for stale-reply detection')
        if scope is None:
            from controller.diagnosis import DiagnosisModule
            from controller.selector import ScopeSelector
            from controller.settings import frozen_weights
            from controller.settings import frozen_p_fix_params
            from planning.repair.domain_hierarchy import goal_record
            if self.case.fallback is not None and goal_record(problem) == goal_record(self.case.fallback):
                scope = 'S'
            else:
                posterior = DiagnosisModule().diagnose(self.case.failure, base_llm_client=None)
                decision = ScopeSelector(frozen_weights(), frozen_p_fix_params()).select(self.case.failure, posterior, {}, self.case.world.c_edge_for(self.case.failure.agent_id), base_reachable=link['state'] == 'Connected')
                scope = decision.sigma.value
        actor = getattr(node, 'assigned_agent_id', None) or info.get('repair_actor', self.case.failure.agent_id)
        edge = {s.value for s in self.case.world.c_edge_for(actor)}
        state = link['state']
        service = route(scenario=problem.scenario_id, actor=actor, operation=operation, repair_scope=scope, communication=state, edge_scopes=edge, services=self.services, actor_type=problem.observation['actor_types'].get(actor))
        if not service.observed_ready:
            raise ServiceNotReady('target model/assets/memory readiness has not been observed')
        constraints = {**deepcopy(constraints or {}), 'prompt_cap_candidate': service.context_tokens - service.output_tokens, 'completion_reservation': service.output_tokens, 'service_role': service.role, 'service_model': service.model, 'runtime_routing': deepcopy(info)}
        if self.goal_alignment_enabled:
            from planning.repair.scene_alignment import request_alignment
            constraints['scene_alignment'] = request_alignment(problem, node, original_information, scope)
        system, user = self.message_builder(operation, problem, node, feedback, original_information, constraints or {})
        if self.share_context:
            system, user = render_messages(system, user, task_action_view=self.task_action_view)
        record = {'ordinal': len(self.requests) + 1, 'operation': operation, 'system': system, 'user': user, 'route': {**asdict(service), 'repair_scope': scope, 'generated_layer': operation, 'actor': actor, 'communication': state, 'communication_revision': link['revision'], 'execution_host': self.execution_host, 'onboard_inference': service.placement == 'orin_onboard' and service.host == self.execution_host}, 'response': None}
        return self._invoke(service, link, before, record, request_current=lambda: current_request() == frozen_request)

    def _invoke(self, service, link, before, record, *, request_current=None):
        system, user = (record['system'], record['user'])
        self.requests.append(record)
        try:
            if self.communication() != link:
                raise RoutingRejected('communication changed before send')
            client = self.clients.setdefault(service.role, self.client_factory(service, self.ledger)) if service.role not in self.clients else self.clients[service.role]
            if getattr(client, 'call_ledger', None) is not self.ledger or not hasattr(client, 'posted_physical_ids'):
                raise TypeError('role client must share the trial ledger and guarded transport post accounting')

            def guard():
                continuous = getattr(self, 'continuous_control', None)
                if continuous is not None:
                    continuous.checkpoint('request_guard', expected=link['revision'])
                if self.communication() != link:
                    raise RoutingRejected('communication changed during metering/before transport')
                if request_current is not None and (not request_current()):
                    raise StaleResponse('request goal/node/original candidate changed before transport')
                if binding_observation(self.case.world, self.case.nominal.scenario_id) != before:
                    raise StaleResponse('world changed before transport')
            client.request_guard = guard
            continuous = getattr(self, 'continuous_control', None)
            if continuous is not None:
                client.transport_entry = lambda: continuous.transport_entry(service.role, link['revision'], record, client.call_ledger.physical_attempts[-1].physical_attempt_id)
                client.request_timeout_provider = lambda: max(0.01, continuous.limit - continuous.elapsed())

            def before_transport():
                record.update(logical_call_id=client.last_logical_call_id, request_measurement=deepcopy(client.last_request_measurement), tokenizer_measurement=deepcopy(client.server_counter.last), transport_pending=True)
                self.persist_records()
            client.before_transport = before_transport
            client.seed = self.case.seed
            client.last_provider_response = None
            client.last_cache_telemetry = None
            client.server_counter.last = None
            raw = client.generate(system, user)
            record.update(response=raw, logical_call_id=client.last_logical_call_id, transport_pending=False, provider_response=deepcopy(getattr(client, 'last_provider_response', None)), request_measurement=deepcopy(client.last_request_measurement), tokenizer_measurement=deepcopy(getattr(getattr(client, 'server_counter', None), 'last', None)), cache_telemetry=deepcopy(getattr(client, 'last_cache_telemetry', None)))
            self.persist_records()
            if continuous is not None:
                continuous.record('response_saved', ordinal=record['ordinal'], operation=record['operation'])
                continuous.checkpoint('response_adoption', expected=link['revision'])
            if self.communication() != link or binding_observation(self.case.world, self.case.nominal.scenario_id) != before or (request_current is not None and (not request_current())):
                raise StaleResponse('saved response belongs to stale communication/state; no dispatch')
            if continuous is not None:
                continuous.record('response_adopted', ordinal=record['ordinal'], operation=record['operation'])
            return raw
        except Exception as exc:
            if 'client' in locals():
                record['tokenizer_measurement'] = deepcopy(getattr(getattr(client, 'server_counter', None), 'last', None))
                record['provider_response'] = deepcopy(getattr(client, 'last_provider_response', None))
            record['failure'] = type(exc).__name__ + ': ' + str(exc)
            record['transport_pending'] = False
            self.persist_records()
            raise

    def diagnosis_client(self, *, tier='base'):
        """Explicit optional diagnosis adapter; default remains base/Connected.

        If enabled in a new experiment, budget its call before generation bounds;
        this does not silently add a call to the historical method.
        """
        session = self
        if tier not in {'base', 'edge'}:
            raise ValueError('explicit diagnosis tier required')

        class Adapter:

            def generate(self, system, user):
                link = deepcopy(session.communication())
                if not isinstance(link, dict) or 'state' not in link or 'revision' not in link:
                    raise RoutingRejected('diagnosis needs observed communication and revision')
                if tier == 'base' and link.get('state') != 'Connected':
                    raise RoutingRejected('base diagnosis blocked by current communication')
                if session.logical_n >= session.logical_cap:
                    raise RuntimeError('diagnosis shares the trial call ceiling')
                actor = session.case.failure.agent_id
                operation = 'edge_diagnosis' if tier == 'edge' else 'diagnosis'
                service = route(scenario=session.case.nominal.scenario_id, actor=session.case.failure.agent_id, operation=operation, repair_scope=None, communication=link['state'], edge_scopes={s.value for s in session.case.world.c_edge_for(actor)}, services=session.services, actor_type=session.case.nominal.observation['actor_types'].get(actor))
                if not service.observed_ready:
                    raise ServiceNotReady('diagnosis service not observed ready')
                record = {'ordinal': len(session.requests) + 1, 'operation': operation, 'system': system, 'user': user, 'route': {**asdict(service), 'communication': link['state'], 'communication_revision': link['revision'], 'onboard_inference': service.placement == 'orin_onboard' and service.host == session.execution_host, 'execution_host': session.execution_host}, 'response': None}
                before = binding_observation(session.case.world, session.case.nominal.scenario_id)
                return session._invoke(service, link, before, record)
        return Adapter()

    def records(self):
        record = super().records()
        physical = self.ledger.physical_attempts
        posted = set().union(*(getattr(c, 'posted_physical_ids', set()) for c in self.clients.values()))
        actual = [a for a in physical if a.measurement_provenance == 'actual' and a.physical_attempt_id in posted]
        known = sum((a.usage.total_tokens for a in actual if type(a.usage.total_tokens) is int))
        unknown = sum((type(a.usage.total_tokens) is not int for a in actual))
        record.update(evidence_class='role_routed_generation; inspect transport provenance', actual_api_calls=sum((len(getattr(c, 'posted_logical_ids', ())) for c in self.clients.values() if getattr(c, '_measurement_provenance', None) == 'actual')), actual_physical_posts=sum((getattr(c, 'transport_posts', 0) for c in self.clients.values() if getattr(c, '_measurement_provenance', None) == 'actual')), actual_provider_tokens=known if not unknown else None, known_provider_tokens=known, synthetic_ledger_tokens=sum((a.usage.total_tokens for a in physical if a.measurement_provenance != 'actual' and type(a.usage.total_tokens) is int)), unknown_usage_physical_n=unknown)
        return record

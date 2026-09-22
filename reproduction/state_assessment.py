"""One independent cross_actor state-assessment/plan diagnostic, one physical post."""
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import MethodType
from reproduction.request_replay import read
from reproduction.request_replay import save
from reproduction.request_replay import services
from reproduction.request_replay import ReplaySession
from models.grounded_client import SchemaServerCounter
from models.local_client import build_client
from models.local_client import http_json
from models.local_routing import RoutedHierarchySession
from models.local_routing import LinkState
from models.clients import CallLedger
from models.clients import RunCallBudget
VERSION = 'cross_actor_state_plan_basis_v1'
SOURCE = Path('data/local_repair/state_assistance/assessment_request.json')

def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

def diagnostic_schema(original):
    names = {'type': 'array', 'items': {'type': 'string'}}
    assessment = obj({'rover_1_position_xyz': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3}, 'sample_1_owner': {'type': 'string'}, 'sample_1_status': {'type': 'string'}, 'current_task_delivery_samples': deepcopy(names), 'offload_preconditions': obj({'satisfied': {'type': 'boolean'}, 'missing_positive': deepcopy(names), 'violated_negative': deepcopy(names)})})
    chain = deepcopy(original['oneOf'][0]['properties']['chain'])
    basis = {'type': 'string'}
    normal = obj({'state_assessment': assessment, 'chain': chain, 'brief_plan_basis': basis})
    declined = obj({'state_assessment': deepcopy(assessment), 'decline': {'type': 'boolean', 'enum': [True]}, 'brief_plan_basis': deepcopy(basis)})
    return {'oneOf': [normal, declined]}

def request_messages():
    q = read(SOURCE)['requests'][0]
    p = json.loads(q['user'])
    schema = diagnostic_schema(q['response_format']['json_schema']['schema'])
    p['output_schema'] = deepcopy(schema)
    p['generation_constraints'].update(transport_retry_upper=0, remaining_physical_capacity=1, remaining_token_reservation=65536, decline_shape={'state_assessment': 'same schema', 'decline': True, 'brief_plan_basis': 'brief refusal basis; not an impossibility proof'})
    p['state_and_plan_diagnostic'] = {'version': VERSION, 'questions': ['From request_initial_facts and the supplied current world, report rover_1 position in x,y,z order.', 'Report the current owner and status of sample_1.', 'List the samples which this current Task requires delivering; distinguish the separately reported parent goal.', 'For sample_offload by the bound Task actor with the supplied domain parameters, are its preconditions satisfied in the actual request initial state? List every missing positive and violated negative precondition.', 'Generate a complete chain for this same Task from the actual initial state, retaining all actor permissions and obligations.', 'After the chain, give a brief plan basis: the initial facts, goal and key preconditions you used. If the chain uses collect/store/move, summarize why those actions are necessary. You may describe differences from the rejected reference.'], 'format': 'One JSON object. Use state_assessment, chain, brief_plan_basis in that order. Position is an ordered x,y,z array; sample/predicate arrays are unordered sets without duplicates. Each field type and required key is in output_schema. A refusal uses state_assessment, decline:true, brief_plan_basis instead.', 'basis_limit': 'One to three concise sentences, not private reasoning or a token-by-token thought trace; not a report of the prior run internal process.', 'execution_boundary': 'Only the chain enters the existing strict parser, gate, resource forecast and executor. Assessment and brief_plan_basis never change the world, scope or candidate.'}
    system = 'Independent state-assessment and plan diagnostic. Return only the new output_schema envelope with state_assessment first, the chain second, and brief_plan_basis last. Provide a brief decision summary, not hidden chain-of-thought. All state questions refer to actual request_initial_facts, not the rejected candidate simulation. The outer diagnostic envelope replaces the old chain-only envelope for this request only; its chain items retain the original strict interface. ' + q['system']
    fmt = {'type': 'json_schema', 'json_schema': {'name': VERSION, 'strict': True, 'schema': schema}}
    return (system, json.dumps(p, ensure_ascii=False, separators=(',', ':')), fmt)

def diagnostic_client(service, ledger, *, transport=None, http=http_json):
    c = build_client(service, ledger, transport=transport, http=http, counter_factory=SchemaServerCounter)
    c.max_transport_retries = 0
    c.temperature = 0
    c.timeout_s = 21600
    c.extra_body = {}
    c.prompt_version = VERSION
    original = c.generate

    def generate(self, system, user, **kw):
        expected_system, expected_user, fmt = request_messages()
        if system != expected_system or user != expected_user:
            raise ValueError('diagnostic request changed')
        return original(system, user, response_format=fmt, **kw)
    c.generate = MethodType(generate, c)
    return c

class DiagnosticSession(RoutedHierarchySession):

    def _invoke(self, service, link, before, record, **kw):
        record['response_format'] = request_messages()[2]
        record['decoding_profile'] = VERSION
        return super()._invoke(service, link, before, record, **kw)

class DiagnosticReplay(ReplaySession):

    def _invoke(self, service, link, before, record, **kw):
        q = self.saved['requests'][self.replayed]
        if q['response_format'] != request_messages()[2] or q['decoding_profile'] != VERSION:
            raise ValueError('diagnostic response schema changed')
        raw = super()._invoke(service, link, before, record, **kw)
        self.checks[-1]['response_format_equal'] = True
        self.persist_records()
        return raw

def make_session(case, path, *, source=None, client_override=None):

    def messages(operation, problem, node, feedback, information, constraints):
        system, user, _ = request_messages()
        p = json.loads(user)
        from planning.repair.domain_hierarchy import goal_record
        if operation != 'D_T' or goal_record(problem) != p['goal'] or sorted(problem.initial_state) != p['request_initial_facts'] or (node.id != p['node']['id']):
            raise ValueError('diagnostic context mismatch')
        return (system, user)
    from models.literal_actions import task_messages
    messages.interface_version = task_messages('full').interface_version
    messages.goal_alignment_enabled = True

    def client(service, ledger):
        if source is not None:
            raise RuntimeError('no replay transport')
        if service.role != 'edge' or service.model != 'qwen-7b-local':
            raise RuntimeError('7B only')
        c = client_override(service, ledger) if client_override else diagnostic_client(service, ledger)
        if c.max_transport_retries != 0:
            raise ValueError('zero transport retries required')
        return c
    cls = DiagnosticReplay if source else DiagnosticSession
    session = cls(case, VERSION, 1, services={'edge': services()['edge']}, client_factory=client, communication=LinkState(case.communication), records_path=path, execution_host=socket.gethostname(), share_context=False, message_builder=messages, task_action_view='full', **{'source_calls': source} if source else {})
    session.run_budget = RunCallBudget(max_logical_calls=1, max_physical_attempts=1, max_total_tokens=65536)
    session.ledger = CallLedger(max_logical_calls_total=1, max_physical_attempts_total=1, max_total_tokens=65536, run_budget=session.run_budget)
    session.set_repair_context('T', 'rover_1')
    return session

def extract_chain(raw):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(value, dict):
        return raw
    if 'chain' in value:
        return json.dumps({'chain': value['chain']}, ensure_ascii=False)
    if value.get('decline') is True:
        return json.dumps({'decline': True, 'reason': value.get('brief_plan_basis', '')})
    return raw

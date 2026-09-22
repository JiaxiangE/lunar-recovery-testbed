"""L1 one-shot state planning; L2 initial-only support iff L1 validly failed."""
import json
from pathlib import Path
from types import MethodType
import reproduction.state_assessment as base
from reproduction.request_replay import read
from reproduction.request_replay import save
from reproduction.request_replay import services
from models.grounded_client import SchemaServerCounter
from models.local_client import build_client
from models.local_client import http_json
from planning.repair.state_planning_guidance import augment
from planning.repair.state_planning_guidance import initial_support
ROOT = Path('data/local_repair/state_assistance')
SOURCE = Path('data/local_repair/state_assistance/input_request.json')

def request_messages(root, layer):
    prep = read(Path(root) / layer / 'preparation.json')
    q = read(SOURCE)['requests'][0]
    if prep['initial_support'] is not None:
        if prep['support_initial_facts'] != json.loads(q['user'])['request_initial_facts']:
            raise ValueError('initial action list bound to different state')
    system, user = augment(q['system'], q['user'], enabled=True, initial=prep['initial_support'])
    return (system, user, q['response_format'])

def client_for(root, layer, service, ledger, *, transport=None, http=http_json):
    c = build_client(service, ledger, transport=transport, http=http, counter_factory=SchemaServerCounter)
    c.max_transport_retries = 0
    c.temperature = 0
    c.timeout_s = 21600
    c.extra_body = {}
    c.prompt_version = 'bounded_state_planning_' + layer
    original = c.generate

    def generate(self, system, user, **kw):
        expected_system, expected_user, fmt = request_messages(root, layer)
        if system != expected_system or user != expected_user:
            raise ValueError('prepared planning request changed')
        return original(system, user, response_format=fmt, **kw)
    c.generate = MethodType(generate, c)
    return c

def make_session(root, layer, case, path, *, source=None, client_override=None):

    def client(service, ledger):
        return client_override(service, ledger) if client_override else client_for(root, layer, service, ledger)
    session = base.make_session(case, path, source=source, client_override=client)
    original = session.message_builder

    def messages(*args, **kw):
        original(*args, **kw)
        return request_messages(root, layer)[:2]
    messages.interface_version = original.interface_version
    messages.goal_alignment_enabled = original.goal_alignment_enabled
    session.message_builder = messages
    session.trial_id = 'bounded_state_planning_' + layer
    return session

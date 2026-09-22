"""Grounded requests."""
import json
from pathlib import Path
import socket
from models.literal_actions import task_messages
from reproduction.request_replay import services
from reproduction.request_replay import read
from reproduction.request_replay import save
from reproduction.request_replay import ReplaySession
from models.grounded_client import GroundedSchemaSession
from models.grounded_client import build_schema_client
from models.local_routing import LinkState
from models.services import health
from models.services import sampling_for
from planning.repair.grounded_decoding import response_format_from_packet
from planning.repair.grounded_decoding import accepts
from planning.repair.grounded_decoding import VERSION
ROOT = Path('data/local_repair/task_generation')

class SchemaReplaySession(ReplaySession):

    def _invoke(self, service, link, before, record, **kw):
        fmt = response_format_from_packet(json.loads(record['user']))
        source = self.saved['requests'][self.replayed]
        if source.get('response_format') != fmt or source.get('decoding_profile') != VERSION:
            raise ValueError('replay decoder/schema mismatch')
        raw = super()._invoke(service, link, before, record, **kw)
        self.checks[-1]['response_format_equal'] = True
        self.persist_records()
        return raw

def factory(folder, source=None, client_override=None):

    def make(case, view):

        def client(service, ledger):
            if source is not None:
                raise RuntimeError('no transport fallback during CPU replay')
            if service.role != 'edge' or service.model != 'qwen-7b-local':
                raise RuntimeError('original 7B edge only')
            c = client_override(service, ledger) if client_override else build_schema_client(service, ledger)
            c.timeout_s = 21600
            c.temperature = sampling_for(service)['temperature']
            c.extra_body = {k: v for k, v in sampling_for(service).items() if k != 'temperature'}
            return c
        cls = SchemaReplaySession if source is not None else GroundedSchemaSession
        return cls(case, VERSION + '-' + case.cell_id, 1, services=services(), client_factory=client, communication=LinkState(case.communication), records_path=Path(folder) / 'calls.jsonl', execution_host=socket.gethostname(), share_context=False, message_builder=task_messages(view), task_action_view=view, **{'source_calls': source} if source is not None else {})
    return make

def schema_result(row, calls, source=None):
    data = read(source) if source else calls
    if not data.get('requests'):
        return {'complete': False, 'reason': 'no response'}
    q = data['requests'][0]
    fmt = q['response_format']
    response = q.get('provider_response') or {}
    finish = (response.get('choices') or [{}])[0].get('finish_reason')
    if not isinstance(row.get('raw_response'), str):
        return {'complete': False, 'finish_reason': finish}
    try:
        value = json.loads(row['raw_response'])
    except json.JSONDecodeError:
        return {'complete': False, 'finish_reason': finish, 'backend_inspection_required': finish == 'stop'}
    valid = accepts(fmt['json_schema']['schema'], value)
    return {'complete': True, 'schema_accepted': valid, 'finish_reason': finish, 'backend_inspection_required': not valid and finish == 'stop'}

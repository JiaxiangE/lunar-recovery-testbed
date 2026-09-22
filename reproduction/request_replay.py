"""Final translation cohort and symmetric single-request actor-interface study.

All model posts are explicit run commands. Replay reconstructs complete requests
through the real routed session and never constructs a transport client.
"""
from dataclasses import asdict
import json
from pathlib import Path
from models.local_routing import RoutedHierarchySession
from models.local_routing import Service
from models.local_routing import LinkState
from models.services import build_client
from models.services import health
from models.services import sampling_for
from models.services import is_vllm
from models.task_messages import generation_messages_v7
from reproduction.serialization import CELLS
from reproduction.serialization import write_json
from planning.repair.actor_interface import VERSION as ACTOR_VERSION
OLD = Path('data/main_comparison/return_cases')
VIEW_VERSION = 'task_view_canonical_actor_v1'

def read(p):
    from reproduction.records import record_path
    return json.loads(record_path(p).read_text(encoding='utf-8'))

def save(p, v):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, v)

def services():
    return {k: Service(**v) for k, v in read(OLD / 'local_confirmation/design.json')['services'].items()}

class ReplaySession(RoutedHierarchySession):
    """Strict full-system/user and service/route replay; no extra response copy."""

    def __init__(self, *args, source_calls, **kwargs):
        self.replayed = 0
        self.checks = []
        self.source_calls = str(source_calls)
        self.saved = read(source_calls)
        super().__init__(*args, **kwargs)

    def request(self, operation, problem, node, feedback, original_information, *, constraints=None):
        from reproduction.identity import historical_context
        problem, original_information = historical_context(problem, original_information)
        return super().request(operation, problem, node, feedback, original_information, constraints=constraints)

    @property
    def logical_n(self):
        return super().logical_n + self.replayed

    def _invoke(self, service, link, before, record, **kwargs):
        if self.replayed >= len(self.saved['requests']):
            raise RuntimeError('saved answers exhausted; no model fallback')
        q = self.saved['requests'][self.replayed]
        from reproduction.identity import require_same_request
        require_same_request(q, record)
        for key in (*asdict(service), 'communication', 'communication_revision', 'repair_scope', 'actor'):
            if q['route'].get(key) != record['route'].get(key):
                raise ValueError('route mismatch: ' + key)
        if not isinstance(q.get('response'), str) or q.get('transport_pending') or q.get('failure'):
            raise ValueError('only complete adopted provider answers are reusable')
        attempts = [a for a in self.saved['physical_attempts'] if a['logical_call_id'] == q['logical_call_id']]
        if not attempts:
            raise ValueError('saved usage/transport provenance missing')
        for a in attempts:
            if a['seed'] != self.case.seed or a['temperature'] != sampling_for(service)['temperature'] or a['max_tokens'] != service.output_tokens:
                raise ValueError('sampling or output allowance differs')
        self.replayed += 1
        self.checks.append({'ordinal': q['ordinal'], 'complete_messages_equal': True, 'route_equal': True, 'sampling_and_output_cap_equal': True, 'source_logical_id': q['logical_call_id']})
        self.persist_records()
        return q['response']

    def records(self):
        out = super().records()
        out.update(replayed_source=self.source_calls, strict_replay_checks=self.checks, new_model_calls=0, source_logical_requests_consumed=self.replayed)
        return out

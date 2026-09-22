"""Three explicit modes: offline CPU evidence, new local generation, Isaac ROS."""
import json
from pathlib import Path

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def local(args):
    """One explicitly selected fresh local request, never a replay fallback."""
    from urllib.parse import urlsplit
    from models.local_routing import Service
    from models.local_routing import LinkState
    from models.local_routing import RoutedHierarchySession
    from models.literal_actions import task_messages
    from planning.repair.grounded_decoding import response_format_from_packet
    from models.grounded_client import GroundedSchemaSession
    from examples.task_requests import task_input
    from examples.task_requests import evaluate_unit
    from models.services import health
    from models.clients import CallLedger
    from models.clients import RunCallBudget
    profile = read(args.service_profile)
    service = Service(**profile)
    if service.role != 'edge' or service.model != 'qwen-7b-local' or service.context_tokens != 65536 or (service.output_tokens != 4096) or (urlsplit(service.endpoint).hostname not in ('127.0.0.1', 'localhost')):
        raise ValueError('this entry supports only the declared local 7B/64K/4K edge profile')
    h = health(service)
    if not h['model_present'] or h['context_observed'] != 65536:
        raise RuntimeError('actual service mismatch')
    if args.output.exists():
        raise FileExistsError('fresh output directory required')

    def make(case, view):
        builder = task_messages(view)

        def client(s, ledger):
            from models.local_client import build_client
            from models.grounded_client import SchemaServerCounter
            from types import MethodType
            c = build_client(s, ledger, counter_factory=SchemaServerCounter)
            c.max_transport_retries = 0
            c.temperature = 0
            c.prompt_version = 'public_local_grounded_task'
            gen = c.generate
            c.generate = MethodType(lambda self, system, user, **kw: gen(system, user, response_format=response_format_from_packet(json.loads(user)), **kw), c)
            return c
        session = GroundedSchemaSession(case, 'public_local_single', 1, services={'edge': service}, client_factory=client, communication=LinkState(case.communication), records_path=args.output / 'calls.jsonl', share_context=False, message_builder=builder, task_action_view=view)
        session.run_budget = RunCallBudget(max_logical_calls=1, max_physical_attempts=1, max_total_tokens=65536)
        session.ledger = CallLedger(max_logical_calls_total=1, max_physical_attempts_total=1, max_total_tokens=65536, run_budget=session.run_budget)
        return session
    row, calls = evaluate_unit(args.cell, args.arm, make)
    row['chain_parser_input'] = row.pop('raw_response', None)
    row['raw_response'] = (calls.get('requests') or [{}])[0].get('response')
    save(args.output / 'trial.json', row)
    save(args.output / 'health.json', h)
    print(json.dumps({'status': row['status'], 'Task': row['task_goal_met'], 'parent': row['parent_goal_met']}))

import argparse
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--service-profile', type=Path, required=True)
    p.add_argument('--cell', choices=('B05_psr_strict_disconnected', 'E05_lava_communication_return', 'cross_actor'), required=True)
    p.add_argument('--arm', choices=('D_T-full', 'D_T-view', 'flat-full'), required=True)
    p.add_argument('--output', type=Path, required=True)
    local(p.parse_args())
if __name__ == '__main__':
    main()

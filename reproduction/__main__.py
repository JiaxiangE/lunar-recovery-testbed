"""Recompute submitted-paper results using packaged records and CPU execution."""
import argparse
import json
from pathlib import Path
import sys

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tables-only', action='store_true')
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError('select a new output directory')
    a.output.mkdir(parents=True)
    import z3
    if z3.get_version_string() != '5.1.0':
        raise RuntimeError('paper checks require Z3 5.1.0')

    def forbid_network(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('paper reproduction is offline')
    sys.addaudithook(forbid_network)
    from reproduction.tables import write_tables
    tables = write_tables(a.output / 'tables')
    result = {'known_main_tokens': tables['main']['known_tokens_lower_bound'], 'scope_decisions': tables['scope_S2']['paper_denominators'], 'new_model_calls': 0}
    if not a.tables_only:
        from reproduction.replay import tasks
        replay = tasks(a.output / 'task_replay')
        (a.output / 'replay.json').write_text(json.dumps(replay, indent=2) + '\n', encoding='utf-8')
        result['saved_task_requests_replayed'] = len(replay['records'])
    from integrations.isaac.saved_evidence import verify
    result['isaac_saved_binding'] = verify()
    (a.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
if __name__ == '__main__':
    main()

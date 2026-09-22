"""Read the fixed six units; independently replay dispatches without model calls."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json
from reproduction.communication_inputs import OUTPUT
from reproduction.communication_inputs import CELLS
from reproduction.communication_inputs import source
from reproduction.costs import cost
from reproduction.costs import plus
from execution.continuous_link import ctmc
from domains.common import observe

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def build(root):
    folder = root / OUTPUT
    d = read(folder / 'design.json')
    rows = []
    total = cost({})
    first = {}
    seen_physical = set()
    seen_logical = set()
    for cell, condition in d['schedule']:
        path = folder / cell / condition / 'trial.json'
        cp = path.parent / 'calls.jsonl'
        if not path.exists():
            rows.append({'cell': cell, 'condition': condition, 'status': 'no_terminal_record', 'parent_goal_met': None, 'source_trial': None, 'source_calls': cp.relative_to(root).as_posix() if cp.exists() else None, 'cost_status': 'not yet included in terminal-unit totals; pending usage unknown'})
            continue
        t = read(path)
        calls = read(cp) if cp.exists() else {}
        qs = calls.get('requests', [])
        events = t['events']
        history = t['actual_history']
        if qs:
            first[cell, condition] = qs[0]
        committed = [e for e in events if e['kind'] == 'transport_committed']
        ids = {e['physical_attempt_id'] for e in committed}
        attempts = [a for a in calls.get('physical_attempts', []) if a['physical_attempt_id'] in ids]
        logical = {a['logical_call_id'] for a in attempts}
        if seen_physical & ids or seen_logical & logical:
            raise AssertionError('transport/response work reused across independent units')
        seen_physical.update(ids)
        seen_logical.update(logical)
        amounts = cost({'requests': [q for q in qs if q.get('logical_call_id') in logical], 'physical_attempts': attempts})
        if amounts != t['cost']:
            raise AssertionError('committed transport cost mismatch')
        if any((a['measurement_provenance'] != 'actual' for a in attempts)):
            raise AssertionError('stub promoted to real study')
        total = plus(total, amounts)
        for q in qs:
            for entry in q.get('continuous_transport_entries', []):
                if q['route']['role'] == 'base' and entry['link']['state'] != 'Connected':
                    raise AssertionError('base transport entry without permission')
            if q.get('response') is not None and (not q.get('failure')):
                if not any((e['kind'] == 'response_adopted' and e['ordinal'] == q['ordinal'] for e in events)):
                    raise AssertionError('response lacks adoption event')
        c = source(cell)
        world = deepcopy(c.world)
        for i, h in enumerate(history):
            world.experiment_supervisory_link = h['link_at_dispatch']
            snapshot = lambda: json.loads(json.dumps({**world.snapshot_state(), 'experiment_supervisory_link': h['link_at_dispatch']}))
            if snapshot() != h['before']:
                raise AssertionError('dispatch initial state differs from independent world')
            rebound = [e for e in events if e['kind'] == 'candidate_rebound' and e['elapsed_s'] <= h['started_elapsed_s']]
            if not rebound:
                raise AssertionError('dispatch has no fresh verified candidate')
            accepted = rebound[-1]
            if accepted['scope'] in ('M', 'S') and h['link_at_dispatch']['state'] != 'Connected':
                raise AssertionError('unauthorized scope dispatch')
            start = sum((x['started_elapsed_s'] < accepted['elapsed_s'] for x in history[:i]))
            candidate_index = i - start
            if accepted['candidate'][candidate_index] != h['step']:
                raise AssertionError('actual dispatch not bound to accepted content/order')
            s = h['step']
            result = world.step(s['agent_id'], s['primitive'], s['params']) if c.nominal.scenario_id == 'lava' else world.step(s['primitive'], s['agent_id'], s['params'])
            if result.success != h['result']['success']:
                raise AssertionError('independent step differs')
            if json.loads(json.dumps(asdict(result))) != h['result']:
                raise AssertionError('step effects/cost differ')
            if snapshot() != h['after']:
                raise AssertionError('dispatch result state differs from independent world')
        if c.nominal.goal_met(observe(world, c.nominal.scenario_id)) != t['parent_goal_met']:
            raise AssertionError('independent parent differs')
        if abs(world.sim_time_s - t['initial_world']['sim_time_s'] - t['world_elapsed_s']) > 1e-09:
            raise AssertionError('simulator work time differs')
        consumed = [e for e in events if e['kind'] == 'trajectory_event']
        if any((e['elapsed_s'] < e['planned_elapsed_s'] for e in consumed)):
            raise AssertionError('event consumed early')
        if cell != CELLS[0]:
            initial, expected = ctmc(('lava' if 'lava' in cell else 'psr') + '/S1/fast', 0, horizon=d['observation_limit_s'])
            if d['cases'][cell]['initial'] != initial or d['cases'][cell]['events'] != expected:
                raise AssertionError('CTMC source changed')
        elif condition == 'dynamic' and t['first_base_anchor'] is not None:
            expected = [{'at': t['first_base_anchor'] + offset, 'state': state} for offset, state in d['cases'][cell]['anchor_offsets']]
            if t['trajectory_events'] != expected:
                raise AssertionError('controlled reconnect depends on outcome')
        requests_by_state = {}
        replies = []
        for q in qs:
            k = q['route']['communication']
            r = requests_by_state.setdefault(k, Counter())
            r['request_records'] += 1
            r[q['operation']] += 1
            r['transport_entries'] += len(q.get('continuous_transport_entries', []))
            r['received'] += isinstance(q.get('response'), str)
            r['adopted'] += isinstance(q.get('response'), str) and (not q.get('failure'))
            r['stale'] += str(q.get('failure', '')).startswith(('LinkInterrupted:', 'StaleResponse:'))
            saved = next((e for e in events if e['kind'] == 'response_saved' and e['ordinal'] == q['ordinal']), None)
            adopted = next((e for e in events if e['kind'] == 'response_adopted' and e['ordinal'] == q['ordinal']), None)

            def state_at(event):
                if event is None:
                    return None
                changes = [e for e in consumed if e['elapsed_s'] <= event['elapsed_s']]
                return changes[-1]['current'] if changes else {'state': d['cases'][cell]['initial'], 'revision': 0}
            replies.append({'ordinal': q['ordinal'], 'operation': q['operation'], 'request_link': {'state': q['route']['communication'], 'revision': q['route']['communication_revision']}, 'saved_elapsed_s': saved['elapsed_s'] if saved else None, 'state_at_saved_from_event_order': state_at(saved), 'adopted_elapsed_s': adopted['elapsed_s'] if adopted else None, 'state_at_adoption_from_event_order': state_at(adopted), 'failure': q.get('failure'), 'interpretation': 'host durable save/adoption events, not remote packet arrival'})
        rows.append({'cell': cell, 'condition': condition, 'source_trial': path.relative_to(root).as_posix(), 'source_calls': cp.relative_to(root).as_posix() if cp.exists() else None, 'status': t['status'], 'parent_goal_met': t['parent_goal_met'], 'completed_recovery': t['status'] == 'executed' and t['parent_goal_met'], 'events_planned_n': len(t['trajectory_events']), 'events_consumed_n': len(consumed), 'events_before_terminal': [{'order': e['order'], 'planned': e['planned_elapsed_s'], 'consumed': e['elapsed_s'], 'phase': e['phase'], 'state': e['current']} for e in consumed], 'not_touched_dynamic': condition == 'dynamic' and (not consumed), 'phase_coverage': dict(Counter((e['phase'] for e in consumed))), 'requests_by_state': {k: dict(v) for k, v in requests_by_state.items()}, 'reply_bindings': replies, 'entries_n': t['entry_n'], 'scope_decisions': [a.get('native', {}).get('scope_decision', a.get('scope_decision')) for entry in t['entries'] for a in entry.get('attempts', [])], 'history_prefix_source_n': len(t['original_information'].get('executed_prefix', [])), 'actual_plan': [h['step'] for h in history], 'new_actions_with_same_content_as_source_history': sum((any((h['step'] == p['step'] for p in t['original_information'].get('executed_prefix', []))) for h in history)), 'revalidated_remaining_plan_entries': sum((e.get('entry_kind') == 'remaining_plan_revalidated' for e in t['entries'])), 'dispatch_n': len(history), 'software_wall_s': t['software_wall_s'], 'world_elapsed_s': t['world_elapsed_s'], 'energy_wh': t['energy_wh'], 'resource_work': t.get('resource_work'), 'model_cost': amounts, 'ledger_reservations': t.get('ledger_reservations'), 'interpretation': 'communication refusal, actual parent, and observation cutoff separate; no deadline success or speed ratio'})
    matches = []
    for cell in CELLS:
        a = first.get((cell, 'stable'))
        b = first.get((cell, 'dynamic'))
        if a and b:
            same = a['system'] == b['system'] and a['user'] == b['user']
            if not same:
                raise AssertionError('stable/dynamic initial request differs')
            matches.append({'cell': cell, 'initial_complete_request_identical': True, 'answers_shared': False})
    total['accounting'] = 'unique actual committed transport across six units; unposted reservations listed separately; unknown not zero'
    return {'version': 'e13_continuous_link_v1', 'planned_units': 6, 'rows': rows, 'initial_request_pairs': matches, 'total_model_cost': total, 'terminal_units': sum((r['source_trial'] is not None for r in rows)), 'new_audit_model_calls': 0, 'cost_scope': 'terminal units only until all six close; no inference of zero pending usage', 'scope': 'controlled base-entry offsets and original S1 software CTMC separately reported; old experiments unchanged; public guard not an Ours-only efficacy advantage'}

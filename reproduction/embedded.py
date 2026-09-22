"""Goal-focused native-board records reported in Supplement S8."""
import json
from reproduction.records import ROOT
from reproduction.accounting import accounting

def build():
    index = json.loads((ROOT / 'data/embedded/goal_focused/index.json').read_text())
    result = []
    for row in index:
        trial = json.loads((ROOT / row['trial']).read_text())
        calls = json.loads((ROOT / row['calls']).read_text())
        cost, requests = accounting([row['calls']])
        if not trial['original_goal_met']:
            raise AssertionError('recorded return goal was not attained')
        if len(requests) != 1 or any((q['route']['role'] != 'edge' or q['route']['communication'] != 'Disconnected' for q in requests)):
            raise AssertionError('board request count or local route differs')
        result.append({**row, 'original_goal_met': trial['original_goal_met'], 'cost': cost, 'scope': 'saved goal-focused native-board run; no new timing measurement', 'execution': trial['execution']})
    if [r['cost']['known_tokens'] for r in result] != [6665, 38435]:
        raise AssertionError('recorded token accounting differs')
    return result

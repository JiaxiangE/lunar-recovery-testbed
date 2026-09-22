"""Opt-in parameter-value clarification; no action, alias, goal or retry changes."""
from copy import deepcopy
import json

def parameter_bindings(problem, actor=None):
    """Retain actor/parameter tuples, never independent argument Cartesian products."""
    groups = {}
    for action in problem.actions:
        if actor is not None and action.agent_id != actor:
            continue
        key = (action.primitive, action.agent_id)
        params = deepcopy(dict(action.params))
        groups.setdefault(key, {})[json.dumps(params, sort_keys=True)] = params
    return [{'primitive': p, 'agent_id': a, 'allowed_complete_params': list(values.values())} for (p, a), values in sorted(groups.items())]

"""Literal, complete domain descriptions with shared repeated effect arrays."""
from copy import deepcopy
import json
from domains.transition import describe_problem
ENCODING = 'readable_shared_effects_v1'
FORMAT_HEADER = 'Domain fields and facts are literal. For an action with conditional_effects_ref, read the exact conditional-effect array under shared_conditional_effects at that key. All guards read the pre-action state; adds override simultaneous deletes. References share representation only: each listed action remains one physical operation.'

def encode_description(description):
    result = deepcopy(description)
    shared, index = ({}, {})
    for action in result['actions']:
        effects = action.get('conditional_effects')
        if not effects:
            continue
        key = json.dumps(effects, sort_keys=True, separators=(',', ':'))
        name = index.get(key)
        if name is None:
            name = f'effect_group_{len(shared) + 1}'
            index[key] = name
            shared[name] = effects
        del action['conditional_effects']
        action['conditional_effects_ref'] = name
    return {'encoding': ENCODING, 'domain': result, 'shared_conditional_effects': shared}

def encode_problem(problem, *, include_actor_types=False):
    return encode_description(describe_problem(problem, include_actor_types=include_actor_types))

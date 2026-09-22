"""Lossless prompt representation of describe_problem; no second action semantics.

Repeated fact arrays, conditional effects, effect lists and action-field values
are interned. All rows and all extra fields survive decoding. No action, actor,
goal, prerequisite, cost or source field is filtered to meet a token limit.
"""
from __future__ import annotations
from copy import deepcopy
import json
from typing import Any
SCHEMA = 'paper4_context_pool_v1'
FORMAT_HEADER = 'Lossless common-domain context, paper4_context_pool_v1 (all references are zero-based).\nF is the fact-string dictionary. S contains ordered arrays of F indices.\nV contains literal JSON field values. E contains conditional-effect rows; EC gives\ntheir columns as [original_field_name, kind]. C contains ordered arrays of E indices.\nA contains action rows; AC gives their columns in the same [name, kind] format.\nColumn kinds: f = S index, c = C index, v = V index, r = literal value.\nA row entry -1 means the original field was absent; null, false and zero are values,\nnot absence. Apply the same rule to E rows. Restore each row to its original object.\nR is the remaining original context: its initial_facts, goal.positive, goal.negative\nand each goal.cardinality[*].entities are S indices; restore those fact arrays and\nadd actions decoded from A. All other R fields remain literal, including policies,\nlimits and source scope. Follow the restored argument_policy and goal. Internal pool\nindices are compression references, not robot arguments or replacement action IDs.\nConditional guards refer to the pre-action state; one listed batch operation is one\nphysical action. This encoding adds no actions, permissions, effects or goal changes.'
_ACTION_FACT_FIELDS = frozenset({'preconditions', 'negative_preconditions', 'add', 'delete'})
_EFFECT_FACT_FIELDS = frozenset({'positive_guards', 'negative_guards', 'add', 'delete'})

def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def _check_json(value):
    if type(value) in (str, int, float, bool) or value is None:
        _json(value)
    elif type(value) is list:
        for child in value:
            _check_json(child)
    elif type(value) is dict and all((type(key) is str for key in value)):
        for child in value.values():
            _check_json(child)
    else:
        raise ValueError(f'context contains a non-JSON value: {type(value).__name__}')

class _Pool:

    def __init__(self):
        self.values, self.index = ([], {})

    def add(self, value):
        key = _json(value)
        if key not in self.index:
            self.index[key] = len(self.values)
            self.values.append(deepcopy(value))
        return self.index[key]

def encode_context(description: dict[str, Any]) -> dict[str, Any]:
    """Encode the exact JSON-ready dictionary returned by describe_problem."""
    _check_json(description)
    if type(description) is not dict or type(description.get('actions')) is not list:
        raise ValueError('describe_problem context with an actions array is required')
    facts, sets, values, effects, condition_lists = (_Pool() for _ in range(5))

    def fact_array(array):
        if type(array) is not list or any((type(f) is not str for f in array)):
            raise ValueError('fact arrays must contain strings')
        return sets.add([facts.add(f) for f in array])
    all_effects = [effect for action in description['actions'] for effect in action.get('conditional_effects', [])]

    def columns(rows, fact_fields, *, actions=False):
        keys = sorted({key for row in rows for key in row})
        return [[key, 'f' if key in fact_fields else 'c' if actions and key == 'conditional_effects' else 'r' if actions and key == 'action_id' else 'v'] for key in keys]
    effect_columns = columns(all_effects, _EFFECT_FACT_FIELDS)
    action_columns = columns(description['actions'], _ACTION_FACT_FIELDS, actions=True)

    def row_values(row, fields):
        result = []
        for key, kind in fields:
            if key not in row:
                result.append(-1)
            elif kind == 'f':
                result.append(fact_array(row[key]))
            elif kind == 'c':
                result.append(condition_lists.add([effects.add(row_values(e, effect_columns)) for e in row[key]]))
            elif kind == 'v':
                result.append(values.add(row[key]))
            else:
                if type(row[key]) is not str:
                    raise ValueError('action_id must be a string')
                result.append(row[key])
        return result
    remaining = {key: deepcopy(value) for key, value in description.items() if key != 'actions'}
    if 'initial_facts' in remaining:
        remaining['initial_facts'] = fact_array(remaining['initial_facts'])
    goal = remaining.get('goal', {})
    for key in ('positive', 'negative'):
        if key in goal:
            goal[key] = fact_array(goal[key])
    for cardinality in goal.get('cardinality', []):
        if 'entities' in cardinality:
            cardinality['entities'] = fact_array(cardinality['entities'])
    action_rows = [row_values(action, action_columns) for action in description['actions']]
    return {'schema': SCHEMA, 'F': facts.values, 'S': sets.values, 'V': values.values, 'EC': effect_columns, 'E': effects.values, 'C': condition_lists.values, 'AC': action_columns, 'A': action_rows, 'R': remaining}

def encode_problem(problem):
    from domains.transition import describe_problem
    return encode_context(describe_problem(problem))

def render_context(encoded):
    return FORMAT_HEADER + '\n' + _json(encoded)

def measure_context(description):
    """Exact bytes/characters; deliberately no unsupported char-to-token conversion."""
    encoded = encode_context(description)
    original, compact, rendered = (_json(description), _json(encoded), render_context(encoded))
    return {'original_minified_chars': len(original), 'original_utf8_bytes': len(original.encode('utf-8')), 'encoded_chars': len(compact), 'encoded_utf8_bytes': len(compact.encode('utf-8')), 'with_schema_header_chars': len(rendered), 'with_schema_header_utf8_bytes': len(rendered.encode('utf-8')), 'character_ratio': len(rendered) / len(original), 'actions_preserved_n': len(description['actions']), 'fact_dictionary_n': len(encoded['F']), 'unique_conditional_effects_n': len(encoded['E']), 'unique_conditional_lists_n': len(encoded['C']), 'token_count': None, 'fits_8192_tokens': None, 'token_limit_note': 'Exact tokenizer count required; byte/character length is not a token count. No context was truncated.'}

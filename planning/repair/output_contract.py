"""Versioned JSON output structures shared by prompts, parsers and probes.

This deliberately small schema vocabulary is emitted as JSON Schema. Array
ordering is explicit via x-order. There is no wrapper removal or type coercion.
Legacy response paths remain available when this profile is not selected.
"""
from copy import deepcopy
import json
import math
from planning.repair.candidate_format import CandidateFormatError
VERSION = 'paper4_generation_output_v3'

def obj(properties, required=None, *, additional=False):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else list(required), 'additionalProperties': additional}

def array(items, *, order='sequence', minimum=0):
    return {'type': 'array', 'items': items, 'minItems': minimum, 'x-order': order}
STRING = {'type': 'string', 'minLength': 1}
TEXT = {'type': 'string'}
STRINGS = array(STRING, order='unordered')
CARDINALITY = array({'type': 'array', 'prefixItems': [{'type': 'integer', 'minimum': 0}, STRINGS], 'minItems': 2, 'maxItems': 2, 'x-order': 'threshold then fact array'}, order='unordered')
GOAL = obj({'positive': STRINGS, 'negative': STRINGS, 'cardinality': CARDINALITY})

def generation_schema(operation, *, allow_decline=True, actor_types=None, bound_actor=None):
    if operation in {'D_S', 'D_M'}:
        properties = {'id': STRING, 'postcondition': TEXT, 'context': obj({'domain_goal': GOAL}, additional=True), 'dependencies': STRINGS, 'goal_nl': TEXT, 'precondition': TEXT, 'estimated_duration_s': {'type': 'number', 'minimum': 0}, 'layer': {'type': 'string', 'enum': ['M' if operation == 'D_S' else 'T']}, 'parent_id': {'type': ['string', 'null']}, 'children_ids': STRINGS, 'status': {'type': 'string', 'enum': ['pending', 'active', 'complete', 'failed']}}
        required = ['id', 'postcondition', 'context', 'dependencies']
        if operation == 'D_M':
            properties.update(assigned_agent_id=STRING, required_primitives=STRINGS)
            required.append('assigned_agent_id')
        else:
            properties['required_agent_types'] = STRINGS
        key = 'missions' if operation == 'D_S' else 'tasks'
        schema = obj({key: array(obj(properties, required), order='proposal order; dependencies govern execution', minimum=1)})
    elif operation in {'D_T', 'flat'}:
        props = {'primitive': STRING, 'params': obj({}, additional=True), 'agent_id': STRING, 'agent_type': STRING}
        required = ['primitive', 'params'] + (['agent_id'] if operation == 'flat' else [])
        schema = obj({'chain': array(obj(props, required))})
    elif operation == 'translation':
        schema = obj({'goal_facts': STRINGS, 'negative_goal_facts': STRINGS, 'goal_cardinality': CARDINALITY})
    else:
        raise ValueError('unknown native operation: ' + operation)
    schema = deepcopy(schema)
    if actor_types is not None:
        if operation in {'D_T', 'flat'}:
            item = schema['properties']['chain']['items']
            if operation == 'D_T':
                if bound_actor not in actor_types:
                    raise ValueError('bound Task actor absent from canonical roster')
                item['properties']['agent_id'] = {'type': 'string', 'const': bound_actor}
                item['properties']['agent_type'] = {'type': 'string', 'const': actor_types[bound_actor]}
            else:
                alternatives = []
                for actor, kind in sorted(actor_types.items()):
                    branch = deepcopy(item)
                    branch['properties']['agent_id'] = {'type': 'string', 'const': actor}
                    branch['properties']['agent_type'] = {'type': 'string', 'const': kind}
                    alternatives.append(branch)
                schema['properties']['chain']['items'] = {'oneOf': alternatives}
        elif operation == 'D_M':
            schema['properties']['tasks']['items']['properties']['assigned_agent_id'] = {'type': 'string', 'enum': sorted(actor_types)}
        elif operation == 'D_S':
            schema['properties']['missions']['items']['properties']['required_agent_types'] = array({'type': 'string', 'enum': sorted(set(actor_types.values()))}, order='unordered')
    if allow_decline and operation != 'translation':
        schema = {'oneOf': [schema, obj({'decline': {'type': 'boolean', 'const': True}, 'reason': TEXT}, ['decline'])]}
    return deepcopy(schema)

def schema_errors(value, schema, path='$', *, enforce_order=True):
    """Validate only the explicitly supported schema vocabulary; strict types."""
    if 'oneOf' in schema:
        matches = sum((not schema_errors(value, s, path, enforce_order=enforce_order) for s in schema['oneOf']))
        return [] if matches == 1 else [path + ': must match exactly one declared native envelope']
    kind = schema['type']
    kinds = kind if isinstance(kind, list) else [kind]
    checks = {'object': lambda: isinstance(value, dict), 'array': lambda: isinstance(value, list), 'string': lambda: isinstance(value, str), 'boolean': lambda: type(value) is bool, 'null': lambda: value is None, 'integer': lambda: type(value) is int, 'number': lambda: type(value) in (int, float) and math.isfinite(value)}
    if not any((checks[k]() for k in kinds)):
        return [f'{path}: expected {kind}']
    errors = []
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        errors.append(path + ': wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(path + ': outside enum')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        errors += [f'{path}.{k}: required' for k in schema.get('required', ()) if k not in value]
        if schema.get('additionalProperties') is False:
            errors += [f'{path}.{k}: extra field' for k in value if k not in props]
        for k in value.keys() & props.keys():
            errors += schema_errors(value[k], props[k], path + '.' + k, enforce_order=enforce_order)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            errors.append(path + ': wrong array length')
        for i, item in enumerate(value):
            child = schema.get('items')
            if 'prefixItems' in schema:
                child = schema['prefixItems'][i] if i < len(schema['prefixItems']) else None
            if child is not None:
                errors += schema_errors(item, child, f'{path}[{i}]', enforce_order=enforce_order)
        if enforce_order and schema.get('x-order') == 'lexicographic' and all((isinstance(x, str) for x in value)):
            if value != sorted(value):
                errors.append(path + ': array must be lexicographically ordered')
        sort_key = schema.get('x-sort-key')
        if enforce_order and sort_key and all((isinstance(x, dict) and isinstance(x.get(sort_key), str) for x in value)):
            if value != sorted(value, key=lambda x: x[sort_key]):
                errors.append(path + ': array must be sorted by ' + sort_key)
    elif isinstance(value, str) and len(value) < schema.get('minLength', 0):
        errors.append(path + ': string too short')
    elif type(value) in (int, float) and value < schema.get('minimum', -math.inf):
        errors.append(path + ': below minimum')
    return errors

def json_response(raw):

    def pairs(entries):
        out = {}
        for key, value in entries:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out

    def constant(value):
        raise ValueError('non-JSON numeric constant: ' + value)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as exc:
        raise CandidateFormatError(str(exc)) from exc

def parse_output(raw, schema):
    value = json_response(raw)
    errors = schema_errors(value, schema)
    if errors:
        raise CandidateFormatError('; '.join(errors))
    return value

def parse_generation(raw, operation, *, actor_types=None, bound_actor=None):
    if actor_types is not None and operation in {'D_T', 'flat'}:
        from planning.repair.actor_interface import actor_error
        from planning.repair.actor_interface import ActorContractError
        value = json_response(raw)
        rows = value.get('chain') if isinstance(value, dict) else None
        if isinstance(rows, list):
            for i, step in enumerate(rows):
                error = actor_error(step, i, actor_types, bound_actor=bound_actor if operation == 'D_T' else None)
                if error is not None:
                    raise ActorContractError(error)
    return parse_output(raw, generation_schema(operation, actor_types=actor_types, bound_actor=bound_actor))

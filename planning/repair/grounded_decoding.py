"""Route A prototype: finite grounded tuples constrain syntax, never goals/state.

No runtime alias, candidate rewrite, primitive substitution or state filtering.
The provider emits canonical object/key/numeric spellings; physical tuple coverage
is complete for the supplied finite domain, not for all legacy alias spellings.
"""
import json
from planning.repair.output_contract import schema_errors
VERSION = 'grounded_tuple_decoding_v1'

def literal_schema(value):
    if isinstance(value, dict):
        return {'type': 'object', 'properties': {k: literal_schema(v) for k, v in value.items()}, 'required': list(value), 'additionalProperties': False}
    if isinstance(value, (list, tuple)):
        return {'type': 'array', 'prefixItems': [literal_schema(v) for v in value], 'minItems': len(value), 'maxItems': len(value)}
    kind = 'null' if value is None else 'boolean' if type(value) is bool else 'number' if type(value) in (int, float) else 'string' if isinstance(value, str) else None
    if kind is None:
        raise TypeError('unsupported grounded argument type')
    return {'type': kind, 'enum': [value]}

def canonical_tuples(bindings, actor_types, operation, bound_actor=None):
    if operation not in {'D_T', 'flat'}:
        raise ValueError('leaf generation only')
    if operation == 'D_T' and bound_actor not in actor_types:
        raise ValueError('known bound Task actor required')
    unique = {}
    for group in bindings:
        primitive = group['primitive']
        actor = group['agent_id']
        if actor not in actor_types:
            raise ValueError('binding actor not in roster')
        if operation == 'D_T' and actor != bound_actor:
            continue
        for params in group['allowed_complete_params']:
            if not isinstance(params, dict):
                raise TypeError('complete params object required')
            params = json.loads(json.dumps(params, sort_keys=True))
            key = json.dumps([primitive, actor, params], sort_keys=True)
            unique[key] = {'primitive': primitive, 'params': params, 'agent_id': actor}
    return [unique[k] for k in sorted(unique)]

def response_schema(bindings, actor_types, operation, bound_actor=None):
    alternatives = []
    for step in canonical_tuples(bindings, actor_types, operation, bound_actor):
        props = {'primitive': {'type': 'string', 'enum': [step['primitive']]}, 'params': literal_schema(step['params']), 'agent_id': {'type': 'string', 'enum': [step['agent_id']]}, 'agent_type': {'type': 'string', 'enum': [actor_types[step['agent_id']]]}}
        alternatives.append({'type': 'object', 'properties': props, 'required': ['primitive', 'params'] + (['agent_id'] if operation == 'flat' else []), 'additionalProperties': False})
    chain = {'type': 'array', 'items': {'oneOf': alternatives}} if alternatives else {'type': 'array', 'maxItems': 0, 'items': {'type': 'object'}}
    return {'oneOf': [{'type': 'object', 'properties': {'chain': chain}, 'required': ['chain'], 'additionalProperties': False}, {'type': 'object', 'properties': {'decline': {'type': 'boolean', 'enum': [True]}, 'reason': {'type': 'string'}}, 'required': ['decline'], 'additionalProperties': False}]}

def response_format_from_packet(packet):
    if packet.get('prompt_version') != 'literal_task_interface_v2':
        raise ValueError('reviewed literal Task interface required')
    policy = packet['actor_policy']
    schema = response_schema(packet['allowed_action_bindings'], policy['canonical_types'], packet['operation'], policy['bound_actor'])
    return {'type': 'json_schema', 'json_schema': {'name': VERSION, 'strict': True, 'schema': schema}}

def accepts(schema, value):
    return not schema_errors(value, schema)

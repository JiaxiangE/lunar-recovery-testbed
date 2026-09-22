"""Model-owned JSON errors, separated from bugs in verification or execution."""
import json

class CandidateFormatError(ValueError):
    pass

def object_response(raw):
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CandidateFormatError('response must be a JSON object') from exc
    if not isinstance(value, dict):
        raise CandidateFormatError('response must be a JSON object')
    return value

def parse_leaf(raw, *, actor=None):
    value = object_response(raw)
    if value.get('decline') is True:
        return None
    if 'chain' not in value or not isinstance(value['chain'], list):
        raise CandidateFormatError('chain is required and must be an array of action objects')
    chain = []
    for index, step in enumerate(value['chain']):
        if not isinstance(step, dict):
            raise CandidateFormatError(f'chain[{index}] must be an object; string actions are not supported')
        if set(step) - {'primitive', 'params', 'agent_id', 'agent_type'}:
            raise CandidateFormatError(f'chain[{index}] has unknown fields')
        if not isinstance(step.get('primitive'), str) or not step['primitive'].strip():
            raise CandidateFormatError(f'chain[{index}].primitive must be a nonempty string')
        if 'params' not in step or not isinstance(step['params'], dict):
            raise CandidateFormatError(f'chain[{index}].params is required and must be an object')
        aid = step.get('agent_id', actor)
        if not isinstance(aid, str) or not aid:
            raise CandidateFormatError(f'chain[{index}].agent_id must identify an actor')
        if 'agent_type' in step and (not isinstance(step['agent_type'], str)):
            raise CandidateFormatError(f'chain[{index}].agent_type must be a string')
        chain.append({**step, 'agent_id': aid})
    return chain

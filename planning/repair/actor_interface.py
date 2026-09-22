"""Opt-in public actor contract; canonical values come from the live domain."""
from copy import deepcopy
from planning.repair.candidate_format import CandidateFormatError
VERSION = 'shared_actor_observation_v2'

class ActorContractError(CandidateFormatError):

    def __init__(self, error):
        self.error_record = deepcopy(error)
        self.reason = error['reason']
        super().__init__(self.reason)

def actor_error(step, index, actor_types, *, bound_actor=None):
    if not isinstance(step, dict):
        return None
    actor = step.get('agent_id', bound_actor)

    def error(reason, field, actual, expected, allowed):
        return {'reason': reason, 'step_index': index, 'field': field, 'path': f'$.chain[{index}].{field}', 'actual': deepcopy(actual), 'expected': expected, 'allowed': allowed, 'expected_json_type': 'string'}
    if 'agent_id' in step and bound_actor is not None and (actor != bound_actor):
        return error('CHILD_ACTOR_REASSIGNMENT', 'agent_id', actor, bound_actor, [bound_actor])
    if actor is not None and (not isinstance(actor, str) or actor not in actor_types):
        return error('MAPPER_REJECTED', 'agent_id', actor, None, sorted(actor_types))
    if 'agent_type' in step and actor in actor_types and (step['agent_type'] != actor_types[actor]):
        return error('ACTOR_TYPE_MISMATCH', 'agent_type', step['agent_type'], actor_types[actor], [actor_types[actor]])
    return None

def parser_options(session, problem, node=None):
    if getattr(session, 'interface_version', None) != VERSION:
        return {}
    return {'actor_types': problem.observation['actor_types'], 'bound_actor': getattr(node, 'assigned_agent_id', None)}

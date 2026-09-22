"""Canonical actor and output-schema fields for generation requests."""
import json
from models.task_messages import generation_messages_v7
from planning.repair.actor_interface import VERSION as ACTOR_VERSION
VIEW_VERSION = 'task_view_canonical_actor_v1'
def actor_messages(operation, problem, node, feedback, information, constraints):
    from domains.readable_context import encode_problem
    from planning.repair.output_contract import generation_schema
    system, user = generation_messages_v7(operation, problem, node, feedback, information, constraints)
    p = json.loads(user)
    p['context'] = encode_problem(problem, include_actor_types=True)
    p['output_schema'] = generation_schema(operation, actor_types=problem.observation['actor_types'], bound_actor=getattr(node, 'assigned_agent_id', None))
    p['prompt_version'] = VIEW_VERSION
    system += ' Canonical actor IDs and exact case-sensitive types are in context.domain.actor_types. An optional agent_type must equal the value for the action actor; it may be omitted. D_T keeps its assigned actor; flat may use any actor permitted by the full action domain.'
    return (system, json.dumps(p, ensure_ascii=False, separators=(',', ':')))

actor_messages.goal_alignment_enabled = True
actor_messages.interface_version = ACTOR_VERSION

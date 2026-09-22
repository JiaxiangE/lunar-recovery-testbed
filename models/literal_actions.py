"""Offline-ready Task interface v2: literal current scope, packed background only.

Explicit Task-study opt-in. Pair with RoutedHierarchySession(share_context=False);
this builder applies the original view itself. Old builders/defaults are untouched.
"""
from copy import deepcopy
import json
from models.context_encoding import fixed_task_view
from models.context_encoding import factor_action_table
from models.context_encoding import pack
from models.context_encoding import unpack
from models.context_encoding import expand_action_table
from models.actor_messages import actor_messages
from models.parameter_values import parameter_bindings
VERSION = 'literal_task_interface_v2'

def _goal_identity(goal):
    if not isinstance(goal, dict):
        return None
    try:
        for field in ('positive', 'negative'):
            if not isinstance(goal[field], (list, tuple)) or not all((isinstance(x, str) for x in goal[field])):
                return None
        if not isinstance(goal['cardinality'], (list, tuple)):
            return None
        for k, group in goal['cardinality']:
            if type(k) is not int or not isinstance(group, (list, tuple)) or (not all((isinstance(x, str) for x in group))):
                return None
        return (frozenset(goal['positive']), frozenset(goal['negative']), frozenset(((k, frozenset(group)) for k, group in goal['cardinality'])))
    except (KeyError, TypeError, ValueError):
        return None

def check_task_description(operation, problem, node, information):
    """Reject contradictory caller descriptions before any model request.

    This checks the limited study's input identity, not feasibility. Flat retains
    its actor freedom; source Task assignment is not a flat actor restriction.
    """
    from planning.repair.domain_hierarchy import goal_record
    contract = information['microstudy_task_contract']
    source = contract['source_task']
    goal = goal_record(problem)
    identity = _goal_identity(goal)
    if _goal_identity(contract.get('current_goal')) != identity or _goal_identity(source.get('context', {}).get('domain_goal')) != identity:
        raise ValueError('source_task/current_goal disagree with requested Task goal')
    if operation == 'D_T':
        if node is None:
            raise ValueError('D_T needs the current Task node')
        current = node.model_dump(mode='json')
        if _goal_identity(current.get('context', {}).get('domain_goal')) != identity:
            raise ValueError('current node goal disagrees with requested Task goal')
        for key in ('id', 'layer', 'assigned_agent_id', 'precondition', 'postcondition'):
            if current.get(key) != source.get(key):
                raise ValueError('source_task/current node disagree: ' + key)
        for key in ('dependencies', 'required_primitives'):
            if set(current.get(key, [])) != set(source.get(key, [])):
                raise ValueError('source_task/current node disagree: ' + key)

def task_messages(view):
    if view not in {'full', 'view'}:
        raise ValueError('explicit Task view required')

    def messages(operation, problem, node, feedback, information, constraints):
        if operation not in {'D_T', 'flat'} or 'microstudy_task_contract' not in information:
            raise ValueError('this opt-in profile is limited to the same-Task study; no Scene/Mission override')
        check_task_description(operation, problem, node, information)
        _, raw = actor_messages(operation, problem, node, feedback, information, constraints)
        old = json.loads(raw)
        selected = fixed_task_view(old) if view == 'view' else deepcopy(old)
        if view == 'full' and operation == 'D_T':
            selected['context_view'] = {'version': 'full_task_action_view_v1', 'omitted_action_rows': 0, 'rule': 'full domain; the same fixed Task actor contract still applies'}
        source = information['microstudy_task_contract']['source_task']
        actor = getattr(node, 'assigned_agent_id', None) if operation == 'D_T' else None
        full_constraints = selected['generation_constraints']
        shown = deepcopy(full_constraints)
        for key in ('scene_alignment', 'output_limit_scope', 'max_immediate_children', 'child_contract_rule', 'reserved_node_ids', 'dependency_rule'):
            shown.pop(key, None)
        if 'limits' in shown:
            shown['limits'].pop('max_children', None)
        protected = {'operation': operation, 'prompt_version': VERSION, 'goal': deepcopy(selected['goal']), 'node': deepcopy(selected['node']), 'request_initial_facts': sorted(problem.initial_state), 'actor_policy': {'bound_actor': actor, 'canonical_types': deepcopy(problem.observation['actor_types']), 'rule': 'keep the Task actor' if actor else 'any actor authorized by the full domain; source Task actor does not bind flat'}, 'task_requirements': {'source_task_id': source['id'], 'precondition': source.get('precondition', ''), 'postcondition': source.get('postcondition', ''), 'required_primitives': deepcopy(source.get('required_primitives', [])), 'dependencies': deepcopy(source.get('dependencies', [])), 'explicit_process_obligations': deepcopy(information.get('mandatory_network_obligations', []))}, 'output_schema': deepcopy(selected['output_schema']), 'allowed_action_bindings': parameter_bindings(problem, actor), 'generation_constraints': shown, 'feedback': feedback, 'context_view': deepcopy(selected.get('context_view', {}))}
        support = {'context': deepcopy(selected['context']), 'original_information': deepcopy(selected['original_information']), 'outer_composition_reference': {'evaluation_parent_goal': deepcopy(information['microstudy_task_contract']['parent_goal_reported_separately']), 'scene_alignment_record': deepcopy(selected.get('current_obligation_view', {})), 'scope_note': 'outer Scene/Mission composition/evaluation context; these root children are not children to generate inside the current Task; actual Task dependencies and explicit process duties above remain required'}, 'original_generation_constraints_record': deepcopy(full_constraints)}
        protected['supporting_context'] = pack(factor_action_table(support))
        system = 'Generate one complete ordered action chain for the current goal from request_initial_facts. Return only one JSON instance of output_schema, not a wrapper or schema definition. The front goal, node, actor_policy, task_requirements and allowed_action_bindings are literal, inline authoritative fields. Use an exact primitive/actor/complete-params binding; a parameter value allowed for another action is not an alias. A legal binding still needs its state preconditions and effects. Reaching the same coordinates does not make two primitives equivalent. Check positive, negative and cardinality goals plus the Task precondition, required primitives, dependencies and explicit process obligations. Do not invent additional task goals. Follow generation_constraints; a decline is a refusal, not proof of impossibility. ' + ('D_T may omit agent_id but must retain its bound actor. ' if operation == 'D_T' else 'flat names each action actor and may use any domain-authorized actor. ') + 'supporting_context retains the full source information and action semantics for this view. Only that background uses encoding: expand its $shared_object from its shared_objects; $literal_object is a literal dictionary. For action_template_ref combine common_fields and row fields, union common_set_members, and resolve conditional_effects_ref in context.shared_conditional_effects. Outer Scene/Mission records are composition/evaluation context, not extra goals assigned to this Task. This labeling does not waive Task dependencies, source process obligations, external constraints or final parent evaluation.'
        return (system, json.dumps(protected, ensure_ascii=False, separators=(',', ':')))
    messages.goal_alignment_enabled = True
    messages.interface_version = actor_messages.interface_version
    return messages

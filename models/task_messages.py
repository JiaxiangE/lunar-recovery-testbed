"""Versioned request organization; no changed goal, action, parser or budget."""
from copy import deepcopy
import json
from models.generation import generation_messages_v3
VERSION = 'task_focused_native_generation_v4'

def _type(value):
    if type(value) is bool:
        return 'boolean'
    if type(value) in (int, float):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, (tuple, list)):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    if value is None:
        return 'null'
    raise TypeError('unsupported grounded parameter type')

def action_index(problem):
    """Index the actual registry-backed grounded rows; never derive names from effects."""
    groups = {}
    for action in problem.actions:
        group = groups.setdefault(action.primitive, {'actors': set(), 'shapes': {}})
        group['actors'].add(action.agent_id)
        shape = {'required_arguments': sorted(action.params), 'argument_types': {k: _type(v) for k, v in sorted(action.params.items())}}
        group['shapes'][json.dumps(shape, sort_keys=True)] = shape
    return [{'primitive': name, 'actors': sorted(g['actors']), 'params_shapes': list(g['shapes'].values())} for name, g in sorted(groups.items())]

def generation_messages_v4(operation, problem, node, feedback, original_information, constraints):
    system, user = generation_messages_v3(operation, problem, node, feedback, original_information, constraints)
    payload = json.loads(user)
    focus = {'requested_goal_field': 'goal', 'requested_node_field': 'node', 'source_obligations': deepcopy(original_information.get('mandatory_network_obligations', [])), 'source_obligation_note': 'These explicit source requirements and the supplied node contract remain mandatory. Missing extra requirements does not waive the supplied positive/negative/cardinality goals.', 'background_fields': ['original_information.tree', 'original_information.original_plan', 'original_information.original_observation'], 'background_note': 'Original global mission/history explains the failure. It is not an instruction to perform every global goal within this request. Retained suffix/source duties remain available for composition checks.'}
    instruction = ' The current request is defined by goal and node. Separate that obligation from the original global mission history. For D_T satisfy this bound Task goal and its required_primitives/precondition; preserve required return/delivery and negative goals. Do not append unrelated sampling, delivery or communication just because it appears in background information. Include enabling actions only when needed by actual preconditions or explicit source obligations. Emit a complete JSON candidate; reaching a goal never permits incomplete JSON or ignoring required source duties. For D_M/D_S propose children to meet the supplied parent; avoid inventing extra objectives as immutable source requirements. Only primitive names in allowed_primitive_index are executable. Facts in add/delete/preconditions are state predicates, not action names. The index summarizes registry-backed grounded rows; select exact actor/params from context.domain.actions and check their full preconditions and delete/conditional effects. '
    if operation == 'translation':
        instruction += 'Translate goal only: positive facts into goal_facts, negative facts into negative_goal_facts, and each cardinality requirement into [integer_threshold, array_of_literal_fact_strings] inside goal_cardinality. Do not substitute the background mission or current observed facts. A fact is a string, not an object. Return the three required top-level fields even when lists are empty. No explanation, wrapper, plan or decline is defined here. '
    ordered = {'operation': operation, 'goal': payload.pop('goal'), 'node': payload.pop('node'), 'request_focus': focus, 'output_schema': payload.pop('output_schema'), 'generation_constraints': payload.pop('generation_constraints'), 'allowed_primitive_index': action_index(problem), 'prompt_version': VERSION, **payload}
    return (system + instruction, json.dumps(ordered, ensure_ascii=False, separators=(',', ':')))

def generation_messages_v5(operation, problem, node, feedback, original_information, constraints):
    from planning.repair.scene_alignment import VERSION as version
    from planning.repair.scene_alignment import scoped_history
    system, user = generation_messages_v4(operation, problem, node, feedback, original_information, constraints)
    payload = json.loads(user)
    alignment = deepcopy(constraints.get('scene_alignment', {}))
    payload['original_information'] = scoped_history(original_information)
    payload['prompt_version'] = version
    payload['current_obligation_view'] = alignment
    if alignment.get('authorized_scene_replacement') and operation in {'D_S', 'flat'}:
        current = payload['node']
        payload['replacement_history'] = {'source_root_id': alignment['root_id'], 'replaced_children_ids': alignment['replaced_children_ids'], 'original_node': deepcopy(current), 'structure_reference': 'original_information.tree (historical decomposition)'}
        if current is not None:
            current['children_ids'] = [c['id'] for c in alignment['retained_children']]
        system += ' For this authorized whole-Scene request, replacement_history and original_information.tree describe the old decomposition. Only retained children in current_obligation_view remain current child obligations. Propose a new internal decomposition/order for the exact current goal. Preserve current positive, negative and cardinality goals and every explicit external/process obligation. Do not revive replaced child goals as mandatory.'
    system += ' Historical scene_unsatisfiable observations are scoped historical records, not assertions about the current goal. Unknown provenance stays unknown. A goal clarification contains no plan and no assertion of feasibility; you may decline again.'
    return (system, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
generation_messages_v5.goal_alignment_enabled = True
ACTION_SCOPE_VERSION = 'current_goal_action_scope_v6'

def generation_messages_v6(operation, problem, node, feedback, original_information, constraints):
    """Scope the existing child bound without changing any executable contract."""
    system, user = generation_messages_v5(operation, problem, node, feedback, original_information, constraints)
    payload = json.loads(user)
    shown = payload['generation_constraints']
    hierarchy = operation in {'D_S', 'D_M'}
    bound = shown.get('limits', {}).get('max_children', shown.get('max_immediate_children'))
    shown['max_immediate_children'] = bound if hierarchy else None
    shown['output_limit_scope'] = {'hierarchy_child_limit_applies_to': ['D_S.missions', 'D_M.tasks'], 'current_output_is_child_specifications': hierarchy, 'primitive_chain_inherits_child_limit': False, 'meaning': 'The tree max_children parameter is used for Mission/Task specification counts and call-budget derivation. It is not an action-count limit on D_T or flat. Read output_schema for the actual array shape; output/context/execution/resource constraints still apply.'}
    if operation == 'flat':
        system = system.replace('Propose a new internal decomposition/order for the exact current goal.', 'Return the complete primitive action chain for the exact current goal, not Mission or Task specifications.')
    system += f' The hierarchical max_children value ({bound}) applies only to D_S Mission specifications and D_M Task specifications. D_T and flat primitive chains are not bounded by that value. Longer chains must still pass their actual schema, preconditions, goal and resource checks; this is not a feasibility guarantee.'
    payload['prompt_version'] = ACTION_SCOPE_VERSION
    return (system, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
generation_messages_v6.goal_alignment_enabled = True
TRANSLATION_VERSION = 'literal_goal_translation_v7'

def generation_messages_v7(operation, problem, node, feedback, original_information, constraints):
    """Prompt-only translation correction on v6; strict parser and retries unchanged."""
    system, user = generation_messages_v6(operation, problem, node, feedback, original_information, constraints)
    payload = json.loads(user)
    payload['prompt_version'] = TRANSLATION_VERSION
    if operation == 'translation':
        payload['translation_structure_examples'] = {'purpose': 'Generic shape examples only; placeholder predicates are NOT this task answer.', 'empty': {'goal_facts': [], 'negative_goal_facts': [], 'goal_cardinality': []}, 'nonempty': {'goal_facts': ['example_positive(a)'], 'negative_goal_facts': ['example_negative(b)'], 'goal_cardinality': [[2, ['example_member(c)', 'example_member(d)', 'example_member(e)']]]}}
        system += ' Translate the current goal by literal field mapping: goal.positive -> goal_facts, goal.negative -> negative_goal_facts, goal.cardinality -> goal_cardinality. Preserve every predicate string, argument, threshold and group exactly; no inferred, renamed, observed or background predicates. Do not add achieved facts or replace one storage predicate by another. goal_cardinality is always an array of requirements: [] for zero requirements; [[k,[fact1,fact2]]] for one requirement, never [k,[fact1,fact2]]. Do not copy example predicates. Check the three top-level fields and nested array shape before returning JSON. There is still one translation per goal policy, with no new translation retry.'
    return (system, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
generation_messages_v7.goal_alignment_enabled = True

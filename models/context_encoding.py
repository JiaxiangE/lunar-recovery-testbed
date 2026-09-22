"""Lossless readable JSON object sharing; no action/state/goal pruning."""
from copy import deepcopy
import json
VERSION = 'complete_context_sharing_v1'
REF = '$shared_object'
LITERAL = '$literal_object'

def fixed_task_view(payload):
    """Authorization-based D_T view, never a goal/winner-based action subset.

    A Task's actor cannot change. Retain *all* its action rows, all facts/entities,
    full parent/original-plan information, and other actors' original-plan actions
    for cross-Task effects. Whole-plan/decomposition requests keep every action.
    """
    result = deepcopy(payload)
    node = result.get('node') or {}
    if result.get('operation') != 'D_T' or node.get('layer') != 'T':
        return result
    actor = node.get('assigned_agent_id')
    if not actor:
        return result
    domain = result.get('context', {}).get('domain', {})
    actions = domain.get('actions', [])
    original = result.get('original_information', {}).get('original_plan', [])
    cross = {(s.get('agent_id'), s.get('primitive')) for s in original if isinstance(s, dict)}
    kept = [a for a in actions if a['agent_id'] == actor or (a['agent_id'], a['primitive']) in cross]
    domain['actions'] = kept
    result['context_view'] = {'version': 'fixed_task_actor_view_v1', 'assigned_actor': actor, 'rule': 'all fixed-actor actions plus every other-actor primitive used by the original plan; all rows of those primitives, not only the planned parameter value', 'omitted_action_rows': len(actions) - len(kept), 'preserved': "all initial facts, world/entity observations, original plan/tree, parent policies, retained actions' preconditions and full delete/conditional effects", 'not_a_global_plan_view': True}
    return result

def factor_action_table(payload):
    """Factor common fields within primitive/actor groups, retaining every row.

    Sorted string effect/precondition arrays are sets in the domain definition;
    common members and row-specific members reconstruct the exact sorted array.
    No reachability/winner/answer-dependent action selection is performed.
    """
    result = deepcopy(payload)
    domain = result.get('context', {}).get('domain', {})
    actions = domain.get('actions')
    if not actions:
        return result
    groups = {}
    for a in actions:
        groups.setdefault((a['primitive'], a['agent_id']), []).append(a)
    templates, lookup = ({}, {})
    for i, (key, rows) in enumerate(groups.items()):
        common, sets = ({}, {})
        for field in rows[0]:
            values = [r.get(field) for r in rows]
            if all((v == values[0] for v in values)):
                common[field] = values[0]
            elif field in {'preconditions', 'negative_preconditions', 'add', 'delete'} and all((isinstance(v, list) and all((isinstance(x, str) for x in v)) and (v == sorted(v)) for v in values)):
                members = set(values[0]).intersection(*(set(v) for v in values[1:]))
                if members:
                    sets[field] = sorted(members)
        ident = f'action_template_{i + 1}'
        templates[ident] = {'common_fields': common, 'common_set_members': sets}
        lookup[key] = ident
    rows = []
    for action in actions:
        ident = lookup[action['primitive'], action['agent_id']]
        template = templates[ident]
        row = {k: v for k, v in action.items() if k not in template['common_fields']}
        for key, members in template['common_set_members'].items():
            row[key] = sorted(set(row[key]) - set(members))
        rows.append({'action_template_ref': ident, 'fields': row})
    if len(json.dumps([templates, rows])) < len(json.dumps(actions)):
        domain['actions'] = rows
        domain['action_templates'] = templates
    return result

def expand_action_table(payload):
    result = deepcopy(payload)
    domain = result.get('context', {}).get('domain', {})
    templates = domain.pop('action_templates', None)
    if templates is None:
        return result
    expanded = []
    for row in domain['actions']:
        template = templates[row['action_template_ref']]
        action = {**deepcopy(template['common_fields']), **deepcopy(row['fields'])}
        for key, values in template['common_set_members'].items():
            action[key] = sorted(set(values) | set(action.get(key, [])))
        expanded.append(action)
    domain['actions'] = expanded
    return result

def pack(value, minimum_bytes=160):
    counts, values = ({}, {})

    def collect(v):
        if isinstance(v, (dict, list)):
            key = json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            if len(key.encode()) >= minimum_bytes:
                counts[key] = counts.get(key, 0) + 1
                values[key] = v
            for child in v.values() if isinstance(v, dict) else v:
                collect(child)
    collect(value)
    ids = {key: 'object_' + str(i + 1) for i, key in enumerate(sorted((k for k, n in counts.items() if n > 1)))}

    def encode(v, root=False):
        if isinstance(v, (dict, list)):
            key = json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            if key in ids and (not root):
                return {REF: ids[key]}
            if isinstance(v, dict):
                result = {k: encode(child) for k, child in v.items()}
                return {LITERAL: result} if REF in v or LITERAL in v else result
            return [encode(child) for child in v]
        return v
    return {'encoding': VERSION, 'content': encode(value), 'shared_objects': {ids[k]: encode(values[k], True) for k in ids}}

def unpack(value):
    if value['encoding'] != VERSION:
        raise ValueError('unknown context encoding')

    def decode(v, seen=()):
        if isinstance(v, dict):
            if set(v) == {REF}:
                key = v[REF]
                if key in seen:
                    raise ValueError('cyclic context reference')
                return decode(value['shared_objects'][key], seen + (key,))
            if set(v) == {LITERAL}:
                return {k: decode(x, seen) for k, x in v[LITERAL].items()}
            return {k: decode(x, seen) for k, x in v.items()}
        if isinstance(v, list):
            return [decode(x, seen) for x in v]
        return deepcopy(v)
    return decode(value['content'])

def render_messages(system, user, *, task_action_view='view'):
    if task_action_view not in {'view', 'full'}:
        raise ValueError('task_action_view must be full or view')
    source = json.loads(user)
    selected = fixed_task_view(source) if task_action_view == 'view' else deepcopy(source)
    if task_action_view == 'full' and source.get('operation') == 'D_T':
        selected['context_view'] = {'version': 'full_task_action_view_v1', 'omitted_action_rows': 0, 'rule': 'full domain; the same fixed Task actor contract still applies'}
    packed = pack(factor_action_table(selected))
    compact = json.dumps(packed, ensure_ascii=False, separators=(',', ':'))
    if len(compact.encode()) >= len(user.encode()):
        return (system, json.dumps(selected, ensure_ascii=False, separators=(',', ':')))
    instruction = ' Shared-object encoding is lossless: expand an object with the single $shared_object key from shared_objects; $literal_object represents a literal dictionary. No actions, history, custody, delete effects or parent obligations have been removed. Read content after expansion. '
    instruction += " For each action_template_ref, combine the template common_fields with row fields; union common_set_members with the row's corresponding precondition/effect arrays. Every original action row is retained. "
    if source.get('operation') == 'D_T' and task_action_view == 'view':
        instruction = instruction.replace('No actions, history', 'No fixed-actor actions, history')
        instruction = instruction.replace('Every original action row is retained.', "Every action allowed for this Task's fixed actor is retained; see context_view for other-actor rows. Whole-plan and parent checks use the full domain.")
    return (system + instruction, compact)

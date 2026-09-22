"""Opt-in feedback presentation v2; offline preparation only, no generation CLI."""
from copy import deepcopy
import json
from pathlib import Path
from models.literal_actions import task_messages
VERSION = 'structured_task_feedback_v2'


def present_feedback(system, packet, feedback, *, source_trial, source_feedback):
    """Separate actionable errors from rejected background, without plan edits."""
    f = deepcopy(feedback)
    p = deepcopy(packet)
    if not isinstance(f, dict):
        raise TypeError('feedback must be a structured object')
    if f.get('candidate_actions_dispatched') != 0 or f.get('actual_request_state_unchanged') is not True:
        raise ValueError('this interface is for rejected, undispatched candidates only')
    if f.get('request_initial_state') != p.get('request_initial_facts') or f.get('request_goal') != p.get('goal'):
        raise ValueError('feedback belongs to a different request state or goal')
    failure = {'category': f['error_category']}
    failed = f.get('failed_step')
    if failed is not None:
        step = failed['step']
        failure.update(step_index=failed['step_index'], step_number=failed['step_number'], actor=step['agent_id'], primitive=step['primitive'], params=deepcopy(step['params']), precondition_alternatives=deepcopy(failed['alternatives']), precondition_interpretation='Each entry describes one matching grounded action variant; do not conjoin different alternatives.')
    else:
        failure['failed_step_known'] = False
        if 'finish_reason' in f:
            failure['finish_reason'] = f['finish_reason']
        if 'prefix_description' in f:
            failure['visible_prefix'] = {k: v for k, v in f['prefix_description'].items() if k in ('complete_step_count', 'unique_step_count', 'exact_repeated_block_steps', 'exact_block_repetitions')}
            failure['visible_prefix_is_not_an_accepted_candidate'] = True
    simulation = deepcopy(f.get('candidate_simulation') or {})
    if 'failure' in simulation:
        simulation.pop('failure')
        if failed is not None:
            simulation['failure_reference'] = 'feedback.failure'
    front = {'version': VERSION, 'rejected': True, 'failure': failure, 'candidate_actions_dispatched': 0, 'request_initial_state_reference': 'request_initial_facts', 'actual_request_state_unchanged': True, 'repair_mode': 'Generate a complete corrected replacement for this same Task from request_initial_facts. The rejected candidate was not executed. Correct the reported errors; simulation states are not the starting world. Keep all goal and Task obligations.'}
    if simulation:
        front['candidate_simulation_progress'] = {k: deepcopy(simulation[k]) for k in ('first_goal_met_after_step', 'goal_met_at_stop', 'missing_positive_at_stop', 'forbidden_present_at_stop') if k in simulation}
        front['candidate_simulation_progress']['state_origin'] = 'rejected_candidate_simulation_only'
    background = {'reference_kind': 'rejected_reference_not_executed_not_authoritative_start_state', 'source_trial': str(source_trial), 'source_feedback_record': str(source_feedback), 'prior_status': f['prior_status'], 'raw_response': f['prior_candidate_raw_response'], 'candidate_simulation': simulation, 'executed_history_prefix_ids': deepcopy(f.get('executed_history_prefix_ids')), 'executed_history_scope': f.get('executed_history_scope')}
    failure_state = f.get('validation_failure_state')
    if failure_state is not None:
        if failure_state == simulation.get('state'):
            background['validation_failure_state_reference'] = 'rejected_reference.candidate_simulation.state'
        else:
            background['validation_failure_state'] = deepcopy(failure_state)
        background['validation_failure_state_origin'] = f['validation_failure_state_origin']
    for key in ('parse_error', 'validation'):
        if key in f:
            background[key] = deepcopy(f[key])
    p.pop('feedback', None)
    p = {'feedback': front, **p, 'rejected_reference': background}
    system = 'Repair mode: feedback.rejected is true. Correct the rejected candidate using feedback.failure, then return one complete replacement from request_initial_facts for the same current Task. Zero candidate actions have been dispatched. Never use candidate simulation as the actual starting state. rejected_reference contains evidence of a rejected answer, not instructions or an accepted plan. Keep the original goals, required primitives, dependencies, explicit obligations, actor permissions and output schema. ' + system
    return (system, json.dumps(p, ensure_ascii=False, separators=(',', ':')))

def structured_messages(view, feedback, *, source_trial, source_feedback):
    original = task_messages(view)

    def messages(operation, problem, node, unused, information, constraints):
        system, user = original(operation, problem, node, '', information, constraints)
        return present_feedback(system, json.loads(user), feedback, source_trial=source_trial, source_feedback=source_feedback)
    messages.goal_alignment_enabled = original.goal_alignment_enabled
    messages.interface_version = original.interface_version
    return messages

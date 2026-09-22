"""Generate, validate and execute a supplied Task, reporting its parent separately."""
from copy import deepcopy
import time
import traceback
from planning.resources import attach_resources
from planning.repair.domain_hierarchy import ancestor
from planning.repair.domain_hierarchy import problem_for_node
from planning.repair.domain_hierarchy import goal_record
from planning.repair.domain_hierarchy import HierarchyLimits
from planning.repair.domain_hierarchy import _pre_met
from planning.repair.hierarchy_budget import request_constraints
from planning.repair.candidate_format import CandidateFormatError
from planning.repair.candidate_format import parse_leaf
from planning.repair.output_contract import parse_generation
from planning.repair.resource_support import ResourceSupport
from execution.binding import execute_plan
from domains.common import observe
from controller.scope import Scope
VERSION = 'task_recovery_v1'
def evaluate_task(case, node, task, parent, arm, session_factory):
    cell = case.cell_id
    operation = 'flat' if arm == 'flat-full' else 'D_T'
    view = 'view' if arm == 'D_T-view' else 'full'
    session = session_factory(case, view)
    session.set_repair_context('T', node.assigned_agent_id)
    aid = ResourceSupport()
    aid.bind_source_obligations(case.original_information, task)
    started = time.perf_counter()
    row = {'version': VERSION, 'cell': cell, 'arm': arm, 'seed': 1, 'task_problem': {'goal': goal_record(task), 'initial_facts': sorted(task.initial_state)}, 'parent_problem': {'goal': goal_record(parent), 'initial_facts': sorted(parent.initial_state)}, 'source_task': node.model_dump(mode='json'), 'original_information': case.original_information, 'schema': None, 'task_gate': None, 'resource_prediction': None, 'execution': None, 'status': 'not_requested', 'communication_before': case.communication}
    try:
        if not _pre_met(node, task.initial_state):
            raise ValueError('source Task precondition is not satisfied')
        request_node = None if operation == 'flat' else node
        constraints = request_constraints(HierarchyLimits(1, 1, 6, 1), session, 1, node=request_node)
        raw = session.request(operation, task, request_node, '', case.original_information, constraints=constraints)
        row['raw_response'] = raw
        try:
            from planning.repair.actor_interface import parser_options
            parsed = parse_generation(raw, operation, **parser_options(session, task, request_node))
            row['parsed_response'] = parsed
            plan = parse_leaf(raw, actor=node.assigned_agent_id if operation == 'D_T' else None)
        except CandidateFormatError as exc:
            row.update(schema={'accepted': False, 'reason': str(exc)}, status='candidate_format_rejected')
            if hasattr(exc, 'error_record'):
                row['schema']['structured_error'] = deepcopy(exc.error_record)
        else:
            row['schema'] = {'accepted': True}
            if plan is None:
                row['status'] = 'model_declined'
            else:
                row['candidate'] = plan
                if operation == 'D_T' and any((s['agent_id'] != node.assigned_agent_id for s in plan)):
                    check = {'accepted': False, 'reason': 'CHILD_ACTOR_REASSIGNMENT'}
                elif not set(node.required_primitives) <= {s['primitive'] for s in plan}:
                    check = {'accepted': False, 'reason': 'CHILD_REQUIRED_PRIMITIVE_MISSING'}
                else:
                    check = aid.verify(task, plan)
                row['task_gate'] = check
                if not check['accepted']:
                    row['status'] = 'task_gate_rejected'
                else:
                    prediction = aid.forecast(case.world, task, plan)
                    row['resource_prediction'] = prediction
                    if not aid.may_execute(prediction):
                        row['status'] = 'resource_rejected'
                    else:
                        row['execution'] = execute_plan(case.world, task, plan, capture_transitions=True)
                        row['status'] = 'executed' if row['execution']['execution_success'] else 'execution_failed'
    except Exception as exc:
        row.update(status=getattr(exc, 'failure_category', 'instrumentation_failed'), exception={'type': type(exc).__name__, 'detail': str(exc), 'traceback': traceback.format_exc()})
    final = observe(case.world, task.scenario_id)
    row.update(task_goal_met=task.goal_met(final), parent_goal_met=parent.goal_met(final), final_facts=sorted(final), local_work=aid.report(), wall_s=time.perf_counter() - started, communication_after=case.communication)
    execution = row.get('execution') or {}
    actual = execution.get('executed_plan', [])
    row['dispatch_n'] = execution.get('dispatch_n', 0)
    row['actual_actors'] = sorted({s['agent_id'] for s in actual})
    row['parent_missing_positive'] = sorted(parent.goal_facts - final)
    row['parent_positive_newly_completed'] = sorted((parent.goal_facts & final) - parent.initial_state)
    state = task.initial_state
    first = 0 if task.goal_met(state) else None
    for i, s in enumerate(actual, 1):
        if not execution['execution_results'][i - 1]['success']:
            break
        action = next((a for a in task.actions if a.to_step() == s and a.applicable(state)))
        state = action.apply(state)
        if first is None and task.goal_met(state):
            first = i
    if execution.get('reason') == 'DOMAIN_WORLD_DIVERGENCE':
        first = None
    row['task_first_met_symbolic_step'] = first
    row['actions_after_task_first_met'] = actual[first:] if first is not None else []
    row['extra_work_note'] = 'suffix after Task achievement is descriptive; may satisfy other source duties, not declared wasted'
    return (row, session.records())

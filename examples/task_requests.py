"""Task requests."""
from copy import deepcopy
import time
import traceback
from examples.recovery_inputs import build_case
from examples.retained_work import cross_actor_case
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
VERSION = 'task_view_microstudy_v7'
CELLS = ('B05_psr_strict_disconnected', 'E05_lava_communication_return', 'cross_actor')
ARMS = ('D_T-full', 'D_T-view', 'flat-full')
SCHEDULE = tuple(((c, a) for c, arms in zip(CELLS, (ARMS, (ARMS[1], ARMS[2], ARMS[0]), (ARMS[2], ARMS[0], ARMS[1]))) for a in arms))

def task_input(cell):
    case = attach_resources(cross_actor_case() if cell == 'cross_actor' else build_case(cell))
    node = case.original_tree.nodes['offload_1'] if cell == 'cross_actor' else ancestor(case.original_tree, case.failed_node_id, Scope.T)
    task = problem_for_node(case.nominal, node)
    case.original_information['microstudy_task_contract'] = {'source_task': node.model_dump(mode='json'), 'current_goal': goal_record(task), 'parent_goal_reported_separately': goal_record(case.nominal), 'flat_actor_policy': 'may use any source-authorized actor; this source Task identifies the requested obligation, not an additional flat actor restriction'}
    return (case, node, task, case.nominal)

from controller.task_recovery import evaluate_task

def evaluate_unit(cell, arm, session_factory):
    case,node,task,parent=task_input(cell)
    result,calls=evaluate_task(case,node,task,parent,arm,session_factory)
    result["cell"]=cell
    return result,calls

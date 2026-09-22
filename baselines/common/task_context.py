"""Create a native repair context from an observed task network."""
from planning.repair.domain_hierarchy import leaves
from planning.schema.decomposition import Task
def make_native_context(case, problem):
    from baselines.htn_repair_holler.domain_repair import make_task_repair_context
    lookup, state, ids, leaf_ids = (case.original_problem.actions, case.original_problem.initial_state, [], [])
    for node_id, s in leaves(case.original_tree):
        candidates = [a for a in lookup if a.to_step() == s and a.applicable(state)]
        if not candidates:
            raise ValueError(f'original supplied plan not valid in pre-deviation domain: {node_id}')
        action = candidates[0]
        ids.append(action.action_id)
        leaf_ids.append(node_id)
        state = action.apply(state)
    prefix = tuple(ids[:len(case.executed_prefix_ids)])
    if case.policy_id:
        tasks = [{'task_id': 'public_scene_obligation', 'action_ids': tuple(ids), 'goal_options': tuple(problem.goal_options()), 'negative_goal_facts': problem.negative_goal_facts}]
    else:
        by_leaf = dict(zip(leaf_ids, ids))
        tasks = []
        for node in case.original_tree.nodes.values():
            if isinstance(node, Task):
                p = problem_for_node(problem, node)
                tasks.append({'task_id': node.id, 'action_ids': tuple((by_leaf[i] for i, _ in leaves(case.original_tree, node))), 'goal_options': tuple(p.goal_options()), 'negative_goal_facts': p.negative_goal_facts})
    return make_task_repair_context(case.original_problem, problem, tasks=tasks, executed_prefix=prefix, original_plan=tuple(ids))

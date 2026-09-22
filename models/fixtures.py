"""Explicit deterministic responses from a caller-supplied plan table."""
from planning.schema.decomposition import Scene, Mission, Task
from planning.networks import build_tree
from planning.repair.domain_hierarchy import goal_record, problem_for_node, leaves
from models.generation import response_key
def make_scripts(case, problem):
    """Declared response table keyed by actual operation and actual requested goal."""
    label = next((name for name, p in case.goals() if goal_record(p) == goal_record(problem)), None)
    if label is None:
        raise ValueError('no declared response fixture for this parent goal')
    tree = build_tree(problem, case.script_groups[label])
    scripts = {}

    def store(operation, p, response):
        key = response_key(operation, goal_record(p))
        if key in scripts and scripts[key] != [response]:
            raise ValueError('fixture has ambiguous responses for one operation/goal')
        scripts[key] = [response]
    for node in tree.nodes.values():
        p = problem_for_node(problem, node)
        if isinstance(node, Scene):
            store('D_S', p, {'missions': [tree.nodes[i].model_dump(mode='json') for i in node.children_ids]})
        elif isinstance(node, Mission):
            store('D_M', p, {'tasks': [tree.nodes[i].model_dump(mode='json') for i in node.children_ids]})
        elif isinstance(node, Task):
            store('D_T', p, {'chain': [s for _, s in leaves(tree, node)]})
    store('flat', problem, {'chain': [s for _, s in leaves(tree)]})
    store('translation', problem, {'goal_facts': sorted(problem.goal_facts), 'negative_goal_facts': sorted(problem.negative_goal_facts), 'goal_cardinality': [[k, sorted(atoms)] for k, atoms in problem.goal_cardinality]})
    return scripts

def ready_scripts(case, problem):
    values = make_scripts(case, problem)
    if case.fallback is not None and problem is case.nominal:
        for operation in ('flat', 'D_S', 'D_M'):
            key = response_key(operation, goal_record(problem))
            if key in values:
                values[key] = [values[key][0], {'decline': True, 'reason': 'No candidate for this original obligation; consider only the independently allowed policy.'}]
    return values

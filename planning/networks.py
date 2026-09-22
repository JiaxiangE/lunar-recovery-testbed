"""Build an ordered task network from caller-supplied groups and contracts."""
from copy import deepcopy
from planning.schema.decomposition import Scene, Mission, Task, Primitive, DecompositionTree
from planning.repair.domain_hierarchy import set_node_goal
from domains.common import with_goal
def build_tree(problem, groups):
    root = set_node_goal(Scene(id='scene'), problem)
    nodes, previous_mission = ({root.id: root}, None)
    for i, group in enumerate(groups, 1):
        mid = f'mission_{i}'
        positive = {f for t in group for f in t['goals']}
        mission = set_node_goal(Mission(id=mid, parent_id=root.id, dependencies=[previous_mission] if previous_mission else []), with_goal(problem, facts=positive))
        root.children_ids.append(mid)
        nodes[mid] = mission
        previous_task = None
        for t in group:
            node = set_node_goal(Task(id=t['id'], parent_id=mid, assigned_agent_id=t['actor'], dependencies=[previous_task] if previous_task else []), with_goal(problem, facts=t['goals']))
            mission.children_ids.append(node.id)
            nodes[node.id] = node
            for j, s in enumerate(t['chain']):
                if s['agent_id'] != t['actor']:
                    raise ValueError('original Task cannot silently change leaf actor')
                pid = f'{node.id}.p{j + 1}'
                primitive = Primitive(id=pid, parent_id=node.id, primitive_name=s['primitive'], primitive_args=deepcopy(s['params']), dependencies=[node.children_ids[-1]] if node.children_ids else [])
                node.children_ids.append(pid)
                nodes[pid] = primitive
            previous_task = node.id
        previous_mission = mid
    return DecompositionTree(root_id=root.id, nodes=nodes)

"""D m."""
from __future__ import annotations
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from planning.schema.decomposition import Mission
from planning.schema.decomposition import Task
from domains.predicates import sanitize_predicate
from validation.contracts import validate_D_M_output
from validation.contracts import render_feedback_for_llm
from models.examples import load_few_shot_examples
from models.examples import render_few_shot_prompt
from planning.decomposers.d_s import DecompositionFailure
from planning.decomposers.d_s import _extract_json
_SYSTEM = 'You are a Mission-level task decomposer for lunar multi-agent operations. Given a Mission and the current system state, produce a Task list (a DAG) satisfying:\n1. Contract 1: the conjunction of Task postconditions entails the Mission postcondition.\n2. Contract 2: the first Task\'s precondition is implied by the current state.\n3. Contract 3: along the dependency order, each Task\'s precondition is implied by the current state plus prior Tasks\' postconditions.\n4. Each Task is assigned to a SPECIFIC agent id (not just a type).\n5. Each Task uses only primitives in that agent\'s capability set.\n\nCritical principle: Tasks must reflect the CURRENT agent state (energy, position, storage). The same Mission decomposes differently if rover_1 is at base with full battery vs in the PSR with 30% battery.\n\nPredicate format: atomic predicates `name(arg1, arg2)` joined by \' & \'; reuse names across pre/postconditions for syntactic entailment.\n\nOutput STRICT JSON only, no prose:\n{"tasks":[{"id":"...","goal_nl":"...","assigned_agent_id":"rover_1","precondition":"...","postcondition":"...","required_primitives":["move_to"],"dependencies":[]}],"decomposition_rationale":"...","confidence":0.0}'

def build_d_m_prompt(mission: Mission, current_state: dict, agents: List[Any], scenario: str='psr', feedback: str='') -> Tuple[str, str]:
    few_shot = render_few_shot_prompt(load_few_shot_examples(scenario, 'd_m'))
    agents_render = json.dumps([_agent_brief(a) for a in agents], ensure_ascii=False)
    user = f'Mission: {mission.goal_nl}\nMission precondition: {mission.precondition}\nMission postcondition: {mission.postcondition}\nCurrent state: {json.dumps(current_state, ensure_ascii=False)}\nAvailable agents (with capabilities): {agents_render}\n\n{few_shot}\n{feedback}\nDecompose this Mission into a Task list. Return JSON only.'
    return (_SYSTEM, user)

def _agent_brief(a: Any) -> dict:
    if isinstance(a, dict):
        return {k: a.get(k) for k in ('id', 'type', 'agent_type', 'capabilities') if k in a}
    return {'id': getattr(a, 'id', None), 'type': getattr(a, 'type', getattr(a, 'agent_type', None)), 'capabilities': getattr(a, 'capabilities', None)}

def parse_tasks(text: str, parent_mission_id: str) -> List[Task]:
    obj = _extract_json(text)
    tasks: List[Task] = []
    if not obj or not isinstance(obj.get('tasks'), list):
        return tasks
    for t in obj['tasks']:
        if not isinstance(t, dict) or 'id' not in t:
            continue
        tasks.append(Task(id=str(t['id']), parent_id=parent_mission_id, goal_nl=str(t.get('goal_nl', '')), assigned_agent_id=str(t.get('assigned_agent_id', '')), precondition=sanitize_predicate(str(t.get('precondition', ''))), postcondition=sanitize_predicate(str(t.get('postcondition', ''))), required_primitives=[str(x) for x in t.get('required_primitives', [])], dependencies=[str(x) for x in t.get('dependencies', [])], estimated_duration_s=float(t.get('estimated_duration_s', 0.0) or 0.0), context=dict(t.get('context', {}))))
    return tasks

def D_M_with_validation(mission: Mission, current_state: dict, available_agents: List[Any], base_llm_client: Any=None, max_retries: int=3, scenario: str='psr', scene_goal: Any=None) -> List[Task]:
    """Mission → Task decomposition with per-layer validation + feedback retry (A2 §2.4).
    `scene_goal` (optional Scene) is forwarded to validation so argument-validity treats
    Scene-level goal entities as legitimate (Bug-3 fix)."""
    if base_llm_client is None:
        raise ValueError('base_llm_client is required')
    history: List[dict] = []
    feedback = ''
    for attempt in range(max_retries + 1):
        system, user = build_d_m_prompt(mission, current_state, available_agents, scenario=scenario, feedback=feedback)
        raw = base_llm_client.generate(system, user)
        tasks = parse_tasks(raw, parent_mission_id=mission.id)
        if not tasks:
            reasons, vr = (['output did not parse into any tasks'], None)
        else:
            vr = validate_D_M_output(tasks, mission, current_state, available_agents, scene_goal=scene_goal)
            reasons = vr.reasons
        if not reasons:
            return tasks
        history.append({'attempt': attempt, 'raw': raw, 'reasons': reasons})
        feedback = render_feedback_for_llm(tasks, vr) if vr is not None else 'Your previous output was rejected: ' + '; '.join(reasons) + ". Return STRICT JSON with a non-empty 'tasks' array."
    raise DecompositionFailure(layer='Mission', history=history)

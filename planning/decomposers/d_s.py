"""D s."""
from __future__ import annotations
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from planning.schema.decomposition import Scene
from planning.schema.decomposition import Mission
from domains.predicates import sanitize_predicate
from validation.contracts import validate_D_S_output
from validation.contracts import render_feedback_for_llm
from models.examples import load_few_shot_examples
from models.examples import render_few_shot_prompt
MISSION_CAP = 8

class DecompositionFailure(Exception):

    def __init__(self, layer: str, history: List[dict]):
        self.layer = layer
        self.history = history
        super().__init__(f'{layer} decomposition failed after {len(history)} attempt(s)')
_SYSTEM = f"""You are a Scene-level task decomposer for lunar multi-agent operations. Given a Scene goal and environment, produce a Mission decomposition (a DAG) satisfying:\n1. Goal sufficiency (Contract 1): the conjunction of Mission postconditions entails the Scene goal.\n2. Sequential consistency (Contracts 2/3): along the dependency order, each Mission's precondition is implied by the initial state plus prior Missions' postconditions.\n3. Resource feasibility: total estimated duration within the time budget.\n4. Agent-type coverage: each Mission has at least one compatible agent type available.\n5. Use at most {MISSION_CAP} Missions.\n\nCritical principle: decomposition is CONTEXT-DEPENDENT — adapt the Mission structure to the specific terrain, agent availability, and hazards in THIS scenario.\n\nPredicate format: atomic predicates `name(arg1, arg2)` joined by ' & ' (e.g. 'deployed(rover_1) & relay_ready(relay_1)'). Use the SAME predicate names across preconditions/postconditions so entailment can be checked syntactically.\n\nOutput STRICT JSON only, no prose:\n{{"missions":[{{"id":"...","goal_nl":"...","precondition":"...","postcondition":"...","required_agent_types":["ROVER"],"dependencies":[],"estimated_duration_s":0}}],"decomposition_rationale":"...","confidence":0.0}}"""

def build_d_s_prompt(scene_goal: Scene, environment: dict, agents: List[Any], constraints: Optional[dict]=None, scenario: str='psr', feedback: str='') -> Tuple[str, str]:
    few_shot = render_few_shot_prompt(load_few_shot_examples(scenario, 'd_s'))
    agents_render = json.dumps([_agent_brief(a) for a in agents], ensure_ascii=False)
    user = f"Scene goal: {scene_goal.goal_nl}\nFormal goal: {scene_goal.formal_postcondition or scene_goal.postcondition}\nInitial state: {environment.get('initial_state', '')}\nEnvironment: {json.dumps(environment, ensure_ascii=False)}\nAvailable agents: {agents_render}\nConstraints: {json.dumps(constraints or {}, ensure_ascii=False)}\n\n{few_shot}\n{feedback}\nDecompose this Scene into a Mission DAG. Return JSON only."
    return (_SYSTEM, user)

def _agent_brief(a: Any) -> dict:
    if isinstance(a, dict):
        return {k: a.get(k) for k in ('id', 'type', 'agent_type', 'capabilities') if k in a}
    return {'id': getattr(a, 'id', None), 'type': getattr(a, 'type', getattr(a, 'agent_type', None))}

def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if '```' in t:
        for seg in t.split('```'):
            seg = seg[4:] if seg.lower().startswith('json') else seg
            if '{' in seg:
                t = seg
                break
    start = t.find('{')
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == '{':
            depth += 1
        elif t[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None

def parse_missions(text: str, parent_id: str) -> List[Mission]:
    """Parse a D_S JSON response into schema.Mission objects (tolerant of missing fields)."""
    obj = _extract_json(text)
    missions: List[Mission] = []
    if not obj or not isinstance(obj.get('missions'), list):
        return missions
    for m in obj['missions']:
        if not isinstance(m, dict) or 'id' not in m:
            continue
        missions.append(Mission(id=str(m['id']), parent_id=parent_id, goal_nl=str(m.get('goal_nl', '')), precondition=sanitize_predicate(str(m.get('precondition', ''))), postcondition=sanitize_predicate(str(m.get('postcondition', ''))), required_agent_types=[str(x) for x in m.get('required_agent_types', [])], dependencies=[str(x) for x in m.get('dependencies', [])], estimated_duration_s=float(m.get('estimated_duration_s', 0.0) or 0.0)))
    return missions

def D_S_with_validation(scene_goal: Scene, environment: dict, agents: List[Any], constraints: Optional[dict]=None, base_llm_client: Any=None, max_retries: int=2, scenario: str='psr') -> List[Mission]:
    """Call the base LLM with per-layer validation + feedback retry (A2 §1.6)."""
    if base_llm_client is None:
        raise ValueError('base_llm_client is required')
    history: List[dict] = []
    feedback = ''
    for attempt in range(max_retries + 1):
        system, user = build_d_s_prompt(scene_goal, environment, agents, constraints, scenario=scenario, feedback=feedback)
        raw = base_llm_client.generate(system, user)
        missions = parse_missions(raw, parent_id=scene_goal.id)
        if not missions:
            reasons = ['output did not parse into any missions']
            vr = None
        elif len(missions) > MISSION_CAP:
            reasons = [f'too many missions ({len(missions)} > cap {MISSION_CAP})']
            vr = None
        else:
            vr = validate_D_S_output(missions, scene_goal, environment, agents)
            reasons = vr.reasons
        if not reasons:
            return missions
        history.append({'attempt': attempt, 'raw': raw, 'reasons': reasons})
        feedback = render_feedback_for_llm(missions, vr) if vr is not None else 'Your previous output was rejected: ' + '; '.join(reasons) + ". Return STRICT JSON with a non-empty 'missions' array."
    raise DecompositionFailure(layer='Scene', history=history)

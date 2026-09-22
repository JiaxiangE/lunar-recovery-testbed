"""Default-off instruction and initial-only symbolic support; never a planner."""
from copy import deepcopy
import json
import time
POLICY = {'version': 'state_remaining_obligations_instruction_v1', 'algorithm': ['Start from request_initial_facts, keeping facts already true. Track the current assigned goal and all explicit required primitives, fresh-action requirements, dependencies and process obligations.', 'Evaluate positive, negative and cardinality goals in the current state, and track remaining explicit obligations separately. An already true goal does not waive a required action or process obligation.', 'Add an action only when needed for an unmet goal or an explicit remaining obligation, including necessary preparatory steps. Do not regenerate a fact already established unless another unmet obligation or an explicit fresh-action requirement needs that action.', 'Before selecting each action, check every positive and negative precondition against the state reached so far. A legal primitive/actor/params tuple alone does not make the action executable.', 'Advance the state using the supplied original add/delete effects and conditional effects. Evaluate conditional-effect conditions in the pre-action state; do not keep facts which the action deletes.', 'Repeat state and obligation checks after each action. End the chain when all current Task goals and explicit obligations are satisfied; do not add work solely for separately reported parent goals. If no valid plan can be supplied, use the permitted refusal interface without claiming impossibility.'], 'output': 'Keep the existing diagnostic output schema and brief basis only. Do not output private reasoning or an internal step-by-step thought trace.', 'boundary': 'This is planning guidance, not an extra goal, action-count cap, execution certificate or permission to waive any source obligation.'}

def applicable_at_state(problem, state, actor=None):
    """All applicable source tuples, no goal test, resource guess or search."""
    started = time.perf_counter()
    state = frozenset(state)
    unique = {}
    checked = 0
    matched = 0
    for action in problem.actions:
        if actor is not None and action.agent_id != actor:
            continue
        checked += 1
        if action.applicable(state):
            matched += 1
            step = action.to_step()
            unique[json.dumps(step, sort_keys=True, separators=(',', ':'))] = step
    steps = [unique[k] for k in sorted(unique)]
    return (steps, {'source_action_rows_tested': checked, 'applicable_source_rows': matched, 'unique_applicable_tuples': len(steps), 'wall_s': time.perf_counter() - started, 'world_steps': 0, 'rollouts': 0, 'state_transition_computations': 0, 'model_calls': 0, 'goal_search_performed': False})

def initial_support(problem, actor=None):
    steps, cost = applicable_at_state(problem, problem.initial_state, actor)
    return ({'initial_applicable_actions': steps, 'scope': 'Only the first step from request_initial_facts, under the original symbolic positive/negative preconditions and the existing actor bound. This is not an energy forecast or goal-directed ranking.', 'later_steps': 'For every later step use the complete unchanged action domain and original effects to recompute applicability from the preceding state. Actions absent from this initial list may become legal later. Never restrict the whole chain to the initial list.', 'authority': 'Advisory source-derived input, not a replacement for the unchanged full tuple schema, strict gate or actual executor.'}, cost)

def augment(system, user, *, enabled=False, initial=None):
    if not enabled:
        if initial is not None:
            raise ValueError('initial support requires explicit planning-guidance opt-in')
        return (system, user)
    p = json.loads(user)
    if 'state_and_remaining_obligations_policy' in p or 'initial_action_support' in p:
        raise ValueError('guidance already present')
    p['state_and_remaining_obligations_policy'] = deepcopy(POLICY)
    system = 'Use state_and_remaining_obligations_policy to construct a plan from the actual initial state and remaining explicit obligations. ' + system
    if initial is not None:
        p['initial_action_support'] = deepcopy(initial)
        system = 'initial_action_support applies only to the first step; later applicability must be recomputed using the full domain and original effects. ' + system
    return (system, json.dumps(p, ensure_ascii=False, separators=(',', ':')))

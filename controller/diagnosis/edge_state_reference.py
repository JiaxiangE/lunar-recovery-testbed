"""Fixed observable-precondition comparator for the bounded PSR study.

This is a policy encoded through the unchanged selector, not a calibrated
estimate of latent causes. No gate outcomes, witnesses, or model responses.
"""
from controller.scope import Scope
from controller.scope import SCOPES
from controller.diagnosis.post_rule import smooth_distribution

def distribution(failure, problem, information, *, include_raw=False):
    nodes = information.get('tree', {}).get('nodes', {})
    node = nodes.get(failure.node_id, {})
    primitive = node.get('primitive_name')
    params = node.get('primitive_args')
    actions = [a for a in problem.actions if a.primitive == primitive and a.agent_id == failure.agent_id and (a.params == params)]
    supported = problem.scenario_id == 'psr' and failure.symptom == 'SAMPLE_POSITION_MISMATCH' and (primitive == 'sample_collect') and (len(actions) == 1)
    applicable = False
    if supported:
        action = actions[0]
        applicable = action.preconditions <= problem.initial_state and (not action.negative_preconditions & problem.initial_state)
    raw = {s: float(s == (Scope.P if applicable else Scope.T)) if supported else 0.25 for s in SCOPES}
    return (smooth_distribution(raw), {**({'raw_distribution': {s.value: v for s, v in raw.items()}} if include_raw else {}), 'supported': supported, 'matched_actions': len(actions), 'source_preconditions_satisfied': applicable if supported else None, 'action_rows_inspected': len(problem.actions), 'world_rollouts': 0, 'world_steps': 0, 'rule': 'named collect current grounded positive/negative preconditions; P if applicable, T otherwise; unsupported uniform', 'causal_diagnosis_claim': False})

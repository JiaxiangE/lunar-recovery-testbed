"""Record the existing two-stage choice without changing its policy."""
from copy import deepcopy
from dataclasses import asdict
from dataclasses import replace
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order
from controller.calibration.selector_config_v2 import SELECTOR_CONFIG_VERSION
VERSION = 'scope_decision_record_v1'

def names(values):
    return [s.value for s in sorted(values, key=scope_order)]

def choose(selector, failure, posterior, ctx, *, connected, actor_edge_scopes, operation_scopes, audit):
    operation = set(SCOPES if operation_scopes is None else (Scope(s) for s in operation_scopes))
    communication = set(SCOPES) if connected else set(actor_edge_scopes)
    permitted = operation & communication
    audit.update(version=VERSION, status='not_invoked', selector_invoked=False, posterior={Scope(k).value: float(v) for k, v in posterior.items()}, ctx=deepcopy(ctx), base_reachable_used=connected, actor_edge_scopes=None if actor_edge_scopes is None else names(actor_edge_scopes), actor_edge_scope_note='not queried/used on the original Connected branch' if connected else 'observed actor c_edge_for result used for intersection', communication_actor_scopes=names(communication), operation_scopes=names(operation), final_permitted_scopes=names(permitted), selector_input_c_edge=names(permitted), parameters={'weights': asdict(selector.weights), 'p_fix': asdict(selector.p_fix_params), 'subtree_size': {s.value: v for s, v in selector.subtree_size.items()}, 'llm_cost': {s.value: v for s, v in selector.llm_cost.items()}, 'undershoot_mult': selector.undershoot_mult, 'configuration_version': SELECTOR_CONFIG_VERSION}, tie_break='max(score, -scope_order); narrower wins exact ties', interpretation='raw selection is before the operation post-filter; communication may already constrain it. Initial selection is not later escalation.')
    if not permitted:
        audit['reason'] = 'empty_communication_operation_intersection'
        raise ValueError('no source-authorized scope within the declared operation bounds')
    audit.update(selector_invoked=True, status='selector_pending')
    try:
        decision = selector.select(failure, posterior, ctx, permitted, base_reachable=connected)
    except Exception as exc:
        audit.update(status='selector_failed', reason=type(exc).__name__)
        raise
    admissible = [s for s in decision.available_scopes if s in permitted]
    if not admissible:
        audit.update(status='no_admissible_scope', reason='empty_after_selector')
        raise ValueError('no scope satisfies communication and operation constraints')
    selected = max(admissible, key=lambda s: (decision.scores[s], -scope_order(s)))
    panels = {s.value: {'score': score, 'terms': selector._terms(s, decision.delta_posterior, ctx)} for s, score in decision.scores.items()}
    raw = decision.sigma
    final = replace(decision, sigma=selected, available_scopes=admissible, utility_breakdown=deepcopy(panels[selected.value]['terms']))
    audit.update(status='selected', initial_selection_before_operation_filter=raw.value, selector_available_scopes=names(decision.available_scopes), comm_constrained=decision.comm_constrained, evaluated_scopes=panels, original_selected_terms=deepcopy(decision.utility_breakdown), selected_scope=selected.value, final_selected_terms=deepcopy(final.utility_breakdown), operation_filter_changed_selection=raw != selected, allowed_escalation_sequence=[s.value for s in SCOPES if s in permitted and scope_order(s) >= scope_order(selected)])
    return (final, raw, permitted)

"""Observable optional client integration; priors, prompt and smoothing unchanged."""
from copy import deepcopy
import json
import math
import time
from collections.abc import Mapping
from controller.diagnosis.delta import DiagnosisModule
from controller.diagnosis.delta import FailureEvent
from controller.diagnosis.delta import RULE_PRIORS
from controller.diagnosis.delta import _SYSTEM
from controller.scope import SCOPES

class RequestFailure(Exception):
    pass

class ObservedDiagnosis(DiagnosisModule):

    def _llm_layer(self, failure, client):
        owner = self

        class TextClient:

            def generate(self, system, user):
                owner.audit['llm_requested'] = True
                try:
                    result = client.generate(system, user)
                except Exception as exc:
                    owner.audit['request_error'] = {'type': type(exc).__name__, 'detail': str(exc)}
                    raise RequestFailure() from exc
                owner.audit['return_type'] = type(result).__name__
                if isinstance(result, str):
                    from controller.diagnosis.delta import _extract_json
                    parsed = _extract_json(result)
                    owner.audit['response_category'] = 'declined' if isinstance(parsed, dict) and parsed.get('decline') is True else 'distribution_present' if isinstance(parsed, dict) and 'distribution' in parsed else 'missing_distribution' if isinstance(parsed, dict) else 'malformed_or_nonobject_json'
                    return result
                if isinstance(result, Mapping) and 'distribution' in result:
                    return json.dumps(result)
                from models.clients.clients import _response_text
                return _response_text(result)
        try:
            dist = super()._llm_layer(failure, TextClient())
            if any((not math.isfinite(v) or v < 0 or v > 1 for v in dist.values())):
                raise ValueError('invalid probability values')
            if self.audit.get('interface_version'):
                self.audit['raw_distribution'] = {s.value: v for s, v in dist.items()} if self._last_llm_parse_ok else None
            return dist
        except (ValueError, TypeError, AttributeError) as exc:
            self._last_llm_parse_ok = False
            self.audit['parse_error'] = {'type': type(exc).__name__, 'detail': str(exc)}
            return {s: 1 / len(SCOPES) for s in SCOPES}

def diagnose(session, failure, problem, information, mode):
    started = time.perf_counter()
    if mode not in {'offline_v2', 'connected_refinement', 'edge_diagnostic_refinement', 'edge_observation_reference'}:
        raise ValueError('unknown diagnosis mode')
    context = deepcopy(failure.context)
    context['observations'] = {k: deepcopy(information[k]) for k in ('pre_deviation_observation', 'post_deviation_observation', 'executed_prefix', 'shared_resource_information') if k in information}
    context['current_facts'] = sorted(problem.initial_state)
    from planning.repair.actor_interface import VERSION as interface_version
    v2 = information.get('observation_packet_version') == interface_version
    if v2:
        for key in ('failure_snapshot', 'current_decision_snapshot', 'observation_updates', 'observation_timeline', 'observation_interpretation'):
            context[key] = deepcopy(information[key])
        context['actor_types'] = deepcopy(problem.observation['actor_types'])
    observed = FailureEvent(failure.symptom, failure.agent_id, failure.node_id, context)
    dx = ObservedDiagnosis()
    rule = dx._rule_layer(observed)
    dx.audit = {'mode': mode, 'failure': {'symptom': observed.symptom, 'agent_id': observed.agent_id, 'node_id': observed.node_id, 'context': context}, 'rule_matched': rule is not None, 'rule_confident': rule is not None and dx._is_confident(rule), 'client_injected': False, 'llm_requested': False, 'fallback_reason': None, 'logical_before': session.logical_n}
    if v2:
        dx.audit['interface_version'] = interface_version
    client = None
    if mode == 'edge_observation_reference':
        from controller.diagnosis.edge_state_reference import distribution
        posterior, reference = distribution(failure, problem, information, include_raw=v2)
        if v2:
            dx.audit['raw_distribution'] = reference['raw_distribution']
        dx.audit.update(diagnosis_source='observable_precondition_reference', parse_ok=None, posterior={s.value: v for s, v in posterior.items()}, logical_after=session.logical_n, reference=reference, wall_s=time.perf_counter() - started, interpretation='predeclared action-applicability policy encoded as distribution; not a calibrated causal posterior')
        return (posterior, dx.audit)
    if mode == 'edge_diagnostic_refinement':
        if not hasattr(session, 'diagnosis_client'):
            dx.audit['fallback_reason'] = 'client_not_configured'
        else:
            client = session.diagnosis_client(tier='edge')
            dx.audit['client_injected'] = True
    elif mode == 'connected_refinement':
        if problem.observation['communication_policy'].get('state') != 'Connected':
            dx.audit['fallback_reason'] = 'communication_not_connected'
        elif not hasattr(session, 'diagnosis_client'):
            dx.audit['fallback_reason'] = 'client_not_configured'
        else:
            client = session.diagnosis_client()
            dx.audit['client_injected'] = True
    else:
        dx.audit['fallback_reason'] = 'explicit_offline_mode'
    try:
        posterior = dx.diagnose(observed, base_llm_client=client)
    except RequestFailure:
        posterior = dx.diagnose(observed, base_llm_client=None)
        dx.last_source = 'rule_llm_request_fail' if rule is not None else 'uniform_llm_request_fail'
        dx.last_parse_ok = False
        dx.audit['fallback_reason'] = 'diagnosis_request_failed'
    if dx.last_source == 'uniform_llm_parse_fail':
        dx.audit['fallback_reason'] = 'diagnosis_parse_failed'
    if dx.last_source == 'rule_confident':
        dx.audit['fallback_reason'] = None
    if v2 and (not dx.audit['llm_requested']):
        dx.audit['raw_distribution'] = {s.value: float((rule or {}).get(s, 1 / len(SCOPES))) for s in SCOPES}
    dx.audit.update(diagnosis_source=dx.last_source, parse_ok=dx.last_parse_ok, posterior={s.value: v for s, v in posterior.items()}, logical_after=session.logical_n, wall_s=time.perf_counter() - started, interpretation='cause-level posterior feeds frozen utility selector; not a chosen scope or diagnosis-accuracy score')
    return (posterior, dx.audit)

"""Symmetric candidate-local preparation; runtime adoption defaults OFF.

No model calls or winning-plan cache. Callers provide only their own selected,
fully received native leaf responses plus the assembled candidate. Original
failed/unselected responses stay in the referenced existing trial/calls record.
"""
from copy import deepcopy
import json
import time
from planning.repair.candidate_format import CandidateFormatError
from planning.repair.candidate_format import parse_leaf
from planning.repair.output_contract import parse_generation
from planning.repair.prefix_recomposition import propose
from planning.repair.resource_support import ResourceSupport
from planning.repair.resource_support import validate_resource_children
from execution.binding import execute_plan
from domains.transition import observe
from domains.transition import binding_observation

class RecompositionController:

    def __init__(self, *, allow_adoption=False, resource_support=None):
        self.allow_adoption = allow_adoption
        self.resource_support = resource_support if resource_support is not None else ResourceSupport()
        self._prepared = None
        self._consumed = False

    def prepare_trial(self, case, problem, *, method, trial, calls, selected_ordinals, candidate, scope, source_reference):
        """Bind an offline/next-version selection to that method's existing record."""
        self._prepared = None
        if trial.get('method') != method or calls.get('trial_id') != trial.get('trial_id'):
            return {'status': 'TRIAL_CALL_OR_METHOD_MISMATCH', 'dispatch_n': 0, 'new_candidate': None}
        if not selected_ordinals or selected_ordinals != sorted(set(selected_ordinals)):
            return {'status': 'INVALID_REQUEST_SELECTION', 'dispatch_n': 0, 'new_candidate': None}
        indexed = {r['ordinal']: r for r in calls.get('requests', [])}
        if any((n not in indexed for n in selected_ordinals)):
            return {'status': 'INVALID_REQUEST_SELECTION', 'dispatch_n': 0, 'new_candidate': None}
        parts = [{'method': method, 'operation': indexed[n]['operation'], 'raw_response': indexed[n].get('response'), 'actor': indexed[n].get('route', {}).get('actor'), 'request_ordinal': n} for n in selected_ordinals]
        result = self.prepare(case, problem, method=method, scope=scope, candidate=candidate, source_reference=source_reference, raw_parts=parts)
        result.update(source_trial_id=trial['trial_id'], source_trial_status=trial.get('status'), selected_request_ordinals=list(selected_ordinals))
        if self._prepared is not None:
            self._prepared = deepcopy(result)
        return result

    def prepare(self, case, problem, *, method, scope, candidate, source_reference, raw_parts):
        from planning.repair.domain_hierarchy import IMPLEMENTATION_ID
        from planning.repair.domain_hierarchy import FLAT_IMPLEMENTATION_ID
        started = time.perf_counter()
        event = {'method': method, 'scope': scope, 'source_reference': source_reference, 'raw_parts': deepcopy(raw_parts), 'original_candidate': deepcopy(candidate), 'new_candidate': None, 'adoption_enabled': self.allow_adoption, 'dispatch_n': 0, 'model_calls': 0}
        self._prepared = None
        self._consumed = False

        def finish(status, **extra):
            return {**event, **extra, 'status': status, 'preparation_wall_s': time.perf_counter() - started}
        if method not in {IMPLEMENTATION_ID, FLAT_IMPLEMENTATION_ID}:
            return finish('METHOD_NOT_ELIGIBLE')
        if not isinstance(raw_parts, list) or any((not isinstance(p, dict) or not isinstance(p.get('operation'), str) or (not isinstance(p.get('raw_response'), str)) for p in raw_parts)):
            return finish('SOURCE_RECORD_FORMAT_REJECTED')
        if not raw_parts or any((p.get('method') != method for p in raw_parts)):
            return finish('SOURCE_METHOD_MISMATCH')
        try:
            combined = []
            for part in raw_parts:
                operation = part['operation']
                if operation not in {'D_T', 'flat'}:
                    raise CandidateFormatError('complete selected leaf responses required')
                parsed = parse_generation(part['raw_response'], operation)
                if 'decline' in parsed:
                    raise CandidateFormatError('decline supplies no candidate')
                chain = parse_leaf(part['raw_response'], actor=part.get('actor'))
                combined.extend(chain)
            parse_generation(json.dumps({'chain': candidate}), 'flat')
            key = lambda plan: [{k: s[k] for k in ('primitive', 'agent_id', 'params')} for s in plan]
            if key(combined) != key(candidate):
                return finish('SOURCE_CANDIDATE_MISMATCH')
            result = propose(case, problem, candidate, scope=scope, source_reference=source_reference)
        except CandidateFormatError as exc:
            return finish('CANDIDATE_FORMAT_REJECTED', format_error=str(exc))
        event['proposal'] = result
        if result.get('candidate') is None:
            return finish(result['status'])
        child_parent = validate_resource_children(problem, result['new_children'], result['candidate'])
        aid = self.resource_support
        forecast = aid.forecast(case.world, problem, result['candidate'])
        event.update(new_candidate=deepcopy(result['candidate']), child_parent_check=child_parent, resource_prediction=forecast, local_resource_work=aid.report(), resource_work_scope='cumulative per-arm/controller snapshot; do not add preparation and adoption snapshots', attribution='candidate-local postprocessing, not original model output or causal module benefit')
        if not child_parent['accepted']:
            return finish('CHILD_PARENT_RECHECK_FAILED')
        if not aid.may_execute(forecast):
            return finish('RESOURCE_CHECK_NOT_PASSED')
        event = finish('PREPARED_NOT_DISPATCHED')
        self._prepared = deepcopy(event)
        return event

    def adopt(self, prepared, *, case, problem):
        started = time.perf_counter()

        def reject(reason, **kwargs):
            return {'status': reason, 'dispatch_n': 0, 'adoption_wall_s': time.perf_counter() - started, **kwargs}
        if not self.allow_adoption:
            return reject('AUTOMATIC_ADOPTION_DISABLED')
        if self._consumed:
            return reject('ADOPTION_ALREADY_CONSUMED')
        if self._prepared is None or prepared != self._prepared:
            return reject('PREPARED_CANDIDATE_CHANGED')
        if observe(case.world, problem.scenario_id) != problem.initial_state or binding_observation(case.world, problem.scenario_id) != problem.observation.get('world'):
            return reject('CURRENT_STATE_CHANGED')
        proposal = propose(case, problem, prepared['original_candidate'], scope=prepared['scope'], source_reference=prepared['source_reference'])
        if proposal.get('candidate') != prepared['new_candidate']:
            return reject('SOURCE_OR_POLICY_CHANGED')
        check = validate_resource_children(problem, proposal['new_children'], proposal['candidate'])
        aid = self.resource_support
        prediction = aid.forecast(case.world, problem, proposal['candidate'])
        if not check['accepted'] or not aid.may_execute(prediction):
            return reject('ADOPTION_RECHECK_FAILED', child_parent_check=check, resource_prediction=prediction, local_resource_work=aid.report())
        self._consumed = True
        execution = execute_plan(case.world, problem, proposal['candidate'], capture_transitions=True)
        return {'status': 'executed' if execution.get('execution_success') else 'execution_failed', 'dispatch_n': execution['dispatch_n'], 'execution': execution, 'resource_prediction': prediction, 'local_resource_work': aid.report(), 'adoption_wall_s': time.perf_counter() - started, 'resource_work_scope': 'cumulative per-arm/controller snapshot; not a fresh budget', 'adopted_candidate': deepcopy(proposal['candidate']), 'source_reference': prepared['source_reference']}

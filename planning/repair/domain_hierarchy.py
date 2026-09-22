"""Generated Scene/Mission/Task children, local repair and complete parent checking.

This version retains the existing node schemas, D_S/D_M response parsers,
P retry dispatcher, diagnosis and selector. D_T now requires action objects;
the legacy string-normalizing parser is not used for recovery candidates. The finite-domain verifier
adds actor/parameter/delete/cardinality semantics missing from legacy declarative
layer checks. It never executes speculative children or rolls back a robot.
Historical preassigned/cascade=1 experiments do not use this module.
"""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
from planning.schema.decomposition import Scene
from planning.schema.decomposition import Mission
from planning.schema.decomposition import Task
from planning.schema.decomposition import Primitive
from planning.schema.decomposition import DecompositionTree
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order
from controller.diagnosis import DiagnosisModule
from controller.diagnosis import FailureEvent
from controller.selector import ScopeSelector
from controller.settings import frozen_weights
from controller.settings import frozen_p_fix_params
from planning.decomposers.d_m import parse_tasks
from planning.decomposers.d_s import parse_missions
from planning.decomposers.scoped_generation import generate_for_scope
from planning.decomposers.scoped_generation import ScopedGenerationRequest
from domains.transition import atom
from domains.transition import with_goal
from execution.binding import verify_plan
from execution.binding import execute_plan
from domains.predicates import parse_predicate
from planning.repair.candidate_format import CandidateFormatError
from planning.repair.candidate_format import object_response
from planning.repair.candidate_format import parse_leaf
IMPLEMENTATION_ID = 'paper4_generated_hierarchy_repair_v1'
FLAT_IMPLEMENTATION_ID = 'paper4_whole_plan_equal_budget_v2'

@dataclass(frozen=True)
class HierarchyLimits:
    max_model_logical: int = 24
    node_attempts: int = 2
    max_children: int = 6
    nominal_policy_call_cap: int = 8

    def __post_init__(self):
        for name in ('max_model_logical', 'nominal_policy_call_cap', 'node_attempts', 'max_children'):
            value = getattr(self, name)
            minimum = 0 if name in ('max_model_logical', 'nominal_policy_call_cap') else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f'invalid hierarchy limit: {name}')

def goal_record(problem):
    return {'positive': sorted(problem.goal_facts), 'negative': sorted(problem.negative_goal_facts), 'cardinality': [[k, sorted(atoms)] for k, atoms in problem.goal_cardinality]}

def problem_for_node(problem, node):
    value = node.context.get('domain_goal')
    terms = parse_predicate(node.formal_postcondition or node.postcondition if isinstance(node, Scene) else node.postcondition).atomics
    if value is None:
        value = {'positive': [atom(a.name, *a.args) for a in terms if not a.negated], 'negative': [atom(a.name, *a.args) for a in terms if a.negated], 'cardinality': []}
    else:
        textual_positive = {atom(a.name, *a.args) for a in terms if not a.negated}
        textual_negative = {atom(a.name, *a.args) for a in terms if a.negated}
        if textual_positive != set(value.get('positive', ())) or not textual_negative <= set(value.get('negative', ())):
            raise ValueError('child goal context contradicts the declared postcondition')
    return with_goal(problem, facts=value.get('positive', ()), negative=value.get('negative', ()), cardinality=[(k, frozenset(v)) for k, v in value.get('cardinality', ())])

def set_node_goal(node, problem):
    node.context['domain_goal'] = goal_record(problem)
    node.postcondition = ' & '.join(sorted(problem.goal_facts))
    if isinstance(node, Scene):
        node.formal_postcondition = node.postcondition
    return node

def ordered_children(tree, node):
    remaining = [tree.nodes[i] for i in node.children_ids]
    completed, output = (set(), [])
    while remaining:
        child = next((n for n in remaining if set(n.dependencies) <= completed), None)
        if child is None:
            raise ValueError('cyclic or external child dependency')
        remaining.remove(child)
        completed.add(child.id)
        output.append(child)
    return output

def leaves(tree, node=None):
    node = tree.nodes[tree.root_id] if node is None else node
    if isinstance(node, Primitive):
        task = tree.nodes[node.parent_id]
        return [(node.id, {'primitive': node.primitive_name, 'agent_id': task.assigned_agent_id, 'params': deepcopy(node.primitive_args)})]
    return [item for child in ordered_children(tree, node) for item in leaves(tree, child)]

def ancestor(tree, node_id, scope):
    node = tree.nodes[node_id]
    while node.layer != Scope(scope).value:
        if node.parent_id is None:
            raise ValueError('selected scope absent from original hierarchy')
        node = tree.nodes[node.parent_id]
    return node

def _pre_met(node, state):
    predicates = parse_predicate(node.precondition)
    return all((atom(a.name, *a.args) in state if not a.negated else atom(a.name, *a.args) not in state for a in predicates.atomics))

class _Rejected(Exception):

    def __init__(self, reason, *, failure_origin=None, failure_node_id=None):
        self.reason = reason
        self.failure_origin = failure_origin
        self.failure_node_id = failure_node_id

def _parse_children(raw, parent):
    key = 'missions' if isinstance(parent, Scene) else 'tasks'
    value = object_response(raw)
    if value.get('decline') is True:
        raise _Rejected('MODEL_DECLINED')
    rows = value.get(key)
    if not isinstance(rows, list) or not rows or any((not isinstance(r, dict) for r in rows)):
        raise _Rejected('MALFORMED_DECOMPOSITION')
    try:
        children = parse_missions(raw, parent.id) if key == 'missions' else parse_tasks(raw, parent.id)
    except (ValueError, TypeError, KeyError) as exc:
        raise CandidateFormatError('invalid generated child fields: ' + str(exc)) from exc
    if len(children) != len(rows) or len({c.id for c in children}) != len(children):
        raise _Rejected('MALFORMED_DECOMPOSITION')
    for child, row in zip(children, rows):
        import math
        if not math.isfinite(child.estimated_duration_s) or child.estimated_duration_s < 0:
            raise _Rejected('MALFORMED_CHILD_DURATION')
        context = row.get('context', {})
        if not isinstance(context, dict):
            raise _Rejected('MALFORMED_CHILD_CONTEXT')
        child.context = deepcopy(context)
    return children

def _generated_child_problem(problem, child):
    try:
        return problem_for_node(problem, child)
    except (ValueError, TypeError, KeyError) as exc:
        raise CandidateFormatError('invalid child contract: ' + str(exc)) from exc

class GeneratedHierarchy:

    def __init__(self, session, limits=HierarchyLimits(), resource_support=None, candidate_hook=None, public_preflight=None, task_pruner=None):
        self.session, self.limits = (session, limits)
        self.verify = resource_support.verify if resource_support is not None else verify_plan
        self.events = []
        self.candidate_hook = candidate_hook
        self.public_preflight = public_preflight
        self.task_pruner = task_pruner

    def expand(self, parent, problem, original_information, *, call_ceiling, _node_ids=None, _parent_event_index=None, _speculative_prefix=(), _completed_dependencies=None):
        """Repair only a failed child first; keep accepted speculative prefixes."""
        feedback = ''
        if self.task_pruner is not None and isinstance(parent, Task) and self.task_pruner.check(parent, problem, original_information, parent_event_index=_parent_event_index):
            raise _Rejected('TASK_POSITIVE_GOAL_UNREACHABLE', failure_origin='deterministic_pruning', failure_node_id=parent.id)
        if self.public_preflight is not None and isinstance(parent, Task):
            empty = self.public_preflight.task(parent, problem, original_information, _completed_dependencies)
            if empty is not None:
                return empty
        last_origin = None
        last_failure_node = None
        node_ids = {parent.id} if _node_ids is None else _node_ids
        for attempt in range(self.limits.node_attempts):
            initial_node_ids = set(node_ids)
            if self.session.logical_n >= min(call_ceiling, self.limits.max_model_logical):
                raise _Rejected('MODEL_BUDGET_EXHAUSTED')
            if not _pre_met(parent, problem.initial_state):
                raise _Rejected('CHILD_PRECONDITION_UNMET')
            operation = {'T': 'D_T', 'M': 'D_M', 'S': 'D_S'}[parent.layer]
            event = {'node': parent.model_dump(mode='json'), 'operation': operation, 'attempt': attempt + 1, 'start_state': sorted(problem.initial_state), 'feedback_received': feedback, 'format': {'accepted': None}}
            event.update(event_index=len(self.events), parent_event_index=_parent_event_index, physical_dispatch_during_generation=False)
            self.events.append(event)
            try:
                from planning.repair.hierarchy_budget import request_constraints
                constraints = request_constraints(self.limits, self.session, call_ceiling, node=parent, node_ids=node_ids)
                constraints.update(node_attempt=attempt + 1, node_attempts_remaining_including_this=self.limits.node_attempts - attempt)
                event['generation_constraints'] = constraints
                replay_before = getattr(self.session, 'used', 0)
                if self.candidate_hook is not None and hasattr(self.candidate_hook, 'request_context'):
                    self.candidate_hook.request_context(operation, parent, _speculative_prefix)
                raw = self.session.request(operation, problem, parent, feedback, original_information, constraints=constraints)
                reused = getattr(self.session, 'used', 0) > replay_before
                experimental = getattr(self.session, 'reuse_kind', None) == 'experimental_prefix_reuse'
                event['operational_replay'] = reused and (not experimental)
                if experimental:
                    event['experimental_prefix_reuse'] = reused
                event['raw_response'] = raw
                if self.candidate_hook is not None and hasattr(self.candidate_hook, 'begin_candidate'):
                    self.candidate_hook.begin_candidate(operation, problem, parent, raw, event)
                try:
                    event['parsed_response'] = object_response(raw)
                except CandidateFormatError:
                    event['format'] = {'accepted': False}
                    raise
                if getattr(self.session, 'output_contract_version', None) is not None:
                    from planning.repair.output_contract import parse_generation
                    from planning.repair.actor_interface import parser_options
                    parse_generation(raw, operation, **parser_options(self.session, problem, parent))
                    event['format'] = {'accepted': True}
                if isinstance(parent, Task):
                    chain = parse_leaf(raw, actor=parent.assigned_agent_id)
                    event.update(candidate=deepcopy(chain), format={'accepted': True})
                    if chain is None:
                        raise _Rejected('MODEL_DECLINED')
                    for step in chain:
                        if step['agent_id'] != parent.assigned_agent_id:
                            raise _Rejected('CHILD_ACTOR_REASSIGNMENT')
                    if not set(parent.required_primitives) <= {s['primitive'] for s in chain}:
                        raise _Rejected('CHILD_REQUIRED_PRIMITIVE_MISSING')
                    verdict = self.verify(problem, chain)
                    controlled_feedback = None
                    if self.candidate_hook is not None:
                        if getattr(self.candidate_hook, 'require_preserved_child', False):

                            def ancestry(index):
                                result = []
                                while index is not None:
                                    result.append(index)
                                    index = self.events[index]['parent_event_index']
                                return result
                            current_ancestors = ancestry(_parent_event_index)

                            def same_active_branch(previous):
                                path = ancestry(previous['parent_event_index'])
                                return bool(path and current_ancestors and (path[-1] == current_ancestors[-1]) and all((i in current_ancestors or self.events[i].get('accepted') is True for i in path)))
                            event['preceding_accepted_children'] = [{'event_index': e['event_index'], 'parent_event_index': e['parent_event_index'], 'node_id': e['node']['id'], 'candidate': deepcopy(e['candidate'])} for e in self.events[:-1] if e['operation'] == 'D_T' and e.get('accepted') and (not getattr(self.candidate_hook, 'current_branch_only', False) or same_active_branch(e))]
                            if getattr(self.candidate_hook, 'current_branch_only', False):
                                event['branch_root_event_index'] = current_ancestors[-1] if current_ancestors else None
                                for p in event['preceding_accepted_children']:
                                    prior = self.events[p['event_index']]
                                    p.update(start_state=prior['start_state'], final_state=prior['child_verification']['final_state'])
                        chain, verdict, controlled_feedback = self.candidate_hook.probe(problem, chain, verdict, node=parent, speculative_prefix=_speculative_prefix, event=event)
                    event.update(candidate=chain, child_verification=verdict)
                    if not verdict['accepted']:
                        if controlled_feedback:
                            event['public_error_feedback'] = controlled_feedback
                        raise _Rejected(verdict['reason'], failure_origin=event.get('failure_origin'), failure_node_id=parent.id)
                    event['accepted'] = True
                    return (chain, frozenset(verdict['final_state']))
                children = _parse_children(raw, parent)
                event['format'] = {'accepted': True}
                if any((c.id in node_ids for c in children)):
                    raise _Rejected('HIERARCHY_NODE_ID_COLLISION')
                node_ids.update((c.id for c in children))
                if len(children) > self.limits.max_children:
                    raise _Rejected('CHILD_LIMIT')
                event['generated_children'] = [c.model_dump(mode='json') for c in children]
                if isinstance(parent, Scene):
                    available_types = {kind for aid, kind in problem.observation['actor_types'].items() if atom('available', aid) in problem.initial_state}
                    for child in children:
                        if child.required_agent_types and (not set(child.required_agent_types) & available_types):
                            raise _Rejected('MISSION_AGENT_TYPE_UNAVAILABLE')
                    time_budget = problem.observation.get('resource_constraints', {}).get('time_budget_s')
                    if time_budget is not None and sum((c.estimated_duration_s for c in children)) > time_budget:
                        raise _Rejected('DECLARED_MISSION_DURATION_EXCEEDS_BUDGET')
                known = set(problem.initial_state) | set(problem.goal_facts)
                for action in problem.actions:
                    known.update(action.preconditions | action.negative_preconditions | action.add_effects | action.delete_effects)
                    for effect in action.conditional_effects:
                        known.update(effect.conditions | effect.negative_conditions | effect.add_effects | effect.delete_effects)
                promised = set(problem.initial_state)
                for child in children:
                    cp = _generated_child_problem(problem, child)
                    child_atoms = set(cp.goal_facts | cp.negative_goal_facts)
                    child_atoms.update((a for _, group in cp.goal_cardinality for a in group))
                    if not child_atoms or not child_atoms <= known:
                        raise _Rejected('UNKNOWN_OR_EMPTY_CHILD_GOAL')
                    promised.update(cp.goal_facts)
                positive_parent = replace(problem, negative_goal_facts=frozenset())
                if not any((_generated_child_problem(problem, c).goal_cardinality for c in children)) and (not positive_parent.goal_met(frozenset(promised))):
                    raise _Rejected('CHILDREN_DO_NOT_COVER_PARENT')
                remaining, done, plan, state = (list(children), set(), [], problem.initial_state)
                while remaining:
                    child = next((c for c in remaining if set(c.dependencies) <= done), None)
                    if child is None:
                        raise _Rejected('CHILD_DEPENDENCY_CYCLE')
                    if isinstance(child, Task) and child.assigned_agent_id not in problem.observation['actor_types']:
                        raise _Rejected('UNKNOWN_CHILD_ACTOR')
                    cp = _generated_child_problem(replace(problem, initial_state=state), child)
                    child_plan, state = self.expand(child, cp, original_information, call_ceiling=call_ceiling, _node_ids=node_ids, _parent_event_index=event['event_index'], _speculative_prefix=list(_speculative_prefix) + plan, _completed_dependencies=done)
                    plan.extend(child_plan)
                    remaining.remove(child)
                    done.add(child.id)
                verdict = self.verify(problem, plan)
                event.update(candidate=deepcopy(plan), composed_parent_verification=verdict)
                if not verdict['accepted']:
                    raise _Rejected(verdict['reason'])
                event['accepted'] = True
                return (plan, frozenset(verdict['final_state']))
            except (CandidateFormatError, _Rejected) as exc:
                if isinstance(exc, CandidateFormatError) and event['format']['accepted'] is None:
                    event['format'] = {'accepted': False, 'detail': str(exc)}
                node_ids.clear()
                node_ids.update(initial_node_ids)
                reason = exc.reason if isinstance(exc, _Rejected) else 'MALFORMED_GENERATED_CONTENT'
                feedback = reason + (': ' + str(exc) if isinstance(exc, CandidateFormatError) else '')
                if hasattr(exc, 'error_record'):
                    import json
                    reason = exc.reason
                    event['structured_error'] = deepcopy(exc.error_record)
                    feedback = json.dumps(exc.error_record, ensure_ascii=False, sort_keys=True)
                last_origin = getattr(exc, 'failure_origin', None) or 'natural_candidate'
                last_failure_node = getattr(exc, 'failure_node_id', None) or parent.id
                if self.candidate_hook is not None:
                    if hasattr(self.candidate_hook, 'record_failure'):
                        source = 'model_decline' if reason == 'MODEL_DECLINED' else 'model_invalid' if operation == 'D_T' or isinstance(exc, CandidateFormatError) else 'parent_failure'
                        self.candidate_hook.record_failure(reason, source, event, last_origin)
                    else:
                        self.candidate_hook.natural_failure(reason, last_origin)
                    if hasattr(self.candidate_hook, 'request_context'):
                        self.candidate_hook.request_context(operation, parent, _speculative_prefix)
                    feedback = event.get('public_error_feedback') or self.candidate_hook.feedback(problem, reason)
                    event.update(failure_origin=last_origin, failure_propagated_from_child=last_failure_node != parent.id)
                event.update(accepted=False, rejection=reason, feedback=feedback)
                if reason == 'MODEL_DECLINED' and (event.get('parsed_response') or {}).get('decline') is True:
                    from planning.repair.scene_alignment import clarification
                    clarify = clarification(self.session, problem, parent, original_information, scope=parent.layer, attempt=attempt + 1, limits=self.limits, call_ceiling=call_ceiling)
                    if clarify is not None:
                        import json
                        event['goal_clarification'] = clarify
                        feedback = json.dumps(clarify, ensure_ascii=False, sort_keys=True)
                        continue
                if reason in {'MODEL_BUDGET_EXHAUSTED', 'MODEL_DECLINED'}:
                    raise _Rejected(feedback, failure_origin=last_origin, failure_node_id=last_failure_node)
            finally:
                event['completion_order'] = sum(('completion_order' in e for e in self.events))
        raise _Rejected(feedback, failure_origin=last_origin, failure_node_id=last_failure_node)

def run_hierarchical(world, parent_problem, original_tree, failed_node_id, session, *, failure, executed_prefix_ids=(), permitted_scopes=None, limits=HierarchyLimits(), call_ceiling=None, original_information=None, resource_support=None, candidate_hook=None, diagnosis_mode='legacy_offline', public_preflight=None, task_pruner=None, decision_audit=None):
    """Actual diagnosis/selector -> finite scope escalation -> composed execution."""
    diagnosis = DiagnosisModule()
    continuous = getattr(world, 'continuous_control', None)
    if continuous is not None:
        continuous.checkpoint('diagnosis_start', expected=continuous.entry_revision, problem=parent_problem)
    from planning.repair.scope_decision_record import choose
    from planning.repair.scope_decision_record import VERSION as decision_version
    decision_audit = {} if decision_audit is None else decision_audit
    decision_audit.update(version=decision_version, status='not_invoked', selector_invoked=False, reason='diagnosis_not_completed')
    diagnosis_audit = None
    if diagnosis_mode == 'legacy_offline':
        posterior = diagnosis.diagnose(failure, base_llm_client=None)
    else:
        from controller.diagnosis.connected_refinement import diagnose
        posterior, diagnosis_audit = diagnose(session, failure, parent_problem, original_information or {}, diagnosis_mode)
        diagnosis.last_source = diagnosis_audit['diagnosis_source']
    decision_audit.update(diagnosis_source=diagnosis.last_source, posterior={Scope(k).value: float(v) for k, v in posterior.items()}, reason='selection_inputs_not_completed')
    policy = parent_problem.observation['communication_policy']
    if continuous is not None:
        continuous.checkpoint('selection', expected=continuous.entry_revision, problem=parent_problem)
    connected = policy.get('state') == 'Connected'
    observer = getattr(session, 'communication', None)
    decision_audit.update(diagnosis_source=diagnosis.last_source, diagnosis_mode=diagnosis_mode, communication_policy=deepcopy(policy), link_revision=policy.get('revision'), session_link_revision_attribute=getattr(observer, 'revision', None), failure_identity={'symptom': failure.symptom, 'agent_id': failure.agent_id, 'node_id': failure.node_id})
    edge_scopes = (world.c_edge_for(failure.agent_id) if hasattr(world, 'c_edge_for') else set()) if not connected else None
    decision, raw_selection, permitted = choose(ScopeSelector(frozen_weights(), frozen_p_fix_params()), failure, posterior, {}, connected=connected, actor_edge_scopes=edge_scopes, operation_scopes=permitted_scopes, audit=decision_audit)
    decision_audit.pop('reason', None)
    scopes = [s for s in SCOPES if s in permitted and scope_order(s) >= scope_order(decision.sigma)]
    information = deepcopy(original_information or {'tree': original_tree.model_dump(mode='json'), 'executed_prefix_ids': list(executed_prefix_ids), 'failure': failure.__dict__})
    if resource_support is not None:
        resource_support.bind_source_obligations(information, parent_problem)
    original = [s for s in leaves(original_tree) if s[0] not in set(executed_prefix_ids)]
    expansion = GeneratedHierarchy(session, limits, resource_support, candidate_hook, public_preflight, task_pruner)
    verify = resource_support.verify if resource_support is not None else verify_plan
    attempts = []
    resource_blocked = False
    output = {'implementation_id': IMPLEMENTATION_ID, 'diagnosis_source': diagnosis.last_source, 'posterior': {k.value: v for k, v in posterior.items()}, 'selected_scope': decision.sigma.value, 'unconstrained_selector_scope': raw_selection.value, 'allowed_scope_sequence': [s.value for s in scopes], 'attempts': attempts, 'architecture_effect_scope': 'combined diagnosis/selector, child decomposition/local repair and bounded scope escalation package', 'original_information': information, 'execution': None, 'scope_decision': decision_audit}
    if diagnosis_audit is not None:
        output['diagnosis_audit'] = diagnosis_audit
    if continuous is not None:
        continuous.native = output
    if resource_support is not None:
        output['implementation_id'] = 'paper4_hierarchy_resource_regroup_v1'
        output['architecture_effect_scope'] += '; symmetric Ours/flat resource-search assistance, hierarchy child checking remains distinct'
    for scope in scopes:
        if continuous is not None:
            continuous.set_scope(scope.value)
        if hasattr(session, 'set_repair_context'):
            session.set_repair_context(scope.value, failure.agent_id)
        node = ancestor(original_tree, failed_node_id, scope)
        selected_ids = {i for i, _ in leaves(original_tree, node)}
        selected = [i for i, (nid, _) in enumerate(original) if nid in selected_ids]
        if not selected:
            continue
        left, right = (min(selected), max(selected) + 1)
        if selected != list(range(left, right)):
            raise ValueError('selected original subtree is not contiguous in the declared execution order')
        before, after = ([s for _, s in original[:left]], [s for _, s in original[right:]])
        prefix_check = verify(with_goal(parent_problem), before)
        record = {'scope': scope.value, 'selected_node': node.id, 'preserved_before': before, 'preserved_after': after, 'scope_transition': {'from': attempts[-1]['scope'] if attempts else None, 'to': scope.value, 'previous_attempt_reason': attempts[-1].get('rejection', attempts[-1].get('resource_feedback')) if attempts else None}}
        attempts.append(record)
        if not prefix_check['accepted']:
            record['rejection'] = 'PRESERVED_PREFIX_INVALID'
            continue
        state = frozenset(prefix_check['final_state'])
        try:
            if scope is Scope.P:
                candidate = original[left][1]
                local = generate_for_scope(ScopedGenerationRequest(Scope.P, scenario=parent_problem.scenario_id, failed_primitive=candidate), communication_state=policy.get('state', 'Connected'))
                replacement = [{**s, 'agent_id': candidate['agent_id']} for s in local.chain]
            else:
                np = problem_for_node(replace(parent_problem, initial_state=state), node)
                if scope is Scope.S:
                    np = replace(np, goal_facts=parent_problem.goal_facts, goal_cardinality=parent_problem.goal_cardinality, negative_goal_facts=parent_problem.negative_goal_facts)
                    node = set_node_goal(node.model_copy(deep=True), np)

                def descendants(current):
                    return {current.id}.union(*(descendants(original_tree.nodes[c]) for c in current.children_ids))
                retained_ids = set(original_tree.nodes) - descendants(node) | {node.id}
                replacement, _ = expansion.expand(node, np, information, call_ceiling=limits.max_model_logical if call_ceiling is None else call_ceiling, _node_ids=retained_ids, _speculative_prefix=before)
            combined = before + replacement + after
            parent_verdict = verify(parent_problem, combined)
            record.update(candidate=combined, parent_verification=parent_verdict)
            if parent_verdict['accepted']:
                adoption = {'origin': 'composed_model' if scope is not Scope.P else 'primitive_local_dispatcher', 'scope_attempt_index': len(attempts) - 1}
                if resource_support is not None:
                    forecast = resource_support.forecast(world, parent_problem, combined)
                    record['resource_prediction'] = forecast
                    if not resource_support.may_execute(forecast):
                        resource_blocked = True
                        record['resource_feedback'] = 'symbolically accepted; resource forecast did not establish feasibility'
                        information = {**information, 'resource_feedback': forecast}
                        if scope not in {Scope.M, Scope.S}:
                            continue
                        repaired = resource_support.reorganize(world, parent_problem, np, before, after, scope=scope.value, hierarchical=True, original_candidate=combined)
                        record['resource_reorganization'] = repaired
                        if repaired.get('plan') is None:
                            continue
                        combined = repaired['plan']
                        adoption.update(origin='resource_search', search_event_index=repaired['search_event_index'])
                        record['executed_candidate'] = combined
                output['execution'] = execute_plan(world, parent_problem, combined, capture_transitions=True)
                output['adopted_candidate'] = deepcopy(combined)
                output['adoption'] = adoption
                output.update(status='executed' if output['execution'].get('execution_success') else 'execution_failed', final_scope=scope.value, generated_hierarchy=expansion.events)
                return output
            record['rejection'] = parent_verdict['reason']
            if candidate_hook is not None:
                if hasattr(candidate_hook, 'record_failure'):
                    candidate_hook.record_failure(parent_verdict['reason'], 'deterministic_rejection' if scope is Scope.P else 'parent_failure')
                else:
                    candidate_hook.natural_failure(parent_verdict['reason'])
        except _Rejected as exc:
            record['rejection'] = exc.reason
            if exc.reason == 'MODEL_BUDGET_EXHAUSTED':
                break
    output.update(status='resource_no_feasible_candidate' if resource_blocked else 'no_verified_repair', generated_hierarchy=expansion.events)
    return output

def run_flat(world, problem, session, *, original_information, limits=HierarchyLimits(), call_ceiling=None, resource_support=None, candidate_hook=None):
    """Whole-plan proposals receive the same information and total model balance."""
    attempts, feedback = ([], '')
    if resource_support is not None:
        resource_support.bind_source_obligations(original_information, problem)
    implementation = 'paper4_flat_resource_aided_v1' if resource_support else FLAT_IMPLEMENTATION_ID
    verify = resource_support.verify if resource_support is not None else verify_plan
    while session.logical_n < min(limits.max_model_logical if call_ceiling is None else call_ceiling, limits.max_model_logical):
        from planning.repair.hierarchy_budget import request_constraints
        if candidate_hook is not None and hasattr(candidate_hook, 'request_context'):
            candidate_hook.request_context('flat', None, ())
        raw = session.request('flat', problem, None, feedback, original_information, constraints=request_constraints(limits, session, call_ceiling))
        parsed_response = None
        hook_event = {}
        controlled_feedback = None
        if candidate_hook is not None and hasattr(candidate_hook, 'begin_candidate'):
            candidate_hook.begin_candidate('flat', problem, None, raw, hook_event)
        try:
            parsed_response = object_response(raw)
            if getattr(session, 'output_contract_version', None) is not None:
                from planning.repair.output_contract import parse_generation
                from planning.repair.actor_interface import parser_options
                parse_generation(raw, 'flat', **parser_options(session, problem))
            plan = parse_leaf(raw)
        except CandidateFormatError as exc:
            plan, verdict = (None, {'accepted': False, 'reason': 'MALFORMED_PLAN'})
            verdict['detail'] = str(exc)
            if hasattr(exc, 'error_record'):
                verdict.update(reason=exc.reason, structured_error=deepcopy(exc.error_record))
        else:
            if plan is None:
                if candidate_hook is not None:
                    candidate_hook.natural_failure('MODEL_DECLINED')
                attempts.append({'raw_response': raw, 'parsed_response': parsed_response, 'format': {'accepted': True}, 'feedback_received': feedback, 'candidate': None, 'reason': 'MODEL_DECLINED'})
                from planning.repair.scene_alignment import clarification
                clarify = clarification(session, problem, None, original_information, scope='S', attempt=len(attempts), limits=limits, call_ceiling=call_ceiling)
                if clarify is not None:
                    import json
                    attempts[-1]['goal_clarification'] = clarify
                    feedback = json.dumps(clarify, ensure_ascii=False, sort_keys=True)
                    continue
                return {'implementation_id': implementation, 'attempts': attempts, 'execution': None, 'status': 'model_declined'}
            verdict = verify(problem, plan)
            if candidate_hook is not None:
                plan, verdict, controlled_feedback = candidate_hook.probe(problem, plan, verdict, event=hook_event)
        attempts.append({'raw_response': raw, 'parsed_response': parsed_response, 'format': {'accepted': plan is not None}, 'feedback_received': feedback, 'candidate': deepcopy(plan), 'parent_verification': verdict, **hook_event})
        if candidate_hook is not None and (not verdict['accepted']):
            candidate_hook.natural_failure(verdict['reason'], hook_event.get('failure_origin'))
            attempts[-1]['failure_origin'] = hook_event.get('failure_origin') or 'natural_candidate'
        if verdict['accepted']:
            adoption = {'origin': 'model', 'candidate_attempt_index': len(attempts) - 1}
            if resource_support is not None:
                forecast = resource_support.forecast(world, problem, plan)
                attempts[-1]['resource_prediction'] = forecast
                if not resource_support.may_execute(forecast):
                    repaired = resource_support.reorganize(world, problem, problem, [], [], scope='M', hierarchical=False, original_candidate=plan)
                    attempts[-1]['resource_reorganization'] = repaired
                    if repaired.get('plan') is None:
                        return {'implementation_id': 'paper4_flat_resource_aided_v1', 'attempts': attempts, 'execution': None, 'status': 'resource_no_feasible_candidate'}
                    plan = repaired['plan']
                    adoption.update(origin='resource_search', search_event_index=repaired['search_event_index'])
                    attempts[-1]['executed_candidate'] = plan
            execution = execute_plan(world, problem, plan, capture_transitions=True)
            return {'implementation_id': 'paper4_flat_resource_aided_v1' if resource_support else FLAT_IMPLEMENTATION_ID, 'attempts': attempts, 'execution': execution, 'adopted_candidate': deepcopy(plan), 'adoption': adoption, 'status': 'executed' if execution.get('execution_success') else 'execution_failed'}
        feedback = verdict['reason']
        if verdict.get('structured_error'):
            import json
            feedback = json.dumps(verdict['structured_error'], ensure_ascii=False, sort_keys=True)
        if candidate_hook is not None:
            feedback = controlled_feedback or candidate_hook.feedback(problem, verdict['reason'])
    return {'implementation_id': implementation, 'attempts': attempts, 'execution': None, 'status': 'model_budget_exhausted'}

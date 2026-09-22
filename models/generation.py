"""Generation."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace
from models.clients import InstrumentedBaseLLMClient
from models.clients import CallLedger
from models.clients import RunCallBudget
from models.compact_context import FORMAT_HEADER
from models.compact_context import encode_problem
from models.compact_context import measure_context
from domains.transition import describe_problem
from planning.repair.domain_hierarchy import goal_record
PROMPT_CAP = 49152
COMPLETION_CAP = 4096
TRANSPORT_RETRIES = 1
MAX_FEEDBACK_BYTES = 4096

def generation_messages_v3(operation, problem, node, feedback, original_information, constraints):
    """Full expert-reusable messages; the same contract drives actual parsers."""
    from planning.repair.output_contract import VERSION
    from planning.repair.output_contract import generation_schema
    from domains.readable_context import encode_problem
    from domains.readable_context import FORMAT_HEADER
    schema = generation_schema(operation)
    layer_instruction = {'D_S': 'D_S outputs Mission specifications in missions. Do not output a primitive or Task as a Mission. Their Task children will be requested separately.', 'D_M': 'D_M outputs actor-bound Task specifications in tasks. Each Task states a goal/contract, not a primitive execution step. Do not put primitive or params fields in a Task. Its action chain will be requested separately through D_T.', 'D_T': 'D_T outputs a concrete ordered action chain for the bound Task actor. Use primitive and params on each chain element.', 'flat': 'flat outputs the complete ordered action chain for the supplied goal, naming the actor for every action.', 'translation': 'translation outputs exactly goal_facts, negative_goal_facts and goal_cardinality. It translates the goal only; it does not output a plan or a hierarchy. An empty cardinality list is [], not [0, []].'}[operation]
    system = "Generate only the requested native layer. Return a JSON INSTANCE that satisfies output_schema, not the schema definition itself. For example, a chain contains concrete primitive/params objects, not type/properties/oneOf definitions. Solve the supplied goal using the actual domain actions. Return only that instance, without an answer wrapper, code fence or extra text. Field types and required fields are literal, with no coercion. x-order declares each array's order: sequence is significant; unordered means member order is irrelevant. Use the complete action semantics, argument schemas, observation and generation_constraints. Keep the supplied goal. D_T may omit agent_id only because the supplied Task binds it; an explicit actor must match. Child dependencies must be explicit and acyclic; preserve actor and remaining-call constraints. Optional node metadata does not supply generated children; children are expanded and verified separately. " + layer_instruction + ' The output_schema for this operation is authoritative. generation_constraints.leaf_schema describes leaf chains only, not Mission/Task specification fields. ' + ('A declared decline is a candidate refusal, not proof of impossibility. ' if operation != 'translation' else 'Translation must return the three native goal fields; no decline envelope is defined for this interface. ') + FORMAT_HEADER
    payload = {'operation': operation, 'goal': goal_record(problem), 'node': node.model_dump(mode='json') if node else None, 'context': encode_problem(problem), 'original_information': original_information, 'feedback': feedback, 'generation_constraints': deepcopy(constraints), 'output_contract_version': VERSION, 'output_schema': schema}
    return (system, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

def response_key(operation, goal):
    return json.dumps([operation, goal], sort_keys=True, separators=(',', ':'))

class RequestScriptTransport:
    """Content-sensitive hand-authored fixture; no search or effect simulation."""
    single_physical_attempt_per_create = True

    def __init__(self, scripts):
        self.scripts = deepcopy(scripts)
        self.seen, self.requests = ({}, [])
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        payload = json.loads(kwargs['messages'][1]['content'])
        key = response_key(payload['operation'], payload['goal'])
        self.requests.append(deepcopy(kwargs))
        values = self.scripts.get(key)
        if values is None:
            value = {'fixture_response_missing': True}
        else:
            ordinal = self.seen.get(key, 0)
            self.seen[key] = ordinal + 1
            value = values[min(ordinal, len(values) - 1)]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return SimpleNamespace(id=f'local-response-{len(self.requests)}', model='offline-script', choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason='stop')], usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))

class HierarchyModelSession:

    def __init__(self, trial_id, scripts, *, logical_cap=24, tier='base', metering=None, request_profile='legacy_pooled', output_contract_version=None, records_path=None):
        from models.clients.request_metering import FullRequestMetering
        self.trial_id, self.tier, self.logical_cap = (trial_id, tier, logical_cap)
        self.request_profile = request_profile
        self.records_path = records_path
        from planning.repair.output_contract import VERSION
        if output_contract_version not in (None, VERSION):
            raise ValueError('unknown generation output contract')
        self.output_contract_version = output_contract_version
        self.prompt_cap, self.completion_cap = (PROMPT_CAP, COMPLETION_CAP)
        options = {}
        if request_profile == 'recovery_ready_v2':
            from models.clients.provider_readiness import proposed_metering
            from models.clients.provider_readiness import TOTAL_COMPLETION_CAP
            from models.clients.provider_readiness import COMPLETION_RESERVATION
            self.prompt_cap, self.completion_cap = (786432, COMPLETION_RESERVATION)
            options = {'total_completion_token_cap': TOTAL_COMPLETION_CAP, 'completion_token_overrun_allowance': 10, 'extra_body': {'enable_thinking': True}}
            metering = metering or proposed_metering(bytes_hard_cap=8 * 1024 * 1024, max_feedback_utf8_bytes=MAX_FEEDBACK_BYTES, model='qwen3.8-max' if tier == 'base' else 'offline-local-model-unselected')
        elif request_profile != 'legacy_pooled':
            raise ValueError('unknown model request profile')
        self.transport = RequestScriptTransport(scripts)
        self.requests = []
        self.run_budget = RunCallBudget(max_logical_calls=logical_cap, max_physical_attempts=logical_cap * 2, max_total_tokens=logical_cap * 2 * (self.prompt_cap + self.completion_cap))
        self.ledger = CallLedger(max_logical_calls_total=logical_cap, max_physical_attempts_total=logical_cap * 2, max_total_tokens=logical_cap * 2 * (self.prompt_cap + self.completion_cap), run_budget=self.run_budget)
        self.client = InstrumentedBaseLLMClient(model='qwen3.8-max' if tier == 'base' else 'offline-local-model-unselected', api_key='offline-fixture-not-a-credential', base_url='http://localhost/offline-fixture', max_tokens=self.completion_cap, prompt_token_cap=self.prompt_cap, max_retries=0, max_transport_retries=TRANSPORT_RETRIES, call_ledger=self.ledger, fixture_id=trial_id, prompt_version=output_contract_version or 'paper4_hierarchical_domain_v1', transport=self.transport, _allow_test_transport=True, request_metering=metering or FullRequestMetering(bytes_hard_cap=262144, max_feedback_utf8_bytes=MAX_FEEDBACK_BYTES), **options)

    @property
    def logical_n(self):
        return len(self.ledger.logical_calls)

    def request(self, operation, problem, node, feedback, original_information, *, constraints=None):
        if self.logical_n >= self.logical_cap:
            raise RuntimeError('trial model logical ceiling exhausted')
        if len(feedback.encode('utf-8')) > MAX_FEEDBACK_BYTES:
            raise ValueError('feedback exceeds declared complete-request allowance')
        shapes = {'D_S': '{"missions":[{"id":"...","postcondition":"...","context":{"domain_goal":{"positive":[],"negative":[],"cardinality":[]}},"dependencies":[]}]}', 'D_M': '{"tasks":[{"id":"...","assigned_agent_id":"...","postcondition":"...","context":{"domain_goal":{"positive":[],"negative":[],"cardinality":[]}},"dependencies":[]}]}', 'D_T': '{"chain":[{"primitive":"...","params":{}}]}', 'flat': '{"chain":[{"primitive":"...","agent_id":"...","params":{}}]}', 'translation': '{"goal_facts":[],"negative_goal_facts":[],"goal_cardinality":[]}', 'understanding_probe': '{"answer":{}}'}
        encoder, header, encoding = (encode_problem, FORMAT_HEADER, 'paper4_context_pool_v1')
        if self.request_profile == 'recovery_ready_v2':
            from domains.readable_context import encode_problem as encoder
            from domains.readable_context import FORMAT_HEADER as header
            from domains.readable_context import ENCODING as encoding
        system = 'Generate only the requested native layer. Use the shared observed state and action semantics. Never invent effects or alter a supplied goal. A Task keeps its assigned actor. For decomposition, child obligations must compose to the supplied parent, and dependencies must be explicit. Follow generation_constraints, including child count, dependencies, actor binding and remaining calls. You may instead return {"decline":true,"reason":"..."} when no candidate can be provided; do not consume allowance needlessly. Return JSON only: ' + shapes[operation] + '\n' + header
        payload = {'operation': operation, 'goal': goal_record(problem), 'node': node.model_dump(mode='json') if node else None, 'context': encoder(problem), 'original_information': original_information, 'feedback': feedback, 'generation_constraints': deepcopy(constraints or {'remaining_model_logical_including_this_request': self.logical_cap - self.logical_n})}
        user = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if self.output_contract_version is not None:
            system, user = generation_messages_v3(operation, problem, node, feedback, original_information, constraints or {'remaining_model_logical_including_this_request': self.logical_cap - self.logical_n})
            encoding = 'readable_shared_effects_v1'
        event = {'ordinal': len(self.requests) + 1, 'operation': operation, 'tier': self.tier, 'system': system, 'user': user, 'encoding': encoding, 'context_measurement': measure_context(describe_problem(problem))}
        self.requests.append(event)
        try:
            self.client.last_request_measurement = None
            raw = self.client.generate(system, user)
            event['response'] = raw
            event['logical_call_id'] = self.client.last_logical_call_id
            event['request_measurement'] = deepcopy(self.client.last_request_measurement)
            self.persist_records()
            try:
                json.loads(raw)
                parse = 'valid'
            except (ValueError, TypeError):
                parse = 'malformed_json'
            self.ledger.record_parse_outcome(self.client.last_logical_call_id, parse)
            self.persist_records()
            return raw
        except Exception as exc:
            event['request_measurement'] = deepcopy(getattr(self.client, 'last_request_measurement', None))
            event['failure'] = type(exc).__name__ + ': ' + str(exc)
            self.persist_records()
            raise

    def persist_records(self):
        if self.records_path is None:
            return
        import os
        from pathlib import Path
        path = Path(self.records_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as handle:
            json.dump(self.records(), handle, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def records(self):

        def lean(record):
            return {k: v for k, v in asdict(record).items() if k != 'prompt_sha256'}
        return {'trial_id': self.trial_id, 'requests': self.requests, **({'output_contract_version': self.output_contract_version} if self.output_contract_version else {}), 'logical_calls': [lean(v) for v in self.ledger.logical_calls], 'physical_attempts': [lean(v) for v in self.ledger.physical_attempts], 'budget': self.run_budget.summary(), 'logical_n': self.logical_n, 'actual_api_calls': 0, 'actual_provider_tokens': 0, 'evidence_class': 'instrumented_content_dependent_fixture_non_evidence'}

"""Local client."""
from types import SimpleNamespace
from contextlib import nullcontext
import json
from urllib.request import Request
from urllib.request import urlopen
from models.clients import InstrumentedBaseLLMClient
from models.clients.request_metering import FullRequestMetering
from models.local_routing import RoutingRejected
from models.local_routing import ServiceNotReady
from models.local_routing import ContextCapacityError

def http_json(endpoint, path, data=None):
    base = endpoint.removesuffix('/v1').rstrip('/')
    request = Request(base + path, data=None if data is None else json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=30) as response:
        return json.load(response)

def health(service, http=http_json):
    status = http(service.endpoint, '/health')
    props = http(service.endpoint, '/props')
    models = http(service.endpoint, '/v1/models')
    settings = props.get('default_generation_settings', {})
    context = settings.get('n_ctx', settings.get('params', {}).get('n_ctx'))
    return {'service_role': service.role, 'host': service.host, 'health': status, 'properties': props, 'models': models, 'context_observed': context, 'model_present': service.model in [m.get('id') for m in models.get('data', [])], 'actual_generation_calls': 0, 'memory_fit': None, 'interpretation': 'loading/metadata only; pair with host memory inventory; no generation performance claim'}

class ServerCounter:
    official = True
    covers_template = True
    name = 'deployed_llama_server_template_and_tokenizer'
    source = 'same server /apply-template then /tokenize; no byte/token approximation'

    def __init__(self, service, guard, http=http_json):
        self.service, self.model, self.guard, self.http = (service, service.model, guard, http)
        self.version = service.backend
        self.last = None

    def count_request_upper_bound(self, wire, *, template_text, maximum_feedback_utf8_bytes):
        if maximum_feedback_utf8_bytes or template_text:
            raise ValueError('count each full actual message; no unmeasured future/template allowance')
        if any((k in wire for k in ('tools', 'response_format', 'chat_template_kwargs', 'enable_thinking'))):
            raise ValueError('unsupported template override; configure verified model-specific server defaults')
        self.guard()
        rendered = self.http(self.service.endpoint, '/apply-template', {'messages': wire['messages']})['prompt']
        self.guard()
        tokens = self.http(self.service.endpoint, '/tokenize', {'content': rendered, 'add_special': False, 'parse_special': True})['tokens']
        self.last = {'logical_input_tokens': len(tokens), 'cache_reduction_claimed': False}
        if len(tokens) + self.service.output_tokens > self.service.context_tokens:
            raise ContextCapacityError('complete input plus reserved output exceeds the loaded service context; no clipping or generation')
        return len(tokens)

def build_client(service, ledger, *, api_key='local-service', transport=None, http=http_json, counter_factory=ServerCounter):
    if not service.observed_ready:
        raise ServiceNotReady('service load and memory must first be observed on target host')
    holder = {}

    def guard():
        holder['client'].request_guard()
    counter = counter_factory(service, guard, http)

    class GuardedClient(InstrumentedBaseLLMClient):
        transport_posts = 0

        def _ensure_client(self):
            actual = super()._ensure_client()
            outer = self

            class Completion:

                def create(self, **kwargs):
                    with getattr(outer, 'transport_entry', nullcontext)():
                        guard()
                        accounting = outer.last_request_measurement.get('completion_accounting', {})
                        accounting.update(includes_reasoning=True, scope=getattr(counter, 'completion_scope', 'llama.cpp max_tokens covers reasoning plus final output; optional reasoning usage detail may be unknown'))
                        before_transport = getattr(outer, 'before_transport', None)
                        if before_transport is not None:
                            before_transport()
                        outer.transport_posts += 1
                        outer.posted_logical_ids.add(outer.last_logical_call_id)
                        outer.posted_physical_ids.add(outer.call_ledger.physical_attempts[-1].physical_attempt_id)
                    if hasattr(outer, 'request_timeout_provider'):
                        kwargs['timeout'] = outer.request_timeout_provider()
                    response = actual.chat.completions.create(**kwargs)
                    outer.last_provider_response = json.loads(json.dumps(response, default=lambda v: v.model_dump(mode='json') if hasattr(v, 'model_dump') else vars(v)))
                    usage = getattr(response, 'usage', None)
                    details = getattr(usage, 'prompt_tokens_details', None)
                    timings = getattr(response, 'timings', None)
                    timings = json.loads(json.dumps(timings, default=lambda v: v.model_dump(mode='json') if hasattr(v, 'model_dump') else {'unavailable_type': type(v).__name__}))
                    outer.last_cache_telemetry = {'cached_prompt_tokens': getattr(details, 'cached_tokens', None), 'timings': timings, 'logical_input_tokens_not_discounted': counter.last}
                    return response
            return SimpleNamespace(chat=SimpleNamespace(completions=Completion()))
    client = GuardedClient(model=service.model, api_key=api_key, base_url=service.endpoint, max_tokens=service.output_tokens, prompt_token_cap=service.context_tokens - service.output_tokens, max_retries=0, max_transport_retries=1, timeout_s=600, call_ledger=ledger, fixture_id='local-role-' + service.role, prompt_version='local_role_generation_v1', transport=transport, _allow_test_transport=transport is not None, request_metering=FullRequestMetering(bytes_hard_cap=8 * 1024 * 1024, provider_input_limit=service.context_tokens, provider_completion_limit=service.output_tokens, provider_window_limit=service.context_tokens, provider_counter=counter))
    client.request_guard = lambda: None
    client.server_counter = counter
    client.posted_logical_ids = set()
    client.posted_physical_ids = set()
    holder['client'] = client
    return client

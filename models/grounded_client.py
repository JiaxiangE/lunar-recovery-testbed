"""llama.cpp schema-aware transport and complete-request token counting."""
from copy import deepcopy
import json
from types import MethodType
from models.local_client import ServerCounter
from models.local_client import build_client
from models.local_client import http_json
from models.local_routing import RoutedHierarchySession
from models.local_routing import ContextCapacityError
from planning.repair.grounded_decoding import response_format_from_packet
from planning.repair.grounded_decoding import VERSION

class SchemaServerCounter(ServerCounter):
    name = 'deployed_llama_full_schema_template_and_tokenizer'
    source = 'pinned server /apply-template full request through same oaicompat_chat_params_parse as generation, then /tokenize'

    def count_request_upper_bound(self, wire, *, template_text, maximum_feedback_utf8_bytes):
        if template_text or maximum_feedback_utf8_bytes:
            raise ValueError('only complete current requests are counted')
        if any((k in wire for k in ('tools', 'tool_choice', 'grammar', 'json_schema', 'chat_template_kwargs', 'enable_thinking'))):
            raise ValueError('only the explicit response_format prototype is supported')
        fmt = wire.get('response_format')
        if not isinstance(fmt, dict) or fmt.get('type') != 'json_schema' or (not isinstance(fmt.get('json_schema', {}).get('schema'), dict)):
            raise ValueError('explicit JSON schema required')
        self.guard()
        rendered = self.http(self.service.endpoint, '/apply-template', deepcopy(wire))['prompt']
        self.guard()
        tokens = self.http(self.service.endpoint, '/tokenize', {'content': rendered, 'add_special': False, 'parse_special': True})['tokens']
        self.last = {'logical_input_tokens': len(tokens), 'cache_reduction_claimed': False, 'response_schema_sent_to_same_template_parser': True}
        if len(tokens) + self.service.output_tokens > self.service.context_tokens:
            raise ContextCapacityError('full schema-aware input plus output reservation exceeds loaded context; no clipping')
        return len(tokens)

def build_schema_client(service, ledger, *, transport=None, http=http_json):
    if not service.backend.lower().startswith('llama.cpp'):
        raise ValueError('this prototype is for the inspected llama.cpp backend, not an unverified provider')
    c = build_client(service, ledger, transport=transport, http=http, counter_factory=SchemaServerCounter)
    original = c.generate

    def generate(self, system, user, **kw):
        if 'response_format' in kw:
            raise ValueError('schema is compiled from the source tuple index, not supplied by candidate')
        fmt = response_format_from_packet(json.loads(user))
        self.last_response_format = deepcopy(fmt)
        return original(system, user, response_format=fmt, **kw)
    c.generate = MethodType(generate, c)
    c.prompt_version = 'literal_task_interface_v2'
    c.decoding_profile = VERSION
    return c

class GroundedSchemaSession(RoutedHierarchySession):
    """Existing calls.jsonl owns the added wire field; no parallel receipt."""

    def _invoke(self, service, link, before, record, **kwargs):
        record['response_format'] = response_format_from_packet(json.loads(record['user']))
        record['decoding_profile'] = VERSION
        return super()._invoke(service, link, before, record, **kwargs)

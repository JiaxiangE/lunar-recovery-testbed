"""Opt-in whole-request metering; proxy diagnostics never authorize provider calls.

The legacy client policy remains unchanged. This policy measures the actual
assembled SDK request, response schema/tools, declared message template and an
additional maximum feedback allowance. A provider-specific official local
counter may be injected only when its exact model/template coverage is known;
none is bundled here and no tokenizer/model is downloaded.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re
import sys

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

@dataclass(frozen=True)
class LexicalProxy:
    """Dependency-free diagnostic units, deliberately neither BPE nor an upper bound."""
    name: str = 'unicode_word_punctuation_proxy'
    version: str = sys.version.split()[0]
    source: str = "Python re.findall(r'\\w+|[^\\w\\s]', text); not a provider tokenizer"

    def count(self, text):
        return len(re.findall('\\w+|[^\\w\\s]', text))
BYTE_BOUND_POLICY = 'utf8_bytes_are_a_token_upper_bound'
BYTE_BOUND_JUSTIFICATION = 'For any BPE or word-piece tokenizer over UTF-8 text, one token spans at least one character and one character spans at least one byte, so token_count <= char_count <= utf8_byte_count. The assembled-envelope byte count is therefore a sound but loose upper bound on prompt tokens. It is NOT tokenizer-exact and must not be reported as an official provider count or as an expected spend.'

@dataclass
class FullRequestMetering:
    bytes_hard_cap: int
    provider_input_limit: int | None = None
    provider_completion_limit: int | None = None
    provider_window_limit: int | None = None
    template_text: str | None = None
    max_feedback: str = ''
    max_feedback_utf8_bytes: int = 0
    proxy_counter: object | None = None
    provider_counter: object | None = None
    byte_bound_policy: str | None = None

    def __post_init__(self):
        for name in ('bytes_hard_cap', 'max_feedback_utf8_bytes'):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == 'bytes_hard_cap' else 0):
                raise ValueError(f'invalid {name}')
        for name in ('provider_input_limit', 'provider_completion_limit', 'provider_window_limit'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'invalid {name}')
        if self.proxy_counter is None:
            self.proxy_counter = LexicalProxy()
        if self.byte_bound_policy is not None and self.byte_bound_policy != BYTE_BOUND_POLICY:
            raise ValueError('unsupported byte_bound_policy')
        if self.byte_bound_policy is not None and self.provider_counter is not None:
            raise ValueError('byte_bound_policy and an official provider_counter are mutually exclusive')

    def measure(self, request, *, prompt_cap, completion_cap):
        wire = deepcopy(request)
        extra = wire.pop('extra_body', {}) or {}
        for key, value in extra.items():
            if key in wire and wire[key] != value:
                raise ValueError(f'extra_body overrides a metered request field: {key}')
            wire[key] = value
        request_text = _json(wire)
        request_bytes = len(request_text.encode('utf-8'))
        template = self.template_text or ''
        feedback_bytes = max(len(self.max_feedback.encode('utf-8')), self.max_feedback_utf8_bytes)
        total_bytes = request_bytes + len(template.encode('utf-8')) + feedback_bytes
        components = {'messages_utf8_bytes': len(_json(wire.get('messages', [])).encode('utf-8')), 'tools_utf8_bytes': len(_json(wire['tools']).encode('utf-8')) if 'tools' in wire else 0, 'response_format_utf8_bytes': len(_json(wire['response_format']).encode('utf-8')) if 'response_format' in wire else 0, 'template_utf8_bytes': len(template.encode('utf-8')), 'maximum_additional_feedback_utf8_bytes': feedback_bytes}
        measured_proxy_text = request_text + template + self.max_feedback
        proxy_count = self.proxy_counter.count(measured_proxy_text)
        result = {'policy': 'full_request_metering_v1', 'request_utf8_bytes': request_bytes, 'full_envelope_utf8_bytes': total_bytes, 'bytes_hard_cap': self.bytes_hard_cap, 'bytes_fit': total_bytes <= self.bytes_hard_cap, 'components': components, 'accounting_scope': 'assembled wire JSON plus explicit template plus maximum additional feedback allowance; JSON component sizes are diagnostic and not summed twice', 'proxy': {'name': self.proxy_counter.name, 'version': self.proxy_counter.version, 'source': self.proxy_counter.source, 'count': proxy_count, 'estimated_fits_prompt_cap': proxy_count <= prompt_cap, 'is_provider_upper_bound': False, 'unrepresented_feedback_allowance_bytes': feedback_bytes - len(self.max_feedback.encode('utf-8'))}, 'provider_input_tokens_upper_bound': None, 'provider_fits': None, 'provider_counter': None, 'declared_template_available': self.template_text is not None, 'prompt_cap': prompt_cap, 'completion_cap': completion_cap, 'provider_input_limit': self.provider_input_limit, 'provider_completion_limit': self.provider_completion_limit, 'provider_window_limit': self.provider_window_limit, 'unknown_reason': 'No verified official local model/template counter; proxy is diagnostic only'}
        counter = self.provider_counter
        if counter is not None:
            if counter.model != wire['model'] or counter.official is not True or counter.covers_template is not True:
                raise ValueError('provider counter does not cover this exact model/template')
            upper = counter.count_request_upper_bound(wire, template_text=self.template_text, maximum_feedback_utf8_bytes=feedback_bytes)
            if type(upper) is not int or upper < 0:
                raise ValueError('official request counter returned no valid upper bound')
            result.update(provider_input_tokens_upper_bound=upper, provider_counter={'name': counter.name, 'version': counter.version, 'source': counter.source})
            limits = (self.provider_input_limit, self.provider_completion_limit, self.provider_window_limit)
            if all((value is not None for value in limits)):
                result.update(provider_fits=upper <= min(prompt_cap, self.provider_input_limit) and completion_cap <= self.provider_completion_limit and (upper + completion_cap <= self.provider_window_limit), unknown_reason=None)
            else:
                result['unknown_reason'] = 'provider input/completion/window limits are not all verified'
        elif self.byte_bound_policy == BYTE_BOUND_POLICY:
            result.update(provider_input_tokens_upper_bound=total_bytes, provider_counter=None, bound_basis=BYTE_BOUND_POLICY, bound_justification=BYTE_BOUND_JUSTIFICATION, bound_tightness='conservative_byte_bound_not_tokenizer_exact')
            limits = (self.provider_input_limit, self.provider_completion_limit, self.provider_window_limit)
            if all((value is not None for value in limits)):
                result.update(provider_fits=total_bytes <= min(prompt_cap, self.provider_input_limit) and completion_cap <= self.provider_completion_limit and (total_bytes + completion_cap <= self.provider_window_limit), unknown_reason=None)
            else:
                result['unknown_reason'] = 'provider input/completion/window limits are not all verified'
        return result

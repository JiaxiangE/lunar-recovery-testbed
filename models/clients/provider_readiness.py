"""Official model facts and unresolved metering, consumed by the unsigned proposal.

No API, tokenizer download, model alias substitution, or readiness authorization.
Official documentation checked 2026-09-06. Numeric caps below are prospective
request policy, not scientific constants or verified input measurements.
"""
from models.clients.request_metering import FullRequestMetering
from importlib.metadata import version
from importlib.metadata import PackageNotFoundError
MODEL_SOURCE = 'https://help.aliyun.com/zh/model-studio/qwen3-8-max'
THINKING_SOURCE = 'https://help.aliyun.com/en/model-studio/deep-thinking'
PARAMETER_SOURCE = 'https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions'
TEMPLATE_SOURCE = 'https://help.aliyun.com/en/model-studio/text-generation'
TOTAL_COMPLETION_CAP = 65536
COMPLETION_OVERRUN_ALLOWANCE = 10
COMPLETION_RESERVATION = TOTAL_COMPLETION_CAP + COMPLETION_OVERRUN_ALLOWANCE

def provider_facts(model='qwen3.8-max'):
    known = model in {'qwen3.8-max', 'qwen3.8-max-0902', 'qwen3.8-max-2026-09-02'}
    try:
        sdk_version = version('openai')
    except PackageNotFoundError:
        sdk_version = None
    return {'requested_model': model, 'checked_date': '2026-09-06', 'client_runtime': {'sdk': 'openai', 'installed_version': sdk_version, 'surface': 'chat.completions.create', 'provider': 'Alibaba Cloud Model Studio OpenAI-compatible Chat API', 'actual_endpoint': 'recorded per existing call ledger; no credentials or endpoint inferred by this fact record', 'validated_local_signature': 'OpenAI 3.8.0 exposes max_completion_tokens; this does not test provider acceptance.'}, 'alias_policy': 'qwen3.8-max is an alias; no automatic substitution with the 0902 snapshot. Actual response model must be retained.', 'official_limits': {'input_non_thinking': 991808, 'input_thinking': 983616, 'answer_output': 131072, 'context_window': 1000000, 'reasoning_chain': 262144, 'source': MODEL_SOURCE, 'caveat': 'Official limits may vary with API parameter combinations.'} if known else None, 'thinking': {'enable_thinking': True, 'default_for_known_model': True if known else None, 'source': THINKING_SOURCE}, 'completion_policy': {'wire_parameter': 'max_completion_tokens', 'wire_limit': TOTAL_COMPLETION_CAP, 'reasoning_plus_answer': True, 'official_maximum_count_difference': COMPLETION_OVERRUN_ALLOWANCE, 'reserved_completion_tokens': COMPLETION_RESERVATION, 'source': PARAMETER_SOURCE, 'parameter_choice': 'Client sends only max_completion_tokens. This is an adapter rule; official mutual exclusion is not asserted.'}, 'metering': {'provider_input_tokens': None, 'provider_fits': None, 'official_local_counter': None, 'exact_tokenizer_revision': None, 'complete_chat_template': None, 'known_template_evidence': 'Official documentation illustrates added role/boundary/thinking markers for a one-message Hi example, not a complete arbitrary-request counter.', 'source': TEMPLATE_SOURCE, 'unknown_reason': 'No verified exact-model official tokenizer/template adapter is installed; serialized bytes and lexical proxy cannot authorize real calls.'}, 'input_cap_policy': 'Set each request-type cap from a trustworthy complete-request count plus feedback bound when available; do not delete task fields to fit a prior cap.'}

def proposed_metering(*, bytes_hard_cap=16777216, max_feedback_utf8_bytes=65536, model='qwen3.8-max', byte_bound_policy=None):
    """Metering with the documented provider limits.

    ``byte_bound_policy`` is opt-in and defaults to None, which preserves the historical
    behaviour of leaving ``provider_fits`` unknown. Passing
    ``request_metering.BYTE_BOUND_POLICY`` declares the assembled-envelope UTF-8 byte count
    as the prompt-token upper bound. That bound is mathematically sound for BPE/word-piece
    tokenizers but loose, and the measurement records it as
    ``bound_tightness="conservative_byte_bound_not_tokenizer_exact"`` so it can never be
    mistaken for an official provider count or for an expected spend.
    """
    known = provider_facts(model)['official_limits']
    return FullRequestMetering(bytes_hard_cap=bytes_hard_cap, provider_input_limit=known['input_thinking'] if known else None, provider_completion_limit=known['answer_output'] if known else None, provider_window_limit=known['context_window'] if known else None, max_feedback_utf8_bytes=max_feedback_utf8_bytes, byte_bound_policy=byte_bound_policy)

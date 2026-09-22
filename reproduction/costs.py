"""Distinct full paths versus unique newly generated work; read-only sources."""

def cost(record, requests=None):
    reqs = record.get('requests', []) if requests is None else requests
    ids = {q.get('logical_call_id') for q in reqs}
    attempts = [p for p in record.get('physical_attempts', []) if p['logical_call_id'] in ids]
    known = {}
    unknown = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [p['usage'].get(key) for p in attempts]
        known[key] = sum((v for v in values if type(v) is int))
        unknown[key] = sum((type(v) is not int for v in values))
    return {'logical_requests': len(reqs), 'physical_attempts': len(attempts), 'known_tokens_lower_bound': known, 'unknown_usage_attempts': unknown, 'transport_elapsed_s_lower_bound': sum((p['elapsed_s'] for p in attempts if type(p.get('elapsed_s')) in (int, float))), 'transport_elapsed_unknown_n': sum((type(p.get('elapsed_s')) not in (int, float) for p in attempts)), 'accounting': 'physical transport usage/time including failures; not isolated GPU time or continuous full-path wall; unknown is not zero'}

def plus(a, b):
    return {'logical_requests': a['logical_requests'] + b['logical_requests'], 'physical_attempts': a['physical_attempts'] + b['physical_attempts'], 'known_tokens_lower_bound': {k: a['known_tokens_lower_bound'][k] + b['known_tokens_lower_bound'][k] for k in a['known_tokens_lower_bound']}, 'unknown_usage_attempts': {k: a['unknown_usage_attempts'][k] + b['unknown_usage_attempts'][k] for k in a['unknown_usage_attempts']}, 'transport_elapsed_s_lower_bound': a['transport_elapsed_s_lower_bound'] + b['transport_elapsed_s_lower_bound'], 'transport_elapsed_unknown_n': a['transport_elapsed_unknown_n'] + b['transport_elapsed_unknown_n'], 'accounting': 'required method-internal prefix plus new branch work; not a unique experiment bill'}

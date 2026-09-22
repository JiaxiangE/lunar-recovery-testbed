"""Vllm client."""
from models.local_client import build_client as guarded_client
from models.local_client import http_json
SAMPLING = {'temperature': 1.0, 'top_p': 0.95, 'top_k': 20, 'min_p': 0.0, 'presence_penalty': 0.0, 'repetition_penalty': 1.0}

def health(service, http=http_json):
    models = http(service.endpoint, '/v1/models')
    matched = [m for m in models.get('data', []) if m.get('id') == service.model]
    return {'models': models, 'model_present': bool(matched), 'context_observed': matched[0].get('max_model_len') if matched else None, 'requested_context_cap': service.context_tokens, 'interpretation': 'loaded model metadata only; generation and observed memory checked separately'}

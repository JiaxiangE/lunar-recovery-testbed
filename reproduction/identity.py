"""Exact request identity after a declared, reversible source-locator migration."""
import json
from functools import lru_cache
from reproduction.records import PACKAGE_ROOT

@lru_cache(None)
def references():
    return json.loads((PACKAGE_ROOT / 'data/source_references.json').read_text(encoding='utf-8'))

def historical_message(text):
    for public, original in references().items():
        text = text.replace(json.dumps(public, ensure_ascii=False), json.dumps(original, ensure_ascii=False))
    return text

def require_same_request(saved, actual):
    for key in ('system', 'user', 'operation'):
        reconstructed = historical_message(actual[key]) if key == 'user' else actual[key]
        if saved[key] != reconstructed:
            raise ValueError('strict replay mismatch: ' + key)

def historical_context(problem, information):
    from dataclasses import replace

    def restore(value):
        if isinstance(value, str):
            return references().get(value, value)
        if isinstance(value, dict):
            return {k: restore(v) for k, v in value.items()}
        if isinstance(value, tuple):
            return tuple((restore(v) for v in value))
        if isinstance(value, list):
            return [restore(v) for v in value]
        return value
    actions = tuple((replace(a, source_references=restore(a.source_references)) for a in problem.actions))
    return (replace(problem, actions=actions, observation=restore(problem.observation)), restore(information))

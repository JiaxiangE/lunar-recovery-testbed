"""Decode the recorded complete-context representation."""
import json
from models.context_encoding import unpack
from models.context_encoding import expand_action_table

def payload(q):
    p = json.loads(q['user'])
    return expand_action_table(unpack(p)) if p.get('encoding') == 'complete_context_sharing_v1' else p

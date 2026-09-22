"""Feedback requests."""
import json
from reproduction.identity import historical_message
from pathlib import Path
from models.task_feedback import structured_messages
from models.task_feedback import VERSION
from reproduction.feedback_inputs import JOBS
from reproduction.feedback_inputs import ROOT as V1
from reproduction.feedback_inputs import SOURCE
from reproduction.grounded_requests import factory
from reproduction.grounded_requests import schema_result
from reproduction.request_replay import read
from reproduction.request_replay import save
from reproduction.request_replay import services
from planning.repair.grounded_decoding import response_format_from_packet
ROOT = Path('data/local_repair/task_feedback')
PREVIEW = Path('data/local_repair/feedback_input_template')

def prepared_factory(folder, cell, arm, *, replay_source=None, client_override=None, persist=True):
    source = SOURCE / 'runs' / cell / arm / 'trial.json'
    feedback_path = V1 / 'preparation' / cell / arm / 'feedback.json'
    feedback = read(feedback_path)['feedback']
    preview = read(PREVIEW / cell / arm / 'request_preview.json')
    base = factory(folder, source=replay_source, client_override=client_override)

    def make(case, view):
        session = base(case, view)
        from reproduction.records import historical_identifier
        builder = structured_messages(view, feedback, source_trial=historical_identifier(source), source_feedback=historical_identifier(feedback_path))

        def messages(*args, **kw):
            system, user = builder(*args, **kw)
            packet = json.loads(user)
            if system != preview['system'] or json.loads(historical_message(user)) != preview['user'] or response_format_from_packet(packet) != preview['response_format']:
                raise ValueError('actual request differs from reviewed fixed v2 preview')
            return (system, user)
        messages.goal_alignment_enabled = builder.goal_alignment_enabled
        messages.interface_version = builder.interface_version
        session.message_builder = messages
        if not persist:
            session.records_path = None
        return session
    return make

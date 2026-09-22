"""Two bounded feedback diagnoses from preserved failures; no hidden plan repair."""
from pathlib import Path
from reproduction.grounded_requests import ROOT as SOURCE
from reproduction.grounded_requests import factory
from reproduction.grounded_requests import schema_result
ROOT = Path('data/local_repair/feedback_input')
JOBS = (('B05_psr_strict_disconnected', 'flat-full'), ('cross_actor', 'D_T-full'))

"""Build the three fixed inputs for the stable/changing-link pairs."""
from pathlib import Path
from reproduction.task_view_inputs import case_input as drift_case
from examples.recovery_inputs import build_case
VERSION = 'e13_continuous_link_v1'
OUTPUT = Path('data/communication')
CELLS = ('B02_psr_connected_named_store', 'R_T_psr_collection_position_drift', 'R_T_lava_collection_position_drift')

def source(cell):
    return build_case(cell, 1) if cell == CELLS[0] else drift_case(cell)

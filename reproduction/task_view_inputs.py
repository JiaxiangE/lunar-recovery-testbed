"""Two fixed source repairs x three full-method arms; edge7B only."""
from copy import deepcopy
from pathlib import Path
from examples.recovery_inputs import build_case
from examples.recovery_inputs import audit_case
from domains.observed_policy import PolicyObservedPSRWorld
from models.context_encoding import fixed_task_view
from domains.worlds.lava_execution_world import LavaExecutionWorld
from domains.common import binding_observation
VERSION = 'edge_repair_view_applicability_v1'
CELLS = ('R_T_psr_collection_position_drift', 'R_T_lava_collection_position_drift')
ARMS = ('ours_full', 'ours_view', 'flat_full')
OUTPUT = Path('data/local_repair/complete_task_view')

class PolicyObservedLavaWorld(LavaExecutionWorld):

    def snapshot_state(self):
        return {**super().snapshot_state(), 'experiment_supervisory_link': deepcopy(self.experiment_supervisory_link)}

def case_input(cell):
    if cell not in CELLS:
        raise ValueError('outside fixed two-input design')
    c = build_case(cell, seed=1)
    if c.fallback is not None:
        raise ValueError('no new degradation policy')
    c.world.__class__ = PolicyObservedPSRWorld if c.nominal.scenario_id == 'psr' else PolicyObservedLavaWorld
    c.world.experiment_supervisory_link = {'state': 'Disconnected', 'revision': 0}
    c.communication = 'Disconnected'
    c.nominal.observation['communication_policy'] = deepcopy(c.world.experiment_supervisory_link)
    c.nominal.observation['world'] = binding_observation(c.world, c.nominal.scenario_id)
    c.original_information['current_experiment_communication'] = deepcopy(c.world.experiment_supervisory_link)
    c.original_information['communication_experiment_definition'] = {'version': VERSION, 'meaning': 'strict supervisory Disconnected synchronized across world/domain/session/selector; original physical radio and source history unchanged'}
    return c

def check_views(full, view):
    expected = fixed_task_view(full)
    if view != expected:
        raise AssertionError('view differs beyond existing fixed-actor transformation')
    actor = view['node']['assigned_agent_id']
    a = full['context']['domain']['actions']
    b = view['context']['domain']['actions']
    if [x for x in a if x['agent_id'] == actor] != [x for x in b if x['agent_id'] == actor]:
        raise AssertionError('authorized actor semantics lost')
    if any((x not in a for x in b)):
        raise AssertionError('invented view semantics')
    return {'full_actions': len(a), 'view_actions': len(b), 'actor': actor, 'preserved_all_authorized_actions_and_effects': True, 'other_facts_and_public_information_unchanged': True}

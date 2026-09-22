"""Prospective source-compatible mechanism fixtures; no model evidence/selection."""
from examples.task_inputs import task
from examples.task_inputs import delivery
from examples.task_inputs import step
from examples.task_inputs import _dispatch
from examples.recovery_inputs import _assemble
from examples.recovery_inputs import audit_case
from domains.common import atom

def cross_actor_case():
    from domains.worlds.psr_world import PSRWorld
    from domains.actions.psr.strict_profile import STRICT_PSR_PROFILE
    world = PSRWorld(n_samples=2, semantics_profile=STRICT_PSR_PROFILE)
    world.reset(0)
    for i in (1, 2):
        sid = f'sample_{i}'
        actor = f'rover_{i}'
        world.samples[sid]['status'] = 'stored'
        world.agents[actor]['cargo'] = {sid}
        world.agents[actor]['facts'].add('sample_stored')
    goals = [atom('in_base_storage', s) for s in world.samples]
    groups = [[task('offload_' + str(i), f'rover_{i}', [atom('in_base_storage', f'sample_{i}')], [step('sample_offload', f'rover_{i}', base='base')]) for i in (1, 2)]]
    return _assemble('mechanism_cross_actor_custody', 1, world, 'psr', groups, goals, (), 0, lambda w: {'kind': 'controlled_mixed_ownership_initialization', 'agent': 'rover_1', 'symptom': 'pending_obligation'}, groups, 'T')

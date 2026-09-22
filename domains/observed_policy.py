"""Three bounded protocol units; injected supervision is not physical radio."""
from copy import deepcopy
from domains.worlds.psr_world import PSRWorld

class PolicyObservedPSRWorld(PSRWorld):

    def snapshot_state(self):
        return {**super().snapshot_state(), 'experiment_supervisory_link': deepcopy(self.experiment_supervisory_link)}

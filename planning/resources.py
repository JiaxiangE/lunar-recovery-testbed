"""Attach the common observed resource packet to a recovery input."""
from dataclasses import replace
from validation.resource_prediction import resource_information

def attach_resources(case):
    packet = resource_information(case.world, case.nominal.scenario_id)
    case.original_information = {**case.original_information, 'shared_resource_information': packet}
    for name in ('nominal', 'fallback'):
        problem = getattr(case, name)
        if problem is not None:
            setattr(case, name, replace(problem, observation={**problem.observation, 'shared_resource_information': packet}))
    return case

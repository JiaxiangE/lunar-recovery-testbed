"""Single registry for the three Paper 4 scenarios."""
from __future__ import annotations
from domains.scenarios.construction import CONSTRUCTION_SCENARIO_SPEC
from domains.scenarios.lava import LAVA_SCENARIO_SPEC
from domains.scenarios.psr import PSR_SCENARIO_SPEC
from domains.scenarios.spec import ScenarioSpec
_REGISTRY = {'psr': PSR_SCENARIO_SPEC, 'lava': LAVA_SCENARIO_SPEC, 'construction': CONSTRUCTION_SCENARIO_SPEC}

def scenario_ids() -> tuple[str, ...]:
    return tuple(_REGISTRY)

def get_scenario_spec(scenario_id: str) -> ScenarioSpec:
    try:
        return _REGISTRY[str(scenario_id).strip().lower()]
    except KeyError as exc:
        raise KeyError(f'unknown scenario_id: {scenario_id!r}') from exc

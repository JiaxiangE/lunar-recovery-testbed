"""Psr grounding."""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Sequence
from typing import Tuple
from validation.grounding import build_grounding_map
from validation.grounding import is_noop
from validation.grounding import resolve_move_target
from execution.realization.interface import PrimitiveRealizationProvenance
from execution.realization.interface import RealizationError
from execution.realization.interface import RealizationResult
RAW_STRICT = 'raw_strict'
GROUNDED_BUNDLE = 'grounded_bundle'
PSR_GROUNDING_BUNDLE_COMPONENTS = ('grounding_map', 'target_resolution', 'noop_pruning')

class UnknownTargetError(RealizationError):
    """A grounded PSR move target is still unresolved.

    ``provenance`` is attached so a miss can never be converted into an
    unqualified success or disappear from the trace.
    """

    def __init__(self, message: str, provenance: PrimitiveRealizationProvenance) -> None:
        super().__init__(message)
        self.provenance = provenance

def _name_and_args(step: Any) -> Tuple[str, Dict[str, Any]]:
    if isinstance(step, Mapping):
        name = step.get('primitive', step.get('primitive_name', step.get('name', '')))
        for key in ('params', 'primitive_args', 'args'):
            if key in step:
                raw_args = step[key]
                break
        else:
            raw_args = {}
        return (str(name or ''), dict(raw_args) if isinstance(raw_args, Mapping) else {})
    if isinstance(step, (tuple, list)):
        name = step[0] if step else ''
        raw_args = step[1] if len(step) >= 2 else {}
        return (str(name or ''), dict(raw_args) if isinstance(raw_args, Mapping) else {})
    name = getattr(step, 'primitive_name', '')
    raw_args = getattr(step, 'primitive_args', {})
    return (str(name or ''), dict(raw_args) if isinstance(raw_args, Mapping) else {})

def _with_args(step: Any, args: Mapping[str, Any]) -> Any:
    """Copy a canonical Primitive or mapping and replace only its argument object."""
    if isinstance(step, Mapping):
        out = deepcopy(dict(step))
        key = next((item for item in ('params', 'primitive_args', 'args') if item in out), 'params')
        out[key] = deepcopy(dict(args))
        return out
    if isinstance(step, (tuple, list)):
        out = list(deepcopy(step))
        replacement = deepcopy(dict(args))
        if len(out) >= 2:
            out[1] = replacement
        else:
            out.append(replacement)
        return tuple(out) if isinstance(step, tuple) else out
    out = deepcopy(step)
    if hasattr(out, 'primitive_args'):
        out.primitive_args = deepcopy(dict(args))
    return out

class PSRGroundingRealizer:
    """Live-state PSR realization adapter.

    ``realize_step`` is the safe execution-boundary API: callers invoke it
    immediately before each accepted primitive, after prior primitives have
    updated the world.  This preserves the existing ``is_noop`` algorithm's
    live-state semantics and avoids speculative plan-wide pruning.
    """
    implementation_id = 'psr_grounding_v1'

    def __init__(self, *, treatment: str=GROUNDED_BUNDLE, map_builder: Callable[[Any], Any]=build_grounding_map, target_resolver: Callable[..., Tuple[Any, bool]]=resolve_move_target, noop_checker: Callable[[str, Dict[str, Any], Any, str], bool]=is_noop) -> None:
        if treatment not in {RAW_STRICT, GROUNDED_BUNDLE}:
            raise ValueError(f'unknown PSR realization treatment: {treatment!r}')
        self.treatment = treatment
        self._map_builder = map_builder
        self._target_resolver = target_resolver
        self._noop_checker = noop_checker
        self._grounding_maps: Dict[int, Any] = {}
        self._events: list[PrimitiveRealizationProvenance] = []
        self._calls = {name: 0 for name in PSR_GROUNDING_BUNDLE_COMPONENTS}

    @property
    def runtime_controls(self) -> Dict[str, Any]:
        grounded = self.treatment == GROUNDED_BUNDLE
        return {'strict_collect': not grounded, 'sample_identity_fallback_enabled': grounded, 'coverage_retry_enabled': grounded, 'grounding_bundle_enabled': grounded}

    @property
    def provenance(self) -> Tuple[PrimitiveRealizationProvenance, ...]:
        return tuple(self._events)

    def component_provenance(self) -> Dict[str, Dict[str, Any]]:
        grounded = self.treatment == GROUNDED_BUNDLE
        out: Dict[str, Dict[str, Any]] = {}
        for name in PSR_GROUNDING_BUNDLE_COMPONENTS:
            out[name] = {'status': 'executed' if self._calls[name] else 'ready' if grounded else 'disabled', 'calls': self._calls[name], 'owner': 'psr_realizer'}
        return out

    def _map_for(self, world: Any) -> Any:
        key = id(world)
        if key not in self._grounding_maps:
            self._grounding_maps[key] = self._map_builder(world)
            self._calls['grounding_map'] += 1
        return self._grounding_maps[key]

    def realize_step(self, step: Any, *, world: Any, agent_id: str, step_index: int=0) -> RealizationResult:
        name, args = _name_and_args(step)
        realized = _with_args(step, args)
        if self.treatment == RAW_STRICT:
            raw_target = args.get('target') if name == 'move_to' else None
            event = PrimitiveRealizationProvenance(step_index=step_index, primitive_name=name, target_input=deepcopy(raw_target), target_output=deepcopy(raw_target), target_resolution='disabled_raw' if name == 'move_to' else 'not_applicable', no_op=None, detail='complete PSR grounding bundle disabled')
            self._events.append(event)
            return RealizationResult(realizer_implementation_id=self.implementation_id, realized_plan=[realized], treatment=self.treatment, primitive_provenance=[event], component_provenance=self.component_provenance(), runtime_controls=self.runtime_controls)
        target_input = args.get('target') if name == 'move_to' else None
        target_output = target_input
        resolution = 'not_applicable'
        soft_grounded = False
        if name == 'move_to':
            grounded_map = self._map_for(world)
            target_output, soft_grounded = self._target_resolver(args, world, agent_id, grounded_map, strict=False)
            self._calls['target_resolution'] += 1
            args['target'] = target_output
            realized = _with_args(step, args)
            if soft_grounded:
                resolution = 'soft_ground'
            elif world.resolve_target('move_to', {'target': target_output}, agent_id) is not None:
                resolution = 'hit'
            else:
                resolution = 'miss'
        no_op = self._noop_checker(name, args, world, agent_id)
        self._calls['noop_pruning'] += 1
        event = PrimitiveRealizationProvenance(step_index=step_index, primitive_name=name, target_input=deepcopy(target_input), target_output=deepcopy(target_output), target_resolution=resolution, soft_grounded=soft_grounded, no_op=no_op, detail='no-op pruned' if no_op else 'ready for accepted-only execution')
        self._events.append(event)
        if resolution == 'miss':
            raise UnknownTargetError(f'PSR move target remains unresolved: {target_input!r}', event)
        plan = [] if no_op else [realized]
        return RealizationResult(realizer_implementation_id=self.implementation_id, realized_plan=plan, treatment=self.treatment, primitive_provenance=[event], component_provenance=self.component_provenance(), runtime_controls=self.runtime_controls)

    def realize(self, candidate: Sequence[Any], *, world: Any, agent_id: str) -> RealizationResult:
        """Convenience for a single currently actionable primitive.

        Multi-step callers must use ``realize_step`` immediately before each
        execution so the no-op decision observes state produced by prior steps.
        """
        steps = list(candidate)
        if len(steps) != 1:
            raise RealizationError('PSR live-state realization accepts exactly one actionable step; realize accepted plans step-by-step')
        return self.realize_step(steps[0], world=world, agent_id=agent_id)

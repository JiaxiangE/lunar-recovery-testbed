"""P0-7 — Template Library v0 (Build Spec §3.3).

Option C Tier 1: when a Task's type is known, instantiate a hand-authored primitive
sequence with ZERO LLM calls. `instantiate` substitutes `$placeholders` from params;
`is_applicable` checks the template fits the Task + context. Primitive names are validated
against the 12-primitive PSR set (`psr_primitive_spec.PRIMITIVE_NAMES`).
"""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from domains.actions.psr.tokens import PRIMITIVE_NAMES

@dataclass
class PrimitiveCall:
    """One instantiated primitive invocation (template output unit)."""
    primitive_name: str
    args: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class TaskTemplate:
    task_type: str
    description: str
    primitive_sequence: Tuple[Tuple[str, Dict[str, str]], ...]
    requires_context: Dict[str, Any] = field(default_factory=dict)

    def primitive_names(self) -> List[str]:
        return [p for p, _ in self.primitive_sequence]

def _t(task_type, description, seq, requires_context=None) -> TaskTemplate:
    return TaskTemplate(task_type=task_type, description=description, primitive_sequence=tuple(((n, dict(a)) for n, a in seq)), requires_context=dict(requires_context or {}))
TASK_TEMPLATES: Dict[str, TaskTemplate] = {t.task_type: t for t in [_t('move_and_sample', 'Move to target, scan, collect, store sample', [('move_to', {'target': '$position'}), ('scan_spectral', {'target': '$position'}), ('sample_collect', {'sample_id': '$id'}), ('sample_store', {'sample_id': '$id'})]), _t('collect_no_scan', 'Move to target, collect and store (no spectral scan)', [('move_to', {'target': '$position'}), ('sample_collect', {'sample_id': '$id'}), ('sample_store', {'sample_id': '$id'})]), _t('survey_scan', 'Move to target and perform a spectral survey scan', [('move_to', {'target': '$position'}), ('scan_spectral', {'target': '$position'})]), _t('sample_and_return', 'Full sortie: collect a sample and bring it home to offload', [('move_to', {'target': '$position'}), ('scan_spectral', {'target': '$position'}), ('sample_collect', {'sample_id': '$id'}), ('sample_store', {'sample_id': '$id'}), ('return_to_base', {}), ('dock_with_base', {}), ('sample_offload', {'base': 'base'})]), _t('return_with_payload', 'Energy check, return to base, dock, offload stored sample', [('energy_check', {}), ('return_to_base', {}), ('dock_with_base', {}), ('sample_offload', {'base': 'base'})]), _t('return_to_base_simple', 'Return to base and dock', [('return_to_base', {}), ('dock_with_base', {})]), _t('report_status', 'Send a status report to base over the direct link', [('communicate_status', {'target': 'base'})]), _t('energy_check_only', 'Report the current energy level', [('energy_check', {})]), _t('relay_restore_link', 'Restore the base link via a relay node', [('navigate_to_relay', {'relay': '$relay'}), ('wait_for_relay', {}), ('communicate_relay', {'relay': '$relay'})], requires_context={'comm': 'lost_to_base'})]}

def get_template(task_type: str) -> Optional[TaskTemplate]:
    return TASK_TEMPLATES.get(task_type)

def instantiate(template: TaskTemplate, params: Dict[str, Any]) -> List[PrimitiveCall]:
    """Substitute `$placeholder` args from `params`; literals pass through.
    Raises KeyError if a referenced placeholder is missing from params."""
    out: List[PrimitiveCall] = []
    for name, arg_spec in template.primitive_sequence:
        args: Dict[str, Any] = {}
        for k, v in arg_spec.items():
            if isinstance(v, str) and v.startswith('$'):
                key = v[1:]
                if key not in params:
                    raise KeyError(f'template {template.task_type!r} needs param {key!r}')
                args[k] = params[key]
            else:
                args[k] = v
        out.append(PrimitiveCall(primitive_name=name, args=args))
    return out

def is_applicable(template: TaskTemplate, task: Any, ctx: Dict[str, Any]) -> bool:
    """True if `template` can serve `task` under `ctx`.

    Rules (v0): (1) every primitive in the template is in the 12-primitive set;
    (2) if the Task declares `required_primitives`, the template must cover them (as a set);
    (3) any `requires_context` key/value must match `ctx`.
    """
    if any((p not in PRIMITIVE_NAMES for p in template.primitive_names())):
        return False
    req = set(getattr(task, 'required_primitives', []) or [])
    if req and (not req.issubset(set(template.primitive_names()))):
        return False
    for k, v in template.requires_context.items():
        if (ctx or {}).get(k) != v:
            return False
    return True

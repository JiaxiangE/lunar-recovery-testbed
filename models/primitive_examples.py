"""Primitive examples."""
from __future__ import annotations
import json
from typing import Any
from typing import Dict
from typing import List

def _s(primitive: str, **params: Any) -> Dict[str, Any]:
    return {'primitive': primitive, 'params': params}
D_T_FEWSHOT: List[Dict[str, Any]] = [{'id': 'sampling_full', 'task': 'rover_1: drive to sample_2, scan it, collect it, and stow it.', 'context': {'agent': 'rover_1', 'energy_pct': 88, 'position': 'PSR_interior', 'comm': 'nominal'}, 'chain': [_s('move_to', target='sample_2.pos'), _s('scan_spectral', target='sample_2.pos'), _s('sample_collect', sample_id='sample_2'), _s('sample_store', sample_id='sample_2')], 'note': 'On-site before scan/collect; collect before store; emit the full chain.'}, {'id': 'return_offload_full', 'task': 'rover_2: it is already carrying a stored sample — return to base and offload it.', 'context': {'agent': 'rover_2', 'energy_pct': 40, 'position': 'PSR_interior', 'comm': 'degraded'}, 'chain': [_s('energy_check'), _s('return_to_base'), _s('dock_with_base'), _s('sample_offload', base='base')], 'note': 'Reach base and dock before offloading; finish the bookkeeping tail.'}, {'id': 'report_status_context_contrast', 'task': 'report mission status to base.', 'context': {'agent': 'rover_1', 'comm': 'nominal', 'position': 'PSR_interior'}, 'chain': [_s('communicate_status', target='base')], 'note': 'Direct link available -> one primitive.', 'contrast': {'context': {'agent': 'rover_1', 'comm': 'lost_to_base', 'position': 'PSR_core', 'relay_available': True}, 'chain': [_s('navigate_to_relay', relay='relay_1'), _s('wait_for_relay'), _s('communicate_relay', relay='relay_1')], 'note': 'SAME task, link lost -> restore via relay first. Decomposition is context-dependent.'}}]

def _fmt(task: str, context: Dict[str, Any], chain: List[Dict[str, Any]]) -> str:
    return f"Task: {task}\nContext: {json.dumps(context, ensure_ascii=False)}\n{json.dumps({'chain': chain}, ensure_ascii=False)}"

def render_fewshot_block(exemplars: List[Dict[str, Any]] | None=None) -> str:
    """Render the supplied D_T worked examples, defaulting to D_T_FEWSHOT."""
    parts: List[str] = ['Worked examples (match this style — always emit the FULL chain: every prerequisite AND the finishing steps):']
    n = 0
    for ex in exemplars or D_T_FEWSHOT:
        n += 1
        parts.append(f"\nExample {n} — {_fmt(ex['task'], ex['context'], ex['chain'])}")
        if 'contrast' in ex:
            c = ex['contrast']
            parts.append(f"\nExample {n} (SAME task, different context -> different chain): {_fmt(ex['task'], c['context'], c['chain'])}\n# {c['note']}")
    return '\n'.join(parts) + '\n'

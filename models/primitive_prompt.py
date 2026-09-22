"""A0.5 Sub-TaskGraph Capacity Test harness (CC writes; PI runs on the workstation).

Question (Addendum §1 / design §1): given a PSR Task + the 12-primitive library, can
Qwen-{0.5B,1.5B,7B,14B} emit a LEGAL + COMPLETE primitive chain, fast enough?

What this file does (design §9):
  build prompt -> call edge LLM via Paper 3 `vllm_client.generate` -> thin-JSON parse ->
  score 4 metrics (legal / complete / retry_fix / latency_p95) -> one retry on failure ->
  aggregate -> per-model CSV + auto-generated method/Subgraph_Capacity_Calibration_v1.md.

Design choices (documented per design §132 / §4):
  - THIN-PROMPT decomposition (not the 5-op recombination `LLMDecisionInterface`): D_T is a
    pure Task->Primitive listing, so a thin "output a JSON chain" prompt + our own parser is
    cleaner than the recombination tool-call stack. The ONLY repo dependency is
    `llm_interface.vllm_client` (stdlib-only; imported lazily so offline scoring/tests need
    no GPU and never touch Paper 3's `core`).
  - legal_rate via THIS PSR 12-primitive spec (`psr_primitive_spec`), not Paper 2's unrelated
    ActionRegistry (different names). complete_rate via ground-truth fact-coverage (the
    chain reaches the same goal facts as the sample's ground-truth chain) rather than the
    physical `postconditions.py` checker — keeps scoring abstract + self-contained; physical
    grounding is a later option.

PI usage (workstation, per design §8):
  # one size at a time (pkill vllm between sizes):
  python -m models.primitive_prompt run       --label 7B --model Qwen/Qwen2.5-7B-Instruct --base-url http://localhost:8000/v1       --out data/a0_5
  # after all sizes:
  python -m models.primitive_prompt report --out data/a0_5
"""
from __future__ import annotations
import json
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from domains.actions.psr.tokens import PSR_PRIMITIVES
from domains.actions.psr.tokens import chain_legality
from domains.actions.psr.tokens import chain_produces
from domains.actions.psr.tokens import goal_facts_of
from models.primitive_examples import render_fewshot_block

def _primitive_library_block() -> str:
    lines = []
    for name, spec in PSR_PRIMITIVES.items():
        params = ', '.join(spec.params) if spec.params else '(none)'
        lines.append(f'- {name}({params}) — {spec.description}')
    return '\n'.join(lines)

def build_system_prompt(*, few_shot: bool=True, exemplars=None) -> str:
    """D_T prompt = rules-explicit ordering block + the 12-primitive library + (default)
    few-shot worked exemplars (`few_shot_d_t`). Rules-explicit alone ~doubled legal_rate
    vs a terse prompt (2026-05-30 diagnostic); few-shot adds complete-chain exemplars to
    fix the 'stops short' failure. `few_shot=False` reproduces the rules-only ablation."""
    base = f"""You are a Task decomposer for a lunar PSR (permanently-shadowed region) rover. Decompose the given Task into an ORDERED JSON chain of primitives chosen ONLY from the library below.\nCRITICAL RULES:\n1. The rover starts at the position given in Context and is NOT co-located with any sample, relay, or the base unless explicitly stated. You MUST emit every prerequisite primitive explicitly.\n2. Emit move_to(target) BEFORE scan_spectral or sample_collect at that target.\n3. Emit sample_collect BEFORE sample_store.\n4. Emit return_to_base BEFORE dock_with_base or sample_offload.\n5. To use a relay: emit navigate_to_relay, THEN wait_for_relay, THEN communicate_relay.\n6. Emit the FULL chain through to the task's goal — do not stop after collecting (also store), and complete any return/offload the task asks for.\nA chain that skips a prerequisite or stops short of the goal is INVALID.\n\nPSR primitive library (use these names EXACTLY):\n{_primitive_library_block()}\n\nOutput STRICT JSON only, no prose: {{"chain": [{{"primitive": "<name>", "params": {{...}}}}, ...]}}\n"""
    if few_shot:
        base += '\n' + render_fewshot_block(exemplars)
    return base

def _extract_json_blob(text: str) -> Optional[str]:
    """First balanced {...} or [...] in text (handles ```json fences + surrounding prose)."""
    if not text:
        return None
    t = text.strip()
    if '```' in t:
        parts = t.split('```')
        for seg in parts:
            seg2 = seg[4:] if seg.lower().startswith('json') else seg
            if '{' in seg2 or '[' in seg2:
                t = seg2
                break
    candidates = []
    for open_c, close_c in (('{', '}'), ('[', ']')):
        pos = t.find(open_c)
        if pos != -1:
            candidates.append((pos, open_c, close_c))
    candidates.sort()
    for start, open_c, close_c in candidates:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == open_c:
                depth += 1
            elif t[i] == close_c:
                depth -= 1
                if depth == 0:
                    return t[start:i + 1]
    return None

def parse_chain(raw_text: str) -> List[Dict[str, Any]]:
    """Parse LLM output -> normalized [{'primitive': str, 'params': dict}, ...].

    Empty list on any parse failure (treated as illegal downstream).
    """
    blob = _extract_json_blob(raw_text)
    if not blob:
        return []
    try:
        obj = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return []
    seq = obj.get('chain') if isinstance(obj, dict) else obj
    if not isinstance(seq, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in seq:
        if isinstance(item, dict) and ('primitive' in item or 'name' in item):
            name = item.get('primitive', item.get('name'))
            params = item.get('params', {})
            out.append({'primitive': str(name), 'params': params if isinstance(params, dict) else {}})
        elif isinstance(item, str):
            out.append({'primitive': item, 'params': {}})
    return out

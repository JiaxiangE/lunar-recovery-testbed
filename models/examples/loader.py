"""Load and render the supplied, authored decomposition examples."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import yaml
_PKG_DIR = Path(__file__).resolve().parent

@dataclass
class FewShotExample:
    scenario: str
    layer: str
    type: str
    data: Dict[str, Any]
    path: Path

    def get(self, key: str, default: Any=None) -> Any:
        return self.data.get(key, default)

def load_few_shot_examples(scenario: str, layer: str, base_dir: Optional[Path]=None) -> List[FewShotExample]:
    """Load all `{layer}_*.yaml` for a scenario, sorted by filename (stable order)."""
    root = (base_dir or _PKG_DIR) / scenario
    out: List[FewShotExample] = []
    if not root.exists():
        return out
    for f in sorted(root.glob(f'{layer}_*.yaml')):
        data = yaml.safe_load(f.read_text(encoding='utf-8')) or {}
        out.append(FewShotExample(scenario=scenario, layer=layer, type=str(data.get('type', '')), data=data, path=f))
    return out

def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

def render_few_shot_prompt(examples: List[FewShotExample], max_chars: int=4800) -> str:
    """Concatenate examples into a prompt-ready string (≤ ~1000 tokens ≈ `max_chars`).

    Positive examples render context -> expected_output; the negative example renders the
    attempted (bad) output + validator feedback, teaching what to avoid.
    """
    if not examples:
        return ''
    _PRIORITY = {'positive_standard': 0, 'context_contrast': 1, 'negative': 2}
    examples = sorted(examples, key=lambda e: _PRIORITY.get(e.type, 3))
    blocks: List[str] = ['Reference examples (study these before decomposing):']
    for i, ex in enumerate(examples, 1):
        head = f"\nExample {i} [{ex.type or 'example'}]:"
        if ex.type == 'context_contrast':
            body = f" SAME Scene goal, different context -> different decomposition.\n  Context A: {_compact(ex.get('context_A', {}))} => {_compact(ex.get('expected_output_A', {}))}\n  Context B: {_compact(ex.get('context_B', {}))} => {_compact(ex.get('expected_output_B', {}))}"
        elif ex.type == 'negative':
            body = f" REJECTED decomposition (do NOT do this).\n  Context: {_compact(ex.get('context', {}))}\n  Bad output: {_compact(ex.get('attempted_output', {}))}\n  Why rejected: {ex.get('validator_feedback', ex.get('rationale', ''))}"
        else:
            body = f" Context: {_compact(ex.get('context', {}))}\n  Output: {_compact(ex.get('expected_output', {}))}"
        rationale = ex.get('rationale')
        if rationale and ex.type != 'negative':
            body += f'\n  Note: {rationale}'
        blocks.append(head + body)
    text = '\n'.join(blocks) + '\n'
    if len(text) > max_chars:
        text = text[:max_chars].rsplit('\n', 1)[0] + '\n[...examples truncated to budget...]\n'
    return text

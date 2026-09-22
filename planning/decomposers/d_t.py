"""D t."""
from __future__ import annotations
import json
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from domains.scenarios import PromptContext
from domains.scenarios import ScenarioSpec
from planning.schema.decomposition import Task
from planning.schema.decomposition import Primitive
from planning.templates import TASK_TEMPLATES
from planning.templates import instantiate
from planning.templates import is_applicable
from planning.templates import PrimitiveCall
from domains.actions.psr.tokens import chain_legality
from domains.actions.psr.tokens import INITIAL_FACTS_FIELD
from models.primitive_prompt import build_system_prompt as _edge_system_prompt
from models.primitive_prompt import parse_chain as _parse_chain
from validation.contracts import validate_D_T_output
from planning.decomposers.d_s import DecompositionFailure

def _calls_to_primitives(calls: List[Any], task_id: str) -> List[Primitive]:
    out: List[Primitive] = []
    for i, c in enumerate(calls, 1):
        name = c.primitive_name if isinstance(c, PrimitiveCall) else c.get('primitive', '')
        args = c.args if isinstance(c, PrimitiveCall) else c.get('params', {})
        primitive_id = f'{task_id}_p{i}'
        out.append(Primitive(id=primitive_id, parent_id=task_id, goal_nl=name, primitive_name=name, primitive_args=dict(args), dependencies=[] if i == 1 else [f'{task_id}_p{i - 1}']))
    return out

def _try_template(task: Task, context: dict) -> Optional[List[Primitive]]:
    params = (context or {}).get('params', {})
    for tmpl in TASK_TEMPLATES.values():
        if is_applicable(tmpl, task, context or {}):
            try:
                calls = instantiate(tmpl, params)
            except KeyError:
                continue
            return _calls_to_primitives(calls, task.id)
    return None

def _edge_user_prompt(task: Task, prompt_context: PromptContext, scenario_spec: Optional[ScenarioSpec], feedback: str='') -> str:
    if scenario_spec is None:
        safe_context = {'observable_failure': dict(prompt_context.observable_failure), 'observable_state': dict(prompt_context.observable_state), 'agents': [dict(agent) for agent in prompt_context.agents], 'communication_state': prompt_context.communication_state, 'params': dict(prompt_context.params)}
    else:
        safe_context = prompt_context.to_prompt_payload(scenario_spec)
    return f'Task: {task.goal_nl}\nContext: {json.dumps(safe_context, ensure_ascii=False)}\n{feedback}\nReturn the primitive chain as JSON.'

def _scenario_system_prompt(scenario_spec: Optional[ScenarioSpec], exemplars: Any) -> str:
    if scenario_spec is None or scenario_spec.scenario_id == 'psr':
        base = _edge_system_prompt(exemplars=exemplars)
    else:
        base = 'You generate an ordered recovery primitive chain. Return JSON only and do not invent primitives or postconditions. Output STRICT JSON with exactly this shape: {"chain":[{"primitive":"<allowed primitive>","params":{}}]}.'
    if scenario_spec is None:
        return base
    vocabulary = '\n'.join((f'- {item}' for item in scenario_spec.render_prompt_vocabulary()))
    return f'{base}\nScenario: {scenario_spec.scenario_id}\nAllowed primitive vocabulary:\n{vocabulary}'

def build_d_t_prompts(task: Task, prompt_context: PromptContext, scenario_spec: ScenarioSpec, *, exemplars: Any=None, feedback: str='') -> tuple[str, str]:
    """Expose the production D_T prompt builder for staged transport tests.

    Wave-3 mock/replay and a later paid pilot must exercise the exact prompt
    construction used by :func:`D_T`; keeping this thin public adapter avoids a
    second, test-only prompt implementation.
    """
    if not isinstance(prompt_context, PromptContext):
        raise TypeError('prompt_context must be PromptContext')
    if not isinstance(scenario_spec, ScenarioSpec):
        raise TypeError('scenario_spec must be ScenarioSpec')
    prompt_context.validate()
    return (_scenario_system_prompt(scenario_spec, exemplars), _edge_user_prompt(task, prompt_context, scenario_spec, feedback))

def parse_d_t_response(raw: str) -> List[dict]:
    """Use the production D_T response parser without running hidden retries."""
    return _parse_chain(raw)

def last_rejected_chain(failure: DecompositionFailure) -> List[dict]:
    """Recover the final parsed proposal from a fail-closed D_T retry history."""
    history = getattr(failure, 'history', None)
    if not isinstance(history, list) or not history:
        return []
    value = history[-1].get('parsed_chain') if isinstance(history[-1], dict) else None
    if not isinstance(value, list):
        return []
    return [{'primitive': str(step.get('primitive', '')), 'params': dict(step.get('params', {}) or {})} for step in value if isinstance(step, dict)]

def D_T(task: Task, context: Optional[dict]=None, edge_llm_client: Any=None, is_failure_recovery: bool=False, max_retries: int=3, initial_facts=INITIAL_FACTS_FIELD, exemplars=None, scenario_spec: Optional[ScenarioSpec]=None, prompt_context: Optional[PromptContext]=None, execution_path: str='legacy', attempt_observer: Optional[Callable[[Dict[str, Any]], None]]=None, runtime_world: Any=None) -> List[Primitive]:
    """Option C Task→Primitive. Tier 1 template (unless failure recovery) → Tier 2 edge LLM.
    `exemplars` selects the edge-prompt few-shot set (None = default D_T_FEWSHOT; D-063 passes the
    comm-aware set for the intervention).

    ``execution_path='corrected'`` requires an explicit ScenarioSpec and PromptContext.  The
    default is retained only for historical/legacy callers and is never inferred as formal.
    """
    if execution_path not in {'legacy', 'corrected'}:
        raise ValueError("execution_path must be 'legacy' or 'corrected'")
    if execution_path == 'corrected':
        if scenario_spec is None or prompt_context is None:
            raise ValueError('corrected D_T requires explicit ScenarioSpec and PromptContext')
        if context is not None:
            raise ValueError('corrected D_T rejects raw context; use PromptContext only')
    if prompt_context is not None and (not isinstance(prompt_context, PromptContext)):
        raise TypeError('prompt_context must be PromptContext, never EvaluationMetadata')
    raw_context = context if context is not None else task.context or {}
    safe_prompt_context = prompt_context or PromptContext.from_legacy(raw_context)
    safe_prompt_context.validate()
    template_context = safe_prompt_context.template_context()
    contract = scenario_spec.build_recovery_contract(safe_prompt_context) if execution_path == 'corrected' and scenario_spec is not None else None
    if not is_failure_recovery and (scenario_spec is None or scenario_spec.scenario_id == 'psr'):
        prims = _try_template(task, template_context)
        if prims is not None:
            if execution_path == 'legacy' or scenario_spec is None:
                return prims
            template_chain = [{'primitive': item.primitive_name, 'params': item.primitive_args} for item in prims]
            verdict = scenario_spec.validate_candidate(template_chain, contract=contract)
            ok = verdict.accepted
            reason = verdict.detail or ', '.join(verdict.reason_codes)
            if ok and scenario_spec.scenario_id == 'psr':
                task_verdict = validate_D_T_output(task, template_chain, scenario='psr', prompt_context=safe_prompt_context, initial_facts=initial_facts, runtime_world=runtime_world)
                ok = task_verdict.passed
                reason = '; '.join(task_verdict.reasons) or task_verdict.reason_code
            if ok:
                return prims
    if edge_llm_client is None:
        raise DecompositionFailure(layer='Task', history=[{'reason': 'no template applied and no edge LLM'}])
    system = _scenario_system_prompt(scenario_spec, exemplars)
    feedback = ''
    history: List[dict] = []
    for attempt in range(max_retries + 1):
        raw = edge_llm_client.generate(system, _edge_user_prompt(task, safe_prompt_context, scenario_spec, feedback))
        chain = _parse_chain(raw)
        if execution_path == 'corrected' and scenario_spec is not None:
            verdict = scenario_spec.validate_candidate(chain, contract=contract)
            ok = verdict.accepted
            reason = verdict.detail or ', '.join(verdict.reason_codes)
            if ok and scenario_spec.scenario_id == 'psr':
                task_verdict = validate_D_T_output(task, chain, scenario='psr', prompt_context=safe_prompt_context, initial_facts=initial_facts, runtime_world=runtime_world)
                ok = task_verdict.passed
                reason = '; '.join(task_verdict.reasons) or task_verdict.reason_code
            elif ok and scenario_spec.scenario_id == 'lava':
                task_verdict = validate_D_T_output(task, chain, scenario='lava', prompt_context=safe_prompt_context)
                ok = task_verdict.passed
                reason = '; '.join(task_verdict.reasons) or task_verdict.reason_code
        else:
            ok, reason = chain_legality(chain, frozenset(initial_facts))
        attempt_record = {'attempt': attempt, 'parsed_chain': [{'primitive': item.get('primitive', ''), 'params': dict(item.get('params', {}) or {})} for item in chain if isinstance(item, dict)], 'semantic_accepted': bool(ok and chain), 'reason': reason or ('accepted' if ok and chain else 'empty chain')}
        if attempt_observer is not None:
            attempt_observer(attempt_record)
        if ok and chain:
            return _calls_to_primitives(chain, task.id)
        history.append(attempt_record)
        feedback = f"Your previous chain was rejected: {reason or 'empty chain'}. Emit every prerequisite primitive and return corrected JSON only."
    raise DecompositionFailure(layer='Task', history=history)

"""Delta."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from controller.scope import Scope
from controller.scope import SCOPES
from controller.diagnosis.post_rule import normalize
from controller.diagnosis.post_rule import smooth_distribution
CONFIDENCE_THRESHOLD = 0.7
RULE_PRIORS: Dict[str, Dict[Scope, float]] = {'L-01_wheel_stuck': {Scope.P: 0.5, Scope.T: 0.4, Scope.M: 0.1, Scope.S: 0.0}, 'L-04_path_blocked': {Scope.P: 0.5, Scope.T: 0.4, Scope.M: 0.1, Scope.S: 0.0}, 'S-01_sensor_fail': {Scope.P: 0.8, Scope.T: 0.15, Scope.M: 0.05, Scope.S: 0.0}, 'C-04_base_unreachable': {Scope.P: 0.1, Scope.T: 0.3, Scope.M: 0.6, Scope.S: 0.0}, 'G-01_task_unreachable': {Scope.P: 0.05, Scope.T: 0.6, Scope.M: 0.35, Scope.S: 0.0}, 'R-02_energy_critical': {Scope.P: 0.0, Scope.T: 0.3, Scope.M: 0.7, Scope.S: 0.0}, 'G-02_mission_infeasible': {Scope.P: 0.0, Scope.T: 0.05, Scope.M: 0.85, Scope.S: 0.1}, 'L-03_tip_over': {Scope.P: 0.2, Scope.T: 0.5, Scope.M: 0.3, Scope.S: 0.0}, 'C-02_comm_link_broken': {Scope.P: 0.2, Scope.T: 0.5, Scope.M: 0.3, Scope.S: 0.0}, 'C-05_peer_mismatch': {Scope.P: 0.1, Scope.T: 0.5, Scope.M: 0.4, Scope.S: 0.0}, 'M-02_joint_limit': {Scope.P: 0.4, Scope.T: 0.5, Scope.M: 0.1, Scope.S: 0.0}, 'M-05_assembly_mismatch': {Scope.P: 0.3, Scope.T: 0.5, Scope.M: 0.2, Scope.S: 0.0}, 'Co-01_deadlock': {Scope.P: 0.05, Scope.T: 0.3, Scope.M: 0.65, Scope.S: 0.0}, 'Co-03_goal_conflict': {Scope.P: 0.0, Scope.T: 0.2, Scope.M: 0.8, Scope.S: 0.0}, 'G-03_scene_unsatisfiable': {Scope.P: 0.0, Scope.T: 0.0, Scope.M: 0.1, Scope.S: 0.9}}
_SYSTEM = 'You are a failure diagnoser. Given a failure event, output a calibrated probability distribution over cause levels {P, T, M, S}. Do NOT output a single level.\n\nCalibration:\n- If you are unsure, distribute probability mass across multiple levels.\n- For a clear, observable, single-cause primitive/device failure, a highly confident P diagnosis may assign P in the numeric range 0.94 to 1.00 (never above 1.00). Use this range only when the evidence isolates that cause; do not increase confidence merely to prefer a repair scope.\n- When ambiguous, prefer distributions like {P:0.5,T:0.3,M:0.15,S:0.05} over point estimates.\n\nExamples:\n1. Clear primitive-level single-cause failure (for example, a device returns no reading) -> {P:0.96,T:0.02,M:0.01,S:0.01}\n2. Ambiguous P vs T (wheel stuck) -> {P:0.45,T:0.40,M:0.15,S:0.00}\n3. Clear Mission-level (target unreachable) -> {P:0.05,T:0.15,M:0.75,S:0.05}\n\nOutput STRICT JSON only: {"distribution":{"P":<p>,"T":<p>,"M":<p>,"S":<p>},"confidence":<0-1>,"rationale":"<=50 words"}'

@dataclass
class FailureEvent:
    symptom: str
    agent_id: str = ''
    node_id: str = ''
    context: Dict[str, Any] = field(default_factory=dict)

def _uniform() -> Dict[Scope, float]:
    return {s: 1.0 / len(SCOPES) for s in SCOPES}

def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search('\\{.*\\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None

class DiagnosisModule:
    """Δ diagnoser. `base_llm_client` is injected (real or mock); rule-only when None."""

    def __init__(self, confidence_threshold: float=CONFIDENCE_THRESHOLD):
        self.confidence_threshold = confidence_threshold
        self.last_source: Optional[str] = None
        self.last_parse_ok: bool = True

    def _rule_layer(self, F: FailureEvent) -> Optional[Dict[Scope, float]]:
        prior = RULE_PRIORS.get(F.symptom)
        return dict(prior) if prior is not None else None

    def _is_confident(self, dist: Dict[Scope, float]) -> bool:
        return bool(dist) and max(dist.values()) >= self.confidence_threshold

    def _llm_layer(self, F: FailureEvent, client: Any) -> Dict[Scope, float]:
        user = f'Failure event:\n- symptom: {F.symptom}\n- agent: {F.agent_id}\n- node: {F.node_id}\n- context: {json.dumps(F.context, ensure_ascii=False)}\nOutput the calibrated cause-level distribution as JSON.'
        obj = _extract_json(client.generate(_SYSTEM, user)) or {}
        raw = obj.get('distribution', {}) if isinstance(obj, dict) else {}
        dist = {s: float(raw.get(s.value, 0.0) or 0.0) for s in SCOPES}
        self._last_llm_parse_ok = sum(dist.values()) > 0
        return dist if self._last_llm_parse_ok else _uniform()

    def diagnose(self, F: FailureEvent, base_llm_client: Any=None, ctx: Optional[dict]=None, history: Optional[list]=None) -> Dict[Scope, float]:
        rule = self._rule_layer(F)
        if rule is not None and self._is_confident(rule):
            self.last_source, self.last_parse_ok = ('rule_confident', True)
            return normalize(rule)
        if base_llm_client is None:
            self.last_source = 'rule_offline' if rule is not None else 'uniform_offline'
            self.last_parse_ok = rule is not None
            return normalize(rule) if rule is not None else _uniform()
        dist = self._llm_layer(F, base_llm_client)
        self.last_parse_ok = getattr(self, '_last_llm_parse_ok', True)
        self.last_source = 'llm_smoothed' if self.last_parse_ok else 'uniform_llm_parse_fail'
        return smooth_distribution(dist)

"""Selector."""
from __future__ import annotations
import math
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from controller.scope import Scope
from controller.scope import SCOPES
from controller.scope import scope_order

@dataclass(frozen=True)
class UtilityWeights:
    tcr: float = 10.0
    mks: float = 0.05
    cost: float = 0.01
    risk: float = 0.5

@dataclass(frozen=True)
class PFixParams:
    lambda_: float = 1.0
    p_lucky: float = 0.1
_SUBTREE_SIZE: Dict[Scope, float] = {Scope.P: 1.0, Scope.T: 6.0, Scope.M: 25.0, Scope.S: 90.0}
_LLM_COST: Dict[Scope, float] = {Scope.P: 0.0, Scope.T: 1.0, Scope.M: 3.0, Scope.S: 6.0}

@dataclass
class ScopeDecision:
    sigma: Scope
    scores: Dict[Scope, float]
    available_scopes: List[Scope]
    delta_posterior: Dict[Scope, float]
    comm_constrained: bool
    utility_breakdown: Dict[str, float] = field(default_factory=dict)

def p_fix(sigma: Scope, ell: Scope, params: PFixParams) -> float:
    """Analytic fix probability (Scope_Selector_v2 §4): a scope ≥ the cause level fixes with
    probability 1−e^(−λ(Δbreadth+1)); a too-narrow scope only fixes by luck (p_lucky)."""
    s, e = (scope_order(sigma), scope_order(ell))
    if s >= e:
        return 1.0 - math.exp(-params.lambda_ * (s - e + 1))
    return params.p_lucky

def risk_term(sigma: Scope, ell: Scope, undershoot_mult: float=2.0) -> float:
    """Asymmetric residual risk: under-scoping (s<e) is penalised `undershoot_mult`×/level (leaves the
    real cause unfixed); over-scoping (s>e) is penalised 0.5×/level (wasted breadth); exact match = 0.
    undershoot_mult is the D-018 over-scope-safety calibration — FROZEN at 2.0; D-062-C sweeps it
    (1.0/2.0/3.0) as §7.8 exploration WITHOUT changing the default."""
    s, e = (scope_order(sigma), scope_order(ell))
    if s < e:
        return undershoot_mult * (e - s)
    if s > e:
        return 0.5 * (s - e)
    return 0.0

class ScopeSelector:

    def __init__(self, weights: Optional[UtilityWeights]=None, p_fix_params: Optional[PFixParams]=None, subtree_size: Optional[Dict[Scope, float]]=None, llm_cost: Optional[Dict[Scope, float]]=None, undershoot_mult: float=2.0):
        self.weights = weights or UtilityWeights()
        self.p_fix_params = p_fix_params or PFixParams()
        self.subtree_size = subtree_size or dict(_SUBTREE_SIZE)
        self.llm_cost = llm_cost or dict(_LLM_COST)
        self.undershoot_mult = undershoot_mult

    def _terms(self, sigma: Scope, delta_dist: Dict[Scope, float], ctx: dict) -> Dict[str, float]:
        avg_dur = float((ctx or {}).get('avg_primitive_duration', 1.0))
        e_p_fix = sum((p * p_fix(sigma, ell, self.p_fix_params) for ell, p in delta_dist.items()))
        e_risk = sum((p * risk_term(sigma, ell, self.undershoot_mult) for ell, p in delta_dist.items()))
        return {'tcr': self.weights.tcr * e_p_fix, 'mks': -self.weights.mks * self.subtree_size[sigma] * avg_dur, 'cost': -self.weights.cost * self.llm_cost[sigma], 'risk': -self.weights.risk * e_risk}

    def _utility(self, sigma: Scope, delta_dist: Dict[Scope, float], ctx: dict) -> float:
        return sum(self._terms(sigma, delta_dist, ctx).values())

    def select(self, F: Any, delta_dist: Dict[Scope, float], ctx: Optional[dict]=None, c_edge: Optional[Set[Scope]]=None, base_reachable: bool=True) -> ScopeDecision:
        ctx = ctx or {}
        delta_dist = {Scope(k): float(v) for k, v in delta_dist.items()}
        if not base_reachable:
            available = set(c_edge) if c_edge is not None else {Scope.P, Scope.T}
            if not available:
                raise ValueError('no communication-feasible scopes for the declared actor')
        else:
            available = set(SCOPES)
        scores = {sigma: self._utility(sigma, delta_dist, ctx) for sigma in available}
        sigma_star = max(scores, key=lambda s: (scores[s], -scope_order(s)))
        return ScopeDecision(sigma=sigma_star, scores=scores, available_scopes=sorted(available, key=scope_order), delta_posterior=delta_dist, comm_constrained=not base_reachable, utility_breakdown=self._terms(sigma_star, delta_dist, ctx))

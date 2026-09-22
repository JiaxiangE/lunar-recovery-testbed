"""P fix."""
from __future__ import annotations
import math
from dataclasses import dataclass
from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from controller.scope import Scope
from controller.scope import scope_order
from controller.selector import PFixParams
_EPS = 1e-09
Obs = Tuple[Scope, Scope, bool]

@dataclass
class ProfileFitResult:
    params: PFixParams
    log_likelihood: float
    n_observations: int
    lambda_interval_95: Tuple[float, float]
    p_lucky_interval_95: Tuple[float, float]
    goodness: Dict[str, Any]

def _p_fix(sigma: Scope, ell: Scope, lambda_: float, p_lucky: float) -> float:
    s, e = (scope_order(sigma), scope_order(ell))
    return 1.0 - math.exp(-lambda_ * (s - e + 1)) if s >= e else p_lucky

def _bernoulli_ll(successes: int, total: int, probability: float) -> float:
    p = min(1 - _EPS, max(_EPS, probability))
    return successes * math.log(p) + (total - successes) * math.log(1.0 - p)

def _wilson_interval(successes: int, total: int, z: float=1.959963984540054) -> Tuple[float, float]:
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return (max(0.0, centre - radius), min(1.0, centre + radius))

def calibrate_p_fix_profile(observations: List[Obs], *, lambda_min: float=0.001, lambda_max: float=5.0, lambda_step: float=0.001) -> ProfileFitResult:
    """Profile-MLE with a dense predeclared lambda grid and 95% intervals.

    The analytic model separates: lambda is informed by at-or-above-scope rows,
    while p_lucky is the empirical Bernoulli MLE for under-scope rows.  The
    lambda interval is the one-parameter likelihood-ratio set
    ``2*(LL_max-LL)<=3.841459``; p_lucky uses the Wilson binomial interval.
    """
    if not observations:
        raise ValueError('profile calibration requires observations')
    if lambda_min <= 0 or lambda_max <= lambda_min or lambda_step <= 0:
        raise ValueError('invalid lambda profile grid')
    above = [(sigma, ell, fixed) for sigma, ell, fixed in observations if scope_order(sigma) >= scope_order(ell)]
    under = [(sigma, ell, fixed) for sigma, ell, fixed in observations if scope_order(sigma) < scope_order(ell)]
    if not above or not under:
        raise ValueError('profile calibration needs both supported and under-scope rows')
    under_success = sum((bool(fixed) for _sigma, _ell, fixed in under))
    p_lucky = under_success / len(under)
    n_grid = int(round((lambda_max - lambda_min) / lambda_step)) + 1
    lambdas = [lambda_min + index * lambda_step for index in range(n_grid)]
    profile = []
    for lam in lambdas:
        ll = sum((math.log(min(1 - _EPS, max(_EPS, _p_fix(sigma, ell, lam, p_lucky)))) if fixed else math.log(1.0 - min(1 - _EPS, max(_EPS, _p_fix(sigma, ell, lam, p_lucky)))) for sigma, ell, fixed in above))
        profile.append(ll)
    best_index = max(range(len(profile)), key=profile.__getitem__)
    best_lambda = lambdas[best_index]
    under_ll = _bernoulli_ll(under_success, len(under), p_lucky)
    best_total_ll = profile[best_index] + under_ll
    cutoff = profile[best_index] - 3.841458820694124 / 2.0
    accepted = [lam for lam, ll in zip(lambdas, profile) if ll >= cutoff]
    grouped: Dict[Tuple[Scope, Scope], List[bool]] = defaultdict(list)
    brier = 0.0
    for sigma, ell, fixed in observations:
        grouped[sigma, ell].append(bool(fixed))
        probability = _p_fix(sigma, ell, best_lambda, p_lucky)
        brier += (float(bool(fixed)) - probability) ** 2
    saturated_ll = 0.0
    for values in grouped.values():
        saturated_ll += _bernoulli_ll(sum(values), len(values), sum(values) / len(values))
    null_success = sum((bool(fixed) for _sigma, _ell, fixed in observations))
    null_ll = _bernoulli_ll(null_success, len(observations), null_success / len(observations))
    goodness = {'deviance': 2.0 * (saturated_ll - best_total_ll), 'brier_score': brier / len(observations), 'null_log_likelihood': null_ll, 'mcfadden_pseudo_r2': 1.0 - best_total_ll / null_ll if abs(null_ll) > _EPS else None, 'lambda_grid_boundary_hit': best_index in {0, len(lambdas) - 1}, 'lambda_profile_step': lambda_step}
    return ProfileFitResult(params=PFixParams(lambda_=round(best_lambda, 12), p_lucky=p_lucky), log_likelihood=best_total_ll, n_observations=len(observations), lambda_interval_95=(accepted[0], accepted[-1]), p_lucky_interval_95=_wilson_interval(under_success, len(under)), goodness=goodness)

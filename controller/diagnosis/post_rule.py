"""Post rule."""
from __future__ import annotations
from typing import Dict

def normalize(dist: Dict) -> Dict:
    """Renormalize to sum 1 (uniform if the input sums to ~0)."""
    total = sum(dist.values())
    if total <= 1e-12:
        n = len(dist) or 1
        return {k: 1.0 / n for k in dist}
    return {k: v / total for k, v in dist.items()}

def smooth_distribution(dist: Dict, smoothing: float=0.05, max_cap: float=0.95) -> Dict:
    """Mix with uniform iff the max probability exceeds 0.9, guaranteeing the post-mix max ≤ `max_cap`
    (D-045 Critical-1 A). B-2 fix: a fixed `smoothing=0.05` mix only bounded the max at
    0.95·m + 0.05/n (≈0.9625 for a degenerate m=1.0, n=4) — i.e. it did NOT honour the documented
    ≤0.95. We now raise the mix coefficient adaptively to whatever brings max exactly to `max_cap`
    when the default 0.05 would overshoot (post-mix max = (1−s)·m + s·u; solving for s gives
    s=(m−cap)/(m−u)), while never mixing LESS than `smoothing`. (n=1 is a no-op: the `m>u` guard
    is False since u=1.0 — a single-bucket distribution cannot be made non-degenerate; production
    always has the 4 scope levels, so this boundary is never hit.)"""
    dist = normalize(dist)
    if not dist:
        return dist
    m = max(dist.values())
    if m <= 0.9:
        return dist
    n_levels = len(dist)
    u = 1.0 / n_levels
    s = smoothing
    if (1 - s) * m + s * u > max_cap and m > u:
        s = (m - max_cap) / (m - u)
    dist = {k: (1 - s) * v + s * u for k, v in dist.items()}
    return normalize(dist)

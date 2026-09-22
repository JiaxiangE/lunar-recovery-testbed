"""The 4-level decomposition lattice {P, T, M, S}, shared by injection (expected cause level),
diagnosis Δ (cause-level distribution), and the scope selector (re-decomposition scope).

P < T < M < S in *breadth* of re-decomposition (Primitive ⊂ Task ⊂ Mission ⊂ Scene).
A str-Enum so values serialize to plain "P"/"T"/"M"/"S" and compare to those strings.
"""
from __future__ import annotations
from enum import Enum
from typing import List

class Scope(str, Enum):
    P = 'P'
    T = 'T'
    M = 'M'
    S = 'S'
SCOPES: List[Scope] = [Scope.P, Scope.T, Scope.M, Scope.S]

def scope_order(s: Scope) -> int:
    """Breadth rank (P=0 … S=3); higher = broader re-decomposition."""
    return SCOPES.index(Scope(s))

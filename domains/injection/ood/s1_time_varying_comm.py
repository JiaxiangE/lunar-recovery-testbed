"""Seeded continuous-time communication transitions across Connected, Degraded and Disconnected. The stable cell/seed string fixes the dwell trajectory independently of recovery outcomes."""
from __future__ import annotations
import random
A_STATES = ('Connected', 'Degraded', 'Disconnected')

def _next_state(state: int, rng: random.Random) -> int:
    """Birth-death transition on the chain Connected(0)–Degraded(1)–Disconnected(2)."""
    if state == 0:
        return 1
    if state == 2:
        return 1
    return rng.choice([0, 2])

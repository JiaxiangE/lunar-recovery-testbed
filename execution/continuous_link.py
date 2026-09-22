"""Opt-in software-time trajectory; background never mutates a world."""
import random
from domains.injection.ood.s1_time_varying_comm import A_STATES
from domains.injection.ood.s1_time_varying_comm import _next_state

def ctmc(cell, seed, mean=60.0, decision=300.0, horizon=3600.0):
    rng = random.Random(f'{cell}|{seed}')
    t = 0.0
    state = 0
    events = []
    initial = None
    while t <= decision + horizon:
        end = t + rng.expovariate(1.0 / mean)
        if t <= decision < end:
            initial = A_STATES[state]
        new = _next_state(state, rng)
        if decision < end <= decision + horizon:
            events.append({'at': end - decision, 'state': A_STATES[new]})
        t = end
        state = new
    return (initial, events)

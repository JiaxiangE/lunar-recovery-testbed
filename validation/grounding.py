"""Grounding."""
from __future__ import annotations
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
Pos = Tuple[float, float, float]
_EPS_M = 1.0
_INDEX_RE = re.compile('(\\d+)')

@dataclass
class GroundingMap:
    """Scene-level spatial knowledge available to the planner for grounding symbolic targets."""
    samples: Dict[str, Pos] = field(default_factory=dict)
    regions: Dict[str, Pos] = field(default_factory=dict)
    base_pos: Pos = (0.0, 0.0, 0.0)

def build_grounding_map(world: Any) -> GroundingMap:
    """Build a GroundingMap from the PSR scene (sample candidate sites + base + a default
    survey waypoint at the centroid of the known sites)."""
    samples = {sid: tuple(s['location']) for sid, s in getattr(world, 'samples', {}).items()}
    base = tuple(getattr(world, 'base_pos', (0.0, 0.0, 0.0)))
    regions: Dict[str, Pos] = {}
    if samples:
        xs = [p[0] for p in samples.values()]
        ys = [p[1] for p in samples.values()]
        centroid = (round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1), 0.0)
        for name in ('psr_region', 'survey_area', 'operational_area', 'region', 'field'):
            regions[name] = centroid
    return GroundingMap(samples=samples, regions=regions, base_pos=base)

def ground_target(target: Any, gmap: GroundingMap) -> Any:
    """Resolve a symbolic `move_to` target to something `PSRWorld.resolve_target` accepts.

    Resolution order (conservative — unknowns pass through unchanged):
      1. coordinate (list/tuple)                  -> keep
      2. known sample id (with/without `.pos`)    -> canonical `<sid>.pos`
      3. symbolic alias with a trailing index     -> `sample_<N>.pos` if that sample exists
         (e.g. ice_sample_site_1 / site_1 / sample_site_1 -> sample_1)
      4. known region name                        -> its coordinate
      5. otherwise                                -> unchanged (genuine gap, kept as signal)
    """
    if isinstance(target, (list, tuple)):
        return target
    if not isinstance(target, str):
        return target
    raw = target.strip()
    sid = raw[:-4] if raw.endswith('.pos') else raw
    if sid in gmap.samples:
        return f'{sid}.pos'
    low = sid.lower()
    if low in gmap.regions:
        return list(gmap.regions[low])
    m = _INDEX_RE.search(sid)
    if m and ('sample' in low or 'site' in low or 'ice' in low or ('target' in low) or ('waypoint' in low) or ('wp' in low)):
        cand = f'sample_{m.group(1)}'
        if cand in gmap.samples:
            return f'{cand}.pos'
    return target

def _at(a: Pos, b: Pos, eps: float=_EPS_M) -> bool:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 <= eps

def nearest_uncollected_sample(world: Any, agent_id: str) -> Optional[str]:
    """The id of the closest sample still in the field (not in a rover / stored / in base),
    measured from the agent's current position. None if all samples are accounted for."""
    agent = world.agents.get(agent_id)
    if agent is None:
        return None
    pos = agent['position']
    cands = [(sid, s['location']) for sid, s in world.samples.items() if s.get('status') == 'field' and sid not in world.base_storage]
    if not cands:
        return None
    return min(cands, key=lambda c: (pos[0] - c[1][0]) ** 2 + (pos[1] - c[1][1]) ** 2)[0]

def resolve_move_target(args: Dict[str, Any], world: Any, agent_id: str, gmap: GroundingMap, strict: bool=False) -> Tuple[Any, bool]:
    """Resolve a `move_to` target to something the world can reach, using LIVE state.

    Returns (target, soft_grounded). `soft_grounded=True` flags that the LLM's target was
    NOT directly resolvable and we substituted a plausible concrete waypoint — a quantified
    decomposition-quality signal (NOT hidden): the caller counts these.

      1. static grounding (coord / sample id / index alias / region) that the world resolves
      2. else nearest uncollected sample  (the operational intent of 'go to the ice site')
      3. else the region centroid          (no objective left -> head to the area)

    `strict=True` (DFR / --strict-no-backstop, D-043 §2): DISABLE steps 2-3 — an unresolvable
    target is left as-is so the move honestly fails (path_blocked), measuring the raw
    decomposition's grounding fidelity without the backstop rescuing it."""
    raw = (args or {}).get('target')
    static = ground_target(raw, gmap)
    if world.resolve_target('move_to', {'target': static}, agent_id) is not None:
        return (static, False)
    if strict:
        return (static, False)
    sid = nearest_uncollected_sample(world, agent_id)
    if sid is not None:
        return (f'{sid}.pos', True)
    if gmap.regions:
        return (list(next(iter(gmap.regions.values()))), True)
    return (static, False)

def is_noop(prim_name: str, args: Dict[str, Any], world: Any, agent_id: str) -> bool:
    """True if executing this primitive now would be a verified no-op given LIVE world state.

    Surgical, state-aware plan repair (NOT blanket skipping):
      sample_offload   -> the rover carries NOTHING (cargo empty)
      dock_with_base   -> already docked
      return_to_base   -> already at base
      move_to          -> already at the (resolved) target

    Dropping these is goal-safe: the fact each would (re)produce is already held, so no
    downstream precondition is invalidated.

    NB (Bug-4 fix): offload is a no-op ONLY when cargo is empty. A loaded-but-unstored cargo
    (plan collected a sample but omitted sample_store) must RUN — and fail honestly on the
    missing `sample_stored` precondition — rather than be silently pruned as a benign skip,
    which would hide a real coverage defect from the metrics.
    """
    agent = world.agents.get(agent_id)
    if agent is None:
        return False
    if prim_name == 'sample_offload':
        return not agent.get('cargo')
    if prim_name == 'dock_with_base':
        return bool(agent.get('docked', False))
    if prim_name == 'return_to_base':
        return _at(agent['position'], world.base_pos)
    if prim_name == 'move_to':
        tgt = world.resolve_target('move_to', args or {}, agent_id)
        return tgt is not None and _at(agent['position'], tgt)
    return False

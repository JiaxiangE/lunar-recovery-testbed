"""P3-0b — Construction L1 contract predicates (scenarios/Construction_L1_Contract_v1.md §S4/§S7).

Scene goal: install ≥ 8 of 12 PV panels + ≥ 1 end-to-end circuit connection + 0 wasted material.
The inj_const_005 (G-03 Scene-unsatisfiable) fallback degrades only the panel threshold to ≥ 5;
the independently required circuit remains conjunctive.  Waste is excluded from the degraded
contract because no current primitive provides a candidate-derived waste effect.
"""
from __future__ import annotations
from typing import Any
CONSTRUCTION_PRIMITIVES = ['move_to', 'sample_collect', 'sample_store', 'energy_check', 'communicate_status', 'communicate_relay', 'navigate_to_relay', 'return_to_base', 'dock_with_base', 'peer_communicate', 'headlight_toggle', 'low_light_navigation', 'cargo_pickup', 'cargo_drop', 'dig_regolith', 'place_foundation', 'install_bracket', 'panel_align_and_dock']
PANEL_TARGET = 8
PANEL_DEGRADED = 5

def panels_installed(world: Any) -> int:
    return int(getattr(world, 'installed_panels', 0))

def scene_goal_met(world: Any) -> bool:
    """§S4 hard success: ≥ 8 panels + a connected circuit + no wasted material."""
    return panels_installed(world) >= PANEL_TARGET and bool(getattr(world, 'circuit_connected', False)) and (int(getattr(world, 'wasted_material', 0)) == 0)

"""SensorSnapshot + PeerReport — Paper 3 §3.1 multi-modal sensor schema.

All four architectures (WAIT / FSM / BT / LLM) receive an identical
`SensorSnapshot` at each decision point. Architecture differences reflect
*decision-making mechanisms*, not information access (Paper 3 §3.10.4,
§7.1 Reproducibility).

Sprint 0.2 Task 1 scope:
  - Pydantic v2 field definitions with five physical groups
  - PeerReport sub-model for cross-agent context
  - Validation of required vs optional + default values
  - Round-trip JSON serialization
  - NOT in scope: population logic from simulator (Sprint 0.4 M0.E)

Field groups (used by complexity/group_checker.py):
  TERRAIN — terrain_slope, terrain_hardness, terrain_hardness_variance,
            terrain_type
  MOTION  — wheel_sinkage (per-wheel dict), body_tilt_deviation,
            traction_coefficient
  VISUAL  — visual_anomaly_detected, visual_anomaly_description,
            obstacle_detected, obstacle_type, obstacle_dimensions
  ENERGY  — battery_remaining_pct, power_consumption_rate,
            consumption_vs_expected
  COMM    — signal_strength_dbm, signal_trend, comm_state

Plus cross-agent and confidence annotations (not grouped — treated
separately by coordination metrics).
"""
from __future__ import annotations
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

class PeerReport(BaseModel):
    """Last-known state of another agent.

    During comm disruption, a PeerReport is stale evidence — the `timestamp`
    field tells the consumer how recent the information is. E6 adversarial
    template "peer_stale" exploits this by keeping timestamps fresh while
    contents are outdated (§5.13.2).
    """
    model_config = ConfigDict(frozen=False, extra='forbid')
    peer_agent_id: str
    last_update_timestamp: float
    last_known_position: Optional[tuple[float, float, float]] = None
    last_known_status: Literal['nominal', 'degraded', 'unreachable', 'unknown'] = 'unknown'
    task_progress_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    remaining_energy_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    note: str = ''

class SensorSnapshot(BaseModel):
    """Multi-modal sensor reading at a single decision point (Paper 3 §3.1).

    Contains five physical-sensor groups (TERRAIN / MOTION / VISUAL / ENERGY
    / COMM), plus peer context and information-confidence annotations. All
    four Paper 3 architectures receive this identical structure.

    Serialization: Pydantic v2 `model_dump_json()` for persistence.
    """
    model_config = ConfigDict(frozen=False, extra='forbid')
    timestamp: float
    agent_id: str
    terrain_slope: float = Field(..., description='Slope in degrees (0-90)', ge=0.0, le=90.0)
    terrain_hardness: float = Field(..., description='Effective bearing capacity in kPa', ge=0.0)
    terrain_hardness_variance: float = Field(default=0.0, description='Sample variance of recent hardness measurements', ge=0.0)
    terrain_type: Literal['regolith', 'rock', 'compacted', 'loose_regolith', 'ice', 'unknown'] = 'regolith'
    wheel_sinkage: Dict[str, float] = Field(default_factory=dict)
    body_tilt_deviation: float = 0.0
    traction_coefficient: float = Field(default=0.6, ge=0.0, le=2.0)
    visual_anomaly_detected: bool = False
    visual_anomaly_description: str = ''
    obstacle_detected: bool = False
    obstacle_type: str = ''
    obstacle_dimensions: Dict[str, float] = Field(default_factory=dict)
    battery_remaining_pct: float = Field(..., description='Battery state-of-charge percentage (0-100)', ge=0.0, le=100.0)
    power_consumption_rate: float = Field(default=0.0, ge=0.0)
    consumption_vs_expected: float = Field(default=1.0, ge=0.0)
    signal_strength_dbm: float = Field(default=-70.0, description='Signal strength in dBm (more negative = weaker)')
    signal_trend: Literal['improving', 'stable', 'declining', 'critical', 'unknown'] = 'stable'
    comm_state: Literal['A', 'B', 'C'] = Field(default='A', description='Three-state model: A=full-bandwidth, B=degraded+delayed, C=fully disconnected (§3.x of Paper 2/3)')
    peer_agent_reports: List[PeerReport] = Field(default_factory=list)
    current_cell_confidence: Literal['Verified', 'Inferred', 'Unknown'] = 'Unknown'
    ahead_cells_confidence: List[Literal['Verified', 'Inferred', 'Unknown']] = Field(default_factory=list)

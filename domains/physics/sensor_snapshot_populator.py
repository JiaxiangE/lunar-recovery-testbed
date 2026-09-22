"""Sensor snapshot populator."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from domains.schema_support.schema.sensor_snapshot import PeerReport
from domains.schema_support.schema.sensor_snapshot import SensorSnapshot

class TerrainContext(BaseModel):
    """Sim-side TERRAIN group state."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    terrain_slope: float = Field(..., ge=0.0, le=90.0)
    terrain_hardness: float = Field(..., ge=0.0)
    terrain_hardness_variance: float = Field(default=0.0, ge=0.0)
    terrain_type: Literal['regolith', 'rock', 'compacted', 'loose_regolith', 'ice', 'unknown'] = 'regolith'

class MotionContext(BaseModel):
    """Sim-side MOTION group state."""
    model_config = ConfigDict(extra='forbid')
    wheel_sinkage: dict[str, float] = Field(default_factory=dict)
    body_tilt_deviation: float = 0.0
    traction_coefficient: float = Field(default=0.6, ge=0.0, le=2.0)

class VisualContext(BaseModel):
    """Sim-side VISUAL group state."""
    model_config = ConfigDict(extra='forbid')
    visual_anomaly_detected: bool = False
    visual_anomaly_description: str = ''
    obstacle_detected: bool = False
    obstacle_type: str = ''
    obstacle_dimensions: dict[str, float] = Field(default_factory=dict)

class EnergyContext(BaseModel):
    """Sim-side ENERGY group state."""
    model_config = ConfigDict(extra='forbid')
    battery_remaining_pct: float = Field(..., ge=0.0, le=100.0)
    power_consumption_rate: float = Field(default=0.0, ge=0.0)
    consumption_vs_expected: float = Field(default=1.0, ge=0.0)

class CommContext(BaseModel):
    """Sim-side COMM group state."""
    model_config = ConfigDict(extra='forbid')
    signal_strength_dbm: float = -70.0
    signal_trend: Literal['improving', 'stable', 'declining', 'critical', 'unknown'] = 'stable'
    comm_state: Literal['A', 'B', 'C'] = 'A'

class ConfidenceContext(BaseModel):
    """Sim-side perception-confidence state.

    Carried alongside the 5 physical groups; not part of any group_checker
    classification (per ``domains/schema_support/schema/sensor_snapshot.py`` field-grouping
    documentation).
    """
    model_config = ConfigDict(extra='forbid')
    current_cell_confidence: Literal['Verified', 'Inferred', 'Unknown'] = 'Unknown'
    ahead_cells_confidence: list[Literal['Verified', 'Inferred', 'Unknown']] = Field(default_factory=list)

class PopulationContext(BaseModel):
    """Aggregated sim-side context consumed by ``SensorSnapshotPopulator``.

    Each per-group field is a separate Pydantic model, allowing sim-side
    adapters to populate slices independently and let the populator
    assemble the canonical ``SensorSnapshot``.
    """
    model_config = ConfigDict(extra='forbid')
    timestamp: float
    agent_id: str
    mission_phase_id: str = Field(..., description="Mission phase identifier from CSSkeleton (e.g. 'G3'). Carried for audit + diagnostic logs; not directly mapped to a SensorSnapshot field but used by downstream complexity_level evaluation + ConstraintEvaluator phase-aware checks.")
    terrain: TerrainContext
    motion: MotionContext
    visual: VisualContext
    energy: EnergyContext
    comm: CommContext
    confidence: ConfidenceContext
    peer_agent_reports: list[PeerReport] = Field(default_factory=list)

class SensorSnapshotPopulator:
    """Assemble a canonical ``SensorSnapshot`` from a ``PopulationContext``.

    Stateless: every call to ``populate()`` is a pure function of its input.
    M1.E (Disruption Injection) wraps this populator with injection layers
    that mutate the source ``PopulationContext`` before populator runs;
    populator itself stays unaware of injection mechanics — separation of
    concerns.

    Usage:
        >>> ctx = PopulationContext(
        ...     timestamp=0.0,
        ...     agent_id="rover_1",
        ...     mission_phase_id="G1",
        ...     terrain=TerrainContext(terrain_slope=5.0, terrain_hardness=80.0),
        ...     motion=MotionContext(),
        ...     visual=VisualContext(),
        ...     energy=EnergyContext(battery_remaining_pct=95.0),
        ...     comm=CommContext(),
        ...     confidence=ConfidenceContext(),
        ... )
        >>> populator = SensorSnapshotPopulator()
        >>> snapshot = populator.populate(ctx)
        >>> snapshot.battery_remaining_pct
        95.0
    """

    def populate(self, ctx: PopulationContext) -> SensorSnapshot:
        """Transform a PopulationContext into a canonical SensorSnapshot.

        Args:
            ctx: simulator-side populated context.

        Returns:
            A new ``SensorSnapshot`` instance with all 5-group + peer +
            confidence + metadata fields populated.
        """
        return SensorSnapshot(timestamp=ctx.timestamp, agent_id=ctx.agent_id, terrain_slope=ctx.terrain.terrain_slope, terrain_hardness=ctx.terrain.terrain_hardness, terrain_hardness_variance=ctx.terrain.terrain_hardness_variance, terrain_type=ctx.terrain.terrain_type, wheel_sinkage=dict(ctx.motion.wheel_sinkage), body_tilt_deviation=ctx.motion.body_tilt_deviation, traction_coefficient=ctx.motion.traction_coefficient, visual_anomaly_detected=ctx.visual.visual_anomaly_detected, visual_anomaly_description=ctx.visual.visual_anomaly_description, obstacle_detected=ctx.visual.obstacle_detected, obstacle_type=ctx.visual.obstacle_type, obstacle_dimensions=dict(ctx.visual.obstacle_dimensions), battery_remaining_pct=ctx.energy.battery_remaining_pct, power_consumption_rate=ctx.energy.power_consumption_rate, consumption_vs_expected=ctx.energy.consumption_vs_expected, signal_strength_dbm=ctx.comm.signal_strength_dbm, signal_trend=ctx.comm.signal_trend, comm_state=ctx.comm.comm_state, peer_agent_reports=list(ctx.peer_agent_reports), current_cell_confidence=ctx.confidence.current_cell_confidence, ahead_cells_confidence=list(ctx.confidence.ahead_cells_confidence))

    def populate_nominal(self, timestamp: float, agent_id: str, mission_phase_id: str, battery_remaining_pct: float=95.0) -> SensorSnapshot:
        """Convenience: populate a nominal-state SensorSnapshot.

        Used by tests + by M1.G dry-run defaults. NOT for production use
        without explicit per-field overrides; production simulator MUST
        construct a full ``PopulationContext`` from real sim state.
        """
        ctx = PopulationContext(timestamp=timestamp, agent_id=agent_id, mission_phase_id=mission_phase_id, terrain=TerrainContext(terrain_slope=2.0, terrain_hardness=30.0), motion=MotionContext(), visual=VisualContext(), energy=EnergyContext(battery_remaining_pct=battery_remaining_pct), comm=CommContext(), confidence=ConfidenceContext())
        return self.populate(ctx)

    def populate_multi_agent_rendezvous(self, timestamp: float, agent_id: str, mission_phase_id: str, scenario: str='CS3', peer_agent_id: str='rover_2', battery_remaining_pct: float=95.0) -> SensorSnapshot:
        """Round 50.5+ Option β condition 1 NEW: populate multi-agent rendezvous
        SensorSnapshot for M2.H Merge Op telemetry capture (sub-plan 34 §零 #12
        codified spec).

        Constructs a snapshot containing peer_agent_reports describing a peer
        agent at adjacent cell with overlapping task primitive scope (per scenario
        — CS1 panel cleaning coordination, CS2 sample handover, CS3 drilling
        shared site per round 28 expert §3.4 + Plan 36 v1.4 §6.4.3). Peer chain
        encoded in PeerReport.note for downstream MergeOp.generate_candidates
        consumption.

        Per AgentRendezvousEvent.agent_ids min_length=2 invariant: the snapshot
        will produce 1 self_agent_id + 1 peer_agent_id = 2 agents at rendezvous,
        meeting min_length=2 requirement.

        Args:
            timestamp: snapshot timestamp.
            agent_id: this agent's id (self).
            mission_phase_id: mission phase (e.g. G3_late mid-mission).
            scenario: CS1/CS2/CS3 — determines peer chain encoding for MergeOp.
            peer_agent_id: peer's id (must differ from agent_id; default "rover_2").
            battery_remaining_pct: nominal battery for this agent (default 95%).

        Returns:
            SensorSnapshot with peer_agent_reports[0] containing peer's
            last-known position + status + scenario-specific peer chain note.
        """
        if peer_agent_id == agent_id:
            raise ValueError(f'populate_multi_agent_rendezvous: peer_agent_id ({peer_agent_id}) must differ from self agent_id ({agent_id}); AgentRendezvousEvent requires min_length=2 distinct agents per round 28 expert §3.4')
        peer_chain_descriptions = {'CS1': 'Peer at adjacent panel site (pos=110,52,-2) recently completed [p_unroll_blanket, p_deploy_panel] (panel deployment); next chain: [p_trigger_EDS_cycle] (thermal recovery on shared panel cluster). Capability overlap: panel ops; rendezvous opportunity for coordinated thermal recovery on shared cluster.', 'CS2': 'Peer at adjacent tether anchor (pos=105,48,-15) recently completed [p_rappel_over_rim, p_stop_at_target_altitude] (descent); next chain: [p_scoop_sample, p_send_magneto_inductive_packet] (sample handover via comm relay). Capability overlap: in-cave science + comm relay; rendezvous opportunity for sample handover coordination.', 'CS3': 'Peer at adjacent drilling site (pos=108,55,-1) recently completed [p_localize_pose, p_position_trident] (drill setup); next chain: [p_rotary_percussive_drill, p_scoop_sample] (drilling + sampling at adjacent site). Capability overlap: drilling + sampling; rendezvous opportunity for shared drilling site coordination.'}
        scenario_key = scenario if scenario in peer_chain_descriptions else 'CS3'
        peer_note = peer_chain_descriptions[scenario_key]
        peer_position = (110.0, 52.0, -2.0) if scenario_key == 'CS1' else (105.0, 48.0, -15.0) if scenario_key == 'CS2' else (108.0, 55.0, -1.0)
        peer_report = PeerReport(peer_agent_id=peer_agent_id, last_update_timestamp=timestamp - 30.0, last_known_position=peer_position, last_known_status='nominal', task_progress_pct=50.0, remaining_energy_pct=88.0, note=peer_note)
        ctx = PopulationContext(timestamp=timestamp, agent_id=agent_id, mission_phase_id=mission_phase_id, terrain=TerrainContext(terrain_slope=2.0, terrain_hardness=30.0), motion=MotionContext(), visual=VisualContext(), energy=EnergyContext(battery_remaining_pct=battery_remaining_pct), comm=CommContext(), confidence=ConfidenceContext(), peer_agent_reports=[peer_report])
        return self.populate(ctx)

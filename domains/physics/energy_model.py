"""
Energy consumption model for lunar surface agents.

MVP: Linear model — energy_cost = distance × unit_cost + task_base_cost.
Slope adds a multiplier to travel cost.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict
from typing import Optional
_DEFAULT_TRAVEL_COST_PER_M = 0.5
_DEFAULT_SLOPE_FACTOR = 0.02
_DEFAULT_TASK_BASE_COST = 5.0
_OP_TYPE_ENERGY: Dict[str, float] = {'ExploreCell': 3.0, 'DeployRelay': 8.0, 'Approach': 2.0, 'DeployArm': 10.0, 'Extract': 15.0, 'Store': 2.0, 'ActivateSensor': 5.0, 'CollectData': 8.0, 'UploadData': 4.0, 'Scan': 3.0}

@dataclass
class EnergyConfig:
    """Tunable energy model parameters."""
    travel_cost_per_m: float = _DEFAULT_TRAVEL_COST_PER_M
    slope_factor: float = _DEFAULT_SLOPE_FACTOR
    task_base_cost: float = _DEFAULT_TASK_BASE_COST
    low_energy_threshold: float = 10.0

class EnergyModel:
    """
    Deterministic energy consumption model.

    Given agent movement and task execution, computes total energy cost.
    """

    def __init__(self, config: Optional[EnergyConfig]=None):
        self.cfg = config or EnergyConfig()

    def compute_travel_cost(self, distance: float, slope: float=0.0) -> float:
        """
        Energy cost to travel a given distance on a given slope.

        Args:
            distance: Travel distance in meters.
            slope: Average slope in degrees along the path.

        Returns:
            Energy cost in Wh.
        """
        base = distance * self.cfg.travel_cost_per_m
        slope_penalty = distance * abs(slope) * self.cfg.slope_factor
        return base + slope_penalty

    def compute_task_cost(self, op_type: str) -> float:
        """
        Energy cost to execute a task of the given operation type.

        Args:
            op_type: Task operation type string.

        Returns:
            Energy cost in Wh.
        """
        return _OP_TYPE_ENERGY.get(op_type, self.cfg.task_base_cost)

    def check_energy_feasible(self, agent_energy: float, cost: float) -> bool:
        """Check if the agent has enough energy for the operation."""
        return agent_energy >= cost

    def is_low_energy(self, agent_energy: float) -> bool:
        """Check if energy is below the forced-return threshold."""
        return agent_energy < self.cfg.low_energy_threshold

    @staticmethod
    def euclidean_distance(pos_a: tuple[float, float, float], pos_b: tuple[float, float, float]) -> float:
        """3D Euclidean distance between two positions."""
        return math.sqrt((pos_a[0] - pos_b[0]) ** 2 + (pos_a[1] - pos_b[1]) ** 2 + (pos_a[2] - pos_b[2]) ** 2)

"""Settings."""
from __future__ import annotations
from controller.calibration.selector_config_v2 import CORRECTED_P_FIX
from controller.calibration.selector_config_v2 import CORRECTED_UTILITY_WEIGHTS
from controller.selector import ScopeSelector
from controller.selector import UtilityWeights

def frozen_weights() -> UtilityWeights:
    """Stage-2 frozen constant; no per-process grid rerun."""
    return CORRECTED_UTILITY_WEIGHTS

def frozen_p_fix_params():
    """Stage-1 empirical P_fix parameters for corrected selectors."""
    return CORRECTED_P_FIX

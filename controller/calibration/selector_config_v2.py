"""Versioned selector inputs frozen at the Stage-1/Stage-2 boundary."""
from controller.selector import PFixParams
from controller.selector import UtilityWeights
STAGE1_FROZEN_P_FIX = PFixParams(lambda_=2.295, p_lucky=0.26)
STAGE1_FIXED_UTILITY_WEIGHTS = UtilityWeights(tcr=5.0, mks=0.05, cost=0.01, risk=1.0)
CORRECTED_P_FIX = STAGE1_FROZEN_P_FIX
CORRECTED_UTILITY_WEIGHTS = STAGE1_FIXED_UTILITY_WEIGHTS
SELECTOR_CONFIG_VERSION = 'corrected_calibrated_v2'

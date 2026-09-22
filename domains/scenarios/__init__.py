"""Scenario registry and prompt-safe scenario contracts."""
from domains.scenarios.registry import get_scenario_spec
from domains.scenarios.registry import scenario_ids
from domains.scenarios.spec import ArgumentSchema
from domains.scenarios.spec import CandidateValidation
from domains.scenarios.spec import EvaluationMetadata
from domains.scenarios.spec import PromptContext
from domains.scenarios.spec import PromptIsolationError
from domains.scenarios.spec import RecoveryContract
from domains.scenarios.spec import ScenarioSpec
from domains.scenarios.spec import SymbolicEffect

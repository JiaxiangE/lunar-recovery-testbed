"""Finite-domain Construction cardinality verification.

This module replaces the historical ``panels_installed_ge_5`` surrogate with a
real Boolean assignment over the frozen ``panel_1`` .. ``panel_12`` domain.  Its
input is deliberately a collection of authoritative *candidate effects* plus
declared observable-initial facts; no world object or scalar installed-panel
counter is accepted by the API.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any
from typing import FrozenSet
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Tuple
from domains.actions.spec import Fact
from domains.scenarios.construction import ConstructionCardinalityContract
from domains.scenarios.construction import get_construction_cardinality_contract
CARDINALITY_FAIL = 'CARDINALITY_FAIL'
BAD_ARGUMENTS = 'BAD_ARGUMENTS'
GOAL_INCOMPLETE = 'GOAL_INCOMPLETE'
SOLVER_ERROR = 'SOLVER_ERROR'
_PANEL_PREDICATE = 'panel_installed'
_SURROGATE_PREDICATES = frozenset({'panels_installed_ge_5', 'panels_installed_ge_8'})
_CANDIDATE_EFFECT_SOURCE = 'authoritative_candidate_effects'
_OBSERVED_SOURCE = 'observed_initial'

@dataclass(frozen=True)
class CardinalitySolverInput:
    """The complete answer-label-free assignment sent to Z3."""
    contract_id: str
    threshold: int
    panel_domain: Tuple[str, ...]
    installed_panel_ids: Tuple[str, ...]
    required_observed_initial_facts: Tuple[str, ...]
    observed_initial_facts: Tuple[str, ...]
    installed_set_source: str = _CANDIDATE_EFFECT_SOURCE

@dataclass(frozen=True)
class CardinalityCertificate:
    """Auditable finite-cardinality certificate retained in the gate trace."""
    contract_id: str
    threshold: int
    panel_domain: Tuple[str, ...]
    unique_panel_ids: Tuple[str, ...]
    unique_panel_count: int
    installed_set_source: str
    required_observed_initial_facts: Tuple[str, ...]
    goal_discharge: Mapping[str, str]
    solver_input: CardinalitySolverInput
    solver_status: str
    constraint_summary: str
    encode_latency_s: float
    check_latency_s: float
    source_references: Tuple[str, ...]
    solver_version: Optional[str] = None

    @property
    def total_latency_s(self) -> float:
        return self.encode_latency_s + self.check_latency_s

@dataclass(frozen=True)
class CardinalityCheckResult:
    """Full contract verdict with cardinality and observable conjuncts split."""
    accepted: bool
    cardinality_holds: bool
    observed_conjuncts_hold: bool
    reason_code: str = ''
    detail: str = ''
    certificate: Optional[CardinalityCertificate] = None

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return (self.reason_code,) if self.reason_code else ()

def _normalize_fact_set(values: Iterable[Fact], label: str) -> FrozenSet[Fact]:
    try:
        frozen = frozenset(values)
    except Exception as exc:
        raise ValueError(f'{label} must be an iterable of Fact: {exc}') from exc
    invalid = [value for value in frozen if not isinstance(value, Fact)]
    if invalid:
        raise ValueError(f'{label} contains non-Fact value(s): {invalid!r}')
    return frozen

def _constraint_summary(contract: ConstructionCardinalityContract) -> str:
    panel_terms = ', '.join((f'panel_installed({panel_id})' for panel_id in contract.panel_domain))
    terms = [f'AtLeast({contract.threshold}, {panel_terms})']
    terms.extend(contract.required_observed_initial_facts)
    return ' AND '.join(terms)

def _discharge_map(contract: ConstructionCardinalityContract, cardinality_holds: bool, observed_names: FrozenSet[str]) -> dict[str, str]:
    aggregate = f'AtLeast({contract.threshold}, panel_installed({contract.panel_domain[0]}..{contract.panel_domain[-1]}))'
    discharge = {aggregate: 'candidate_entailed' if cardinality_holds else 'not_entailed'}
    for required in contract.required_observed_initial_facts:
        discharge[required] = _OBSERVED_SOURCE if required in observed_names else 'missing_observed_initial'
    return discharge

def _make_certificate(contract: ConstructionCardinalityContract, unique_panel_ids: Tuple[str, ...], observed_names: FrozenSet[str], *, solver_status: str, encode_latency_s: float, check_latency_s: float, solver_version: Optional[str]=None) -> CardinalityCertificate:
    cardinality_holds = len(unique_panel_ids) >= contract.threshold
    solver_input = CardinalitySolverInput(contract_id=contract.contract_id, threshold=contract.threshold, panel_domain=contract.panel_domain, installed_panel_ids=unique_panel_ids, required_observed_initial_facts=contract.required_observed_initial_facts, observed_initial_facts=tuple((name for name in contract.required_observed_initial_facts if name in observed_names)))
    return CardinalityCertificate(contract_id=contract.contract_id, threshold=contract.threshold, panel_domain=contract.panel_domain, unique_panel_ids=unique_panel_ids, unique_panel_count=len(unique_panel_ids), installed_set_source=_CANDIDATE_EFFECT_SOURCE, required_observed_initial_facts=contract.required_observed_initial_facts, goal_discharge=_discharge_map(contract, cardinality_holds, observed_names), solver_input=solver_input, solver_status=solver_status, constraint_summary=_constraint_summary(contract), encode_latency_s=float(encode_latency_s), check_latency_s=float(check_latency_s), source_references=contract.source_references, solver_version=solver_version)

def check_construction_cardinality(contract_id: str, candidate_effects: Iterable[Fact], observed_initial_facts: Iterable[Fact], *, z3_module: Any=None, solver_factory: Any=None, clock: Any=time.perf_counter) -> CardinalityCheckResult:
    """Check one frozen Construction contract with a real ``AtLeast`` encoding.

    ``candidate_effects`` must be the authoritative effects resolved from the
    candidate primitive sequence.  The function intentionally has no ``world``
    parameter, making the scalar ``ConstructionWorld.installed_panels`` unable
    to act as a distinctness certificate.
    """
    contract = get_construction_cardinality_contract(contract_id)
    if contract is None:
        return CardinalityCheckResult(False, False, False, BAD_ARGUMENTS, f'unknown Construction cardinality contract: {contract_id!r}')
    try:
        effects = _normalize_fact_set(candidate_effects, 'candidate_effects')
        initial = _normalize_fact_set(observed_initial_facts, 'observed_initial_facts')
    except ValueError as exc:
        return CardinalityCheckResult(False, False, False, BAD_ARGUMENTS, str(exc))
    surrogate = sorted((effect.predicate for effect in effects if effect.predicate in _SURROGATE_PREDICATES))
    panel_effects = [effect for effect in effects if effect.predicate == _PANEL_PREDICATE]
    malformed = [effect for effect in panel_effects if len(effect.args) != 1]
    domain = frozenset(contract.panel_domain)
    unknown = sorted((str(effect.args[0]) for effect in panel_effects if len(effect.args) == 1 and effect.args[0] not in domain))
    unique_panel_ids = tuple((panel_id for panel_id in contract.panel_domain if any((effect.args == (panel_id,) for effect in panel_effects))))
    observed_names = frozenset((effect.predicate for effect in initial if not effect.args))
    if surrogate or malformed or unknown:
        details = []
        if surrogate:
            details.append(f'surrogate effect(s) forbidden: {surrogate}')
        if malformed:
            details.append('panel_installed effects require exactly one canonical panel_id')
        if unknown:
            details.append(f'out-of-domain panel ID(s): {unknown}')
        certificate = _make_certificate(contract, unique_panel_ids, observed_names, solver_status='not_run', encode_latency_s=0.0, check_latency_s=0.0)
        return CardinalityCheckResult(False, False, all((required in observed_names for required in contract.required_observed_initial_facts)), BAD_ARGUMENTS, '; '.join(details), certificate)
    cardinality_holds = len(unique_panel_ids) >= contract.threshold
    observed_holds = all((required in observed_names for required in contract.required_observed_initial_facts))
    encode_started = clock()
    check_latency_s = 0.0
    try:
        module = z3_module
        if module is None:
            import z3 as module
        panel_bools = {panel_id: module.Bool(f'{contract.contract_id}__{panel_id}__installed') for panel_id in contract.panel_domain}
        observed_bools = {name: module.Bool(f'{contract.contract_id}__{name}') for name in contract.required_observed_initial_facts}
        solver = solver_factory() if solver_factory is not None else module.Solver()
        for panel_id, variable in panel_bools.items():
            solver.add(variable == module.BoolVal(panel_id in unique_panel_ids))
        for name, variable in observed_bools.items():
            solver.add(variable == module.BoolVal(name in observed_names))
        cardinality_constraint = module.AtLeast(*tuple(panel_bools.values()) + (contract.threshold,))
        solver.add(module.And(cardinality_constraint, *observed_bools.values()))
        encode_latency_s = clock() - encode_started
        check_started = clock()
        raw_status = solver.check()
        check_latency_s = clock() - check_started
        solver_status = str(raw_status)
        if solver_status not in {'sat', 'unsat'}:
            raise RuntimeError(f'cardinality solver returned {solver_status!r}')
        expected_sat = cardinality_holds and observed_holds
        if (solver_status == 'sat') != expected_sat:
            raise RuntimeError('cardinality solver result disagrees with the frozen finite assignment')
        version_getter = getattr(module, 'get_version_string', None)
        solver_version = str(version_getter()) if callable(version_getter) else None
    except Exception as exc:
        encode_latency_s = max(0.0, clock() - encode_started)
        certificate = _make_certificate(contract, unique_panel_ids, observed_names, solver_status='error', encode_latency_s=encode_latency_s, check_latency_s=check_latency_s)
        return CardinalityCheckResult(False, cardinality_holds, observed_holds, SOLVER_ERROR, f'Z3 cardinality check failed: {type(exc).__name__}: {exc}', certificate)
    certificate = _make_certificate(contract, unique_panel_ids, observed_names, solver_status=solver_status, encode_latency_s=encode_latency_s, check_latency_s=check_latency_s, solver_version=solver_version)
    if not cardinality_holds:
        return CardinalityCheckResult(False, False, observed_holds, CARDINALITY_FAIL, f'{len(unique_panel_ids)} distinct panels is below threshold {contract.threshold}', certificate)
    if not observed_holds:
        missing = sorted(set(contract.required_observed_initial_facts) - set(observed_names))
        return CardinalityCheckResult(False, True, False, GOAL_INCOMPLETE, f'missing observed-initial contract conjunct(s): {missing}', certificate)
    return CardinalityCheckResult(True, True, True, certificate=certificate)

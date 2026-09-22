"""Z3 entailment for positive-atomic contracts. The solver checks children AND NOT(parent); encoding errors and unknown results are reported to the caller."""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional
from typing import Sequence
from domains.predicates import Conjunction
from domains.predicates import parse_predicate

class Z3BackendError(RuntimeError):
    """The requested entailment certificate could not be produced."""

@dataclass(frozen=True)
class Z3EntailmentResult:
    """Auditable result of one Contract-1 solver call."""
    status: str
    entailment_holds: bool
    fragment_size_atoms: int
    encode_latency_s: float
    check_latency_s: float
    solver_version: Optional[str]

    @property
    def total_latency_s(self) -> float:
        return self.encode_latency_s + self.check_latency_s

def build_conjunction(children_posts: Sequence[str]) -> Conjunction:
    """Combine child postconditions into one de-duplicated conjunction."""
    seen = set()
    atomics = []
    for postcondition in children_posts:
        for atom in parse_predicate(postcondition).atomics:
            identity = (atom.key(), atom.negated)
            if identity not in seen:
                seen.add(identity)
                atomics.append(atom)
    return Conjunction(tuple(atomics))

def encode_entailment_solver(children: Conjunction, parent: Conjunction, *, z3_module: Any=None, solver_factory: Any=None):
    """Encode children AND NOT(parent), allocating one Boolean per atom identity. An unsatisfiable result establishes entailment. The optional solver arguments support error-handling tests."""
    if not isinstance(children, Conjunction) or not isinstance(parent, Conjunction):
        raise Z3BackendError('children and parent must be Conjunction instances')
    try:
        if z3_module is None:
            import z3 as z3_module
        bools: Dict[Any, Any] = {}

        def boolean(atom):
            identity = atom.key()
            if identity not in bools:
                bools[identity] = z3_module.Bool(str(identity))
            return bools[identity]
        solver = solver_factory() if solver_factory is not None else z3_module.Solver()
        for atom in children.atomics:
            solver.add(z3_module.Not(boolean(atom)) if atom.negated else boolean(atom))
        parent_literals = [z3_module.Not(boolean(atom)) if atom.negated else boolean(atom) for atom in parent.atomics]
        solver.add(z3_module.Not(z3_module.And(parent_literals)) if parent_literals else z3_module.BoolVal(False))
        return (solver, len(bools))
    except Z3BackendError:
        raise
    except Exception as exc:
        raise Z3BackendError(f'Z3 Contract-1 encoding failed: {type(exc).__name__}: {exc}') from exc

def check_entailment(children: Conjunction, parent: Conjunction, *, z3_module: Any=None, solver_factory: Any=None, clock: Any=time.perf_counter) -> Z3EntailmentResult:
    """Encode and solve one entailment query without any fallback path."""
    try:
        module = z3_module
        if module is None:
            import z3 as module
        encode_started = clock()
        solver, atom_count = encode_entailment_solver(children, parent, z3_module=module, solver_factory=solver_factory)
        encode_latency = clock() - encode_started
        check_started = clock()
        raw_status = solver.check()
        check_latency = clock() - check_started
        status = str(raw_status)
        if status not in {'sat', 'unsat'}:
            raise Z3BackendError(f'Z3 Contract-1 check returned {status!r}')
        version_getter = getattr(module, 'get_version_string', None)
        version = str(version_getter()) if callable(version_getter) else None
        return Z3EntailmentResult(status=status, entailment_holds=status == 'unsat', fragment_size_atoms=atom_count, encode_latency_s=float(encode_latency), check_latency_s=float(check_latency), solver_version=version)
    except Z3BackendError:
        raise
    except Exception as exc:
        raise Z3BackendError(f'Z3 Contract-1 check failed: {type(exc).__name__}: {exc}') from exc

"""Atomic-conjunction predicates + syntactic entailment (Build Spec §4.7).

Design (D-042 A2-3, choice A): a predicate is a conjunction of atomic predicates
`name(arg1, arg2, ...)`. Two uses:
  - **runtime** `evaluate(state)`: is each atomic true in the abstract state?
  - **contract checking** `entails(p1, p2)`: does p1 (a stronger conjunction) entail p2?
    Syntactic: every atomic of p2 must appear in p1 (matching name + args). This is sound
    for atomic conjunctions (Construction's quantifiers/inequalities trigger the Z3 review).

Serialization: predicates are carried as strings on schema Nodes (`precondition` /
`postcondition`) and parsed with `parse_predicate`. Grammar:
    conj   := atomic (SEP atomic)*          SEP ∈ {"&", "∧", " and "}
    atomic := NAME "(" term ("," term)* ")" | NAME
    ""/"TRUE"/"true" -> empty conjunction (vacuously true, entailed by anything)
    "FALSE"/"false"  -> unsatisfiable sentinel
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any
from typing import List
from typing import Mapping
from typing import Tuple
_FALSE_NAME = '__FALSE__'

@dataclass(frozen=True)
class AtomicPredicate:
    """One atomic predicate, e.g. AtomicPredicate('agent_at', ('rover_1', 'base')).
    `negated=True` represents ~atom / not atom."""
    name: str
    args: Tuple[str, ...] = ()
    negated: bool = False

    def key(self) -> Tuple[str, Tuple[str, ...]]:
        return (self.name, self.args)

    def evaluate(self, state: Mapping[str, Any]) -> bool:
        """True iff this atomic holds in `state` (with negation applied).

        `state` may provide either:
          - `state["facts"]`: an iterable of true atomics (AtomicPredicate, "name(a,b)"
            strings, or (name, args) tuples), OR
          - `state["predicates"]`: a mapping name -> callable(args, state) -> bool.
        """
        if self.name == _FALSE_NAME:
            return False
        preds = state.get('predicates') if isinstance(state, Mapping) else None
        if preds and self.name in preds:
            base = bool(preds[self.name](self.args, state))
        else:
            facts = state.get('facts', ()) if isinstance(state, Mapping) else ()
            base = self.key() in _normalize_facts(facts)
        return not base if self.negated else base

    def __str__(self) -> str:
        body = self.name if not self.args else f"{self.name}({', '.join(self.args)})"
        return '~' + body if self.negated else body

@dataclass(frozen=True)
class Conjunction:
    """A conjunction (logical AND) of atomic predicates."""
    atomics: Tuple[AtomicPredicate, ...] = ()

    def keys(self) -> set:
        return {a.key() for a in self.atomics}

    def is_false(self) -> bool:
        return any((a.name == _FALSE_NAME for a in self.atomics))

    def evaluate(self, state: Mapping[str, Any]) -> bool:
        if self.is_false():
            return False
        return all((a.evaluate(state) for a in self.atomics))

    def __str__(self) -> str:
        if self.is_false():
            return 'FALSE'
        return 'TRUE' if not self.atomics else ' ∧ '.join((str(a) for a in self.atomics))
TRUE = Conjunction(())
FALSE = Conjunction((AtomicPredicate(_FALSE_NAME, ()),))

def entails(p1: Conjunction, p2: Conjunction) -> bool:
    """Syntactic conjunction entailment (closed-world-ish): p1 ⊨ p2 iff every POSITIVE atom
    of p2 is asserted by p1, and no NEGATED atom of p2 is asserted by p1.

    - Anything entails TRUE (empty p2).
    - FALSE entails anything (ex falso).
    """
    if p1.is_false():
        return True
    if p2.is_false():
        return False
    p1_pos = {a.key() for a in p1.atomics if not a.negated}
    p2_pos = {a.key() for a in p2.atomics if not a.negated}
    p2_neg = {a.key() for a in p2.atomics if a.negated}
    return p2_pos.issubset(p1_pos) and p1_pos.isdisjoint(p2_neg)

def _normalize_facts(facts: Any) -> set:
    out = set()
    for f in facts:
        if isinstance(f, AtomicPredicate):
            out.add(f.key())
        elif isinstance(f, tuple) and len(f) == 2:
            name, args = f
            out.add((name, tuple(args)))
        elif isinstance(f, str):
            ap = _parse_atomic(f)
            if ap is not None:
                out.add(ap.key())
    return out
_ATOMIC_RE = re.compile('^\\s*([A-Za-z_]\\w*)\\s*(?:\\((.*)\\))?\\s*$')

def _parse_atomic(token: str) -> AtomicPredicate | None:
    t = token.strip()
    negated = False
    if t.startswith('~'):
        negated, t = (True, t[1:].strip())
    elif t.lower().startswith('not '):
        negated, t = (True, t[4:].strip())
    m = _ATOMIC_RE.match(t)
    if not m:
        return None
    name = m.group(1)
    arg_str = m.group(2)
    if arg_str is None or arg_str.strip() == '':
        return AtomicPredicate(name, (), negated)
    args = tuple((a.strip() for a in arg_str.split(',') if a.strip() != ''))
    return AtomicPredicate(name, args, negated)

def parse_predicate(s: str) -> Conjunction:
    """Parse a predicate string into a Conjunction (see module grammar)."""
    if s is None:
        return TRUE
    text = s.strip()
    low = text.lower()
    if text == '' or low == 'true':
        return TRUE
    if low == 'false':
        return FALSE
    parts = re.split('\\s*(?:&|∧|\\band\\b)\\s*', text)
    atomics: List[AtomicPredicate] = []
    for p in parts:
        if p.strip() == '':
            continue
        ap = _parse_atomic(p)
        if ap is None:
            raise ValueError(f'cannot parse predicate atom: {p!r} (in {s!r})')
        atomics.append(ap)
    return Conjunction(tuple(atomics))

def sanitize_predicate(s: str) -> str:
    """Best-effort cleanup of an LLM-produced predicate string: keep only atoms this DSL can
    parse (positive or negated `name(args)`), DROP the rest (e.g. inequalities / quantifiers
    the v0 grammar doesn't support). Returns "" if nothing parses. Use on LLM output before
    constructing schema nodes so an exotic predicate degrades gracefully instead of crashing.
    """
    if not s or not s.strip():
        return ''
    low = s.strip().lower()
    if low in ('true', 'false'):
        return s.strip()
    kept: List[str] = []
    for part in re.split('\\s*(?:&|∧|\\band\\b)\\s*', s):
        if part.strip() and _parse_atomic(part) is not None:
            kept.append(part.strip())
    return ' & '.join(kept)

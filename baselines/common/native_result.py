"""Native result."""
from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
NATIVE_SOLVED = 'NATIVE_SOLVED'
NATIVE_UNSOLVABLE = 'NATIVE_UNSOLVABLE'
NATIVE_INVALID_OUTPUT = 'NATIVE_INVALID_OUTPUT'
NATIVE_NO_METHOD = 'NATIVE_NO_METHOD'
NATIVE_PREFIX_VIOLATION = 'NATIVE_PREFIX_VIOLATION'
NATIVE_EXHAUSTED = 'NATIVE_EXHAUSTED'
DEPENDENCY_REQUIRED = 'DEPENDENCY_REQUIRED'
_NATIVE_SUCCESS_STATUSES = frozenset({NATIVE_SOLVED})

@dataclass(frozen=True)
class NativePlanResult:
    """What one baseline's own algorithm produced, with nothing of ours added."""
    method_id: str
    implementation_id: str
    native_status: str
    canonical_plan: Tuple[Dict[str, Any], ...] = ()
    native_artifact: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def native_success(self) -> bool:
        return self.native_status in _NATIVE_SUCCESS_STATUSES

    @property
    def dependency_required(self) -> bool:
        return self.native_status == DEPENDENCY_REQUIRED

    def to_dict(self) -> Dict[str, Any]:
        return {'method_id': self.method_id, 'implementation_id': self.implementation_id, 'native_status': self.native_status, 'native_success': self.native_success, 'canonical_plan': [dict(step) for step in self.canonical_plan], 'plan_length': len(self.canonical_plan), 'native_artifact': dict(self.native_artifact), 'diagnostics': dict(self.diagnostics)}

"""Strict profile."""
from dataclasses import replace
from types import MappingProxyType
from domains.actions.psr.tokens import PSR_PRIMITIVES
LEGACY_PSR_PROFILE = 'psr_legacy'
STRICT_PSR_PROFILE = 'psr_location_stored_v1'
STRICT_NAMED_SEMANTICS = 'psr_named_state_v3'
COLLECTION_TOLERANCE_M = 0.5
STRICT_OFFLOAD_STATUSES = frozenset({'stored'})
STRICT_TOKEN_SPECS = MappingProxyType({**PSR_PRIMITIVES, 'sample_collect': replace(PSR_PRIMITIVES['sample_collect'], requires=frozenset())})

def require_psr_profile(world, semantics_version):
    expected = STRICT_PSR_PROFILE if semantics_version == STRICT_NAMED_SEMANTICS else LEGACY_PSR_PROFILE
    actual = getattr(world, 'semantics_profile', LEGACY_PSR_PROFILE)
    if actual != expected:
        raise ValueError(f'PSR_PROFILE_MISMATCH: {semantics_version} requires {expected}, got {actual}')

def runtime_primitive(name):
    from domains.actions.psr.primitives import PSR_PRIMITIVES as runtime
    return replace(runtime[name], spec=STRICT_TOKEN_SPECS[name])

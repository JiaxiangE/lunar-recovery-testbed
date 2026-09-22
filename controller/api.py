"""Configurable recovery through the shared generation, gate and execution path."""
from controller.problem import RecoveryCase
from controller.resource_recovery import execute_integrated
from controller.recovery import OURS, FLAT, HTN, BT, FD
METHODS = {'ours': OURS, 'flat': FLAT, 'htn': HTN, 'bt': BT, 'llmp': FD}

def recover(case: RecoveryCase, output, *, method='ours', session_factory=None, generator='fixture', public_preflight=False, **options):
    if method not in METHODS:
        raise ValueError('unknown method: ' + method)
    if generator not in {'fixture', 'session'}:
        raise ValueError('generator must be fixture or session')
    if generator == 'session' and session_factory is None:
        raise ValueError('session generator requires an explicit factory')
    if generator == 'fixture' and session_factory is not None:
        raise ValueError('choose session to use a supplied generator')
    return execute_integrated(case, METHODS[method], output, session_factory=session_factory, public_preflight=public_preflight, **options)

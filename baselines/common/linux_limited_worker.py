"""Linux cgroup-v2 counterpart to the Windows Job Object worker.

Deployment delegates PAPER4_CGROUP_ROOT to this user once. Each invocation uses
an empty child cgroup; memory and wall limits cover descendants. No unbounded
fallback, system swap change, shell-built command, or privileged target process.
"""
import math
import multiprocessing as mp
import os
from pathlib import Path
import threading
import time
import uuid

def _fields(path):
    return {k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())}

def run_linux_limited(module, function, args=(), kwargs=None, *, timeout_s=30.0, memory_mb=2048):
    from baselines.common.limited_worker import LimitedResult
    from baselines.common.limited_worker import _worker
    if not math.isfinite(timeout_s) or timeout_s <= 0 or type(memory_mb) is not int or (memory_mb <= 0):
        raise ValueError('positive wall/memory limits required')
    metrics = dict(resource_backend='linux_cgroup_v2', wall_limit_s=timeout_s, memory_limit_bytes=memory_mb * 1024 ** 2, limits_enforced=False, memory_accounting='whole cgroup charged memory including descendants/cache/kernel; not Windows committed bytes', fd_comparison='same numeric memory/wall cap; Linux cgroup vs Windows Job Object accounting differs', child_process_tree_covered=False, cleanup_active_processes=None)
    configured = os.environ.get('PAPER4_CGROUP_ROOT')
    if not configured:
        return LimitedResult('RESOURCE_BACKEND_UNAVAILABLE', None, metrics, 'PAPER4_CGROUP_ROOT has not been delegated')
    root = Path(configured).resolve()
    if not root.is_relative_to(Path('/sys/fs/cgroup')) or root == Path('/sys/fs/cgroup'):
        return LimitedResult('RESOURCE_BACKEND_UNAVAILABLE', None, metrics, 'worker root must be an explicitly delegated cgroup subtree')
    started = time.perf_counter()
    parent, child = mp.get_context('spawn').Pipe()
    process = mp.get_context('spawn').Process(target=_worker, args=(child,))
    group = root / f'worker-{os.getpid()}-{uuid.uuid4().hex[:12]}'
    timer = None
    expired = threading.Event()
    status, result, error, pids = ('WORKER_ERROR', None, '', set())

    def kill_group():
        (group / 'cgroup.kill').write_text('1')

    def expire():
        expired.set()
        kill_group()
    try:
        group.mkdir()
        (group / 'memory.max').write_text(str(metrics['memory_limit_bytes']))
        (group / 'memory.swap.max').write_text('0')
        (group / 'memory.oom.group').write_text('1')
        if not (group / 'cgroup.kill').exists():
            raise OSError('kernel lacks atomic descendant cleanup cgroup.kill')
        process.start()
        child.close()
        (group / 'cgroup.procs').write_text(str(process.pid))
        pids.add(process.pid)
        metrics.update(limits_enforced=True, child_process_tree_covered=True, configured_cgroup_memory_limit_bytes=int((group / 'memory.max').read_text()), swap_limit_bytes=0)
        timer = threading.Timer(max(0.001, timeout_s - (time.perf_counter() - started)), expire)
        timer.daemon = True
        timer.start()
        if not expired.is_set():
            parent.send((module, function, args, kwargs or {}))
        while True:
            pids.update((int(p) for p in (group / 'cgroup.procs').read_text().split()))
            events = _fields(group / 'memory.events')
            if events.get('oom', 0) or events.get('oom_kill', 0):
                status = 'MEMORY_LIMIT'
                break
            if expired.is_set() or time.perf_counter() - started >= timeout_s:
                status = 'TIMEOUT'
                break
            if parent.poll(0.005):
                try:
                    kind, payload = parent.recv()
                except EOFError:
                    error = 'worker closed without a result'
                    break
                if kind == 'completed':
                    status, result = ('COMPLETED', payload)
                elif kind == 'memory':
                    status = 'MEMORY_LIMIT'
                else:
                    error = payload
                break
            if not process.is_alive():
                error = f'worker exited without a result: {process.exitcode}'
                break
    except (OSError, ValueError) as exc:
        status, error = ('RESOURCE_BACKEND_ERROR', f'{type(exc).__name__}: {exc}')
    finally:
        if timer:
            timer.cancel()
            timer.join()
        metrics['execution_wall_s'] = time.perf_counter() - started
        if group.exists():
            try:
                events = _fields(group / 'memory.events')
                metrics['memory_events'] = events
                if events.get('oom', 0) or events.get('oom_kill', 0):
                    status, result = ('MEMORY_LIMIT', None)
                elif expired.is_set():
                    status, result = ('TIMEOUT', None)
                peak = group / 'memory.peak'
                metrics['peak_cgroup_memory_bytes'] = int(peak.read_text()) if peak.exists() else None
                pids.update((int(p) for p in (group / 'cgroup.procs').read_text().split()))
                kill_group()
                cleanup = time.perf_counter()
                if process.pid:
                    process.join(3)
                while _fields(group / 'cgroup.events').get('populated') and time.perf_counter() - cleanup < 3:
                    time.sleep(0.005)
                metrics['cleanup_active_processes'] = len((group / 'cgroup.procs').read_text().split())
                metrics['cleanup_populated'] = _fields(group / 'cgroup.events').get('populated')
                metrics['cleanup_wall_s'] = time.perf_counter() - cleanup
                group.rmdir()
            except OSError as exc:
                metrics['cleanup_error'] = f'{type(exc).__name__}: {exc}'
                status, result = ('RESOURCE_BACKEND_ERROR', None)
        if process.pid:
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            metrics['worker_exitcode'] = process.exitcode
        parent.close()
        child.close()
        metrics.update(observed_process_ids=sorted(pids), wall_s=time.perf_counter() - started)
    return LimitedResult(status, result, metrics, error)

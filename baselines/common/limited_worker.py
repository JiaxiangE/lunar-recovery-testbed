"""Windows Job Object isolation for one real local planner invocation.

The whole descendant tree shares a committed-memory hard limit. Allocation
denial / job memory notifications cause tree termination. Wall time includes
worker startup/imports and is enforced externally. This is not RSS: FD's Docker
cgroup memory cap includes a different accounting scope, which is disclosed.
"""
from dataclasses import dataclass
from dataclasses import asdict
import ctypes as ct
from ctypes import wintypes as wt
import importlib
import multiprocessing as mp
import os
import math
import threading
import time

@dataclass
class LimitedResult:
    status: str
    result: object
    metrics: dict
    error: str = ''

    def to_dict(self):
        value = self.result.to_dict() if hasattr(self.result, 'to_dict') else self.result
        return {'status': self.status, 'result': value, 'metrics': self.metrics, 'error': self.error}

class _BasicLimits(ct.Structure):
    _fields_ = [('ProcessTime', ct.c_int64), ('JobTime', ct.c_int64), ('Flags', wt.DWORD), ('MinWS', ct.c_size_t), ('MaxWS', ct.c_size_t), ('ActiveLimit', wt.DWORD), ('Affinity', ct.c_size_t), ('Priority', wt.DWORD), ('Scheduling', wt.DWORD)]

class _IOCounters(ct.Structure):
    _fields_ = [(name, ct.c_uint64) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]

class _ExtendedLimits(ct.Structure):
    _fields_ = [('Basic', _BasicLimits), ('IO', _IOCounters), ('ProcessMemory', ct.c_size_t), ('JobMemory', ct.c_size_t), ('PeakProcess', ct.c_size_t), ('PeakJob', ct.c_size_t)]

class _Accounting(ct.Structure):
    _fields_ = [(name, ct.c_int64) for name in ('User', 'Kernel', 'PeriodUser', 'PeriodKernel')] + [(name, wt.DWORD) for name in ('PageFaults', 'TotalProcesses', 'ActiveProcesses', 'TerminatedProcesses')]

class _CompletionPort(ct.Structure):
    _fields_ = [('Key', ct.c_void_p), ('Port', wt.HANDLE)]

class _Job:

    def __init__(self, memory_bytes):
        self.k = ct.WinDLL('kernel32', use_last_error=True)
        for name, args, restype in (('CreateJobObjectW', [ct.c_void_p, wt.LPCWSTR], wt.HANDLE), ('SetInformationJobObject', [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD], wt.BOOL), ('QueryInformationJobObject', [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.c_void_p], wt.BOOL), ('OpenProcess', [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE), ('AssignProcessToJobObject', [wt.HANDLE, wt.HANDLE], wt.BOOL), ('TerminateJobObject', [wt.HANDLE, wt.UINT], wt.BOOL), ('CreateIoCompletionPort', [wt.HANDLE, wt.HANDLE, ct.c_size_t, wt.DWORD], wt.HANDLE), ('GetQueuedCompletionStatus', [wt.HANDLE, ct.POINTER(wt.DWORD), ct.POINTER(ct.c_size_t), ct.POINTER(ct.c_void_p), wt.DWORD], wt.BOOL), ('CloseHandle', [wt.HANDLE], wt.BOOL)):
            f = getattr(self.k, name)
            f.argtypes = args
            f.restype = restype
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ct.WinError(ct.get_last_error())
        self.port = None
        try:
            limits = _ExtendedLimits()
            limits.Basic.Flags = 512 | 8192
            limits.JobMemory = memory_bytes
            self._set(9, limits)
            self.port = self.k.CreateIoCompletionPort(wt.HANDLE(-1), None, 0, 1)
            if not self.port:
                raise ct.WinError(ct.get_last_error())
            self._set(7, _CompletionPort(ct.c_void_p(1), self.port))
        except BaseException:
            self.close()
            raise

    def _set(self, kind, value):
        if not self.k.SetInformationJobObject(self.handle, kind, ct.byref(value), ct.sizeof(value)):
            raise ct.WinError(ct.get_last_error())

    def attach(self, pid):
        process = self.k.OpenProcess(256 | 1 | 4096, False, pid)
        if not process:
            raise ct.WinError(ct.get_last_error())
        try:
            if not self.k.AssignProcessToJobObject(self.handle, process):
                raise ct.WinError(ct.get_last_error())
        finally:
            self.k.CloseHandle(process)

    def messages(self):
        result = []
        while True:
            message, key, value = (wt.DWORD(), ct.c_size_t(), ct.c_void_p())
            if not self.k.GetQueuedCompletionStatus(self.port, ct.byref(message), ct.byref(key), ct.byref(value), 0):
                break
            result.append((message.value, value.value))
        return result

    def query(self):
        limits, counts = (_ExtendedLimits(), _Accounting())
        for kind, value in ((9, limits), (1, counts)):
            if not self.k.QueryInformationJobObject(self.handle, kind, ct.byref(value), ct.sizeof(value), None):
                raise ct.WinError(ct.get_last_error())
        return {'peak_job_committed_bytes': limits.PeakJob, 'peak_single_process_committed_bytes': limits.PeakProcess, 'configured_job_memory_limit_bytes': limits.JobMemory, 'reported_peak_over_limit_bytes': max(0, limits.PeakJob - limits.JobMemory), 'active_processes': counts.ActiveProcesses, 'total_processes': counts.TotalProcesses}

    def terminate(self):
        self.k.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None
        if self.port:
            self.k.CloseHandle(self.port)
            self.port = None

def _worker(connection):
    try:
        module, function, args, kwargs = connection.recv()
        target = getattr(importlib.import_module(module), function)
        connection.send(('completed', target(*args, **kwargs)))
    except MemoryError:
        connection.send(('memory', None))
    except BaseException as exc:
        connection.send(('error', f'{type(exc).__name__}: {exc}'))
    finally:
        connection.close()

def run_limited(module, function, args=(), kwargs=None, *, timeout_s=30.0, memory_mb=2048):
    """Return a result or an explicit limit outcome; never substitute a solver."""
    if not math.isfinite(timeout_s) or timeout_s <= 0 or type(memory_mb) is not int or (memory_mb <= 0):
        raise ValueError('positive wall/memory limits required')
    if os.name == 'posix' and os.path.exists('/sys/fs/cgroup/cgroup.controllers'):
        from baselines.common.linux_limited_worker import run_linux_limited
        return run_linux_limited(module, function, args, kwargs, timeout_s=timeout_s, memory_mb=memory_mb)
    metrics = {'resource_backend': 'windows_job_object', 'wall_limit_s': timeout_s, 'memory_limit_bytes': memory_mb * 1024 ** 2, 'limits_enforced': False, 'memory_accounting': 'whole-job committed bytes; not RSS or Docker cgroup usage', 'fd_comparison': 'same numeric 2 GiB / 30 s per goal; Windows committed-memory and Docker cgroup scopes differ', 'child_process_tree_covered': False, 'cleanup_active_processes': None}
    if os.name != 'nt':
        return LimitedResult('RESOURCE_BACKEND_UNAVAILABLE', None, metrics, 'this backend requires Windows')
    started = time.perf_counter()
    parent, child = mp.get_context('spawn').Pipe()
    process = mp.get_context('spawn').Process(target=_worker, args=(child,))
    job, result, status, error, pids = (None, None, 'WORKER_ERROR', '', set())
    expired, watchdog = (threading.Event(), None)
    try:
        job = _Job(metrics['memory_limit_bytes'])

        def timeout_tree():
            expired.set()
            job.terminate()
        watchdog = threading.Timer(max(0.001, timeout_s - (time.perf_counter() - started)), timeout_tree)
        watchdog.daemon = True
        watchdog.start()
        process.start()
        child.close()
        job.attach(process.pid)
        pids.add(process.pid)
        metrics.update(limits_enforced=True, child_process_tree_covered=True)
        if not expired.is_set():
            parent.send((module, function, args, kwargs or {}))
        while True:
            memory_notification = False
            for message, pid in job.messages():
                if message == 6 and pid:
                    pids.add(pid)
                if message in (9, 10):
                    memory_notification = True
            if memory_notification:
                status = 'MEMORY_LIMIT'
                metrics['memory_limit_notification'] = True
                job.terminate()
                break
            if expired.is_set() or time.perf_counter() - started >= timeout_s:
                status = 'TIMEOUT'
                job.terminate()
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
                    job.terminate()
                else:
                    error = payload
                break
            if not process.is_alive():
                error = f'worker exited without a result: {process.exitcode}'
                break
    except (OSError, ValueError) as exc:
        status, error = ('RESOURCE_BACKEND_ERROR', f'{type(exc).__name__}: {exc}')
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        if expired.is_set() and status != 'MEMORY_LIMIT':
            status, result = ('TIMEOUT', None)
        metrics['execution_wall_s'] = time.perf_counter() - started
        if job is not None:
            for message, pid in job.messages():
                if message == 6 and pid:
                    pids.add(pid)
                if message in (9, 10):
                    status, result = ('MEMORY_LIMIT', None)
                    metrics['memory_limit_notification'] = True
            metrics.update(job.query())
            job.terminate()
            cleanup_started = time.perf_counter()
            while job.query()['active_processes'] and time.perf_counter() - cleanup_started < 3:
                time.sleep(0.005)
            metrics['cleanup_active_processes'] = job.query()['active_processes']
            metrics['cleanup_wall_s'] = time.perf_counter() - cleanup_started
            job.close()
        if process.pid is not None:
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            metrics['worker_exitcode'] = process.exitcode
        parent.close()
        child.close()
        metrics['observed_process_ids'] = sorted(pids)
        metrics['wall_s'] = time.perf_counter() - started
    return LimitedResult(status, result, metrics, error)

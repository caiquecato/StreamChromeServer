"""Tie stream workers to server lifetime on Windows, including forced shutdown."""
import ctypes
from ctypes import wintypes
import os
import subprocess


def _create_job():
    if os.name != 'nt':
        return None
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),('active_processes',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IoCounters(ctypes.Structure):
        _fields_ = [(name,ctypes.c_ulonglong) for name in ('reads','writes','others','read_bytes','written_bytes','other_bytes')]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic',BasicLimits),('io',IoCounters),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]
    kernel=ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    job=kernel.CreateJobObjectW(None,None)
    if not job:
        return None
    limits=ExtendedLimits()
    limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        return None
    return job


# This handle is intentionally retained; Windows closes it when the server exits.
_STREAM_JOB=_create_job()


def attach_to_server(process):
    if _STREAM_JOB is None:
        return False
    kernel=ctypes.windll.kernel32
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    return bool(kernel.AssignProcessToJobObject(_STREAM_JOB,int(process._handle)))


def run_managed(args, capture_output=False, check=False, timeout=None, **kwargs):
    """Run an engine with subprocess.run semantics and Windows lifetime protection."""
    input_data = kwargs.pop('input', None)
    if input_data is not None:
        if kwargs.get('stdin') is not None:
            raise ValueError('stdin and input arguments may not both be used.')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('stdout and stderr arguments may not be used with capture_output.')
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    with subprocess.Popen(args, **kwargs) as process:
        try:
            if os.name == 'nt' and not attach_to_server(process):
                raise OSError('Could not bind the engine to the server lifetime.')
            stdout, stderr = process.communicate(input_data, timeout=timeout)
        except subprocess.TimeoutExpired as err:
            process.kill()
            err.stdout, err.stderr = process.communicate()
            raise
        except BaseException:
            process.kill()
            process.communicate()
            raise
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result

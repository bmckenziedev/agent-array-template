"""Windows job object: supervisor exit kills every descendant process."""
import ctypes
from ctypes import wintypes
import os
import subprocess


class BasicLimits(ctypes.Structure):
    _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSet', ctypes.c_size_t),
                ('MaximumWorkingSet', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ['ReadOperations', 'WriteOperations', 'OtherOperations',
                 'ReadBytes', 'WriteBytes', 'OtherBytes']]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('Basic', BasicLimits), ('IO', IOCounters),
                ('ProcessMemory', ctypes.c_size_t), ('JobMemory', ctypes.c_size_t),
                ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]


class ProcessJob:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Windows job objects are required')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command, env):
        # Suspend before assignment so children cannot escape between spawn and job registration.
        process = subprocess.Popen(command, env=env, creationflags=0x00000004)
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            raise ctypes.WinError(ctypes.get_last_error())
        resume = ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        if resume(int(process._handle)) != 0:
            process.kill()
            process.wait()
            raise RuntimeError('Could not resume supervised process')
        return process

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

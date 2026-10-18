"""Windows Job Object 封装（ctypes，仅 Windows）。

能力边界（重要）：
Job Object 能限制 CPU 时间、内存、进程数，并在关闭句柄时连同子树一起终止；
它 **不是** 文件系统或网络隔离。因此本模块报告的 ``network_block`` 与
``filesystem_isolation`` 都是 False，能力门禁会据此判定"未通过门禁"。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
LARGE_INTEGER = ctypes.c_int64

JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4

JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", LARGE_INTEGER),
        ("TotalKernelTime", LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ControlFlags", wintypes.DWORD),
        ("CpuRate", wintypes.DWORD),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32First.restype = wintypes.BOOL
kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32Next.restype = wintypes.BOOL
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD


def _raise_last_error(action: str) -> None:
    code = ctypes.get_last_error()
    raise OSError(code, f"{action}失败：{ctypes.FormatError(code)}")


@dataclass
class JobAccounting:
    total_user_time_100ns: int
    total_kernel_time_100ns: int
    active_processes: int
    total_processes: int
    peak_job_memory: int
    peak_process_memory: int

    @property
    def total_user_ms(self) -> int:
        return self.total_user_time_100ns // 10_000


class JobObject:
    """把被测进程放进受限制的 Job Object。"""

    def __init__(
        self,
        *,
        cpu_ms: int,
        memory_bytes: int,
        active_process_limit: int,
        cpu_rate_percent: int,
    ):
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle or self.handle == INVALID_HANDLE_VALUE:
            _raise_last_error("CreateJobObjectW")
        self.cpu_ms = cpu_ms
        self.memory_bytes = memory_bytes
        flags = (
            JOB_OBJECT_LIMIT_PROCESS_TIME
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_ms * 10_000
        info.BasicLimitInformation.PerJobUserTimeLimit = cpu_ms * 10_000
        info.BasicLimitInformation.LimitFlags = flags
        info.BasicLimitInformation.ActiveProcessLimit = max(1, active_process_limit)
        info.ProcessMemoryLimit = memory_bytes
        info.JobMemoryLimit = memory_bytes
        if not kernel32.SetInformationJobObject(
            self.handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            _raise_last_error("SetInformationJobObject(extended)")

        rate = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        rate.ControlFlags = (
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        )
        rate.CpuRate = max(1, min(100, cpu_rate_percent)) * 100
        kernel32.SetInformationJobObject(
            self.handle,
            JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION,
            ctypes.byref(rate),
            ctypes.sizeof(rate),
        )

    def assign(self, process_handle: int) -> None:
        if not kernel32.AssignProcessToJobObject(self.handle, process_handle):
            _raise_last_error("AssignProcessToJobObject")

    def terminate(self, exit_code: int = 1) -> None:
        kernel32.TerminateJobObject(self.handle, exit_code)

    def accounting(self) -> JobAccounting:
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        kernel32.QueryInformationJobObject(
            self.handle,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        extended = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        kernel32.QueryInformationJobObject(
            self.handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
            None,
        )
        return JobAccounting(
            total_user_time_100ns=info.TotalUserTime,
            total_kernel_time_100ns=info.TotalKernelTime,
            active_processes=info.ActiveProcesses,
            total_processes=info.TotalProcesses,
            peak_job_memory=extended.PeakJobMemoryUsed,
            peak_process_memory=extended.PeakProcessMemoryUsed,
        )

    def close(self) -> None:
        if getattr(self, "handle", None):
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "JobObject":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def resume_process_threads(process_id: int) -> int:
    """恢复 CREATE_SUSPENDED 进程的全部线程，返回恢复的线程数。"""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        _raise_last_error("CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(THREADENTRY32)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == process_id:
                handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if handle:
                    kernel32.ResumeThread(handle)
                    kernel32.CloseHandle(handle)
                    resumed += 1
            entry.dwSize = ctypes.sizeof(THREADENTRY32)
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if resumed == 0:
        raise OSError(0, "未能恢复任何线程：进程可能已提前结束")
    return resumed


def waiting_process(*args, **kwargs) -> subprocess.Popen:
    """占位：真正的启动逻辑在 local.py 中，便于单元测试替换。"""
    raise NotImplementedError


def spawn_in_job(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    job: JobObject,
    stdin_path: Path | None,
    stdout_path: Path,
    stderr_path: Path,
    wall_timeout_ms: int,
) -> tuple[int | None, bool, int, JobAccounting, float]:
    """启动进程、加入 Job、等待；返回 (exit_code, timed_out, wall_ms, accounting, start)。"""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stdin_handle = open(stdin_path, "rb") if stdin_path is not None else subprocess.DEVNULL
    started = time.perf_counter()
    with open(stdout_path, "wb") as stdout_handle, open(stderr_path, "wb") as stderr_handle:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=stdin_handle,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        try:
            job.assign(int(process._handle))  # type: ignore[attr-defined]
            resume_process_threads(process.pid)
            timed_out = False
            while True:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                remaining = wall_timeout_ms - elapsed_ms
                if remaining <= 0:
                    timed_out = True
                    job.terminate(2)
                    break
                result = kernel32.WaitForSingleObject(
                    int(process._handle), min(remaining, 200)  # type: ignore[attr-defined]
                )
                if result == WAIT_OBJECT_0:
                    break
            wall_ms = int((time.perf_counter() - started) * 1000)
            accounting = job.accounting()
            if not timed_out and accounting.active_processes > 0:
                # 主进程退出后仍有残留子进程：连树终止（tree_kill）
                job.terminate(3)
                accounting = job.accounting()
            process.wait(timeout=10)
            return process.returncode, timed_out, wall_ms, accounting, started
        finally:
            if process.poll() is None:
                job.terminate(4)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    process.kill()
            if stdin_handle is not subprocess.DEVNULL:
                stdin_handle.close()  # type: ignore[union-attr]


def minimal_environment(work_dir: Path) -> dict[str, str]:
    """只保留定位系统 DLL 与编译器所需变量，减少信息泄露。"""
    env = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "PATH": os.environ.get("PATH", ""),
        "TEMP": str(work_dir),
        "TMP": str(work_dir),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"),
    }
    return env

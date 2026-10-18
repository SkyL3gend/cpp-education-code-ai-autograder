"""本地受限执行后端：Windows Job Object。

诚实声明能力：CPU 时间、内存、进程数、墙钟超时、进程树终止、干净工作目录；
**没有**网络隔离与文件系统隔离。因此它永远报 ``network_block=False``，
能力门禁默认拒绝，只有显式放行时才会执行，且报告标注"未通过门禁"。
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from ..domain.canonical import canonical_json, sha256_hex
from ..domain.enums import (
    CompileStatus,
    FaultOwner,
    IsolationLevel,
    Termination,
)
from ..domain.models import SandboxCapabilities
from .base import CompileOutcome, CompileRequest, RunOutcome, RunRequest
from .win32 import JobObject, minimal_environment, spawn_in_job

BACKEND_ID = "local_win32_job"
BACKEND_VERSION = "0.1.0"


class LocalJobBackend:
    backend_id = BACKEND_ID
    simulated = False

    def __init__(self, notes: list[str] | None = None):
        self._notes = list(notes or [])
        self._notes.extend(
            [
                "Windows Job Object 只约束资源与进程树，不提供网络与文件系统隔离",
                "输出上限在执行后核验，超限按 OUTPUT_LIMIT 处理",
            ]
        )

    # -- 能力 ---------------------------------------------------------------
    def capabilities(self) -> SandboxCapabilities:
        digest_source = canonical_json(
            {
                "backend": BACKEND_ID,
                "version": BACKEND_VERSION,
                "os": platform.platform(),
                "python": platform.python_version(),
            }
        )
        return SandboxCapabilities(
            schema_version="1.0",
            backend_id=BACKEND_ID,
            environment_digest=sha256_hex(digest_source.encode("utf-8")),
            isolation=IsolationLevel.HOST_PROCESS,
            network_block=False,
            cpu_limit=True,
            memory_limit=True,
            process_limit=True,
            wall_timeout=True,
            tree_kill=True,
            clean_reset=True,
            filesystem_isolation=False,
            notes=self._notes,
        )

    # -- 编译 ---------------------------------------------------------------
    def compile(self, request: CompileRequest) -> CompileOutcome:
        compiler = Path(request.compiler)
        if not compiler.is_file():
            return CompileOutcome(
                status=CompileStatus.INFRA_ERROR,
                exit_code=None,
                stdout="",
                stderr=f"找不到编译器：{compiler}",
                duration_ms=0,
                artifact_hash=None,
                fault_owner=FaultOwner.INFRA,
                issue_code="COMPILER_MISSING",
            )
        argv = [str(compiler)]
        argv.extend(request.flags)
        argv.extend(f"-I{directory}" for directory in request.include_dirs)
        argv.extend(str(unit) for unit in request.source_units)
        argv.extend(["-o", str(request.output)])
        request.work_dir.mkdir(parents=True, exist_ok=True)
        request.output.parent.mkdir(parents=True, exist_ok=True)
        env = minimal_environment(request.work_dir)
        env.update(request.env)
        stdout_path = request.work_dir / "compile.stdout.txt"
        stderr_path = request.work_dir / "compile.stderr.txt"
        try:
            with JobObject(
                cpu_ms=request.limits.compile_cpu_ms,
                memory_bytes=request.limits.compile_memory_bytes,
                active_process_limit=request.limits.compile_process_count,
                cpu_rate_percent=request.limits.cpu_rate_percent,
            ) as job:
                exit_code, timed_out, wall_ms, accounting, _ = spawn_in_job(
                    argv,
                    cwd=request.work_dir,
                    env=env,
                    job=job,
                    stdin_path=None,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    wall_timeout_ms=request.limits.compile_wall_ms,
                )
        except OSError as exc:
            return CompileOutcome(
                status=CompileStatus.INFRA_ERROR,
                exit_code=None,
                stdout="",
                stderr=f"启动编译进程失败：{exc}",
                duration_ms=0,
                artifact_hash=None,
                fault_owner=FaultOwner.INFRA,
                issue_code="COMPILER_SPAWN_FAILED",
            )
        stdout = _read_text(stdout_path)
        stderr = _read_text(stderr_path)
        artifact_hash = sha256_hex(request.output.read_bytes()) if request.output.is_file() else None
        if timed_out:
            return CompileOutcome(
                status=CompileStatus.TIMEOUT,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=wall_ms,
                artifact_hash=None,
                fault_owner=FaultOwner.SUBMISSION,
                issue_code="COMPILE_WALL_TIME_LIMIT",
            )
        if exit_code != 0 or artifact_hash is None:
            return CompileOutcome(
                status=CompileStatus.COMPILER_ERROR,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=wall_ms,
                artifact_hash=None,
                fault_owner=FaultOwner.SUBMISSION,
                issue_code="COMPILE_ERROR",
            )
        return CompileOutcome(
            status=CompileStatus.SUCCESS,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=wall_ms,
            artifact_hash=artifact_hash,
            fault_owner=FaultOwner.NONE,
        )

    # -- 执行 ---------------------------------------------------------------
    def run(self, request: RunRequest) -> RunOutcome:
        request.work_dir.mkdir(parents=True, exist_ok=True)
        env = minimal_environment(request.work_dir)
        env.update(request.env)
        try:
            with JobObject(
                cpu_ms=request.limits.case_cpu_ms,
                memory_bytes=request.limits.case_memory_bytes,
                active_process_limit=request.limits.case_process_count,
                cpu_rate_percent=request.limits.cpu_rate_percent,
            ) as job:
                exit_code, timed_out, wall_ms, accounting, _ = spawn_in_job(
                    [str(request.executable), *request.argv],
                    cwd=request.work_dir,
                    env=env,
                    job=job,
                    stdin_path=request.stdin_path,
                    stdout_path=request.stdout_path,
                    stderr_path=request.stderr_path,
                    wall_timeout_ms=request.limits.case_wall_ms,
                )
        except OSError as exc:
            return RunOutcome(
                termination=Termination.INFRA_ERROR,
                exit_code=None,
                wall_ms=0,
                stdout_bytes=0,
                stderr_bytes=0,
                fault_owner=FaultOwner.INFRA,
                resource_events=[f"启动失败：{exc}"],
            )

        stdout_bytes = _size(request.stdout_path)
        stderr_bytes = _size(request.stderr_path)
        events: list[str] = []
        if accounting.active_processes == 0 and accounting.total_processes > 1:
            events.append(f"进程树共 {accounting.total_processes} 个进程，已全部退出")

        termination = _classify(
            timed_out=timed_out,
            exit_code=exit_code,
            accounting_user_ms=accounting.total_user_ms,
            cpu_ms=request.limits.case_cpu_ms,
            peak_memory=accounting.peak_job_memory,
            memory_bytes=request.limits.case_memory_bytes,
            output_bytes=max(stdout_bytes, stderr_bytes),
            output_limit=request.limits.output_bytes,
            events=events,
        )
        fault_owner = (
            FaultOwner.SUBMISSION
            if termination
            in (
                Termination.CRASH,
                Termination.WALL_TIME_LIMIT,
                Termination.CPU_TIME_LIMIT,
                Termination.MEMORY_LIMIT,
                Termination.PROCESS_LIMIT,
                Termination.OUTPUT_LIMIT,
            )
            else FaultOwner.NONE
        )
        if termination is Termination.INFRA_ERROR:
            fault_owner = FaultOwner.INFRA
        return RunOutcome(
            termination=termination,
            exit_code=exit_code,
            wall_ms=wall_ms,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            fault_owner=fault_owner,
            resource_events=events,
        )


def _classify(
    *,
    timed_out: bool,
    exit_code: int | None,
    accounting_user_ms: int,
    cpu_ms: int,
    peak_memory: int,
    memory_bytes: int,
    output_bytes: int,
    output_limit: int,
    events: list[str],
) -> Termination:
    if timed_out:
        events.append("墙钟超时，已终止进程树")
        return Termination.WALL_TIME_LIMIT
    if accounting_user_ms >= cpu_ms * 0.95:
        events.append(f"CPU 时间 {accounting_user_ms}ms 触达上限 {cpu_ms}ms")
        return Termination.CPU_TIME_LIMIT
    if peak_memory >= memory_bytes * 0.95:
        events.append(f"峰值内存 {peak_memory}B 触达上限 {memory_bytes}B")
        return Termination.MEMORY_LIMIT
    if output_bytes > output_limit:
        events.append(f"输出 {output_bytes}B 超过上限 {output_limit}B")
        return Termination.OUTPUT_LIMIT
    if exit_code is None:
        return Termination.INFRA_ERROR
    if exit_code != 0:
        events.append(f"进程异常退出，exit_code={exit_code:#010x}")
        return Termination.CRASH
    return Termination.EXITED


def _read_text(path: Path, limit: int = 1 << 20) -> str:
    if not path.is_file():
        return ""
    data = path.read_bytes()[:limit]
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def compiler_available() -> bool:
    return shutil.which("g++") is not None or shutil.which("g++.exe") is not None


def find_compiler() -> Path | None:
    found = shutil.which("g++") or shutil.which("g++.exe")
    return Path(found) if found else None

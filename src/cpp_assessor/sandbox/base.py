"""沙箱后端接口（架构实施方案 §9）。

沙箱只做"启动、限制、终止、回收"，不理解业务语义，
也不存在任何"宿主直接执行"的后备路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..domain.enums import CompileStatus, FaultOwner, Termination
from ..domain.models import BuildPlan, ResourceLimits, SandboxCapabilities


@dataclass(frozen=True)
class CompileRequest:
    build_plan: BuildPlan
    compiler: Path
    source_units: list[Path]
    include_dirs: list[Path]
    flags: list[str]
    output: Path
    work_dir: Path
    limits: ResourceLimits
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompileOutcome:
    status: CompileStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    artifact_hash: str | None
    fault_owner: FaultOwner
    issue_code: str | None = None


@dataclass(frozen=True)
class RunRequest:
    executable: Path
    argv: list[str]
    stdin_path: Path
    stdout_path: Path
    stderr_path: Path
    work_dir: Path
    limits: ResourceLimits
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunOutcome:
    termination: Termination
    exit_code: int | None
    wall_ms: int
    stdout_bytes: int
    stderr_bytes: int
    fault_owner: FaultOwner
    resource_events: list[str] = field(default_factory=list)


@runtime_checkable
class SandboxBackend(Protocol):
    backend_id: str
    simulated: bool

    def capabilities(self) -> SandboxCapabilities: ...

    def compile(self, request: CompileRequest) -> CompileOutcome: ...

    def run(self, request: RunRequest) -> RunOutcome: ...

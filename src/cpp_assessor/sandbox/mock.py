"""模拟执行后端（架构实施方案 §9.3）。

**它不执行任何提交代码**。观测值由用例期望反推得到，用来验证状态机、
报告结构与导出链路；所有产出都会带 ``EXECUTION_BACKEND_MOCK`` 标记，
不得计入正式验收。
"""

from __future__ import annotations

from typing import Any

from ..domain.canonical import sha256_hex
from ..domain.enums import (
    CompileStatus,
    FaultOwner,
    IsolationLevel,
    Termination,
)
from ..domain.models import SandboxCapabilities
from .base import CompileOutcome, CompileRequest, RunOutcome, RunRequest

BACKEND_ID = "mock"


class MockBackend:
    backend_id = BACKEND_ID
    simulated = True

    def __init__(self) -> None:
        self._compile_outcome: CompileOutcome | None = None
        self._case_outcomes: dict[str, RunOutcome] = {}

    # -- 测试注入 -----------------------------------------------------------
    def script_compile(self, outcome: CompileOutcome) -> None:
        self._compile_outcome = outcome

    def script_case(self, case_id: str, outcome: RunOutcome) -> None:
        self._case_outcomes[case_id] = outcome

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            schema_version="1.0",
            backend_id=BACKEND_ID,
            environment_digest=sha256_hex(b"mock-backend"),
            isolation=IsolationLevel.NONE,
            network_block=False,
            cpu_limit=False,
            memory_limit=False,
            process_limit=False,
            wall_timeout=False,
            tree_kill=False,
            clean_reset=True,
            filesystem_isolation=False,
            notes=["模拟后端：不执行任何代码，观测由用例期望合成，仅用于状态机验证"],
        )

    def compile(self, request: CompileRequest) -> CompileOutcome:
        if self._compile_outcome is not None:
            return self._compile_outcome
        return CompileOutcome(
            status=CompileStatus.SUCCESS,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=0,
            artifact_hash=sha256_hex(b"mock-artifact"),
            fault_owner=FaultOwner.NONE,
        )

    def run(self, request: RunRequest) -> RunOutcome:
        return self._case_outcomes.get(
            request.stdin_path.stem,
            RunOutcome(
                termination=Termination.EXITED,
                exit_code=0,
                wall_ms=0,
                stdout_bytes=0,
                stderr_bytes=0,
                fault_owner=FaultOwner.NONE,
                resource_events=["模拟执行：未启动任何进程"],
            ),
        )

    # -- 供编排器使用 -------------------------------------------------------
    def synthesize_actual(self, expected: dict[str, Any]) -> dict[str, Any]:
        return dict(expected)

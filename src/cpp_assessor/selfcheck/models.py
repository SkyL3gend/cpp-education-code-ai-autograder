# -*- coding: utf-8 -*-
"""基准程序实测的报告模型（全部严格校验，未知字段拒绝）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..domain.enums import CompileStatus, Termination
from ..domain.models import Sha256, StrictModel

Verdict = Literal["PASS", "DEFECT", "NOT_OBSERVED"]


class CompileSummary(StrictModel):
    """一次自检里的编译结果摘要（完整日志以工件形式保存）。"""

    status: CompileStatus
    exit_code: int | None
    duration_ms: int = Field(ge=0)
    stdout_ref: str
    stderr_ref: str
    artifact_sha256: Sha256 | None = None


class CheckOutcome(StrictModel):
    """一条自检规则的一次观察结果。"""

    check_id: str
    title: str
    rule_ref: str | None = None
    expectation: str
    observed: dict[str, Any]
    verdict: Verdict
    detail: str
    termination: Termination
    elapsed_ms: int = Field(ge=0)
    stdout_ref: str


class SelfCheckSummary(StrictModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    defects: int = Field(ge=0)
    not_observed: int = Field(ge=0)


class SelfCheckReport(StrictModel):
    schema_version: str
    selfcheck_id: str
    source_name: str
    source_sha256: Sha256
    source_kind: Literal["reference", "upload"]
    toolchain_id: str
    backend_id: str
    sandbox_attested: bool
    simulated: bool
    compile: CompileSummary
    checks: list[CheckOutcome]
    summary: SelfCheckSummary
    started_at: str
    finished_at: str
    note: str

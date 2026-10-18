# -*- coding: utf-8 -*-
"""基准程序实测的执行器。

流程：校验源码 → 复制快照 → 真实编译 → 逐条脚本运行 → 提取观察 → 判定 → 写报告。
所有编译/运行都走 ``sandbox`` 后端（默认本地 Job Object 后端），没有宿主直跑后备路径。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, data_dir, toolchain_id
from ..domain.canonical import hash_json, sha256_hex, utc_now
from ..domain.enums import BuildRole, CompileStatus, SourceOrigin, Termination
from ..domain.errors import InvalidInputError
from ..domain.models import (
    BuildPlan,
    CapabilityRequirements,
    ResourceLimits,
    SourceUnit,
)
from ..orchestrator.runner import make_backend
from ..sandbox.base import CompileRequest, RunRequest
from ..sandbox.gate import evaluate
from ..sandbox.local import find_compiler
from .checks import CHECKS, CheckSpec
from .models import (
    CheckOutcome,
    CompileSummary,
    SelfCheckReport,
    SelfCheckSummary,
)

SCHEMA_VERSION = "1.0"
SELFCHECK_DIRNAME = "selfchecks"
MAX_SOURCE_BYTES = 20 * 1024 * 1024

SELFCHECK_LIMITS = ResourceLimits(
    limits_id="selfcheck_v1",
    compile_wall_ms=60_000,
    case_wall_ms=5_000,
    total_wall_ms=120_000,
    compile_cpu_ms=60_000,
    case_cpu_ms=4_000,
    cpu_rate_percent=80,
    compile_memory_bytes=1024 * 1024 * 1024,
    case_memory_bytes=256 * 1024 * 1024,
    compile_process_count=8,
    case_process_count=4,
    output_bytes=1024 * 1024,
    workdir_bytes=100 * 1024 * 1024,
)

NORMAL_EXIT = Termination.EXITED
COMPILER_FLAGS = ["-std=c++17", "-O2"]

REPORT_NOTE = (
    "观察型自检：用固定脚本驱动程序的黑盒行为，结论只说明本次观察是否与规则相符，"
    "不参与五道小改题的评分，也不产生 grade_eligible。"
)


@dataclass(frozen=True)
class SelfCheckResult:
    report: SelfCheckReport
    report_path: Path
    run_dir: Path


def default_reference_source() -> Path | None:
    """工作区里随附的原始基准程序；不存在时返回 None。"""
    candidate = PROJECT_ROOT / "参考基准程序" / "通讯录_v6最终版.cpp"
    return candidate if candidate.is_file() else None


def new_selfcheck_id() -> str:
    return f"sc_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _read_text(path: Path) -> str:
    try:
        return _decode(path.read_bytes())
    except FileNotFoundError:
        return ""


def _validate_source(source: Path) -> None:
    if not source.exists():
        raise InvalidInputError(f"找不到源码文件：{source}")
    if not source.is_file():
        raise InvalidInputError(f"源码路径不是文件：{source}")
    if source.suffix.lower() != ".cpp":
        raise InvalidInputError("基准程序实测只接受单个 .cpp 文件")
    size = source.stat().st_size
    if size == 0:
        raise InvalidInputError(f"源码文件为空：{source}")
    if size > MAX_SOURCE_BYTES:
        raise InvalidInputError(
            f"源码文件超过 {MAX_SOURCE_BYTES // (1024 * 1024)} MiB 上限：{source}"
        )
    try:
        _decode(source.read_bytes())
    except Exception as exc:  # pragma: no cover - 极端编码问题
        raise InvalidInputError(f"源码无法按 UTF-8/GBK 解码：{exc}") from exc


def _build_plan(selfcheck_id: str, source: Path, source_sha: str, artifact: str) -> BuildPlan:
    payload = {
        "build_plan_id": f"{selfcheck_id}_build",
        "role": BuildRole.ORIGINAL.value,
        "subject_snapshot_hash": source_sha,
        "toolchain_id": toolchain_id(),
        "source_units": [
            {
                "unit_id": "subject",
                "origin": SourceOrigin.REFERENCE.value,
                "role": BuildRole.ORIGINAL.value,
                "path": f"source/{source.name}",
            }
        ],
        "include_dirs": [],
        "defines": [],
        "libraries": [],
        "entrypoint": None,
        "artifact_name": artifact,
    }
    return BuildPlan(
        schema_version=SCHEMA_VERSION,
        build_plan_id=payload["build_plan_id"],
        role=BuildRole.ORIGINAL,
        subject_snapshot_hash=source_sha,
        toolchain_id=payload["toolchain_id"],
        source_units=[
            SourceUnit(
                unit_id="subject",
                origin=SourceOrigin.REFERENCE,
                role=BuildRole.ORIGINAL,
                path=f"source/{source.name}",
            )
        ],
        include_dirs=[],
        defines=[],
        libraries=[],
        entrypoint=None,
        artifact_name=artifact,
        build_hash=hash_json(payload),
    )


def _run_check(
    *,
    spec: CheckSpec,
    backend: Any,
    executable: Path,
    cases_dir: Path,
    limits: ResourceLimits,
) -> CheckOutcome:
    case_dir = cases_dir / spec.check_id
    work_dir = case_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    stdin_path = case_dir / "stdin.txt"
    stdout_path = case_dir / "stdout.txt"
    stderr_path = case_dir / "stderr.txt"
    _write_text(stdin_path, "\n".join(spec.script) + "\n")

    request = RunRequest(
        executable=executable,
        argv=[],
        stdin_path=stdin_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        work_dir=work_dir,
        limits=limits,
        env={},
    )
    outcome = backend.run(request)
    stdout = _read_text(stdout_path)
    stderr = _read_text(stderr_path)

    observed = spec.extract(stdout, stderr)
    observed["termination"] = outcome.termination.value
    observed["exit_code"] = outcome.exit_code
    verdict, detail = spec.judge(observed)

    return CheckOutcome(
        check_id=spec.check_id,
        title=spec.title,
        rule_ref=spec.rule_ref,
        expectation=spec.expectation,
        observed=observed,
        verdict=verdict,  # type: ignore[arg-type]
        detail=detail,
        termination=outcome.termination,
        elapsed_ms=outcome.wall_ms,
        stdout_ref=f"cases/{spec.check_id}/stdout.txt",
    )


def _summary(checks: list[CheckOutcome]) -> SelfCheckSummary:
    return SelfCheckSummary(
        total=len(checks),
        passed=sum(1 for c in checks if c.verdict == "PASS"),
        defects=sum(1 for c in checks if c.verdict == "DEFECT"),
        not_observed=sum(1 for c in checks if c.verdict == "NOT_OBSERVED"),
    )


def run_selfcheck(
    source: Path,
    *,
    backend_name: str = "local",
    data_root: Path | None = None,
    source_kind: str = "upload",
    specs: tuple[CheckSpec, ...] = CHECKS,
) -> SelfCheckResult:
    """编译并实测一个 C++ 程序，返回报告（同时把报告与证据写到磁盘）。"""
    source = Path(source)
    _validate_source(source)

    root = Path(data_root) if data_root is not None else data_dir()
    selfcheck_id = new_selfcheck_id()
    run_dir = root / SELFCHECK_DIRNAME / selfcheck_id
    source_dir = run_dir / "source"
    build_dir = run_dir / "build"
    cases_dir = run_dir / "cases"
    for directory in (source_dir, build_dir, cases_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_bytes = source.read_bytes()
    source_sha = sha256_hex(source_bytes)
    stored_source = source_dir / source.name
    stored_source.write_bytes(source_bytes)

    backend = make_backend(backend_name)
    capabilities = backend.capabilities()
    gate = evaluate(capabilities, CapabilityRequirements(), allow_unattested=True)
    attested = bool(gate.attested and not backend.simulated)

    compiler_path = find_compiler()
    if compiler_path is None and not backend.simulated:
        raise InvalidInputError(
            "找不到 g++ 编译器（PATH 中没有 g++）；请先安装 MinGW-w64 并加入 PATH"
        )

    started_at = utc_now()
    compile_summary_refs = {
        "stdout_ref": "build/compile.stdout.txt",
        "stderr_ref": "build/compile.stderr.txt",
    }
    artifact = "program.exe"
    plan = _build_plan(selfcheck_id, stored_source, source_sha, artifact)
    executable = build_dir / artifact

    compile_outcome = backend.compile(
        CompileRequest(
            build_plan=plan,
            compiler=compiler_path or Path("g++"),
            source_units=[stored_source],
            include_dirs=[],
            flags=COMPILER_FLAGS,
            output=executable,
            work_dir=build_dir,
            limits=SELFCHECK_LIMITS,
            env={},
        )
    )
    _write_text(build_dir / "compile.stdout.txt", compile_outcome.stdout)
    _write_text(build_dir / "compile.stderr.txt", compile_outcome.stderr)

    checks: list[CheckOutcome] = []
    compile_summary = CompileSummary(
        status=compile_outcome.status,
        exit_code=compile_outcome.exit_code,
        duration_ms=compile_outcome.duration_ms,
        stdout_ref=compile_summary_refs["stdout_ref"],
        stderr_ref=compile_summary_refs["stderr_ref"],
        artifact_sha256=compile_outcome.artifact_hash,
    )

    if compile_outcome.status is CompileStatus.SUCCESS and executable.is_file():
        for spec in specs:
            checks.append(
                _run_check(
                    spec=spec,
                    backend=backend,
                    executable=executable,
                    cases_dir=cases_dir,
                    limits=SELFCHECK_LIMITS,
                )
            )

    summary = _summary(checks)
    note = REPORT_NOTE
    if compile_summary.status is not CompileStatus.SUCCESS:
        note = (
            "编译未成功，未进入观察阶段。请查看 build/compile.stderr.txt；"
            "本报告不参与五道小改题的评分。"
        )

    report = SelfCheckReport(
        schema_version=SCHEMA_VERSION,
        selfcheck_id=selfcheck_id,
        source_name=source.name,
        source_sha256=source_sha,
        source_kind="reference" if source_kind == "reference" else "upload",
        toolchain_id=toolchain_id(),
        backend_id=capabilities.backend_id,
        sandbox_attested=attested,
        simulated=bool(backend.simulated),
        compile=compile_summary,
        checks=checks,
        summary=summary,
        started_at=started_at,
        finished_at=utc_now(),
        note=note,
    )

    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return SelfCheckResult(report=report, report_path=report_path, run_dir=run_dir)


def load_report(run_dir: Path) -> SelfCheckReport:
    path = Path(run_dir) / "report.json"
    if not path.is_file():
        raise InvalidInputError(f"找不到基准程序实测报告：{path}")
    return SelfCheckReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


def list_recent(data_root: Path | None = None, limit: int = 5) -> list[dict[str, Any]]:
    root = Path(data_root) if data_root is not None else data_dir()
    base = root / SELFCHECK_DIRNAME
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for run_dir in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        path = run_dir / "report.json"
        if not path.is_file():
            continue
        try:
            report = SelfCheckReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        rows.append(
            {
                "selfcheck_id": report.selfcheck_id,
                "source_name": report.source_name,
                "compile_status": report.compile.status.value,
                "passed": report.summary.passed,
                "defects": report.summary.defects,
                "not_observed": report.summary.not_observed,
                "total": report.summary.total,
                "finished_at": report.finished_at,
            }
        )
        if len(rows) >= limit:
            break
    return rows

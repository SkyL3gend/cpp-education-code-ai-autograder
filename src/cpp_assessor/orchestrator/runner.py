"""模式一（标准测评）运行编排器。

工作流严格遵循架构实施方案 §6.1 的模式一固定路径：

    CREATED → INGESTING → RESOLVING_TASK → VALIDATING → FROZEN
            → EXECUTING → REPORTING → COMPLETED

模式一没有 LLM 参与，因此不进入 ANALYZING / ADAPTING / PROBING / CALIBRATING。
编译放在 EXECUTING 内部（§6.1），其失败按"归因 SUBMISSION 的编译错误"
直接判 FAIL（§7.3 判定优先级②），不需要跑任何用例。

本模块是唯一把 domain / tasks / judge / sandbox / storage 拼在一起的地方；
它不拼 shell（参数一律数组），不把 LLM 结论混入判定。
"""

from __future__ import annotations

import re
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..config import (
    ASSESSOR_VERSION,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    compiler_path,
    data_dir,
    task_library_dir,
    toolchain_id,
)
from ..domain.canonical import hash_json, utc_now
from ..domain.enums import (
    BuildRole,
    CompileStatus,
    ComparisonStatus,
    DriverKind,
    ExecutionStatus,
    FaultOwner,
    Mode,
    ObserverKind,
    OracleStatus,
    OverallStatus,
    ProtocolStatus,
    PublicationStatus,
    SourceOrigin,
    Termination,
    ValidationLevel,
    WorkflowState,
)
from ..domain.errors import (
    AssessorError,
    BackendNotAttestedError,
    BudgetExceededError,
    CancelledError,
    InvalidInputError,
)
from ..domain.models import (
    AssertionResult,
    AssessmentReport,
    BuildPlan,
    CaseResult,
    CompileResult,
    ContentRef,
    CoverageReport,
    Observation,
    PlannedCase,
    ReportCounts,
    ReportProvenance,
    ResourceLimits,
    SandboxCapabilities,
    SourceUnit,
    SubmissionManifest,
    TaskCase,
    TestPlan,
    ValidationCheck,
    ValidationRecord,
)
from ..intake.snapshot import ingest
from ..judge.aggregate import aggregate, count_results
from ..judge.assertions import evaluate
from ..judge.coverage import compute_coverage
from ..sandbox.base import CompileOutcome, CompileRequest, RunOutcome, RunRequest
from ..sandbox.gate import evaluate as evaluate_gate
from ..sandbox.local import LocalJobBackend
from ..sandbox.mock import MockBackend
from ..sandbox.win32 import minimal_environment
from ..storage.artifacts import Artifact, ArtifactStore
from ..storage.db import Database
from ..tasks.loader import LoadedTask, compose_subject, load_task, subject_source_units
from .protocol import build_request, parse_response

#: 允许"提交文件覆盖基准工程同名文件"的后缀（源码与头文件）。
_OVERLAY_SUFFIXES = {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh"}

#: 退出码约定（CLI 与脚本共用）。
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_ASSESSED = 2
EXIT_INCONCLUSIVE = 3
EXIT_INFRA = 4
EXIT_CANCELLED = 130


# ---------------------------------------------------------------------------
# 选项与结果
# ---------------------------------------------------------------------------


@dataclass
class RunOptions:
    """一次标准测评的输入参数。"""

    task_ref: str
    submission: Path | None = None
    mode: Mode = Mode.STANDARD
    backend: str = "local"
    allow_draft: bool = False
    allow_unattested: bool = False
    target_before: Path | None = None
    variant: str | None = None
    data_dir: Path | None = None
    task_library: Path | None = None
    run_id: str | None = None


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    workflow_state: WorkflowState
    execution_status: ExecutionStatus
    assessment_status: OverallStatus | None
    grade_eligible: bool
    report: AssessmentReport | None
    exit_code: int
    error: str | None = None


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def make_backend(name: str):
    """按名称构造执行后端。不存在"宿主直跑"兜底。"""
    normalized = name.strip().lower()
    if normalized in {"local", "local_win32_job"}:
        return LocalJobBackend()
    if normalized == "mock":
        return MockBackend()
    raise InvalidInputError(
        f"未知执行后端 {name!r}；可用：local（Win32 Job Object）、mock（仅验证状态机）"
    )


def effective_limits(base: ResourceLimits, override: dict[str, int] | None) -> ResourceLimits:
    """用例级限制覆盖：只允许覆盖 ResourceLimits 已声明的字段。"""
    if not override:
        return base
    unknown = set(override) - set(ResourceLimits.model_fields)
    if unknown:
        raise InvalidInputError(f"用例 limits_override 含未知字段：{sorted(unknown)}")
    return base.model_copy(update=dict(override), deep=True)


def _read_text(path: Path, limit: int = 4 * 1024 * 1024) -> str:
    if not path.is_file():
        return ""
    data = path.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")


def _output_schema_problems(schema: dict[str, Any], actual: Any) -> list[str]:
    """按 IO 模板的 output_schema 校验观测文档（§7.2 的宿主核对步骤）。"""
    try:
        validator = Draft202012Validator(schema)
    except Exception as exc:  # schema 已在任务校验阶段检查，这里只做兜底
        return [f"output_schema 不可用：{exc}"]
    problems: list[str] = []
    for error in sorted(validator.iter_errors(actual), key=lambda item: list(item.path))[:3]:
        location = "/".join(str(part) for part in error.path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def _empty_counts() -> ReportCounts:
    return ReportCounts(
        required_cases=0,
        attempted_cases=0,
        completed_cases=0,
        matched_cases=0,
        mismatched_cases=0,
        not_compared_cases=0,
        required_assertions=0,
        matched_assertions=0,
        mismatched_assertions=0,
        not_compared_assertions=0,
    )


def execute_standard_run(options: RunOptions) -> RunResult:
    """执行一次模式一测评并返回结果（报告始终落盘）。"""
    if options.mode is not Mode.STANDARD:
        raise InvalidInputError("当前原型只实现模式一（standard）；模式二尚未交付")
    if options.variant is None and options.submission is None:
        raise InvalidInputError("缺少 --submission（或校准用 --variant）")

    data_root = Path(options.data_dir) if options.data_dir else data_dir()
    library = Path(options.task_library) if options.task_library else task_library_dir()

    # 任务包校验失败时不会创建 run：没有可测评的对象，直接报错更清楚。
    task = load_task(
        options.task_ref,
        task_library=library,
        allow_draft=options.allow_draft,
    )
    backend = make_backend(options.backend)

    run_id = options.run_id or new_run_id()
    if not _RUN_ID_RE.fullmatch(run_id):
        raise InvalidInputError(
            f"run_id 不合法：{run_id!r}（只允许字母、数字、下划线、点、冒号、短横线）"
        )
    run_dir = data_root / "runs" / run_id
    if run_dir.exists():
        raise InvalidInputError(f"run 目录已存在，拒绝覆盖：{run_dir}")
    store = ArtifactStore(data_root / "runs", run_id)
    db = Database(data_root / "app.sqlite3")

    db.create_run(
        run_id=run_id,
        task_id=task.task_id,
        task_version=task.version,
        task_package_hash=task.package_hash,
        mode=Mode.STANDARD.value,
        backend=backend.backend_id,
        submission_id=None,
    )
    db.upsert_task_version(
        task_id=task.task_id,
        version=task.version,
        package_hash=task.package_hash,
        publication_status=str(task.task_spec.publication_status),
        validation_level=str(task.validation.validation_level),
    )

    runner = _StandardRun(
        options=options,
        task=task,
        backend=backend,
        db=db,
        store=store,
        run_id=run_id,
        run_dir=run_dir,
        data_root=data_root,
    )
    return runner.execute()


# ---------------------------------------------------------------------------
# 单次运行
# ---------------------------------------------------------------------------


class _StandardRun:
    def __init__(
        self,
        *,
        options: RunOptions,
        task: LoadedTask,
        backend: Any,
        db: Database,
        store: ArtifactStore,
        run_id: str,
        run_dir: Path,
        data_root: Path,
    ) -> None:
        self.options = options
        self.task = task
        self.backend = backend
        self.db = db
        self.store = store
        self.run_id = run_id
        self.run_dir = run_dir
        self.data_root = data_root

        self.workflow_state = WorkflowState.CREATED
        self.execution_status = ExecutionStatus.INFRA_ERROR
        # 阶段内部可能已经把执行状态定为编译失败等；此时不能被“正常收尾”覆盖。
        self.execution_status_decided = False
        self.error: str | None = None
        self.started_at = utc_now()
        self.finished_at: str | None = None

        self.manifest: SubmissionManifest | None = None
        self.subject_root: Path | None = None
        self.validation: ValidationRecord | None = None
        self.plan: TestPlan | None = None
        self.build_plan: BuildPlan | None = None
        self.compile_result: CompileResult | None = None
        self.observations: dict[str, Observation] = {}
        self.case_results: list[CaseResult] = []
        self.report: AssessmentReport | None = None
        self.attested = False
        self.evidence_refs: list[str] = []
        self.raw_submission_id: str | None = None
        self.overridden: list[str] = []
        self.added: list[str] = []

    # -- 生命周期 -----------------------------------------------------------
    def execute(self) -> RunResult:
        self._event(WorkflowState.CREATED, "RUN_CREATED", {"backend": self.backend.backend_id})
        try:
            self._phase_ingest()
            self._phase_resolve()
            self._phase_validate()
            self._phase_freeze()
            self._phase_execute()
            if not self.execution_status_decided:
                self.execution_status = ExecutionStatus.COMPLETED
        except CancelledError as exc:
            self.execution_status = ExecutionStatus.CANCELLED
            self.error = str(exc)
            self.workflow_state = WorkflowState.CANCELLED
        except AssessorError as exc:
            # 预期内失败：保存部分报告后正常收尾（§6.1）。
            self.execution_status = exc.execution_status
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 内部异常一律 FAILED
            self.execution_status = ExecutionStatus.INFRA_ERROR
            self.error = f"内部错误：{type(exc).__name__}: {exc}"
            self.workflow_state = WorkflowState.FAILED
            self.store.write_text("evidence", "internal_error.txt", traceback.format_exc())
        finally:
            self._phase_report()

        self.finished_at = utc_now()
        return self._finish()

    # -- 阶段 1：导入 -------------------------------------------------------
    def _phase_ingest(self) -> None:
        self._enter(WorkflowState.INGESTING)
        submissions_root = self.data_root / "submissions"

        target_before_id = None
        if self.options.target_before is not None:
            before = ingest(self.options.target_before, submissions_root=submissions_root)
            target_before_id = before.manifest.submission_id
            self._event(WorkflowState.INGESTING, "TARGET_BEFORE_INGESTED", {"submission_id": target_before_id})

        if self.options.variant is not None:
            self._compose_variant()
        else:
            self._overlay_submission(submissions_root)

        composed_dir = self.run_dir / "generated" / "subject_source"
        composed = ingest(composed_dir, submissions_root=submissions_root)
        manifest = composed.manifest
        if target_before_id is not None:
            manifest = manifest.model_copy(update={"target_before_id": target_before_id})

        self.manifest = manifest
        self.subject_root = composed.root
        self.db.set_submission_id(self.run_id, manifest.submission_id)
        self._save_json(
            "evidence",
            "composition.json",
            {
                "run_id": self.run_id,
                "base_project": self.task.project_root.as_posix(),
                "base_task_package_hash": self.task.package_hash,
                "variant": self.options.variant,
                "raw_submission_id": self.raw_submission_id,
                "overridden_files": self.overridden,
                "added_files": self.added,
                "subject_submission_id": manifest.submission_id,
                "snapshot_hash": manifest.snapshot_hash,
                "file_count": len(manifest.files),
            },
        )
        self._event(
            WorkflowState.INGESTING,
            "SUBJECT_INGESTED",
            {
                "submission_id": manifest.submission_id,
                "snapshot_hash": manifest.snapshot_hash,
                "overridden": self.overridden,
                "added": self.added,
            },
        )

    def _compose_variant(self) -> None:
        assert self.options.variant is not None
        variant_dir = self.task.task_dir / "variants" / self.options.variant
        if not variant_dir.is_dir():
            raise InvalidInputError(f"校准变体不存在：{variant_dir}")
        composed_dir = self.run_dir / "generated" / "subject_source"
        self.overridden = compose_subject(self.task, variant_dir, composed_dir)
        self.added = []
        self._event(
            WorkflowState.INGESTING,
            "VARIANT_COMPOSED",
            {"variant": self.options.variant, "overridden": self.overridden},
        )

    def _overlay_submission(self, submissions_root: Path) -> None:
        assert self.options.submission is not None
        raw = ingest(self.options.submission, submissions_root=submissions_root)
        self.raw_submission_id = raw.manifest.submission_id

        base = self.task.project_root
        composed_dir = self.run_dir / "generated" / "subject_source"
        if composed_dir.exists():
            shutil.rmtree(composed_dir)
        shutil.copytree(base, composed_dir)

        existing: dict[str, Path] = {
            path.relative_to(composed_dir).as_posix().casefold(): path
            for path in composed_dir.rglob("*")
            if path.is_file()
        }
        by_name: dict[str, list[Path]] = {}
        for path in existing.values():
            by_name.setdefault(path.name.casefold(), []).append(path)

        source_files = [
            entry
            for entry in raw.manifest.files
            if Path(entry.path).suffix.lower() in _OVERLAY_SUFFIXES
        ]
        if not source_files:
            raise InvalidInputError(
                "提交内容里没有源码/头文件（.cpp/.h）；请提交与基准工程对应的源码文件"
            )

        overridden: list[str] = []
        added: list[str] = []
        # ingest 的 root 是 <submissions>/<id>，实际文件在 original/ 下。
        raw_root = raw.root / "original"
        if not raw_root.is_dir():
            raw_root = raw.root
        for entry in raw.manifest.files:
            source = raw_root / entry.path
            key = entry.path.casefold()
            target: Path | None = existing.get(key)
            matched_as = entry.path
            if target is None:
                candidates = by_name.get(Path(entry.path).name.casefold(), [])
                if len(candidates) == 1:
                    target = candidates[0]
                    matched_as = target.relative_to(composed_dir).as_posix()
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                overridden.append(matched_as)
            else:
                destination = composed_dir / entry.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                added.append(entry.path)
        self.overridden = sorted(set(overridden))
        self.added = sorted(set(added))

        matched_sources = [p for p in self.overridden if Path(p).suffix.lower() in _OVERLAY_SUFFIXES]
        if not matched_sources:
            raise InvalidInputError(
                "提交的源码文件没有匹配基准工程中的任何文件；"
                "请保持相对路径（如 validators.cpp / contactbook.cpp / contactbook.h）"
            )

    # -- 阶段 2：任务解析（已在 load_task 完成，这里只记录） ------------------
    def _phase_resolve(self) -> None:
        self._enter(WorkflowState.RESOLVING_TASK)
        self._event(
            WorkflowState.RESOLVING_TASK,
            "TASK_RESOLVED",
            {
                "task_id": self.task.task_id,
                "version": self.task.version,
                "package_hash": self.task.package_hash,
                "publication_status": str(self.task.task_spec.publication_status),
                "case_count": len(self.task.task_spec.case_ids),
            },
        )

    # -- 阶段 3：校验 -------------------------------------------------------
    def _phase_validate(self) -> None:
        self._enter(WorkflowState.VALIDATING)
        assert self.manifest is not None and self.subject_root is not None

        build_plan = self._build_build_plan()
        plan = self._build_test_plan(build_plan)
        self.build_plan = build_plan
        self.plan = plan
        self.db.set_plan_hash(self.run_id, plan.plan_hash)

        capabilities = self.backend.capabilities()
        # 模拟后端从不执行代码，按设计放行（但 attested=False）；真实后端
        # 必须满足模板声明的全部隔离能力，否则拒绝执行（§9.3 能力门禁）。
        gate = evaluate_gate(
            capabilities,
            self.task.template.capability_requirements,
            allow_unattested=bool(self.backend.simulated) or self.options.allow_unattested,
        )
        # 校验记录先落盘，再按门禁决定是否继续；否则失败时没有证据。
        checks = [
            ValidationCheck(
                check_id="task_package_valid",
                description="任务包通过静态校验",
                passed=self.task.validation.passed,
                detail="; ".join(c.check_id for c in self.task.validation.failures) or None,
            ),
            ValidationCheck(
                check_id="task_published",
                description="任务版本已发布",
                passed=self.task.task_spec.publication_status is PublicationStatus.PUBLISHED,
                detail=(
                    None
                    if self.task.task_spec.publication_status is PublicationStatus.PUBLISHED
                    else "草稿任务只能用于自查，结论不具备判定资格"
                ),
            ),
            ValidationCheck(
                check_id="subject_sources_present",
                description="被测工程至少有一个编译单元",
                passed=bool(build_plan.source_units),
                detail=None,
            ),
            ValidationCheck(
                check_id="compiler_available",
                description="编译工具链可用",
                passed=bool(self.backend.simulated) or compiler_path() is not None,
                detail=None if compiler_path() else "PATH 中找不到 g++",
            ),
            ValidationCheck(
                check_id="harness_present",
                description="测试驱动文件存在",
                passed=any(unit.role is BuildRole.DRIVER for unit in build_plan.source_units),
                detail=None,
            ),
            ValidationCheck(
                check_id="capability_gate",
                description="执行后端满足模板能力要求",
                passed=gate.allowed,
                detail="; ".join(gate.reasons) or None,
            ),
            ValidationCheck(
                check_id="cases_confirmed",
                description="所有必测用例的 oracle 已确认",
                passed=self._cases_confirmed(),
                detail=None if self._cases_confirmed() else "存在 UNCONFIRMED 用例，结论只能是 INCONCLUSIVE",
            ),
            ValidationCheck(
                check_id="limits_sane",
                description="资源限制取值合法",
                passed=all(
                    case.limits.case_wall_ms > 0 and case.limits.output_bytes > 0
                    for case in plan.cases
                ),
                detail=None,
            ),
            ValidationCheck(
                check_id="submission_matched_project",
                description="提交文件与基准工程匹配",
                passed=bool(self.overridden or self.options.variant is not None),
                detail="; ".join(self.overridden[:8]) or None,
            ),
        ]
        record = ValidationRecord(
            schema_version=SCHEMA_VERSION,
            validation_id=f"val_{self.run_id}",
            run_id=self.run_id,
            plan_hash=plan.plan_hash,
            checks=checks,
            passed=all(check.passed for check in checks),
            validation_level=(
                ValidationLevel.CHECKED
                if all(check.passed for check in checks)
                else ValidationLevel.UNVERIFIED
            ),
        )
        self.validation = record
        self._save_json("plans", "validation.json", record.model_dump(mode="json"))
        self._event(
            WorkflowState.VALIDATING,
            "VALIDATION_RECORDED",
            {
                "passed": record.passed,
                "failed_checks": [c.check_id for c in checks if not c.passed],
                "gate": {"allowed": gate.allowed, "missing": gate.missing},
            },
        )

        if not gate.allowed:
            raise BackendNotAttestedError(
                "执行后端不满足能力门禁，已拒绝真实执行：" + "；".join(gate.reasons)
            )
        self.attested = gate.attested
        if not gate.attested:
            self._event(
                WorkflowState.VALIDATING,
                "BACKEND_UNATTESTED",
                {"reasons": gate.reasons, "capabilities": capabilities.model_dump(mode="json")},
            )

    def _build_build_plan(self) -> BuildPlan:
        assert self.subject_root is not None and self.manifest is not None
        build_src = self.run_dir / "generated" / "subject"
        if build_src.exists():
            shutil.rmtree(build_src)
        # 摄取结果把文件放在 <submission>/original/ 下；构建时只复制源码目录本身，
        # 让 build plan 中的路径与 include_dirs 保持一致（generated/subject/<rel>）。
        source_dir = self.subject_root / "original"
        if not source_dir.is_dir():
            source_dir = self.subject_root
        shutil.copytree(source_dir, build_src)

        harness_dst = self.run_dir / "generated" / "harness"
        if harness_dst.exists():
            shutil.rmtree(harness_dst)
        harness_dst.mkdir(parents=True)

        units: list[SourceUnit] = []
        for index, path in enumerate(subject_source_units(self.task, build_src), start=1):
            units.append(
                SourceUnit(
                    unit_id=f"src{index:02d}",
                    origin=SourceOrigin.SUBMISSION,
                    role=BuildRole.ORIGINAL,
                    path=f"generated/subject/{path.relative_to(build_src).as_posix()}",
                )
            )
        for path in self.task.harness_files:
            shutil.copyfile(path, harness_dst / path.name)
            if path.suffix.lower() == ".cpp":
                units.append(
                    SourceUnit(
                        unit_id=f"driver_{path.stem}",
                        origin=SourceOrigin.GENERATED,
                        role=BuildRole.DRIVER,
                        path=f"generated/harness/{path.name}",
                    )
                )

        plan = BuildPlan(
            schema_version=SCHEMA_VERSION,
            build_plan_id=f"build_{self.run_id}",
            role=BuildRole.ORIGINAL,
            subject_snapshot_hash=self.manifest.snapshot_hash,
            toolchain_id=toolchain_id(),
            source_units=units,
            include_dirs=["generated/subject", "generated/harness"],
            defines=[],
            libraries=[],
            entrypoint=None,
            artifact_name="subject.exe",
            build_hash="0" * 64,
        )
        return plan.model_copy(
            update={
                "build_hash": hash_json(plan.model_dump(mode="json", exclude={"build_hash"}))
            }
        )

    def _build_test_plan(self, build_plan: BuildPlan) -> TestPlan:
        assert self.manifest is not None
        template = self.task.template
        cases: list[PlannedCase] = []
        for case_id in self.task.task_spec.case_ids:
            case = self.task.cases[case_id]
            cases.append(
                PlannedCase(
                    schema_version=SCHEMA_VERSION,
                    case_id=case_id,
                    operation_id=template.operation_id,
                    input_hash=hash_json(case.input),
                    assertion_ids=case.assertion_ids,
                    limits=effective_limits(self.task.document.limits, case.limits_override),
                )
            )
        spec = self.task.task_spec
        plan = TestPlan(
            schema_version=SCHEMA_VERSION,
            plan_id=f"plan_{self.run_id}",
            run_id=self.run_id,
            task_ref=ContentRef(
                id=spec.task_id, version=spec.version, sha256=self.task.package_hash
            ),
            method_ref=spec.test_method_ref,
            template_ref=spec.io_template_ref,
            verifier_ref=spec.reference_verifier_ref,
            subject_snapshot_hash=self.manifest.snapshot_hash,
            operation_id=template.operation_id,
            driver_kind=DriverKind.STDIN_SCRIPT,
            observer_kind=ObserverKind.TRUSTED_EXTRACTOR,
            cases=cases,
            build_plan=build_plan,
            plan_hash="0" * 64,
        )
        return plan.model_copy(
            update={"plan_hash": hash_json(plan.model_dump(mode="json", exclude={"plan_hash"}))}
        )

    def _cases_confirmed(self) -> bool:
        return all(
            self.task.cases[case_id].oracle_status is OracleStatus.CONFIRMED
            for case_id in self.task.task_spec.case_ids
        )

    # -- 阶段 4：冻结计划 ---------------------------------------------------
    def _phase_freeze(self) -> None:
        self._enter(WorkflowState.FROZEN)
        assert self.plan is not None and self.build_plan is not None
        self._save_json("plans", "test_plan.json", self.plan.model_dump(mode="json"))
        self._save_json("plans", "build_plan.json", self.build_plan.model_dump(mode="json"))
        for case_id in self.task.task_spec.case_ids:
            case = self.task.cases[case_id]
            request = build_request(
                run_id=self.run_id,
                attempt_id=self._attempt_id(case_id),
                case_id=case_id,
                operation_id=self.task.template.operation_id,
                case_input=case.input,
            )
            self._save_json("plans/requests", f"{case_id}.json", request)
        self._event(
            WorkflowState.FROZEN,
            "PLAN_FROZEN",
            {"plan_hash": self.plan.plan_hash, "case_count": len(self.plan.cases)},
        )

    # -- 阶段 5：执行 -------------------------------------------------------
    def _phase_execute(self) -> None:
        self._enter(WorkflowState.EXECUTING)
        assert self.plan is not None and self.build_plan is not None
        self._check_cancel()

        compile_result = self._compile()
        self.compile_result = compile_result
        if compile_result.status is not CompileStatus.SUCCESS:
            if compile_result.fault_owner is FaultOwner.SUBMISSION:
                self.execution_status = ExecutionStatus.SUBMISSION_COMPILE_ERROR
            elif compile_result.fault_owner is FaultOwner.INFRA:
                self.execution_status = ExecutionStatus.INFRA_ERROR
            else:
                self.execution_status = ExecutionStatus.BUILD_UNRESOLVED
            self.error = (
                f"编译未成功（{compile_result.status.value}，"
                f"issue_code={compile_result.issue_code}）；用例未执行"
            )
            self.execution_status_decided = True
            return

        exe = self.run_dir / "generated" / "build" / self.build_plan.artifact_name
        deadline = time.monotonic() + self.task.document.limits.total_wall_ms / 1000.0
        for case_id in self.task.task_spec.case_ids:
            self._check_cancel()
            if time.monotonic() > deadline:
                raise BudgetExceededError(
                    f"整次运行超过 total_wall_ms={self.task.document.limits.total_wall_ms}，"
                    "剩余用例未执行"
                )
            self._run_case(case_id, exe)

    def _compile(self) -> CompileResult:
        assert self.build_plan is not None
        build_dir = self.run_dir / "generated" / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        limits = self.task.document.limits

        request = CompileRequest(
            build_plan=self.build_plan,
            compiler=Path(compiler_path() or "g++"),
            source_units=[
                self.run_dir / unit.path for unit in self.build_plan.source_units
            ],
            include_dirs=[self.run_dir / directory for directory in self.build_plan.include_dirs],
            flags=list(self.task.document.build.compiler_flags),
            output=build_dir / self.build_plan.artifact_name,
            work_dir=build_dir,
            limits=limits,
            env=minimal_environment(build_dir),
        )
        started = time.monotonic()
        outcome: CompileOutcome = self.backend.compile(request)
        duration = int((time.monotonic() - started) * 1000)
        self.evidence_refs.append("evidence/compile/stdout.txt")
        self.evidence_refs.append("evidence/compile/stderr.txt")

        result = CompileResult(
            schema_version=SCHEMA_VERSION,
            compile_id=f"compile_{self.run_id}",
            build_hash=self.build_plan.build_hash,
            status=outcome.status,
            exit_code=outcome.exit_code,
            stdout_ref="evidence/compile/stdout.txt",
            stderr_ref="evidence/compile/stderr.txt",
            duration_ms=max(outcome.duration_ms, duration),
            artifact_hash=outcome.artifact_hash,
            fault_owner=outcome.fault_owner,
            issue_code=outcome.issue_code,
        )
        store_stdout = self.store.write_text("evidence/compile", "stdout.txt", outcome.stdout)
        store_stderr = self.store.write_text("evidence/compile", "stderr.txt", outcome.stderr)
        self._record(store_stdout)
        self._record(store_stderr)
        self._save_json("evidence/compile", "compile.json", result.model_dump(mode="json"))
        self._event(
            WorkflowState.EXECUTING,
            "COMPILE_FINISHED",
            {
                "status": outcome.status.value,
                "exit_code": outcome.exit_code,
                "duration_ms": result.duration_ms,
                "fault_owner": outcome.fault_owner.value,
                "issue_code": outcome.issue_code,
            },
        )
        return result

    def _run_case(self, case_id: str, exe: Path) -> None:
        assert self.plan is not None
        case = self.task.cases[case_id]
        limits = effective_limits(self.task.document.limits, case.limits_override)
        case_dir = self.run_dir / "evidence" / "cases" / case_id
        work_dir = case_dir / "work"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)

        request_path = self.run_dir / "plans" / "requests" / f"{case_id}.json"
        stdout_path = case_dir / "stdout.txt"
        stderr_path = case_dir / "stderr.txt"
        attempt_id = self._attempt_id(case_id)

        if self.backend.simulated:
            actual = self.backend.synthesize_actual(case.expected)
            termination = Termination.EXITED
            exit_code = 0
            wall_ms = 0
            events = ["模拟执行：未启动任何进程，观测由用例期望合成"]
            stdout_path.write_text(
                '{"note":"mock backend: no code executed"}\n', encoding="utf-8"
            )
            stderr_path.write_text("", encoding="utf-8")
            parsed = ParsedObservationLike(actual)
            protocol_status = ProtocolStatus.OK
            fault_owner = FaultOwner.NONE
            protocol_detail = "模拟后端：未执行被测代码"
        else:
            self._check_cancel()
            outcome: RunOutcome = self.backend.run(
                RunRequest(
                    executable=exe,
                    argv=[],
                    stdin_path=request_path,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    work_dir=work_dir,
                    limits=limits,
                    env=minimal_environment(work_dir),
                )
            )
            termination = outcome.termination
            exit_code = outcome.exit_code
            wall_ms = outcome.wall_ms
            events = list(outcome.resource_events)
            stdout_text = _read_text(stdout_path, min(limits.output_bytes + 65536, 4 * 1024 * 1024))
            parsed_observation = parse_response(
                stdout_text=stdout_text,
                run_id=self.run_id,
                attempt_id=attempt_id,
                case_id=case_id,
                operation_id=self.task.template.operation_id,
                termination=termination,
            )
            parsed = ParsedObservationLike(parsed_observation.actual)
            protocol_status = parsed_observation.status
            fault_owner = parsed_observation.fault_owner
            protocol_detail = parsed_observation.detail

        if protocol_status is ProtocolStatus.OK:
            schema_problems = _output_schema_problems(
                self.task.template.output_schema, parsed.actual
            )
            if schema_problems:
                protocol_status = ProtocolStatus.INVALID
                fault_owner = FaultOwner.ADAPTER
                protocol_detail = "观测不符合 output_schema：" + "；".join(schema_problems)

        observation = Observation(
            schema_version=SCHEMA_VERSION,
            observation_id=f"obs_{self.run_id}_{case_id}",
            run_id=self.run_id,
            phase=WorkflowState.EXECUTING.value,
            execution_manifest_hash=self.manifest.snapshot_hash if self.manifest else "0" * 64,
            plan_hash=self.plan.plan_hash,
            case_id=case_id,
            attempt_id=attempt_id,
            termination=termination,
            exit_code=exit_code,
            stdout_ref=f"evidence/cases/{case_id}/stdout.txt",
            stderr_ref=f"evidence/cases/{case_id}/stderr.txt",
            files_refs=[],
            wall_ms=wall_ms,
            resource_events=events,
            protocol_status=protocol_status,
            actual=parsed.actual,
            fault_owner=fault_owner,
            protocol_detail=protocol_detail,
        )
        self.observations[case_id] = observation
        self._save_json(
            "evidence/cases", f"{case_id}/observation.json", observation.model_dump(mode="json")
        )
        self.evidence_refs.append(observation.stdout_ref or "")
        self.evidence_refs.append(observation.stderr_ref or "")
        self._event(
            WorkflowState.EXECUTING,
            "CASE_FINISHED",
            {
                "case_id": case_id,
                "termination": termination.value,
                "exit_code": exit_code,
                "protocol_status": protocol_status.value,
                "wall_ms": wall_ms,
            },
        )

    # -- 阶段 6：报告 -------------------------------------------------------
    def _phase_report(self) -> None:
        self.finished_at = self.finished_at or utc_now()
        self._enter(WorkflowState.REPORTING)
        try:
            case_results = self._build_case_results()
            self.case_results = case_results
            coverage = compute_coverage(self.task.method, self.task.cases, case_results)
            counts = count_results(case_results, coverage)
            report = self._assemble_report(coverage, counts, case_results)
            self.report = report
            artifact = self.store.write_json("", "objective_report.json", report.model_dump(mode="json"))
            self._record(artifact)
            self._event(
                WorkflowState.REPORTING,
                "REPORT_WRITTEN",
                {
                    "assessment_status": report.assessment_status.value,
                    "grade_eligible": report.grade_eligible,
                    "report_path": artifact.rel_path,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 报告失败不能掩盖原始错误
            if self.error is None:
                self.error = f"报告生成失败：{type(exc).__name__}: {exc}"
            self.store.write_text("evidence", "report_error.txt", traceback.format_exc())
            self.report = None

    def _assemble_report(
        self,
        coverage: CoverageReport,
        counts: ReportCounts,
        case_results: list[CaseResult],
    ) -> AssessmentReport:
        assert self.plan is not None
        published = self.task.task_spec.publication_status is PublicationStatus.PUBLISHED
        confirmed = self._cases_confirmed()
        validation_level = (
            self.validation.validation_level
            if self.validation is not None
            else ValidationLevel.UNVERIFIED
        )
        aggregation = aggregate(
            execution_status=self.execution_status,
            case_results=case_results,
            coverage=coverage,
            validation_level=validation_level,
            task_published=published,
            assertions_confirmed=confirmed,
            execution_backend_attested=self.attested,
        )
        evidence = list(dict.fromkeys(ref for ref in self.evidence_refs if ref))
        evidence.extend(["plans/test_plan.json", "plans/build_plan.json", "plans/validation.json"])
        note = None
        if getattr(self.backend, "simulated", False):
            note = (
                "本次执行使用模拟后端（execution_backend=mock），未运行任何被测代码；"
                "观测由用例期望合成，仅用于验证状态机与报告链路，不得计入验收。"
            )
        elif not self.attested:
            note = (
                "执行后端未通过隔离能力门禁（缺网络/文件系统隔离等），"
                "报告如实标注 grade_eligible=false；能力补齐后可重新测评。"
            )
        return AssessmentReport(
            schema_version=SCHEMA_VERSION,
            run_id=self.run_id,
            mode=Mode.STANDARD,
            execution_status=self.execution_status,
            assessment_status=aggregation.assessment_status,
            grade_eligible=aggregation.grade_eligible,
            grade_eligible_reasons=aggregation.reasons,
            validation_level=validation_level,
            coverage=coverage,
            case_results=case_results,
            counts=counts,
            evidence_refs=evidence,
            provenance=ReportProvenance(
                execution_backend=self.backend.backend_id,
                sandbox_attested=self.attested,
                task_id=self.task.task_id,
                task_version=self.task.version,
                task_package_hash=self.task.package_hash,
                submission_id=self.manifest.submission_id if self.manifest else None,
                toolchain_id=toolchain_id(),
                started_at=self.started_at,
                finished_at=self.finished_at or utc_now(),
                assessor_version=ASSESSOR_VERSION,
                protocol_version=PROTOCOL_VERSION,
            ),
            observations_note=note,
        )

    def _build_case_results(self) -> list[CaseResult]:
        results: list[CaseResult] = []
        for case_id in self.task.task_spec.case_ids:
            case = self.task.cases[case_id]
            observation = self.observations.get(case_id)
            if observation is None:
                results.append(
                    CaseResult(
                        schema_version=SCHEMA_VERSION,
                        case_id=case_id,
                        attempt_id=self._attempt_id(case_id),
                        purpose=case.purpose,
                        comparison=ComparisonStatus.NOT_COMPARED,
                        termination=Termination.NOT_RUN,
                        protocol_status=ProtocolStatus.NOT_APPLICABLE,
                        assertion_results=[],
                        failed_assertions=[],
                        evidence_refs=[],
                        note="NOT_RUN",
                    )
                )
                continue
            results.append(self._judge_case(case, observation))
        return results

    def _judge_case(self, case: TaskCase, observation: Observation) -> CaseResult:
        evidence = [
            f"evidence/cases/{case.case_id}/observation.json",
            observation.stdout_ref or "",
            observation.stderr_ref or "",
        ]
        evidence = [ref for ref in evidence if ref]

        if observation.protocol_status is not ProtocolStatus.OK:
            note = {
                FaultOwner.ADAPTER: "ADAPTER_FAULT",
                FaultOwner.INFRA: "INFRA_FAULT",
                FaultOwner.SUBMISSION: "SUBJECT_FAULT",
            }.get(observation.fault_owner, "UNKNOWN_FAULT")
            return CaseResult(
                schema_version=SCHEMA_VERSION,
                case_id=case.case_id,
                attempt_id=observation.attempt_id,
                purpose=case.purpose,
                comparison=ComparisonStatus.NOT_COMPARED,
                termination=observation.termination,
                protocol_status=observation.protocol_status,
                assertion_results=[],
                failed_assertions=[],
                evidence_refs=evidence,
                observation_id=observation.observation_id,
                note=note,
            )

        assertion_results: list[AssertionResult] = []
        for assertion_id in case.assertion_ids:
            spec = self.task.assertions[assertion_id]
            outcome = evaluate(
                spec,
                case_expected=case.expected,
                actual_document=observation.actual or {},
            )
            assertion_results.append(
                AssertionResult(
                    schema_version=SCHEMA_VERSION,
                    assertion_id=assertion_id,
                    case_id=case.case_id,
                    operator=spec.operator,
                    comparison=outcome.comparison,
                    expected_source=spec.expected_source,
                    expected=outcome.expected,
                    actual=outcome.actual,
                    detail=outcome.detail,
                    evidence_ref=f"evidence/cases/{case.case_id}/observation.json",
                )
            )

        comparisons = [result.comparison for result in assertion_results]
        if all(item is ComparisonStatus.MATCH for item in comparisons):
            comparison = ComparisonStatus.MATCH
        elif any(item is ComparisonStatus.MISMATCH for item in comparisons):
            comparison = ComparisonStatus.MISMATCH
        else:
            comparison = ComparisonStatus.NOT_COMPARED
        failed = [
            result.assertion_id
            for result in assertion_results
            if result.comparison is ComparisonStatus.MISMATCH
        ]
        note = None
        if comparison is ComparisonStatus.NOT_COMPARED:
            note = "OBSERVATION_INCOMPLETE"
        return CaseResult(
            schema_version=SCHEMA_VERSION,
            case_id=case.case_id,
            attempt_id=observation.attempt_id,
            purpose=case.purpose,
            comparison=comparison,
            termination=observation.termination,
            protocol_status=observation.protocol_status,
            assertion_results=assertion_results,
            failed_assertions=failed,
            evidence_refs=evidence,
            observation_id=observation.observation_id,
            note=note,
        )

    # -- 收尾 ---------------------------------------------------------------
    def _finish(self) -> RunResult:
        assessment_status = self.report.assessment_status if self.report else None
        report_path = "objective_report.json" if self.report else None
        if self.workflow_state is not WorkflowState.CANCELLED and self.workflow_state is not WorkflowState.FAILED:
            self.workflow_state = WorkflowState.COMPLETED

        if self.report is not None:
            workflow = WorkflowState.COMPLETED.value
        elif self.workflow_state is WorkflowState.CANCELLED:
            workflow = WorkflowState.CANCELLED.value
        else:
            workflow = WorkflowState.FAILED.value

        self.db.finish_run(
            self.run_id,
            workflow_state=workflow,
            execution_status=self.execution_status.value,
            assessment_status=assessment_status.value if assessment_status else None,
            grade_eligible=self.report.grade_eligible if self.report else None,
            report_path=report_path,
            error=self.error,
        )
        self._event(
            WorkflowState.COMPLETED if self.report else self.workflow_state,
            "RUN_FINISHED",
            {
                "workflow_state": workflow,
                "execution_status": self.execution_status.value,
                "assessment_status": assessment_status.value if assessment_status else None,
                "error": self.error,
            },
        )
        return RunResult(
            run_id=self.run_id,
            run_dir=self.run_dir,
            workflow_state=WorkflowState(workflow),
            execution_status=self.execution_status,
            assessment_status=assessment_status,
            grade_eligible=self.report.grade_eligible if self.report else False,
            report=self.report,
            exit_code=self._exit_code(assessment_status),
            error=self.error,
        )

    def _exit_code(self, assessment_status: OverallStatus | None) -> int:
        if self.workflow_state is WorkflowState.CANCELLED:
            return EXIT_CANCELLED
        if self.execution_status is ExecutionStatus.CANCELLED:
            return EXIT_CANCELLED
        if self.execution_status is ExecutionStatus.INFRA_ERROR:
            return EXIT_INFRA
        if assessment_status is OverallStatus.PASS:
            return EXIT_PASS
        if assessment_status is OverallStatus.FAIL:
            return EXIT_FAIL
        if assessment_status is OverallStatus.INCONCLUSIVE:
            return EXIT_INCONCLUSIVE
        return EXIT_NOT_ASSESSED

    # -- 小工具 -------------------------------------------------------------
    def _attempt_id(self, case_id: str) -> str:
        return f"{case_id}-a1"

    def _enter(self, state: WorkflowState) -> None:
        self.workflow_state = state
        self.db.set_state(self.run_id, state.value)
        self.db.append_event(self.run_id, state.value, "STATE", None)

    def _event(self, state: WorkflowState, kind: str, payload: dict[str, Any] | None = None) -> None:
        self.db.append_event(self.run_id, state.value, kind, payload)

    def _save_json(self, kind: str, name: str, payload: Any) -> Artifact:
        artifact = self.store.write_json(kind, name, payload)
        self._record(artifact)
        return artifact

    def _record(self, artifact: Artifact) -> None:
        self.db.add_artifact(
            artifact_id=artifact.artifact_id,
            run_id=self.run_id,
            kind=artifact.kind,
            rel_path=artifact.rel_path,
            sha256=artifact.sha256,
            size=artifact.size,
        )

    def _check_cancel(self) -> None:
        if (self.run_dir / "CANCEL").is_file():
            raise CancelledError("收到取消请求")


# `parse_response` 的返回值与 mock 分支共用一个极小的取 actual 接口。
@dataclass(frozen=True)
class ParsedObservationLike:
    actual: Any

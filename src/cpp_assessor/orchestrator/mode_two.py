"""模式二（LLM 适配）运行编排器。

模式二回答的是"学生的程序形态和基准工程不一样，怎么测"：

1. **分析**：把任务包声明的契约（规则、输入输出模板、断言、分区、驱动/观测约束）
   与学生的实际文件清单一起交给 LLM。
2. **生成声明**：LLM 只产出一份 ``TargetVerifierBundle``——目标清单、编译方式、驱动方式
   （stdin 脚本或函数驱动）、观测提取器与参数、断言绑定、分区迁移矩阵。它不写判据、
   不接触期望值。
3. **校验**：宿主对 bundle 做静态校验（结构、占位符、提取器、断言绑定、迁移矩阵、
   禁止字段），不合格就带着问题再问一次（有界修正）。
4. **试跑**：按声明编译，用第一个用例做一次探针，确认"驱动能跑、提取器能读出事实"。
   失败只修适配层（驱动/参数），不碰判据。
5. **冻结与执行**：冻结计划后逐个用例执行，事实交给确定性断言引擎比较；
   观测不到就是 NOT_COMPARED，不猜、不判学生错。
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import (
    ASSESSOR_VERSION,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    compiler_path,
    data_dir,
    task_library_dir,
    toolchain_id,
)
from ..domain.canonical import hash_json
from ..domain.enums import (
    BuildRole,
    CasePurpose,
    ComparisonStatus,
    DriverKind,
    ExecutionStatus,
    FaultOwner,
    Mode,
    ObserverKind,
    OracleStatus,
    ProtocolStatus,
    PublicationStatus,
    SourceOrigin,
    Termination,
    ValidationLevel,
    WorkflowState,
)
from ..domain.errors import AssessorError, BackendNotAttestedError, InvalidInputError
from ..domain.models import (
    AssessmentReport,
    BuildPlan,
    CaseResult,
    CoverageReport,
    Observation,
    PlannedCase,
    ReportCounts,
    ReportProvenance,
    SourceUnit,
    TestPlan,
)
from ..intake.snapshot import ingest
from ..judge.aggregate import aggregate, count_results
from ..judge.assertions import evaluate
from ..judge.coverage import compute_coverage
from ..judge.extractors import describe_extractors, extract, extractor_ids
from ..llm.provider import LLMClient, LLMError, LLMSettings
from ..planning.bundle import (
    TargetVerifierBundle,
    assertion_ids_for_case,
    binding_map,
    validate_bundle,
)
from ..sandbox.base import CompileRequest, RunRequest
from ..sandbox.gate import evaluate as evaluate_gate
from ..sandbox.win32 import minimal_environment
from ..storage.artifacts import Artifact, ArtifactStore
from ..storage.db import Database
from ..tasks.loader import LoadedTask, load_task
from .protocol import build_request, parse_response
from .runner import (
    EXIT_CANCELLED,
    EXIT_FAIL,
    EXIT_INCONCLUSIVE,
    EXIT_INFRA,
    EXIT_NOT_ASSESSED,
    EXIT_PASS,
    RunOptions,
    RunResult,
    effective_limits,
    make_backend,
    new_run_id,
)

_SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"}
_COMPILE_SUFFIXES = {".cpp", ".cc", ".cxx"}
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]-]+)\s*\}\}")
_PROMPT_FILE_LIMIT = 120_000
_MAX_REPAIRS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_text(path: Path, limit: int = 4 * 1024 * 1024) -> str:
    data = path.read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_output(path: Path, limit: int = 4 * 1024 * 1024) -> str:
    return _read_text(path, limit) if path.is_file() else ""


def _resolve(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(dotted)
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(dotted)
    return current


def _substitute(value: Any, case_input: dict[str, Any], warnings: list[str]) -> Any:
    """把 ``{{candidate.name}}`` 换成用例输入里的真实值。"""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            path = match.group(1)
            try:
                resolved = _resolve(case_input, path)
            except (KeyError, ValueError, IndexError):
                warnings.append(f"会话占位符 {{{{ {path} }}}} 在用例输入里没有对应值，已替换为空串")
                return ""
            if isinstance(resolved, bool):
                return "1" if resolved else "0"
            return str(resolved)

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_substitute(item, case_input, warnings) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, case_input, warnings) for key, item in value.items()}
    return value


def _skill_text() -> str:
    root = Path(__file__).resolve().parents[3]
    skill = root / "skills" / "cpp-microchange-judge-author" / "SKILL.md"
    if skill.is_file():
        return _read_text(skill)
    return "你是 C++ 小改题判别系统的适配作者，只输出一个 TargetVerifierBundle JSON。"


@dataclass
class _State:
    run_id: str
    run_dir: Path
    task: LoadedTask
    backend: Any
    options: RunOptions
    manifest: Any = None
    files: dict[str, str] = field(default_factory=dict)
    bundle: TargetVerifierBundle | None = None
    validation: Any = None
    build_plan: BuildPlan | None = None
    compile_outcome: Any = None
    exe: Path | None = None
    case_results: list[CaseResult] = field(default_factory=list)
    observations: dict[str, Observation] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repaired: int = 0
    llm_rounds: int = 0
    attested: bool = False
    started_at: str = ""
    finished_at: str = ""
    execution_status: ExecutionStatus | None = None
    report: AssessmentReport | None = None
    error: str | None = None
    subject_files: list[str] = field(default_factory=list)
    entry_files: list[str] = field(default_factory=list)


class ModeTwoRun:
    def __init__(self, options: RunOptions, settings: LLMSettings) -> None:
        if options.mode is not Mode.LLM:
            raise InvalidInputError("execute_llm_run 只接受 Mode.LLM")
        if options.submission is None:
            raise InvalidInputError("模式二需要 --submission")
        data_root = (Path(options.data_dir) if options.data_dir else data_dir()).resolve()
        library = (Path(options.task_library) if options.task_library else task_library_dir()).resolve()
        task = load_task(
            options.task_ref,
            task_library=library,
            allow_draft=options.allow_draft,
        )
        self.data_root = data_root
        self.library = library
        self.settings = settings
        self.client = LLMClient(settings, log_dir=data_root / "logs")
        run_id = options.run_id or new_run_id()
        self.state = _State(
            run_id=run_id,
            run_dir=data_root / "runs" / run_id,
            task=task,
            backend=make_backend(options.backend),
            options=options,
        )
        state = self.state
        self.state.run_dir.mkdir(parents=True, exist_ok=True)
        for name in ("evidence", "plans", "generated", "submissions"):
            (state.run_dir / name).mkdir(parents=True, exist_ok=True)
        self._probe_outcomes: dict[str, Any] = {}
        self.events_path = state.run_dir / "events.jsonl"
        self.db = Database(data_root / "assessor.db")
        state.started_at = _now()

    # ------------------------------------------------------------------ 公共入口
    def execute(self) -> RunResult:
        state = self.state
        self.db.create_run(
            run_id=state.run_id,
            task_id=state.task.task_id,
            task_version=str(state.task.version),
            task_package_hash=state.task.package_hash,
            mode=Mode.LLM,
            backend=str(state.backend.backend_id),
            submission_id=None,
        )
        try:
            self._event("RUN_STARTED", {"mode": "llm", "task": state.task.task_id})
            self._ingest()
            self._check_capabilities()
            self._plan()
            self._build_and_probe()
            if state.bundle is None:
                raise AssessorError("适配声明未生成")
            if state.exe is not None:
                self._execute_cases()
            self._report()
        except Exception as exc:  # noqa: BLE001 - 都要落成报告
            state.finished_at = _now()
            state.execution_status = (
                state.execution_status or ExecutionStatus.ADAPTATION_FAILED
            )
            state.error = str(exc)
            self._event("RUN_FAILED", {"error": str(exc), "type": type(exc).__name__})
            self._write_failure_report(exc)
        state.finished_at = state.finished_at or _now()
        report = getattr(state, "report", None)
        execution = getattr(state, "execution_status", ExecutionStatus.ADAPTATION_FAILED)
        if report is None:
            raise AssessorError(getattr(state, "error", None) or "模式二没有产出报告")
        self.db.finish_run(
            state.run_id,
            workflow_state=WorkflowState.COMPLETED,
            execution_status=execution,
            assessment_status=report.assessment_status,
            grade_eligible=report.grade_eligible,
            report_path=str(state.run_dir / "objective_report.json"),
            error=getattr(state, "error", None),
        )
        self._event("RUN_FINISHED", {"assessment": report.assessment_status.value})
        return RunResult(
            run_id=state.run_id,
            run_dir=state.run_dir,
            workflow_state=WorkflowState.COMPLETED,
            execution_status=execution,
            assessment_status=report.assessment_status,
            grade_eligible=report.grade_eligible,
            report=report,
            exit_code=EXIT_CODES[report.assessment_status],
            error=getattr(state, "error", None),
        )

    # ------------------------------------------------------------------ 阶段 1：摄入
    def _ingest(self) -> None:
        state = self.state
        result = ingest(state.options.submission, state.run_dir / "submissions")
        state.manifest = result.manifest
        self._event("SUBMISSION_INGESTED", {"files": len(result.manifest.files)})
        for entry in result.manifest.files:
            source = result.root / "original" / entry.path
            if entry.path not in state.files:
                state.files[entry.path] = _read_text(source)

    # ------------------------------------------------------------------ 能力门禁
    def _check_capabilities(self) -> None:
        state = self.state
        gate = evaluate_gate(
            state.backend.capabilities(),
            state.task.template.capability_requirements,
            allow_unattested=bool(state.backend.simulated) or state.options.allow_unattested,
        )
        state.attested = gate.attested
        self._check("capability_gate", gate.allowed, "；".join(gate.reasons) or "后端能力满足")
        if not gate.allowed:
            raise BackendNotAttestedError(
                "执行后端不满足能力门禁，已拒绝真实执行：" + "；".join(gate.reasons)
            )

    # ------------------------------------------------------------------ 阶段 2：生成
    def _plan(self) -> None:
        state = self.state
        task = state.task
        system = _skill_text()
        user = self._prompt_payload()
        last_error = ""
        for attempt in range(_MAX_REPAIRS + 1):
            state.llm_rounds += 1
            try:
                response = self.client.complete_json(
                    system=system,
                    user=user if not last_error else user + "\n\n" + last_error,
                    call_name=f"bundle_{state.task.task_id}",
                    max_tokens=self.settings.max_tokens,
                )
            except LLMError as exc:
                raise AssessorError(f"LLM 调用失败：{exc}") from exc
            bundle = self._parse_bundle(response.data)
            if bundle is None:
                parse_error = str(response.data.get("__parse_error__", "未知"))
                last_error = (
                    "上一次输出无法解析成 TargetVerifierBundle。"
                    f"解析错误：{parse_error}。请严格按 schema 重发。"
                )
                self._persist_json(
                    f"plans/llm/attempt_{attempt}.json",
                    {"error": parse_error, "response": response.data},
                )
                self._event(
                    "BUNDLE_PARSE_FAILED", {"attempt": attempt, "error": parse_error[:400]}
                )
                continue
            state.bundle = bundle
            self._persist_json("plans/target_bundle.json", bundle.model_dump())
            validation = validate_bundle(
                bundle,
                task,
                state.files,
                extractor_ids=extractor_ids(),
                case_inputs={cid: task.cases[cid].input for cid in task.task_spec.case_ids},
            )
            state.validation = validation
            state.warnings.extend(validation.warnings)
            if validation.passed:
                self._event(
                    "BUNDLE_VALIDATED",
                    {"level": validation.level, "warnings": len(validation.warnings)},
                )
                return
            last_error = (
                "上一次输出没有通过宿主静态校验，请修正后重新输出完整 JSON。问题：\n- "
                + "\n- ".join(validation.problems)
            )
            self._event(
                "BUNDLE_INVALID", {"attempt": attempt, "problems": validation.problems[:6]}
            )
        bundle = state.bundle
        if bundle is not None and bundle.status == "UNSUPPORTED":
            reason = (bundle.notes or "模型认为这个程序无法按本题契约可靠观察").strip()
            raise AssessorError(f"这道题无法在当前程序上可靠观察：{reason}")
        detail = (
            "；".join(state.validation.problems[:4])
            if state.validation is not None
            else last_error
        )
        raise AssessorError("LLM 生成的适配声明多次未通过校验：" + detail)

    def _parse_bundle(self, data: dict[str, Any]) -> TargetVerifierBundle | None:
        try:
            return TargetVerifierBundle.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            data["__parse_error__"] = str(exc).splitlines()[0]
            return None

    def _prompt_payload(self) -> str:
        state = self.state
        task = state.task
        document = task.document
        spec = document.task_spec
        inventory = []
        total = 0
        for path, text in state.files.items():
            suffix = Path(path).suffix.lower()
            if suffix not in _SOURCE_SUFFIXES:
                inventory.append({"path": path, "chars": len(text), "source": False})
                continue
            if total + len(text) > _PROMPT_FILE_LIMIT:
                inventory.append({"path": path, "chars": len(text), "source": False})
                continue
            total += len(text)
            inventory.append({"path": path, "chars": len(text), "source": True, "text": text})

        payload = {
            "task": {
                "task_id": spec.task_id,
                "version": spec.version,
                "title": spec.title,
                "description": spec.description,
                "rules": [{"rule_id": rule.rule_id, "statement": rule.statement} for rule in spec.rules],
                "subject_level": task.method.subject_level.value,
                "case_ids": spec.case_ids,
                "input_schema": task.template.input_schema,
                "output_schema": task.template.output_schema,
                "assertions": [
                    {
                        "assertion_id": item.assertion_id,
                        "rule_ids": item.rule_ids,
                        "operator": item.operator.value,
                        "actual_observation_key": item.actual_observation_key,
                        "required": item.required,
                    }
                    for item in task.assertions.values()
                ],
                "input_partitions": [
                    item.model_dump() for item in task.method.input_partitions
                ],
                "compiler_flags": list(document.build.compiler_flags),
            },
            "extractors": describe_extractors(),
            "target_files": inventory,
            "cases": [
                {"case_id": case_id, "input": task.cases[case_id].input}
                for case_id in spec.case_ids
            ],
            "requirements": {
                "bundle_schema": "目标清单 target / 编译 build / 驱动 driver / 观测 observer / 断言绑定 assertion_bindings / 迁移矩阵 migration_matrix",
                "must_not_include": ["expected", "answer", "verdict", "reference outputs"],
                "stdin_placeholders": "会话字符串可写 {{candidate.name}} 这类占位符，由宿主用用例输入替换",
                "stdin_per_case": "不同用例需要不同输入序列时，用 driver.session_by_case 按 case_id 覆盖 session；脚本必须与程序真实提示顺序一致（含确认分支）",
                "function_driver": "driver_source 必须能读 stdin 的请求 JSON，最后一行输出 {\"protocol_version\":\"1.0\",\"status\":\"OK\",\"actual\":{...}}",
                "extractor_params": "observer.params 也用同一套占位符；提取器只输出事实，不做比较",
                "observation_first": "剧本必须走到能读出事实的界面（列表/搜索/提醒/统计），只看到“添加成功”这类提示不算观测；菜单程序添加后通常要先回上一层再进查找界面",
                "script_from_target": "剧本要按目标程序实际打印的菜单顺序逐行写：添加成功后是回主菜单还是回添加子菜单、检索是几号键、有没有类型/城市/地址等中间提问，都以目标程序的输出为准，不要照抄提示里的示例（示例来自另一份程序）",
                "stdin_values_only": "剧本里只放输入值，不要复制程序打印的提示语（以 * 开头或以 ：结尾的字符串），否则静态校验会拒绝",
                "script_end": "剧本最后要逐层返回并真正退出：搜索/列表界面按 0 退回上一级，回到主菜单后再按 0 退出；通常末尾需要 2~3 个 0。停在菜单上会因 stdin 结束而无限循环直到超时",
                "startup_capacity": "程序启动时若先问容量/最大条数，给一个明显大于种子数据的常数（例如 200）；容量不足会导致添加被拒，别按当前联系人数猜",
                "reprompt_loop": "业务非法输入若让程序进入“请重新输入”循环，剧本要给一个固定合法的兜底值让程序继续走到可观测界面；观察程序最终是否接受了那条非法输入（接受即缺陷），不要停在被循环卡住的位置",
                "seed_initial_contacts": "菜单程序不能导入用例的 initial_contacts：如果用例声明了初始联系人，用 driver.seed_session 写一段“新建一个联系人”的输入序列，占位符固定为 {{seed.name}} / {{seed.phone}} / {{seed.birthday.year}} / {{seed.birthday.month}} / {{seed.birthday.day}}，宿主会对每个初始联系人重复一遍并接在 session 前面；seed_session 必须覆盖程序新建联系人过程中的每一次提问（中间的类型菜单也要占一行），最后只写一个 0（回主菜单），不要连写两个 0（会退出程序）；程序自带演示联系人列进 baseline_names 排除",
                "extractor_choice": "按题目选提取器：是否添加成功/存了什么用 contactbook_menu_v1；生日提醒名单用 birthday_menu_v1；搜索命中用 keyword_search_v1；联系人编号用 contact_ids_v1；程序自带演示联系人列进 baseline_names",
                "subject_level": "task.subject_level 描述基准工程的接口层级，不约束目标程序；目标是菜单程序时选择 stdin_script，不要因为 subject_level=FUNCTION 就返回 UNSUPPORTED",
                "unsupported": "只有在目标程序没有任何可确定复现的 stdin 路径时才返回 status=UNSUPPORTED",
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------ 阶段 3-4：编译与探针
    def _build_and_probe(self) -> None:
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        self._prepare_subject(bundle)
        self._compile(bundle)
        if state.compile_outcome is None or state.exe is None:
            return
        probe_case_id = self._probe_case_id()
        for attempt in range(_MAX_REPAIRS + 1):
            failures = self._probe(probe_case_id)
            if not failures:
                self._event("PROBE_OK", {"case_id": probe_case_id})
                return
            if attempt >= _MAX_REPAIRS:
                raise AssessorError("适配探针多次未通过：" + "；".join(failures[:3]))
            self._event("PROBE_FAILED", {"attempt": attempt, "problems": failures[:3]})
            repair = self._repair("探针失败，请修正驱动/观测声明（不要改判据）。", failures)
            if repair is None:
                raise AssessorError("适配探针失败且无法修正：" + "；".join(failures[:3]))
            bundle = repair
            state.bundle = bundle
            self._persist_json("plans/target_bundle.json", bundle.model_dump())
            self._prepare_subject(bundle)
            self._compile(bundle)

    def _prepare_subject(self, bundle: TargetVerifierBundle) -> None:
        state = self.state
        subject = state.run_dir / "generated" / "subject"
        if subject.exists():
            shutil.rmtree(subject)
        subject.mkdir(parents=True, exist_ok=True)
        origin = state.run_dir / "submissions" / state.manifest.submission_id / "original"
        # function_driver 自带 main()，要排除程序自己的入口；stdin_script 反而需要入口文件。
        excluded = (
            list(state.task.document.build.exclude_globs or [])
            if bundle.driver.kind == "function_driver"
            else []
        )
        included: list[str] = []
        for entry in state.manifest.files:
            path = entry.path
            if Path(path).suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            posix = Path(path).as_posix()
            if any(
                fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(Path(posix).name, pattern)
                for pattern in excluded
            ):
                continue
            target = subject / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin / path, target)
            included.append(path)
        state.subject_files = included  # type: ignore[attr-defined]
        state.entry_files = [
            path for path in included if Path(path).suffix.lower() in _COMPILE_SUFFIXES
        ]  # type: ignore[attr-defined]
        self._persist_json(
            "plans/subject_inventory.json",
            {"files": included, "compile_units": state.entry_files},  # type: ignore[attr-defined]
        )

    def _compile(self, bundle: TargetVerifierBundle) -> None:
        state = self.state
        build_dir = state.run_dir / "generated" / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        driver_dir = state.run_dir / "generated" / "driver"
        if driver_dir.exists():
            shutil.rmtree(driver_dir)
        driver_dir.mkdir(parents=True, exist_ok=True)

        units: list[SourceUnit] = []
        compile_paths: list[Path] = []
        subject = state.run_dir / "generated" / "subject"
        for index, rel in enumerate(state.entry_files):  # type: ignore[attr-defined]
            units.append(
                SourceUnit(
                    unit_id=f"src{index:02d}",
                    origin=SourceOrigin.SUBMISSION,
                    role=BuildRole.ORIGINAL,
                    path=f"generated/subject/{rel}",
                )
            )
            compile_paths.append(subject / rel)
        include_dirs = ["generated/subject"]
        if bundle.driver.kind == "stdin_script":
            driver_kind = DriverKind.STDIN_SCRIPT
            observer_kind = ObserverKind.TRUSTED_EXTRACTOR
        else:
            driver_path = driver_dir / "driver.cpp"
            driver_path.write_text(bundle.driver.driver_source, encoding="utf-8")
            units.append(
                SourceUnit(
                    unit_id="driver",
                    origin=SourceOrigin.GENERATED,
                    role=BuildRole.DRIVER,
                    path="generated/driver/driver.cpp",
                )
            )
            compile_paths.append(driver_path)
            include_dirs.append("generated/driver")
            include_dirs.append("generated/subject")
            driver_kind = DriverKind.FUNCTION_DRIVER
            observer_kind = ObserverKind.TRUSTED_EXTRACTOR

        plan = BuildPlan(
            schema_version=SCHEMA_VERSION,
            build_plan_id=f"build_{state.run_id}",
            role=BuildRole.ORIGINAL,
            subject_snapshot_hash=state.manifest.snapshot_hash,
            toolchain_id=document_toolchain(state.task),
            source_units=units,
            include_dirs=list(dict.fromkeys(include_dirs)),
            defines=list(bundle.build.defines),
            libraries=list(bundle.build.libraries),
            entrypoint=None,
            artifact_name=bundle.build.artifact_name,
            build_hash=hash_json(
                {"units": [unit.model_dump() for unit in units], "task": state.task.package_hash}
            ),
        )
        state.build_plan = plan
        self._persist_json("plans/build_plan.json", plan.model_dump())
        self._event(
            "PLAN_BUILD",
            {"driver": driver_kind.value, "sources": [str(path.name) for path in compile_paths]},
        )

        artifact = state.run_dir / "generated" / "build" / plan.artifact_name
        request = CompileRequest(
            build_plan=plan,
            compiler=Path(compiler_path() or "g++"),
            source_units=compile_paths,
            include_dirs=[state.run_dir / name for name in plan.include_dirs],
            flags=list(bundle.build.compiler_flags) or list(state.task.document.build.compiler_flags),
            output=artifact,
            work_dir=build_dir,
            limits=state.task.document.limits,
            env=minimal_environment(build_dir),
        )
        outcome = state.backend.compile(request)
        state.compile_outcome = outcome
        evidence_dir = state.run_dir / "evidence" / "compile"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "stdout.txt").write_text(outcome.stdout or "", encoding="utf-8", errors="replace")
        (evidence_dir / "stderr.txt").write_text(outcome.stderr or "", encoding="utf-8", errors="replace")
        self._persist_json("evidence/compile/compile.json", {
            "status": getattr(outcome.status, "value", str(outcome.status)),
            "exit_code": outcome.exit_code,
            "fault_owner": getattr(outcome.fault_owner, "value", str(outcome.fault_owner)),
        })
        self._evidence("evidence/compile/compile.json")
        self._evidence("evidence/compile/stdout.txt")
        self._evidence("evidence/compile/stderr.txt")
        self.driver_kind = driver_kind
        self.observer_kind = observer_kind
        if getattr(outcome.status, "value", "") != "SUCCESS":
            state.exe = None
            state.execution_status = ExecutionStatus.SUBMISSION_COMPILE_ERROR
            self._event("COMPILE_FAILED", {"exit_code": outcome.exit_code})
            return
        state.exe = artifact
        self._event("COMPILE_OK", {"artifact": artifact.name})

    # ------------------------------------------------------------------ 探针
    def _probe_case_id(self) -> str:
        return self.state.task.task_spec.case_ids[0]

    def _probe(self, case_id: str) -> list[str]:
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        case = state.task.cases[case_id]
        try:
            stdout = self._run_case_stream(case_id, case, probe=True)
        except AssessorError as exc:
            return [str(exc)]
        outcome = self._probe_outcomes.get(case_id)
        if outcome is not None and outcome.termination is not Termination.EXITED:
            tail = stdout[-300:].replace("\r", "")
            return [
                f"程序没有正常退出（termination={outcome.termination.value}），剧本末尾缺少返回/退出步骤。"
                "菜单程序读完事实后要逐层按 0 返回：搜索/列表界面 0 → 列表 0 → 主菜单 0；"
                "如果停在菜单上，程序会因为标准输入结束而无限循环直到超时。"
                f"输出末尾：{json.dumps(tail, ensure_ascii=False)}"
            ]
        if bundle.driver.kind == "function_driver":
            parsed = parse_response(
                stdout_text=stdout,
                run_id=state.run_id,
                attempt_id=f"probe_{case_id}",
                case_id=case_id,
                operation_id=state.task.template.operation_id,
                termination=Termination.EXITED,
            )
            if parsed.status is not ProtocolStatus.OK:
                return [f"函数驱动输出不符合协议：{parsed.detail}"]
            return []
        try:
            facts = extract(
                bundle.observer.extractor_id,
                stdout,
                self._observer_params(case.input),
            )
        except KeyError as exc:
            return [f"提取器不可用：{exc}"]
        missing = [key for key in _expected_actual_keys(state, case_id) if key not in facts]
        if missing:
            return [f"提取器没有产出断言需要的字段：{missing}"]
        if bundle.driver.seed_session:
            initial = [
                str(item.get("name") or "")
                for item in (case.input.get("initial_contacts") or [])
                if isinstance(item, dict)
            ]
            initial = [name for name in initial if name]
            listed = {
                str(item.get("name") or "")
                for item in (facts.get("contacts_after") or [])
                if isinstance(item, dict)
            }
            missing = [name for name in initial if name not in listed]
            if missing:
                return (
                    f"种子联系人没有建成：程序里看不到 {missing}。seed_session 必须走完程序新建"
                    "联系人时的**每一次提问**（例如“请选择联系人类型”这样的中间菜单也要占一行），"
                    "并在最后用一个 0 回到主菜单。"
                )
        if bundle.observer.extractor_id == "contactbook_menu_v1" and not facts.get("observed_rows"):
            script = self._case_script(case_id, case)
            tail = stdout[-500:].replace("\r", "")
            return [
                "剧本没有走到能读出事实的界面：输出里没有任何列表行或搜索行，"
                "只看到“添加成功”不算观测。注意菜单语义：添加成功后回到“添加联系人”子菜单，"
                "输入 0 才回主菜单；到主菜单后再输入 0 就是退出程序。"
                "正确顺序：添加 → 0（回主菜单）→ 1（查找）→ 9（搜索）→ 1（按名字）→ 姓名 → 0 → 0（退出）。"
                f"当前剧本：{json.dumps(script, ensure_ascii=False)}；"
                f"实际输出末尾：{json.dumps(tail, ensure_ascii=False)}"
            ]
        return []

    def _observer_params(self, case_input: dict[str, Any]) -> dict[str, Any]:
        """替换观测参数；菜单类提取器需要用例声明的“操作前通讯录”。"""
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        params = _substitute(bundle.observer.params, case_input, state.warnings)
        if not isinstance(params, dict):
            params = {}
        if "initial_contacts" not in params:
            injects = {
                "contactbook_menu_v1",
                "birthday_menu_v1",
                "keyword_search_v1",
                "contact_ids_v1",
            }
            if bundle.observer.extractor_id in injects:
                params["initial_contacts"] = case_input.get("initial_contacts") or []
        return params

    def _seed_lines(self, case_input: dict[str, Any]) -> list[str]:
        """把 ``driver.seed_session`` 对用例声明的每个初始联系人展开一遍。"""
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        template = list(bundle.driver.seed_session or [])
        # 模型常把“返回上一层”和“返回主菜单”连写成两个 0；第二个 0 会直接退出程序。
        while len(template) > 1 and str(template[-1]).strip() == "0" and str(template[-2]).strip() == "0":
            template.pop()
        contacts = case_input.get("initial_contacts") or []
        if not template or not isinstance(contacts, list):
            return []
        lines: list[str] = []
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            birthday = contact.get("birthday")
            birthday = birthday if isinstance(birthday, dict) else {}
            replacements = {
                "seed.name": contact.get("name") or "",
                "seed.phone": contact.get("phone") or "",
                "seed.birthday.year": birthday.get("year") or "",
                "seed.birthday.month": birthday.get("month") or "",
                "seed.birthday.day": birthday.get("day") or "",
            }
            for step in template:
                text = str(step)
                for key, value in replacements.items():
                    text = text.replace("{{" + key + "}}", str(value))
                lines.append(text)
        return lines

    def _case_script(self, case_id: str, case: Any) -> list[str]:
        bundle = self.state.bundle
        assert bundle is not None
        base = list(bundle.driver.session_by_case.get(case_id) or bundle.driver.session)
        seed = self._seed_lines(case.input)
        if seed and base and str(base[0]).strip().isdigit():
            # 程序启动时问的容量/握手输入在种子之前，否则种子会被当成启动输入。
            return [base[0]] + seed + base[1:]
        return seed + base

    def _run_case_stream(self, case_id: str, case: Any, *, probe: bool) -> str:
        state = self.state
        bundle = state.bundle
        assert bundle is not None and state.exe is not None
        work = state.run_dir / ("evidence/probe" if probe else f"evidence/cases/{case_id}") / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        limit = effective_limits(state.task.document.limits, getattr(case, "limits_override", None))
        if bundle.driver.kind == "stdin_script":
            script = self._case_script(case_id, case)
            lines = [
                str(line)
                for line in _substitute(script, case.input, state.warnings)
            ]
            stdin_path = work / "stdin.txt"
            stdin_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            attempt_id = f"probe_{case_id}" if probe else f"attempt_{case_id}"
            stdin_path = work / "request.json"
            stdin_path.write_text(
                json.dumps(
                    build_request(
                        run_id=state.run_id,
                        attempt_id=attempt_id,
                        case_id=case_id,
                        operation_id=state.task.template.operation_id,
                        case_input=case.input,
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        stdout_path = work / "stdout.txt"
        stderr_path = work / "stderr.txt"
        outcome = state.backend.run(
            RunRequest(
                executable=state.exe,
                argv=[],
                stdin_path=stdin_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                work_dir=work,
                limits=limit,
                env=minimal_environment(work),
            )
        )
        self._probe_outcomes[case_id] = outcome  # type: ignore[attr-defined]
        self._event(
            "CASE_RUN" if not probe else "PROBE_RUN",
            {
                "case_id": case_id,
                "termination": outcome.termination.value,
                "wall_ms": outcome.wall_ms,
            },
        )
        return _read_output(stdout_path)

    # ------------------------------------------------------------------ 阶段 5：执行
    def _execute_cases(self) -> None:
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        attempts = {"count": 0}
        deadline = time.monotonic() + state.task.document.limits.total_wall_ms / 1000.0
        for case_id in state.task.task_spec.case_ids:
            self._check_cancel()
            if time.monotonic() > deadline:
                state.execution_status = ExecutionStatus.BUDGET_EXCEEDED
                raise AssessorError("总预算耗尽，已停止执行")
            case = state.task.cases[case_id]
            attempts["count"] += 1
            state.case_results.append(self._run_case(case_id, case))
        state.execution_status = ExecutionStatus.COMPLETED
        self._event("CASES_FINISHED", {"cases": len(state.case_results)})

    def _run_case(self, case_id: str, case: Any) -> CaseResult:
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        attempt_id = f"attempt_{case_id}"
        work = state.run_dir / "evidence" / "cases" / case_id
        stdin_path = work / "work" / "stdin.txt"
        stdout_path = work / "work" / "stdout.txt"
        stderr_path = work / "work" / "stderr.txt"
        self._run_case_stream(case_id, case, probe=False)
        outcome = self._probe_outcomes.get(case_id)
        stdout = _read_output(stdout_path)
        stderr = _read_output(stderr_path)
        evidence_refs = [
            f"evidence/cases/{case_id}/work/stdin.txt",
            f"evidence/cases/{case_id}/work/stdout.txt",
            f"evidence/cases/{case_id}/work/stderr.txt",
        ]
        for ref in evidence_refs:
            self._evidence(ref)

        termination = outcome.termination if outcome is not None else Termination.NOT_RUN
        exit_code = outcome.exit_code if outcome is not None else None
        if bundle.driver.kind == "function_driver":
            parsed = parse_response(
                stdout_text=stdout,
                run_id=state.run_id,
                attempt_id=attempt_id,
                case_id=case_id,
                operation_id=state.task.template.operation_id,
                termination=termination,
            )
            protocol_status = parsed.status
            actual = parsed.actual
            detail = parsed.detail
            fault = parsed.fault_owner
        else:
            if termination is not Termination.EXITED:
                protocol_status = ProtocolStatus.MISSING
                actual = None
                detail = f"程序没有正常结束（{termination.value}），无法观察"
                fault = FaultOwner.SUBMISSION
            else:
                try:
                    actual = extract(
                        bundle.observer.extractor_id,
                        stdout,
                        self._observer_params(case.input),
                    )
                    protocol_status = ProtocolStatus.OK
                    detail = f"按 {bundle.observer.extractor_id} 从输出中提取观测事实"
                    fault = FaultOwner.NONE
                except KeyError as exc:
                    protocol_status = ProtocolStatus.MISSING
                    actual = None
                    detail = f"提取器不可用：{exc}"
                    fault = FaultOwner.ADAPTER

        observation = Observation(
            schema_version=SCHEMA_VERSION,
            observation_id=f"obs_{case_id}",
            run_id=state.run_id,
            phase="execute",
            execution_manifest_hash=state.manifest.snapshot_hash,
            plan_hash=self._plan_hash(),
            case_id=case_id,
            attempt_id=attempt_id,
            termination=termination,
            exit_code=exit_code,
            stdout_ref=evidence_refs[1],
            stderr_ref=evidence_refs[2],
            files_refs=[],
            wall_ms=outcome.wall_ms if outcome is not None else 0,
            resource_events=list(getattr(outcome, "resource_events", []) or []),
            protocol_status=protocol_status,
            actual=actual,
            fault_owner=fault,
            protocol_detail=detail,
        )
        state.observations[case_id] = observation
        self._persist_json(f"evidence/observations/{case_id}.json", observation.model_dump())
        self._evidence(f"evidence/observations/{case_id}.json")

        return self._judge_case(case_id, case, observation, evidence_refs)

    def _judge_case(
        self, case_id: str, case: Any, observation: Observation, evidence_refs: list[str]
    ) -> CaseResult:
        state = self.state
        bundle = state.bundle
        assert bundle is not None
        bindings = binding_map(bundle)
        assertion_ids = [item for item in assertion_ids_for_case(bundle, case) if item in state.task.assertions]
        if observation.protocol_status is not ProtocolStatus.OK:
            note = {
                FaultOwner.SUBMISSION: "SUBJECT_FAULT",
                FaultOwner.ADAPTER: "ADAPTER_FAULT",
                FaultOwner.INFRA: "INFRA_FAULT",
            }.get(observation.fault_owner, "PROTOCOL_NOT_OK")
            return CaseResult(
                schema_version=SCHEMA_VERSION,
                case_id=case_id,
                attempt_id=f"attempt_{case_id}",
                purpose=CasePurpose.ASSESSMENT,
                comparison=ComparisonStatus.NOT_COMPARED,
                termination=observation.termination,
                protocol_status=observation.protocol_status,
                assertion_results=[],
                failed_assertions=[],
                evidence_refs=evidence_refs,
                observation_id=observation.observation_id,
                note=note,
            )
        results = []
        failed: list[str] = []
        for assertion_id in assertion_ids:
            spec = state.task.assertions[assertion_id]
            outcome = evaluate(
                spec,
                case_expected=case.expected,
                actual_document=observation.actual or {},
            )
            binding = bindings.get(assertion_id)
            results.append(
                {
                    "assertion_id": assertion_id,
                    "operator": spec.operator,
                    "outcome": outcome,
                    "actual_key": binding.actual_key if binding else spec.actual_observation_key,
                }
            )
            if outcome.comparison is ComparisonStatus.MISMATCH:
                failed.append(assertion_id)
        comparisons = [item["outcome"].comparison for item in results]
        if any(item is ComparisonStatus.MISMATCH for item in comparisons):
            comparison = ComparisonStatus.MISMATCH
        elif results and all(item is ComparisonStatus.MATCH for item in comparisons):
            comparison = ComparisonStatus.MATCH
        else:
            comparison = ComparisonStatus.NOT_COMPARED
        from ..domain.models import AssertionResult

        assertion_results = [
            AssertionResult(
                schema_version=SCHEMA_VERSION,
                assertion_id=item["assertion_id"],
                case_id=case_id,
                operator=item["operator"],
                comparison=item["outcome"].comparison,
                expected_source=state.task.assertions[item["assertion_id"]].expected_source,
                expected=item["outcome"].expected,
                actual=item["outcome"].actual,
                detail=item["outcome"].detail,
                evidence_ref=evidence_refs[1],
            )
            for item in results
        ]
        return CaseResult(
            schema_version=SCHEMA_VERSION,
            case_id=case_id,
            attempt_id=f"attempt_{case_id}",
            purpose=CasePurpose.ASSESSMENT,
            comparison=comparison,
            termination=observation.termination,
            protocol_status=observation.protocol_status,
            assertion_results=assertion_results,
            failed_assertions=failed,
            evidence_refs=evidence_refs,
            observation_id=observation.observation_id,
            note=None if comparison is not ComparisonStatus.NOT_COMPARED else "OBSERVATION_INCOMPLETE",
        )

    # ------------------------------------------------------------------ 阶段 6：报告
    def _report(self) -> None:
        state = self.state
        coverage = compute_coverage(
            state.task.method,
            {case_id: state.task.cases[case_id] for case_id in state.task.task_spec.case_ids},
            state.case_results,
        )
        self._write_report(coverage, state.execution_status or ExecutionStatus.COMPLETED)

    def _write_report(self, coverage: CoverageReport, execution_status: ExecutionStatus) -> None:
        state = self.state
        spec = state.task.task_spec
        confirmed = all(
            state.task.cases[case_id].oracle_status is OracleStatus.CONFIRMED
            for case_id in spec.case_ids
        )
        published = spec.publication_status is PublicationStatus.PUBLISHED
        aggregation = aggregate(
            execution_status=execution_status,
            case_results=state.case_results,
            coverage=coverage,
            validation_level=ValidationLevel.CHECKED,
            task_published=published,
            assertions_confirmed=confirmed,
            execution_backend_attested=state.attested,
        )
        notes = ["模式二（LLM 适配）：适配声明由模型生成，经宿主静态校验与探针试跑后冻结"]
        if state.bundle is not None:
            notes.append(f"驱动 {state.bundle.driver.kind}，观测 {state.bundle.observer.extractor_id}")
        if state.warnings:
            notes.append("适配提示：" + "；".join(dict.fromkeys(state.warnings))[:600])
        if state.backend.simulated:
            notes.append(
                "本次执行使用模拟后端（execution_backend=mock），未运行任何被测代码；观测由用例期望合成，仅用于验证状态机与报告链路，不得计入验收。"
            )
        elif not state.attested:
            notes.append(
                "执行后端未通过隔离能力门禁（缺网络/文件系统隔离等），报告如实标注 grade_eligible=false；能力补齐后可重新测评。"
            )
        if execution_status is ExecutionStatus.ADAPTATION_FAILED:
            notes.append("适配未完成，未执行正式用例：" + (state.error or ""))
        if state.bundle is not None:
            matrix_ref = "plans/migration_matrix.json"
            self._persist_json(
                matrix_ref,
                {
                    "task_id": spec.task_id,
                    "assertion_bindings": [item.model_dump() for item in state.bundle.assertion_bindings],
                    "migration_matrix": [item.model_dump() for item in state.bundle.migration_matrix],
                    "coverage_gaps": list(state.bundle.coverage_gaps),
                },
            )
            if matrix_ref not in state.evidence:
                state.evidence.append(matrix_ref)
        evidence = list(dict.fromkeys(state.evidence))
        report = AssessmentReport(
            schema_version=SCHEMA_VERSION,
            run_id=state.run_id,
            mode=Mode.LLM,
            execution_status=execution_status,
            assessment_status=aggregation.assessment_status,
            grade_eligible=aggregation.grade_eligible,
            grade_eligible_reasons=list(aggregation.reasons),
            validation_level=ValidationLevel.CHECKED,
            coverage=coverage,
            case_results=state.case_results,
            counts=aggregation.counts or count_results(state.case_results, coverage),
            evidence_refs=evidence,
            provenance=ReportProvenance(
                execution_backend=state.backend.backend_id,
                sandbox_attested=state.attested,
                task_id=state.task.task_id,
                task_version=str(state.task.version),
                task_package_hash=state.task.package_hash,
                submission_id=state.manifest.submission_id,
                toolchain_id=toolchain_id(),
                started_at=state.started_at,
                finished_at=state.finished_at or _now(),
                assessor_version=ASSESSOR_VERSION,
                protocol_version=PROTOCOL_VERSION,
            ),
            observations_note=" ".join(notes),
        )
        state.report = report
        self._persist_json("objective_report.json", report.model_dump())

    def _write_failure_report(self, exc: Exception) -> None:
        state = self.state
        coverage = CoverageReport(
            planned_cases=list(state.task.task_spec.case_ids),
            completed_cases=sorted(state.observations),
            missed_cases=[
                case_id
                for case_id in state.task.task_spec.case_ids
                if case_id not in state.observations
            ],
            planned_partitions=[
                item.partition_id for item in state.task.method.input_partitions
            ],
            covered_partitions=[],
            missing_partitions=[
                item.partition_id for item in state.task.method.input_partitions
            ],
            planned_assertions=[item.assertion_id for item in state.task.assertions.values()],
            executed_assertions=[],
            missing_assertions=[item.assertion_id for item in state.task.assertions.values()],
            complete=False,
        )
        status = state.execution_status or ExecutionStatus.ADAPTATION_FAILED
        self._write_report(coverage, status)

    # ------------------------------------------------------------------ 修补
    def _repair(self, reason: str, problems: list[str]) -> TargetVerifierBundle | None:
        state = self.state
        if state.repaired >= _MAX_REPAIRS or state.bundle is None:
            return None
        state.repaired += 1
        payload = {
            "previous_bundle": state.bundle.model_dump(),
            "problems": problems,
            "instruction": (
                "只修改适配声明（目标文件/编译参数/驱动会话/提取器参数）来解决问题，"
                "不得修改断言绑定、operator、期望语义，不得引入新的判据。输出完整 JSON。"
            ),
        }
        try:
            response = self.client.complete_json(
                system=_skill_text(),
                user=json.dumps(payload, ensure_ascii=False),
                call_name=f"repair_{state.task.task_id}_{state.repaired}",
                max_tokens=self.settings.max_tokens,
            )
        except LLMError as exc:
            self._event("REPAIR_LLM_FAILED", {"error": str(exc)})
            return None
        bundle = self._parse_bundle(response.data)
        if bundle is None:
            return None
        validation = validate_bundle(
            bundle,
            state.task,
            state.files,
            extractor_ids=extractor_ids(),
            case_inputs={cid: state.task.cases[cid].input for cid in state.task.task_spec.case_ids},
        )
        if not validation.passed:
            state.warnings.extend(validation.problems)
            self._event("REPAIR_INVALID", {"problems": validation.problems[:4]})
            return None
        self._persist_json("plans/target_bundle.json", bundle.model_dump())
        self._event("BUNDLE_REPAIRED", {"reason": reason, "round": state.repaired})
        return bundle

    # ------------------------------------------------------------------ 杂项
    def _plan_hash(self) -> str:
        state = self.state
        plan = state.build_plan
        return hash_json(
            {
                "build": plan.model_dump() if plan is not None else None,
                "bundle": state.bundle.model_dump() if state.bundle is not None else None,
            }
        )

    def _check(self, check_id: str, passed: bool, detail: str) -> None:
        self._event("VALIDATION_CHECK", {"check_id": check_id, "passed": passed, "detail": detail[:300]})

    def _check_cancel(self) -> None:
        if (self.state.run_dir / "CANCEL").exists():
            self.state.execution_status = ExecutionStatus.CANCELLED
            raise AssessorError("运行已被取消")

    def _evidence(self, ref: str) -> None:
        if ref not in self.state.evidence:
            self.state.evidence.append(ref)

    def _persist_json(self, relative: str, payload: Any) -> None:
        path = self.state.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8")

    def _event(self, name: str, detail: dict[str, Any] | None = None) -> None:
        record = {
            "run_id": self.state.run_id,
            "at": _now(),
            "event": name,
            "detail": detail or {},
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._event_count = getattr(self, "_event_count", 0) + 1


def document_toolchain(task: LoadedTask) -> str:
    value = getattr(task.document.build, "toolchain_id", "") or ""
    return value or toolchain_id()


def _expected_actual_keys(state: _State, case_id: str) -> list[str]:
    task = state.task
    case = task.cases[case_id]
    return [task.assertions[assertion_id].actual_observation_key for assertion_id in case.assertion_ids]


EXIT_CODES = {
    "PASS": EXIT_PASS,
    "FAIL": EXIT_FAIL,
    "NOT_ASSESSED": EXIT_NOT_ASSESSED,
    "INCONCLUSIVE": EXIT_INCONCLUSIVE,
}


def execute_llm_run(options: RunOptions, settings: LLMSettings | None = None) -> RunResult:
    """执行一次模式二测评。"""

    effective = settings or LLMSettings.from_env()
    if not effective.configured:
        raise InvalidInputError(
            "模式二需要模型 API Key：请在页面填写，或设置 ASSESSOR_LLM_API_KEY"
        )
    run = ModeTwoRun(options, effective)
    try:
        return run.execute()
    except AssessorError:
        raise

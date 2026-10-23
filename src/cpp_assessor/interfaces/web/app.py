# -*- coding: utf-8 -*-
"""本地 Web 界面（任务书任务 4）。

只做“选任务 / 提交 / 看结果 / 导出”，不改变判定链路：页面拿到的结论、计数、
证据位置全部来自 ``objective_report.json``，Web 层不做任何评分逻辑。

安全边界：默认只监听 ``127.0.0.1``，无登录与会话（单机教师使用）；上传文件先落
到 ``data/web_uploads`` 再交给 ``intake``，大小上限沿用导入层限制。
"""

from __future__ import annotations

import csv
import io
import hashlib
import json
import re
import shutil
import threading
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ...config import ASSESSOR_VERSION, data_dir, task_library_dir
from ...domain.enums import Mode
from ...domain.errors import AssessorError, InvalidInputError
from ...domain.models import AssessmentReport, TaskCase
from ...domain.canonical import hash_json
from ...llm import LLMSettings
from ...orchestrator.mode_two import execute_llm_run
from ...orchestrator.runner import RunOptions, execute_standard_run, make_backend, new_run_id
from ...planning.pilot_tasks import get_pilot_profile
from ...reporting.export import CSV_COLUMNS, report_json, report_rows
from ...selfcheck import (
    default_reference_source,
    list_recent,
    load_report,
    run_selfcheck,
)
from ...storage.db import Database
from ...tasks.loader import list_tasks, load_task
from .batch import (
    MAX_PROGRAMS,
    MAX_TASKS,
    PROGRAM_SUFFIXES,
    batch_directory,
    build_batch,
    count_progress,
    list_batches,
    matrix,
    new_batch_id,
    prepare_program,
    read_batch,
    write_batch,
)
from .i18n import label, normalize_lang, translator

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

ALLOWED_UPLOAD_SUFFIXES = {".zip", ".cpp", ".h", ".hpp"}
TERMINAL_WORKFLOW_STATES = {"COMPLETED", "FAILED", "CANCELLED"}

# 判定理由代码 → 面向教师的中文说明（只做展示翻译，不改变判定）。
REASON_LABELS = {
    "VALIDATION_LEVEL_CHECKED": "已完成适配检查，尚未完成权威目标校准或人工签审；有限范围回归不授予正式评分资格，保留测试证据供复核。",
    "BUILD_ATTRIBUTION_UNCONFIRMED": "尚未确认错误来自学生原程序，不能据此判失败。",
    "EXECUTION_BACKEND_NOT_ATTESTED": "执行后端未通过隔离能力门禁，本次不计入正式成绩",
    "ASSERTION_MISMATCH": "存在与预期不一致的必需断言",
    "SUBJECT_CRASH_OR_LIMIT": "被测程序在合规调用中崩溃或触达资源上限",
    "COVERAGE_INCOMPLETE": "必需用例或断言未全部执行",
    "ASSERTIONS_NOT_COMPARED": "存在无法比较的必需断言",
    "ADAPTER_OR_INFRA_FAULT": "出现适配器或基础设施故障",
    "TASK_NOT_PUBLISHED": "任务未发布（DRAFT）",
    "ASSERTIONS_NOT_CONFIRMED": "期望值未经参照版本确认",
    "EXECUTION_INVALID_INPUT": "提交内容或参数不合法，未进入测评",
    "EXECUTION_UNSUPPORTED": "当前环境不支持该执行方式（如隔离后端缺失）",
    "EXECUTION_BUILD_UNRESOLVED": "无法从提交内容确定构建方案",
    "EXECUTION_BUDGET_EXCEEDED": "本次运行超出时间/资源预算",
    "EXECUTION_INFRA_ERROR": "系统内部错误，未得到有效测评结果",
    "EXECUTION_ADAPTATION_FAILED": "适配阶段失败，无法可靠观测被测程序",
    "RUN_CANCELLED": "运行已被取消",
    "SUBMISSION_COMPILE_ERROR": "提交代码编译失败",
    "NO_CASES_EXECUTED": "没有任何用例被执行",
    "VALIDATION_LEVEL_UNVERIFIED": "任务包或提交未经校验，不能正式判定",
}


@dataclass
class WebSettings:
    """Web 服务的运行策略；默认值刻意保守。"""

    backend: str = "local"
    allow_unattested: bool = False
    allow_draft: bool = False
    data_root: Path = field(default_factory=data_dir)
    library: Path = field(default_factory=task_library_dir)
    max_upload_bytes: int = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# 内部小工具
# ---------------------------------------------------------------------------


_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _run_dir(settings: WebSettings, run_id: str) -> Path:
    # run_id 直接来自 URL，必须限制字符集，避免 ../ 之类路径穿越读写出根目录。
    if not _RUN_ID_PATTERN.fullmatch(run_id) or ".." in run_id:
        raise HTTPException(status_code=400, detail=f"非法的运行编号：{run_id}")
    return settings.data_root / "runs" / run_id


def _report_path(settings: WebSettings, run_id: str) -> Path:
    return _run_dir(settings, run_id) / "objective_report.json"


def _read_report(settings: WebSettings, run_id: str) -> AssessmentReport | None:
    path = _report_path(settings, run_id)
    if not path.is_file():
        return None
    try:
        return AssessmentReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return None


def _read_target_challenges(settings: WebSettings, run_id: str,
                            report: AssessmentReport) -> dict[str, Any] | None:
    """Display extra observations only for this historical frozen executable.

    Compare saved provenance, never the current machine's execution backend.
    This is record binding for display and does not confer scoring authority.
    """
    root = _run_dir(settings, run_id)
    try:
        paths = [root / "evidence/target_challenges/target_challenges.json",
                 root / "plans/frozen_plan.json", root / "plans/replay_manifest.json"]
        if any(not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024 for path in paths):
            return None
        raw, frozen_raw, replay_raw = [path.read_bytes() for path in paths]
        candidate, frozen, replay = [json.loads(data.decode("utf-8")) for data in (raw, frozen_raw, replay_raw)]
        if not all(isinstance(item, dict) for item in (candidate, frozen, replay)):
            return None
        bundle = frozen["bundle"]
        profile = get_pilot_profile(bundle["task_id"], bundle["task_version"])
        if profile is None:
            return None
        required = len(profile.challenge_labels)
        artifact = bundle["build"]["artifact_name"]
        if (artifact not in {"subject.wasm", "subject.exe"}
                or any(not isinstance(candidate.get(key), str)
                       or re.fullmatch(r"[a-f0-9]{64}", candidate[key]) is None
                       for key in ("environment_digest", "executable_hash"))):
            return None
        if not (report.run_id == run_id == candidate.get("run_id")
                and candidate.get("policy_version") == profile.challenge_policy
                and candidate.get("purpose") == "SUPPLEMENTAL_RULE_CHECK"
                and candidate.get("backend_id") == report.provenance.execution_backend == "wasm_pilot"
                and candidate.get("task_package_hash") == frozen["task_hash"] == report.provenance.task_package_hash
                and frozen["submission"]["submission_id"] == report.provenance.submission_id
                and bundle["task_id"] == report.provenance.task_id == profile.task_id
                and bundle["task_version"] == report.provenance.task_version == profile.version
                and candidate.get("verifier_bundle_hash") == hash_json(bundle)
                and candidate.get("environment_digest") == replay["environment_digest"]
                and candidate.get("executable_hash") == replay["file_hashes"]["generated/build/" + artifact]
                and replay["file_hashes"]["plans/frozen_plan.json"] == hashlib.sha256(frozen_raw).hexdigest()
                and candidate.get("record_hash") == hash_json({key: value for key, value in candidate.items() if key != "record_hash"})):
            return None
        rows, counts = candidate["challenge_results"], candidate["challenge_counts"]
        allowed_cases = set(profile.challenge_labels)
        if (not isinstance(rows, list) or len(rows) > required or not isinstance(counts, dict)
                or any(not isinstance(row, dict) or row.get("case_id") not in allowed_cases
                       or row.get("comparison") not in {"MATCH", "MISMATCH", "NOT_COMPARED"}
                       or not isinstance(row.get("input"), dict) or not isinstance(row.get("expected"), dict)
                       or ("assertion_results" in row and (not isinstance(row["assertion_results"], list)
                           or any(not isinstance(item, dict) for item in row["assertion_results"])))
                       or "actual" not in row for row in rows)
                or len({row["case_id"] for row in rows}) != len(rows)
                or any(type(counts.get(key)) is not int or not 0 <= counts[key] <= required
                       for key in ("required", "attempted", "matched", "mismatched", "not_compared"))
                or type(candidate.get("passed")) is not bool or type(candidate.get("defect_detected")) is not bool):
            return None
        # A self-consistent record hash does not guarantee that its summary
        # agrees with its individual observations.  Never advertise a defect
        # (or three successful checks) from a malformed or partial summary.
        matched = sum(row["comparison"] == "MATCH" for row in rows)
        mismatched = sum(row["comparison"] == "MISMATCH" for row in rows)
        if (candidate.get("status") not in {"COMPLETED", "UNSUPPORTED", "INVALIDATED", "CANCELLED", "BUDGET_EXCEEDED"}
                or counts != {"required": required, "attempted": len(rows), "matched": matched,
                              "mismatched": mismatched, "not_compared": required - matched - mismatched}
                or candidate["passed"] != (candidate["status"] == "COMPLETED" and matched == required)
                or candidate["defect_detected"] != (candidate["status"] == "COMPLETED" and mismatched > 0)):
            return None
        return candidate
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _read_verifier_calibration(settings: WebSettings, run_id: str,
                               report: AssessmentReport) -> dict[str, Any] | None:
    """Bind a saved reliability check to its task and frozen historical plan."""
    root = _run_dir(settings, run_id)
    try:
        paths = [root / "plans/target_calibration.json", root / "plans/frozen_plan.json",
                 root / "plans/replay_manifest.json"]
        if any(not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024 for path in paths):
            return None
        raw, frozen_raw, replay_raw = [path.read_bytes() for path in paths]
        candidate, frozen, replay = [json.loads(item.decode("utf-8")) for item in (raw, frozen_raw, replay_raw)]
        if not all(isinstance(item, dict) for item in (candidate, frozen, replay)):
            return None
        bundle = frozen["bundle"]
        profile = get_pilot_profile(bundle["task_id"], bundle["task_version"])
        if profile is None or type(candidate.get("passed")) is not bool:
            return None
        if not (report.run_id == run_id
                and candidate.get("policy_version") == profile.calibration_policy
                and candidate.get("backend_id") == report.provenance.execution_backend == "wasm_pilot"
                and bundle["task_id"] == report.provenance.task_id == profile.task_id
                and bundle["task_version"] == report.provenance.task_version == profile.version
                and candidate.get("task_package_hash") == frozen["task_hash"] == report.provenance.task_package_hash
                and candidate.get("record_hash") == hash_json({key: value for key, value in candidate.items() if key != "record_hash"})
                and frozen["submission"]["submission_id"] == report.provenance.submission_id
                and candidate.get("source_snapshot_hash") == frozen["submission"]["snapshot_hash"]
                and isinstance(candidate.get("source_snapshot_hash"), str)
                and re.fullmatch(r"[a-f0-9]{64}", candidate["source_snapshot_hash"])
                and candidate.get("verifier_bundle_hash") == hash_json(bundle)
                and candidate.get("environment_digest") == replay["environment_digest"]
                and isinstance(candidate.get("environment_digest"), str)
                and re.fullmatch(r"[a-f0-9]{64}", candidate["environment_digest"])
                and replay["file_hashes"].get("plans/target_calibration.json") == hashlib.sha256(raw).hexdigest()
                and replay["file_hashes"].get("plans/frozen_plan.json") == hashlib.sha256(frozen_raw).hexdigest()):
            return None
        if candidate["passed"] and not (candidate.get("status") == "COMPLETED"
                and candidate.get("qualification") == profile.qualification
                and candidate.get("formal_case_ids") == list(profile.formal_case_ids)
                and candidate.get("supplemental_case_ids") == list(profile.challenge_labels)
                and _calibration_summary_consistent(candidate, profile)):
            return None
        return candidate
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _calibration_summary_consistent(record: dict[str, Any], profile) -> bool:
    """A passed headline must agree with every saved independent fixture row.

    T1 records predate expected_assertions, so derive that field from the same
    versioned profile when absent. This does not replay or authenticate results.
    """
    if record.get("grade_eligible") is not False or record.get("verifier_validation_level") != "CHECKED":
        return False
    outcomes = record.get("outcomes")
    if (not isinstance(outcomes, list) or len(outcomes) != 2
            or any(not isinstance(item, dict) for item in outcomes)
            or {item.get("variant_id") for item in outcomes} != {"correct", "faulty"}):
        return False
    case_ids = set(profile.formal_case_ids + profile.supplemental_case_ids)
    for outcome in outcomes:
        rows = outcome.get("cases")
        if (outcome.get("passed") is not True or outcome.get("compile_status") != "SUCCESS"
                or not isinstance(rows, list) or len(rows) != len(case_ids)
                or any(not isinstance(row, dict) for row in rows)
                or {row.get("case_id") for row in rows} != case_ids):
            return False
        for row in rows:
            expected = profile.expected_assertions(outcome["variant_id"], row["case_id"])
            if (row.get("passed") is not True or row.get("termination") != "EXITED"
                    or row.get("protocol_status") != "OK" or row.get("assertions") != expected
                    or row.get("expected_assertions", expected) != expected
                    or row.get("initial_contacts_preserved") is not True
                    or row.get("defect_signature_verified") is not True):
                return False
    return True


def _birthday_summary(case_input: dict, expected: Any, actual: Any,
                      contacts_comparison: str | None = None) -> dict[str, Any]:
    today = case_input.get("today")
    date = "未取得日期"
    if isinstance(today, dict) and all(type(today.get(key)) is int for key in ("year", "month", "day")):
        date = f"{today['year']:04d}-{today['month']:02d}-{today['day']:02d}"
    def names(value):
        result = value.get("names") if isinstance(value, dict) else None
        return result if isinstance(result, list) and all(isinstance(name, str) for name in result) else None
    expected_contacts = expected.get("contacts_after") if isinstance(expected, dict) else None
    actual_contacts = actual.get("contacts_after") if isinstance(actual, dict) else None
    return {"today": date, "days_ahead": case_input.get("days_ahead"),
            "expected_names": names(expected), "actual_names": names(actual),
            "contacts_comparison": contacts_comparison,
            "expected_contacts": expected_contacts, "actual_contacts": actual_contacts}


def _birthday_case_views(settings: WebSettings, report: AssessmentReport) -> dict[str, dict[str, Any]]:
    """Use report assertions as observations, and only a matching task for inputs."""
    if (report.provenance.task_id, report.provenance.task_version) != ("t2_birthday_today", "1.0.0"):
        return {}
    try:
        task = load_task("t2_birthday_today@1.0.0", task_library=settings.library, allow_draft=True)
        if task.package_hash != report.provenance.task_package_hash:
            return {}
        result = {}
        labels = {"c_today_exact": "当天生日（仅提醒今天）", "c_today_window": "当天生日与提醒窗口",
                  "c_already_passed": "已过生日", "c_leap_day": "闰年当天生日",
                  "c_window_edge": "提醒窗口之外", "c_negative_window": "负数提醒窗口"}
        for case in report.case_results:
            if case.case_id not in task.cases:
                continue
            assertions = {item.assertion_id: item for item in case.assertion_results}
            names, contacts = assertions.get("a_names"), assertions.get("a_contacts")
            result[case.case_id] = _birthday_summary(task.cases[case.case_id].input,
                {"names": names.expected if names else None, "contacts_after": contacts.expected if contacts else None},
                {"names": names.actual if names else None, "contacts_after": contacts.actual if contacts else None},
                contacts.comparison.value if contacts else None)
            result[case.case_id]["label"] = labels.get(case.case_id, case.case_id)
        return result
    except (AssessorError, OSError, ValueError, TypeError, KeyError):
        return {}


def _stored_error(settings: WebSettings, run_id: str) -> str | None:
    """读取运行器先于数据库收尾写下的错误说明（存在时间窗口）。"""
    path = _run_dir(settings, run_id) / "evidence" / "run_error.txt"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _selfcheck_dir(settings: WebSettings, selfcheck_id: str) -> Path:
    if not _RUN_ID_PATTERN.fullmatch(selfcheck_id) or ".." in selfcheck_id:
        raise HTTPException(status_code=400, detail="非法的自检编号")
    return settings.data_root / "selfchecks" / selfcheck_id


def _read_text_limited(path: Path, limit: int = 20_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n…（输出已截断）"
    return text


STALE_RUN_SECONDS = 180


def _interrupted(row: Any) -> bool:
    """运行进程已退出却留下未收尾的记录（例如服务重启、线程内异常退出）。

    判定依据：数据库行不是终态，且超过 STALE_RUN_SECONDS 没有更新。
    """
    if row is None or row.workflow_state in TERMINAL_WORKFLOW_STATES:
        return False
    try:
        stamp = datetime.fromisoformat(row.updated_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() > STALE_RUN_SECONDS


def _csv_text(report: AssessmentReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in report_rows(report):
        writer.writerow(row)
    return "\ufeff" + buffer.getvalue()


def _status_badge(report: AssessmentReport) -> str:
    return report.assessment_status.value


def _load_suite(settings: WebSettings, task_ref: str) -> dict[str, Any]:
    """读取任务包的参照版本清单，用于页面上的“演示运行”。"""
    try:
        task = load_task(task_ref, task_library=settings.library, allow_draft=True)
    except (AssessorError, Exception):  # noqa: BLE001 - 页面渲染不应因坏包崩溃
        return {}
    path = task.task_dir / "reference_suite.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _case_rows(task: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in task.cases.values():
        rows.append(
            {
                "case_id": case.case_id,
                "purpose": case.purpose.value,
                "partitions": ", ".join(case.partition_ids) or "-",
                "rules": ", ".join(case.rule_ids) or "-",
                "assertions": ", ".join(case.assertion_ids) or "-",
                "oracle_status": case.oracle_status.value,
            }
        )
    return rows


def _save_upload(
    upload: UploadFile,
    destination: Path,
    max_bytes: int,
    allowed: set[str] = ALLOWED_UPLOAD_SUFFIXES,
) -> Path:
    """把上传内容写到磁盘，超过上限立即拒绝（不信任 Content-Length）。"""
    name = Path(upload.filename or "upload.bin").name
    suffix = Path(name).suffix.lower()
    if suffix not in allowed:
        raise InvalidInputError(
            f"不支持的提交格式：{suffix or '(无扩展名)'}；请上传 .zip、.cpp、.h 或 .hpp"
        )
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    written = 0
    with target.open("wb") as handle:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise InvalidInputError(
                    f"上传文件超过 {max_bytes // (1024 * 1024)} MiB 上限：{name}"
                )
            handle.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise InvalidInputError(f"上传内容为空：{name}")
    return target


def _batch_dir(settings: WebSettings, batch_id: str) -> Path:
    try:
        return batch_directory(settings.data_root, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _current_lang(request: Request) -> str:
    query = request.query_params.get("lang")
    if query:
        return normalize_lang(query)
    return normalize_lang(request.cookies.get("assessor_lang"))


def _language_switch_url(request: Request) -> str:
    target = "en" if _current_lang(request) == "zh" else "zh"
    pairs = [(k, v) for k, v in request.query_params.multi_items() if k != "lang"]
    pairs.append(("lang", target))
    return f"{request.url.path}?{urlencode(pairs)}"


def _render(
    request: Request,
    templates: Jinja2Templates,
    name: str,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    lang = _current_lang(request)
    context.setdefault("app_version", ASSESSOR_VERSION)
    context.setdefault("lang", lang)
    context.setdefault("t", translator(lang))
    context.setdefault("label", label)
    context.setdefault("pilot_profile_for", get_pilot_profile)
    context.setdefault("switch_url", _language_switch_url(request))
    response = templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )
    if request.query_params.get("lang"):
        response.set_cookie(
            "assessor_lang", lang, max_age=31_536_000, samesite="lax", httponly=False
        )
    return response


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or WebSettings()
    settings.data_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="C++ 小改自动测评（本地 Web）", version=ASSESSOR_VERSION)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters["reason_label"] = lambda code: REASON_LABELS.get(code, code)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.state.settings = settings
    app.state.templates = templates
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assessor-run")
    app.state.pending: dict[str, str] = {}
    app.state.errors: dict[str, str] = {}
    app.state.upload_root = settings.data_root / "web_uploads"

    @app.middleware("http")
    async def protect_pilot_origin(request: Request, call_next):
        if settings.backend == "wasm_pilot":
            if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse({"detail": "试验服务仅允许本机地址"}, status_code=403)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                same_origin = f"{request.url.scheme}://{request.url.netloc}"
                if (origin is not None and origin != same_origin) or request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse({"detail": "拒绝来自其他网页的试验提交"}, status_code=403)
        return await call_next(request)

    def db() -> Database:
        return Database(settings.data_root / "app.sqlite3")

    # -- 页面 ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def assessment_home(request: Request) -> HTMLResponse:
        # Public UI has one entry point; standard routes remain developer tools.
        return llm_index(request)

    @app.get("/standard", response_class=HTMLResponse)
    def standard_index(request: Request) -> HTMLResponse:
        capabilities = make_backend(settings.backend).capabilities()
        recent = db().list_runs(limit=10)
        return _render(
            request,
            templates,
            "index.html",
            tasks=list_tasks(settings.library),
            recent=recent,
            capabilities=capabilities,
            settings=settings,
        )

    @app.get("/llm", response_class=HTMLResponse)
    def llm_index(request: Request) -> HTMLResponse:
        configured = LLMSettings.from_env().configured
        return _render(
            request,
            templates,
            "llm.html",
            tasks=list_tasks(settings.library),
            capabilities=make_backend(settings.backend).capabilities(),
            settings=settings,
            key_configured=configured,
        )

    @app.get("/llm/tasks/{task_ref}", response_class=HTMLResponse)
    def llm_task_page(request: Request, task_ref: str) -> HTMLResponse:
        try:
            task = load_task(task_ref, task_library=settings.library, allow_draft=True)
        except AssessorError as exc:
            return _render(request, templates, "error.html", status_code=404, message=str(exc))
        suite = _load_suite(settings, task_ref)
        variants = suite.get("variants", []) if isinstance(suite, dict) else []
        return _render(
            request,
            templates,
            "task.html",
            task=task,
            case_rows=_case_rows(task),
            variants=variants,
            settings=settings,
            llm_mode=True,
            form_action="/llm/runs",
            key_configured=LLMSettings.from_env().configured,
        )

    @app.post("/llm/runs")
    async def create_llm_run(
        task_ref: str = Form(...),
        api_key: str = Form(""),
        variant: str = Form(""),
        submission: UploadFile | None = File(None),
        target_before: UploadFile | None = File(None),
    ) -> Response:
        if settings.backend == "wasm_pilot":
            if not settings.allow_unattested:
                raise HTTPException(status_code=403, detail="WebAssembly 试验未显式启用")
            task_id, separator, version = task_ref.partition("@")
            if get_pilot_profile(task_id, version if separator else "1.0.0") is None:
                raise HTTPException(status_code=400, detail="当前仅开放手机号校验、生日提醒两题的 1.0.0 版本")
            if not separator:
                task_ref += "@1.0.0"
        key = (api_key or "").strip()
        try:
            llm_settings = LLMSettings.from_env(api_key=key or None)
        except (ValueError, InvalidInputError) as exc:  # noqa: F821 - 兼容旧版本
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not llm_settings.configured:
            raise HTTPException(
                status_code=400,
                detail="LLM 模式需要 API Key：请在表单里填写，或先设置 ASSESSOR_LLM_API_KEY",
            )
        try:
            task = load_task(task_ref, task_library=settings.library, allow_draft=True)
        except AssessorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        variant = (variant or "").strip()
        if variant and submission is not None and submission.filename:
            raise HTTPException(status_code=400, detail="不能同时指定参照版本和提交文件")
        if not variant and (submission is None or not submission.filename):
            raise HTTPException(status_code=400, detail="请上传提交文件，或选择一个参照版本")

        upload_dir = app.state.upload_root / uuid.uuid4().hex
        submission_path: Path | None = None
        target_before_path: Path | None = None
        try:
            if submission is not None and submission.filename:
                submission_path = _save_upload(submission, upload_dir, settings.max_upload_bytes)
            if target_before is not None and target_before.filename:
                target_before_path = _save_upload(
                    target_before, upload_dir / "target_before", settings.max_upload_bytes
                )
        except InvalidInputError as exc:
            raise HTTPException(status_code=413 if "大" in str(exc) else 400, detail=str(exc)) from exc

        run_id = new_run_id()
        options = RunOptions(
            reserved_run=True,
            task_ref=task_ref,
            submission=submission_path,
            variant=variant or None,
            target_before=target_before_path,
            mode=Mode.LLM,
            backend=settings.backend,
            allow_draft=settings.allow_draft,
            allow_unattested=settings.allow_unattested,
            data_dir=settings.data_root,
            task_library=settings.library,
            run_id=run_id,
        )
        (settings.data_root / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        app.state.pending[run_id] = "queued"

        def job() -> None:
            app.state.pending[run_id] = "running"
            try:
                execute_llm_run(options, llm_settings)
            except BaseException as exc:  # noqa: BLE001 - 线程异常要显示在页面上
                app.state.errors[run_id] = f"{type(exc).__name__}: {exc}"
            finally:
                app.state.pending.pop(run_id, None)

        app.state.executor.submit(job)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request) -> HTMLResponse:
        return _render(
            request,
            templates,
            "runs.html",
            runs=db().list_runs(limit=100),
            settings=settings,
        )

    @app.get("/tasks/{task_ref}", response_class=HTMLResponse)
    def task_page(request: Request, task_ref: str) -> HTMLResponse:
        try:
            task = load_task(task_ref, task_library=settings.library, allow_draft=True)
        except AssessorError as exc:
            return _render(request, templates, "error.html", status_code=404, message=str(exc))
        suite = _load_suite(settings, task_ref)
        variants = suite.get("variants", []) if isinstance(suite, dict) else []
        return _render(
            request,
            templates,
            "task.html",
            task=task,
            case_rows=_case_rows(task),
            variants=variants,
            settings=settings,
        )

    @app.get("/tasks/{task_ref}/base.zip")
    def download_base_project(task_ref: str) -> Response:
        """把任务对应的基准工程打包下载，避免学生误交“参考基准程序”。"""
        try:
            task = load_task(task_ref, task_library=settings.library, allow_draft=True)
        except AssessorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project = task.document.project_ref
        prefix = f"{project.project_id}-{project.version}"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(task.project_root.rglob("*")):
                if not path.is_file():
                    continue
                arcname = path.relative_to(task.project_root).as_posix()
                archive.write(path, arcname=arcname)
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{prefix}.zip"'},
        )

    # -- 提交与运行 ---------------------------------------------------------

    @app.post("/runs")
    async def create_run(
        task_ref: str = Form(...),
        variant: str = Form(""),
        submission: UploadFile | None = File(None),
        target_before: UploadFile | None = File(None),
    ) -> Response:
        variant = (variant or "").strip()
        try:
            task = load_task(task_ref, task_library=settings.library, allow_draft=True)
        except AssessorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if variant and submission is not None and submission.filename:
            raise HTTPException(status_code=400, detail="不能同时指定参照版本和提交文件")
        if not variant and (submission is None or not submission.filename):
            raise HTTPException(status_code=400, detail="请上传提交文件，或选择一个参照版本")
        known_variants = {
            item.get("variant_id")
            for item in _load_suite(settings, task_ref).get("variants", [])
        }
        if variant and variant not in known_variants:
            raise HTTPException(status_code=400, detail=f"未知的参照版本：{variant}")

        upload_dir = app.state.upload_root / uuid.uuid4().hex
        submission_path: Path | None = None
        target_before_path: Path | None = None
        try:
            if submission is not None and submission.filename:
                submission_path = _save_upload(submission, upload_dir, settings.max_upload_bytes)
            if target_before is not None and target_before.filename:
                target_before_path = _save_upload(
                    target_before, upload_dir / "target_before", settings.max_upload_bytes
                )
        except InvalidInputError as exc:
            raise HTTPException(status_code=413 if "上限" in str(exc) else 400, detail=str(exc)) from exc

        run_id = new_run_id()
        options = RunOptions(
            reserved_run=True,
            task_ref=task_ref,
            submission=submission_path,
            variant=variant or None,
            target_before=target_before_path,
            backend=settings.backend,
            allow_draft=settings.allow_draft,
            allow_unattested=settings.allow_unattested,
            data_dir=settings.data_root,
            task_library=settings.library,
            run_id=run_id,
        )
        (settings.data_root / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        app.state.pending[run_id] = "queued"

        def job() -> None:
            app.state.pending[run_id] = "running"
            try:
                execute_standard_run(options)
            except BaseException as exc:  # noqa: BLE001 - 线程内异常必须留存给页面
                app.state.errors[run_id] = f"{type(exc).__name__}: {exc}"
            finally:
                app.state.pending.pop(run_id, None)

        app.state.executor.submit(job)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        report = _read_report(settings, run_id)
        row = db().get_run(run_id)
        # 303 重定向发生在后台线程建库/建目录之前，因此这里必须把“已排队”
        # 也当作合法运行，否则刚提交就会误报 404。
        in_flight = run_id in app.state.pending or run_id in app.state.errors
        run_dir_exists = _run_dir(settings, run_id).is_dir()
        if report is None and row is None and not in_flight and not run_dir_exists:
            return _render(
                request,
                templates,
                "error.html",
                status_code=404,
                message=f"找不到运行记录：{run_id}",
            )
        error = (
            app.state.errors.get(run_id)
            or (row.error if row else None)
            or _stored_error(settings, run_id)
        )
        if report is None and error is None and _interrupted(row):
            error = (
                "运行已中断：后台进程已退出（可能是服务重启或运行器异常），"
                "本次没有生成报告，请重新提交。"
            )
        if report is None and error is None and row is None and run_dir_exists and not in_flight:
            error = "排队运行在建立记录前已中断，请重新提交。"
        if report is None:
            return _render(
                request,
                templates,
                "run_waiting.html",
                run_id=run_id,
                row=row,
                error=error,
                settings=settings,
            )
        calibration = _read_verifier_calibration(settings, run_id, report)
        challenges = _read_target_challenges(settings, run_id, report)
        challenge_views = {}
        if challenges and report.provenance.task_id == "t2_birthday_today":
            for challenge in challenges["challenge_results"]:
                contacts = next((item for item in challenge.get("assertion_results", [])
                                 if isinstance(item, dict) and item.get("assertion_id") == "a_contacts"), {})
                challenge_views[challenge["case_id"]] = _birthday_summary(
                    challenge["input"], challenge["expected"], challenge["actual"], contacts.get("comparison"))
        return _render(
            request,
            templates,
            "run.html",
            report=report,
            row=row,
            error=error,
            badge=_status_badge(report),
            reason_labels=REASON_LABELS,
            settings=settings,
            verifier_calibration=calibration,
            target_challenges=challenges,
            task_profile=get_pilot_profile(report.provenance.task_id, report.provenance.task_version),
            birthday_cases=_birthday_case_views(settings, report),
            birthday_challenges=challenge_views,
        )

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> JSONResponse:
        report = _read_report(settings, run_id)
        row = db().get_run(run_id)
        if report is not None:
            state = "done"
        elif run_id in app.state.pending:
            state = "running"
        elif run_id in app.state.errors:
            state = "failed"
        elif row is not None and row.workflow_state in TERMINAL_WORKFLOW_STATES:
            state = "done" if row.workflow_state == "COMPLETED" else "failed"
        elif row is not None:
            state = "failed" if _interrupted(row) else "running"
        else:
            state = "failed" if _run_dir(settings, run_id).is_dir() else "unknown"
        # 报告文件先于数据库收尾落盘，因此以报告为准补充状态，避免读到“已完成但
        # assessment_status 仍为空”的瞬时中间态。
        execution_status = (
            report.execution_status.value
            if report is not None
            else (row.execution_status if row else None)
        )
        assessment_status = (
            report.assessment_status.value
            if report is not None
            else (row.assessment_status if row else None)
        )
        grade_eligible = (
            report.grade_eligible
            if report is not None
            else (row.grade_eligible if row else None)
        )
        workflow_state = (
            row.workflow_state
            if row is not None
            else ("COMPLETED" if report is not None else None)
        )
        api_error = (
            app.state.errors.get(run_id)
            or (row.error if row else None)
            or _stored_error(settings, run_id)
        )
        if state == "failed" and api_error is None:
            api_error = "运行已中断且没有生成报告，请重新提交。"
        return JSONResponse(
            {
                "run_id": run_id,
                "state": state,
                "error": api_error,
                "workflow_state": workflow_state,
                "execution_status": execution_status,
                "assessment_status": assessment_status,
                "grade_eligible": grade_eligible,
            }
        )

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> Response:
        directory = _run_dir(settings, run_id)
        if not directory.is_dir():
            raise HTTPException(status_code=404, detail=f"找不到运行目录：{run_id}")
        (directory / "CANCEL").write_text("cancel requested from web ui\n", encoding="utf-8")
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    # -- 导出 ---------------------------------------------------------------

    @app.get("/runs/{run_id}/report.json")
    def download_json(run_id: str) -> Response:
        report = _read_report(settings, run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告尚未生成")
        return Response(
            content=report_json(report),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @app.get("/runs/{run_id}/report.csv")
    def download_csv(run_id: str) -> Response:
        report = _read_report(settings, run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告尚未生成")
        return Response(
            content=_csv_text(report),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
        )

    # -- 基准程序实测（观察型自检） -----------------------------------------

    @app.get("/reference", response_class=HTMLResponse)
    def reference_page(request: Request) -> HTMLResponse:
        return _render(
            request,
            templates,
            "reference.html",
            default=default_reference_source(),
            rows=list_recent(settings.data_root, limit=8),
            settings=settings,
        )

    @app.post("/reference")
    def create_selfcheck(
        request: Request,
        source: UploadFile | None = File(None),
    ) -> Response:
        if source is not None and source.filename:
            upload_dir = app.state.upload_root / uuid.uuid4().hex
            path = _save_upload(source, upload_dir, settings.max_upload_bytes)
            kind = "upload"
        else:
            default = default_reference_source()
            if default is None:
                return _render(
                    request,
                    templates,
                    "error.html",
                    status_code=400,
                    message="找不到随附的参考基准程序；请上传一个 .cpp 文件再试。",
                )
            path, kind = default, "reference"
        try:
            result = run_selfcheck(
                path,
                backend_name=settings.backend,
                data_root=settings.data_root,
                source_kind=kind,
            )
        except AssessorError as exc:
            return _render(
                request, templates, "error.html", status_code=400, message=str(exc)
            )
        return RedirectResponse(url=f"/reference/{result.report.selfcheck_id}", status_code=303)

    @app.get("/reference/{selfcheck_id}", response_class=HTMLResponse)
    def reference_result(request: Request, selfcheck_id: str) -> HTMLResponse:
        run_dir = _selfcheck_dir(settings, selfcheck_id)
        if not run_dir.is_dir():
            return _render(
                request,
                templates,
                "error.html",
                status_code=404,
                message=f"找不到实测记录：{selfcheck_id}",
            )
        try:
            report = load_report(run_dir)
        except AssessorError as exc:
            return _render(request, templates, "error.html", status_code=404, message=str(exc))
        outputs = {
            check.check_id: _read_text_limited(
                run_dir / "cases" / check.check_id / "stdout.txt"
            )
            for check in report.checks
        }
        return _render(
            request,
            templates,
            "reference_result.html",
            report=report,
            outputs=outputs,
            compile_stderr=_read_text_limited(run_dir / "build" / "compile.stderr.txt"),
            settings=settings,
        )

    @app.get("/reference/{selfcheck_id}/report.json")
    def download_selfcheck(selfcheck_id: str) -> Response:
        path = _selfcheck_dir(settings, selfcheck_id) / "report.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="报告尚未生成")
        return Response(
            content=path.read_bytes(),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{selfcheck_id}.json"'
            },
        )

    # -- 批量测评 -----------------------------------------------------------

    def _run_batch_job(directory: Path, batch: dict[str, Any]) -> None:
        """在单工作线程里串行执行“程序 × 任务”矩阵。"""
        batch["status"] = "running"
        write_batch(directory, batch)
        cancelled = False
        try:
            for entry in batch["entries"]:
                if (directory / "CANCEL").exists():
                    cancelled = True
                if cancelled:
                    entry["state"] = "cancelled"
                    write_batch(directory, batch)
                    continue
                entry["state"] = "running"
                app.state.pending[entry["run_id"]] = "running"
                write_batch(directory, batch)
                program = next(
                    item
                    for item in batch["programs"]
                    if item["program_id"] == entry["program_id"]
                )
                options = RunOptions(
                    task_ref=entry["task_ref"],
                    submission=directory / program["source"],
                    backend=settings.backend,
                    allow_draft=settings.allow_draft,
                    allow_unattested=settings.allow_unattested,
                    data_dir=settings.data_root,
                    task_library=settings.library,
                    run_id=entry["run_id"],
                )
                try:
                    result = execute_standard_run(options)
                    entry["state"] = "done"
                    report = result.report
                    entry["assessment_status"] = (
                        report.assessment_status.value if report else None
                    )
                    entry["execution_status"] = (
                        report.execution_status.value
                        if report
                        else (
                            result.execution_status.value
                            if result.execution_status
                            else None
                        )
                    )
                except BaseException as exc:  # noqa: BLE001 - 线程内异常要展示给页面
                    entry["state"] = "failed"
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    app.state.errors[entry["run_id"]] = entry["error"]
                finally:
                    app.state.pending.pop(entry["run_id"], None)
                    write_batch(directory, batch)
            batch["status"] = "cancelled" if cancelled else "completed"
        except BaseException as exc:  # noqa: BLE001 - 批次级失败也要落盘
            for pending_entry in batch["entries"]:
                if pending_entry["state"] in {"queued", "running"}:
                    pending_entry["state"] = "failed"
                    pending_entry["error"] = f"{type(exc).__name__}: {exc}"
            batch["status"] = "completed"
            batch["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            batch["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_batch(directory, batch)

    @app.get("/batch", response_class=HTMLResponse)
    def batch_page(request: Request) -> HTMLResponse:
        return _render(
            request,
            templates,
            "batch.html",
            tasks=list_tasks(settings.library),
            batches=list_batches(settings.data_root, limit=8),
            max_programs=MAX_PROGRAMS,
            max_tasks=MAX_TASKS,
            settings=settings,
        )

    @app.post("/batch")
    async def create_batch(
        request: Request,
        tasks: list[str] = Form(...),
        submissions: list[UploadFile] = File(...),
    ) -> Response:
        selected: list[str] = []
        for raw in tasks:
            ref = (raw or "").strip()
            if ref and ref not in selected:
                selected.append(ref)
        if not selected:
            raise HTTPException(status_code=400, detail="请至少选择一道题")
        if len(selected) > MAX_TASKS:
            raise HTTPException(
                status_code=400, detail=f"一次最多选择 {MAX_TASKS} 道题"
            )
        for ref in selected:
            try:
                load_task(ref, task_library=settings.library, allow_draft=True)
            except AssessorError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        uploads = [item for item in submissions if item is not None and item.filename]
        if not uploads:
            raise HTTPException(
                status_code=400, detail="请至少上传一个程序（.zip 或 .cpp/.h/.hpp 等源文件）"
            )
        if len(uploads) > MAX_PROGRAMS:
            raise HTTPException(
                status_code=400, detail=f"一次最多上传 {MAX_PROGRAMS} 个程序"
            )
        for upload in uploads:
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in PROGRAM_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"不支持的批量程序格式：{suffix or '(无扩展名)'}；"
                        "请上传 .zip 或 .cpp/.h/.hpp 等源文件"
                    ),
                )

        batch_id = new_batch_id()
        directory = _batch_dir(settings, batch_id)
        programs: list[dict[str, Any]] = []
        try:
            for index, upload in enumerate(uploads, start=1):
                program_id = f"p{index}"
                saved = _save_upload(
                    upload,
                    directory / "programs" / program_id / "incoming",
                    settings.max_upload_bytes,
                    allowed=PROGRAM_SUFFIXES,
                )
                programs.append(prepare_program(directory, program_id, saved))
        except (InvalidInputError, OSError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        batch = build_batch(
            batch_id=batch_id,
            tasks=selected,
            programs=programs,
            run_id_factory=new_run_id,
        )
        write_batch(directory, batch)
        for entry in batch["entries"]:
            app.state.pending[entry["run_id"]] = "queued"
        app.state.executor.submit(_run_batch_job, directory, batch)
        return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)

    @app.get("/batch/{batch_id}", response_class=HTMLResponse)
    def batch_detail(request: Request, batch_id: str) -> HTMLResponse:
        directory = _batch_dir(settings, batch_id)
        batch = read_batch(directory)
        if batch is None:
            return _render(
                request,
                templates,
                "error.html",
                status_code=404,
                message=f"找不到批次：{batch_id}",
            )
        return _render(
            request,
            templates,
            "batch_detail.html",
            batch=batch,
            rows=matrix(batch),
            progress=count_progress(batch),
            settings=settings,
        )

    @app.get("/api/batches/{batch_id}")
    def batch_status(batch_id: str) -> JSONResponse:
        batch = read_batch(_batch_dir(settings, batch_id))
        if batch is None:
            raise HTTPException(status_code=404, detail="找不到批次")
        return JSONResponse(
            {
                "batch_id": batch["batch_id"],
                "status": batch["status"],
                "progress": count_progress(batch),
            }
        )

    @app.post("/batch/{batch_id}/cancel")
    def cancel_batch(batch_id: str) -> Response:
        directory = _batch_dir(settings, batch_id)
        batch = read_batch(directory)
        if batch is None:
            raise HTTPException(status_code=404, detail="找不到批次")
        (directory / "CANCEL").write_text("cancel requested\n", encoding="utf-8")
        for entry in batch["entries"]:
            if entry.get("state") != "running":
                continue
            run_dir = settings.data_root / "runs" / entry["run_id"]
            if run_dir.is_dir():
                (run_dir / "CANCEL").write_text(
                    "cancel requested from batch\n", encoding="utf-8"
                )
        return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "version": ASSESSOR_VERSION, "backend": settings.backend}

    return app


def serve(
    settings: WebSettings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """启动本地 Web 服务（阻塞，直到 Ctrl+C）。"""
    import uvicorn

    if settings.backend == "wasm_pilot" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("WebAssembly 试验只能监听本机回环地址")
    app = create_app(settings)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"本地 Web 界面：{url}（Ctrl+C 停止）")
    if settings.backend == "local" and not settings.allow_unattested:
        print("提示：本地后端缺少网络/文件系统隔离，未放行时运行结论为 NOT_ASSESSED；")
        print("      演示请加 --allow-unattested-backend（报告会标注 grade_eligible=false）。")
    uvicorn.run(app, host=host, port=port, log_level="info")

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
import json
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ...config import ASSESSOR_VERSION, data_dir, task_library_dir
from ...domain.errors import AssessorError, InvalidInputError
from ...domain.models import AssessmentReport, TaskCase
from ...orchestrator.runner import RunOptions, execute_standard_run, make_backend, new_run_id
from ...reporting.export import CSV_COLUMNS, report_json, report_rows
from ...storage.db import Database
from ...tasks.loader import list_tasks, load_task

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

ALLOWED_UPLOAD_SUFFIXES = {".zip", ".cpp", ".h", ".hpp"}
TERMINAL_WORKFLOW_STATES = {"COMPLETED", "FAILED", "CANCELLED"}

# 判定理由代码 → 面向教师的中文说明（只做展示翻译，不改变判定）。
REASON_LABELS = {
    "EXECUTION_BACKEND_NOT_ATTESTED": "执行后端未通过隔离能力门禁，本次不计入正式成绩",
    "ASSERTION_MISMATCH": "存在与预期不一致的必需断言",
    "SUBJECT_CRASH_OR_LIMIT": "被测程序在合规调用中崩溃或触达资源上限",
    "COVERAGE_INCOMPLETE": "必需用例或断言未全部执行",
    "ASSERTIONS_NOT_COMPARED": "存在无法比较的必需断言",
    "ADAPTER_OR_INFRA_FAULT": "出现适配器或基础设施故障",
    "TASK_NOT_PUBLISHED": "任务未发布（DRAFT）",
    "ASSERTIONS_NOT_CONFIRMED": "期望值未经参照版本确认",
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


def _run_dir(settings: WebSettings, run_id: str) -> Path:
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


def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> Path:
    """把上传内容写到磁盘，超过上限立即拒绝（不信任 Content-Length）。"""
    name = Path(upload.filename or "upload.bin").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
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


def _render(
    request: Request,
    templates: Jinja2Templates,
    name: str,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    context.setdefault("app_version", ASSESSOR_VERSION)
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


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

    def db() -> Database:
        return Database(settings.data_root / "app.sqlite3")

    # -- 页面 ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
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
        if report is None and row is None:
            return _render(
                request,
                templates,
                "error.html",
                status_code=404,
                message=f"找不到运行记录：{run_id}",
            )
        if report is None:
            return _render(
                request,
                templates,
                "run_waiting.html",
                run_id=run_id,
                row=row,
                error=app.state.errors.get(run_id),
                settings=settings,
            )
        return _render(
            request,
            templates,
            "run.html",
            report=report,
            row=row,
            badge=_status_badge(report),
            reason_labels=REASON_LABELS,
            settings=settings,
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
            state = "running"
        else:
            state = "unknown"
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
        return JSONResponse(
            {
                "run_id": run_id,
                "state": state,
                "error": app.state.errors.get(run_id),
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

    app = create_app(settings)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"本地 Web 界面：{url}（Ctrl+C 停止）")
    if settings.backend == "local" and not settings.allow_unattested:
        print("提示：本地后端缺少网络/文件系统隔离，未放行时运行结论为 NOT_ASSESSED；")
        print("      演示请加 --allow-unattested-backend（报告会标注 grade_eligible=false）。")
    uvicorn.run(app, host=host, port=port, log_level="info")

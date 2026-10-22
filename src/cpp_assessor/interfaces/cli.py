# -*- coding: utf-8 -*-
"""命令行入口（架构实施方案 §13 的最小交付）。

命令：
    assessor task validate <task_dir> [--json]
    assessor task publish <task_dir>
    assessor task list [--json]
    assessor sandbox capabilities [--backend local|mock]
    assessor run --task <task_id[@version]> (--submission <path> | --variant <id>)
                 [--target-before <path>] [--backend local|mock] [--allow-draft]
                 [--allow-unattested-backend] [--json]
    assessor status <run_id> [--json]
    assessor cancel <run_id>
    assessor report <run_id> [--format json|csv] [--out <path>]
    assessor replay <run_id>
    assessor selfcheck [--source <file.cpp>] [--backend local|mock] [--json] [--strict]
    assessor web [--host] [--port] [--backend local|mock]
                 [--allow-unattested-backend] [--allow-draft] [--no-open]

退出码：PASS=0、FAIL=1、NOT_ASSESSED=2、INCONCLUSIVE=3、基础设施错误=4、取消=130。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ..config import data_dir
from ..domain.enums import ComparisonStatus, CompileStatus, Mode, ProtocolStatus
from ..domain.errors import AssessorError, InvalidInputError
from ..domain.models import AssessmentReport, Observation
from ..judge.assertions import evaluate
from ..orchestrator.runner import (
    EXIT_INCONCLUSIVE,
    RunOptions,
    execute_standard_run,
    make_backend,
)
from ..reporting.export import (
    case_result_lines,
    summary_lines,
    write_csv,
    write_json,
)
from ..selfcheck import default_reference_source, run_selfcheck
from ..storage.db import Database
from ..tasks.loader import list_tasks, load_task, publish_task, validate_task


# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------


def _ensure_utf8_stdio() -> None:
    """Windows 上管道重定向时 Python 默认用 GBK，会把中文写坏。

    只在当前编码不是 UTF-8 且不是真实控制台时切换，避免影响控制台自身的
    UTF-16 写入路径。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        if getattr(stream, "isatty", lambda: False)():
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _run_dir(run_id: str) -> Path:
    return data_dir() / "runs" / run_id


def _load_report(run_id: str) -> AssessmentReport:
    path = _run_dir(run_id) / "objective_report.json"
    if not path.is_file():
        raise InvalidInputError(f"找不到运行记录的报告：{path}")
    return AssessmentReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# task 子命令
# ---------------------------------------------------------------------------


def _cmd_task_validate(args: argparse.Namespace) -> int:
    validation = validate_task(Path(args.task_dir))
    if args.json:
        _print_json(validation.summary())
    else:
        print(f"任务包：{validation.task_dir}")
        print(f"包哈希：{validation.package_hash or '(未计算)'}")
        for check in validation.checks:
            mark = "OK  " if check.passed else "FAIL"
            detail = f"  {check.detail}" if (check.detail and not check.passed) else ""
            print(f"[{mark}] {check.check_id}: {check.description}{detail}")
    return 0 if validation.passed else 1


def _cmd_task_publish(args: argparse.Namespace) -> int:
    summary = publish_task(Path(args.task_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_task_list(args: argparse.Namespace) -> int:
    tasks = list_tasks()
    if args.json:
        _print_json(tasks)
        return 0
    if not tasks:
        print("题库为空。")
        return 0
    print(f"{'task_id':28s} {'version':8s} {'status':10s} {'cases':5s} title")
    for item in tasks:
        print(
            f"{item.get('task_id', '?'):28s} {item.get('version', '?'):8s} "
            f"{item.get('publication_status', item.get('status', '?')):10s} "
            f"{str(item.get('case_count', '-')):5s} {item.get('title', item.get('error', ''))}"
        )
    return 0


# ---------------------------------------------------------------------------
# sandbox / run / status / cancel / report / replay
# ---------------------------------------------------------------------------


def _cmd_sandbox_capabilities(args: argparse.Namespace) -> int:
    backend = make_backend(args.backend)
    capabilities = backend.capabilities()
    payload = capabilities.model_dump(mode="json")
    payload["simulated"] = backend.simulated
    _print_json(payload)
    if not backend.simulated and not capabilities.network_block:
        print(
            "\n注意：该后端不具备网络隔离与文件系统隔离，按 §9.3 能力门禁，"
            "真实提交被拒绝；--allow-unattested-backend 仅放行明确选择的内置可信参考样例，不具备正式评分资格。",
            file=sys.stderr,
        )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.submission and not args.variant:
        raise InvalidInputError("必须提供 --submission 或 --variant 之一")
    mode = Mode.LLM if getattr(args, "llm", False) else Mode.STANDARD
    options = RunOptions(
        task_ref=args.task,
        submission=Path(args.submission) if args.submission else None,
        mode=mode,
        backend=args.backend,
        allow_draft=args.allow_draft,
        allow_unattested=args.allow_unattested_backend,
        target_before=Path(args.target_before) if args.target_before else None,
        variant=args.variant,
    )
    if mode is Mode.LLM:
        from ..orchestrator.mode_two import execute_llm_run

        result = execute_llm_run(options)
    else:
        result = execute_standard_run(options)
    if args.json:
        _print_json(
            {
                "run_id": result.run_id,
                "workflow_state": result.workflow_state.value,
                "execution_status": result.execution_status.value,
                "assessment_status": (
                    result.assessment_status.value if result.assessment_status else None
                ),
                "grade_eligible": result.grade_eligible,
                "exit_code": result.exit_code,
                "run_dir": str(result.run_dir),
                "error": result.error,
            }
        )
        return result.exit_code
    print(f"运行目录：{result.run_dir}")
    if result.report is not None:
        for line in summary_lines(result.report):
            print(line)
        print("用例结果：")
        for line in case_result_lines(result.report):
            print(line)
        print(f"报告：{result.run_dir / 'objective_report.json'}")
    else:
        print(f"执行状态：{result.execution_status.value}")
    if result.error:
        print(f"说明：{result.error}")
    return result.exit_code


def _cmd_status(args: argparse.Namespace) -> int:
    db = Database(data_dir() / "app.sqlite3")
    run = db.get_run(args.run_id)
    if run is None:
        raise InvalidInputError(f"找不到运行记录：{args.run_id}")
    events = db.events(args.run_id)[-10:]
    if args.json:
        payload = run.as_dict()
        payload["recent_events"] = events
        _print_json(payload)
        return 0
    for key, value in run.as_dict().items():
        print(f"{key:20s}: {value}")
    print("最近事件：")
    for event in events:
        print(f"  #{event['seq']:<3d} {event['state']:14s} {event['kind']}")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    if not run_dir.is_dir():
        raise InvalidInputError(f"找不到运行目录：{run_dir}")
    marker = run_dir / "CANCEL"
    marker.write_text("cancel requested\n", encoding="utf-8")
    print(f"已写入取消标记：{marker}")
    print("运行器会在下一个检查点停止；已完成的运行不受影响。")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    report = _load_report(args.run_id)
    if args.format == "json":
        payload = args.out or str(_run_dir(args.run_id) / "objective_report.json")
        if args.out:
            write_json(report, Path(args.out))
            print(f"已导出 JSON：{Path(args.out).resolve()}")
        else:
            print(report.model_dump_json(indent=2))
        return 0
    out = Path(args.out) if args.out else data_dir() / "exports" / f"{args.run_id}.csv"
    write_csv(report, out)
    print(f"已导出 CSV：{out.resolve()}")
    print(f"共 {len(report.case_results)} 行用例结果。")
    return 0


def _cmd_rejudge(args: argparse.Namespace) -> int:
    report = _load_report(args.run_id)
    run_dir = _run_dir(args.run_id)
    task = load_task(
        f"{report.provenance.task_id}@{report.provenance.task_version}",
        allow_draft=True,
    )
    if task.package_hash != report.provenance.task_package_hash:
        raise InvalidInputError("任务哈希不匹配，拒绝用新规则伪称原判定复现")
    assertions = {item.assertion_id: item for item in task.method.assertions}
    problems: list[str] = []
    replayed = 0
    for case_result in report.case_results:
        case = task.cases[case_result.case_id]
        observation_path = (
            run_dir / "evidence" / "cases" / case_result.case_id / "observation.json"
        )
        if not observation_path.is_file():
            if case_result.comparison is not ComparisonStatus.NOT_COMPARED:
                problems.append(f"{case_result.case_id}: 缺少观测文件但报告有比较结果")
            continue
        observation = Observation.model_validate(
            json.loads(observation_path.read_text(encoding="utf-8"))
        )
        if observation.protocol_status is not ProtocolStatus.OK or observation.actual is None:
            if case_result.comparison is not ComparisonStatus.NOT_COMPARED:
                problems.append(f"{case_result.case_id}: 观测不可用但报告有比较结果")
            continue
        for assertion_id in case.assertion_ids:
            outcome = evaluate(
                assertions[assertion_id],
                case_expected=case.expected,
                actual_document=observation.actual,
            )
            stored = next(
                (
                    item
                    for item in case_result.assertion_results
                    if item.assertion_id == assertion_id
                ),
                None,
            )
            replayed += 1
            if stored is None or stored.comparison is not outcome.comparison:
                problems.append(
                    f"{case_result.case_id}/{assertion_id}: "
                    f"存储={stored.comparison.value if stored else '缺失'} "
                    f"重算={outcome.comparison.value}"
                )
    if problems:
        print("replay 发现证据与判定不一致：")
        for problem in problems:
            print(f"  - {problem}")
        return EXIT_INCONCLUSIVE
    if replayed == 0:
        raise InvalidInputError("没有重新计算任何断言，不能报告重算一致")
    print(
        f"rejudge 一致：{len(report.case_results)} 个用例、{replayed} 条断言可由证据复现，"
        f"结论 {report.assessment_status.value}。"
    )
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from ..orchestrator.replay import replay_run
    result = replay_run(args.run_id, root=data_dir(), allow_wasm_pilot=args.allow_wasm_pilot)
    print(f"重放生成新运行 {result.run_id}，父运行 {args.run_id}，结果 {result.assessment_status}")
    return result.exit_code


# ---------------------------------------------------------------------------
# selfcheck 子命令
# ---------------------------------------------------------------------------

_VERDICT_MARKS = {"PASS": "OK  ", "DEFECT": "缺陷", "NOT_OBSERVED": "未观察"}


def _cmd_selfcheck(args: argparse.Namespace) -> int:
    source = Path(args.source) if args.source else default_reference_source()
    if source is None:
        raise InvalidInputError(
            "找不到随附的参考基准程序；请用 --source 指定一个 .cpp 文件"
        )
    result = run_selfcheck(
        source,
        backend_name=args.backend,
        source_kind="upload" if args.source else "reference",
    )
    report = result.report
    if args.json:
        _print_json(report.model_dump(mode="json"))
    else:
        print(f"基准程序实测：{report.selfcheck_id}")
        print(f"源码：{report.source_name}（sha256 {report.source_sha256[:12]}…）")
        gate = "通过隔离门禁" if report.sandbox_attested else "未通过隔离门禁，结果仅供参考"
        print(f"后端：{report.backend_id}（{gate}）")
        print(
            f"编译：{report.compile.status.value}"
            f"（{report.compile.duration_ms} ms，退出码 {report.compile.exit_code}）"
        )
        print("观察结果：")
        if report.compile.status is not CompileStatus.SUCCESS:
            stderr_path = result.run_dir / report.compile.stderr_ref
            print(f"  编译未通过，未执行任何检查。编译输出：{stderr_path}")
            if stderr_path.is_file():
                for line in stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[:6]:
                    print(f"    {line}")
        for check in report.checks:
            mark = _VERDICT_MARKS.get(check.verdict, check.verdict)
            print(f"  [{mark}] {check.check_id}：{check.title}")
            print(f"         {check.detail}")
        summary = report.summary
        print(
            f"汇总：共 {summary.total} 项，符合 {summary.passed}，"
            f"不符 {summary.defects}，未观察到 {summary.not_observed}"
        )
        print(f"报告：{result.report_path}")
        print(
            "说明：这是观察型自检，用固定脚本驱动程序行为，不参与五道小改题的评分。"
        )
    if report.compile.status is not CompileStatus.SUCCESS:
        return 2
    if args.strict and report.summary.defects:
        return 1
    return 0


# ---------------------------------------------------------------------------
# web 子命令
# ---------------------------------------------------------------------------


def _cmd_web(args: argparse.Namespace) -> int:
    # 延迟导入：不使用 Web 时不必安装 fastapi/uvicorn。
    from .web.app import WebSettings, serve

    settings = WebSettings(
        backend=args.backend,
        allow_unattested=args.allow_unattested_backend,
        allow_draft=args.allow_draft,
    )
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"警告：Web 界面没有登录与鉴权，当前监听 {args.host}，"
            "同网段任何人都能提交代码；仅建议在本机使用。",
            file=sys.stderr,
        )
    serve(
        settings,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )
    return 0


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assessor",
        description="面向 C++ 程序设计教学的代码小改自动测评原型（模式一）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    task_parser = sub.add_parser("task", help="题库相关操作")
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)

    validate_parser = task_sub.add_parser("validate", help="校验任务包")
    validate_parser.add_argument("task_dir")
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=_cmd_task_validate)

    publish_parser = task_sub.add_parser("publish", help="校验并发布任务包")
    publish_parser.add_argument("task_dir")
    publish_parser.set_defaults(func=_cmd_task_publish)

    list_parser = task_sub.add_parser("list", help="列出题库")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=_cmd_task_list)

    sandbox_parser = sub.add_parser("sandbox", help="沙箱能力检查")
    sandbox_sub = sandbox_parser.add_subparsers(dest="sandbox_command", required=True)
    capabilities_parser = sandbox_sub.add_parser("capabilities", help="打印后端能力")
    capabilities_parser.add_argument("--backend", default="local", choices=["local", "mock", "wasm_pilot"])
    capabilities_parser.set_defaults(func=_cmd_sandbox_capabilities)

    run_parser = sub.add_parser("run", help="执行一次标准测评")
    run_parser.add_argument("--task", required=True, help="任务引用，如 t1_phone_validation@1.0.0")
    run_parser.add_argument("--submission", help="学生提交（目录、单文件或 zip）")
    run_parser.add_argument("--variant", help="参照版本 ID（correct/faulty），用于校准")
    run_parser.add_argument("--target-before", help="学生修改前的工程快照")
    run_parser.add_argument("--backend", default="local", choices=["local", "mock", "wasm_pilot"])
    run_parser.add_argument("--allow-draft", action="store_true", help="允许运行未发布任务")
    run_parser.add_argument(
        "--allow-unattested-backend",
        action="store_true",
        help="显式放行未通过能力门禁的后端（报告标记 grade_eligible=false）",
    )
    run_parser.add_argument(
        "--llm",
        action="store_true",
        help="模式二：让模型为这份程序生成适配声明（需要 ASSESSOR_LLM_API_KEY）",
    )
    run_parser.add_argument("--json", action="store_true")
    run_parser.set_defaults(func=_cmd_run)

    status_parser = sub.add_parser("status", help="查看运行状态")
    status_parser.add_argument("run_id")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=_cmd_status)

    cancel_parser = sub.add_parser("cancel", help="请求取消运行")
    cancel_parser.add_argument("run_id")
    cancel_parser.set_defaults(func=_cmd_cancel)

    report_parser = sub.add_parser("report", help="导出报告")
    report_parser.add_argument("run_id")
    report_parser.add_argument("--format", default="json", choices=["json", "csv"])
    report_parser.add_argument("--out")
    report_parser.set_defaults(func=_cmd_report)

    rejudge_parser = sub.add_parser("rejudge", help="仅用既有观测重新计算断言")
    rejudge_parser.add_argument("run_id")
    rejudge_parser.set_defaults(func=_cmd_rejudge)
    replay_parser = sub.add_parser("replay", help="实际重执行冻结方案，不调用模型，创建新运行")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--allow-wasm-pilot", action="store_true", help="显式允许可信手机号样例的 WebAssembly 试验重放")
    replay_parser.set_defaults(func=_cmd_replay)

    selfcheck_parser = sub.add_parser(
        "selfcheck", help="实测一个原始 C++ 程序（观察型自检，不参与五题评分）"
    )
    selfcheck_parser.add_argument(
        "--source", help="要实测的 .cpp 文件；默认使用随附的参考基准程序"
    )
    selfcheck_parser.add_argument("--backend", default="local", choices=["local", "mock", "wasm_pilot"])
    selfcheck_parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    selfcheck_parser.add_argument(
        "--strict", action="store_true", help="发现 DEFECT 时返回退出码 1"
    )
    selfcheck_parser.set_defaults(func=_cmd_selfcheck)

    web_parser = sub.add_parser("web", help="启动本地 Web 界面")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument("--backend", default="local", choices=["local", "mock", "wasm_pilot"])
    web_parser.add_argument(
        "--allow-unattested-backend",
        action="store_true",
        help="允许在未通过能力门禁的后端上运行（报告标注 grade_eligible=false）",
    )
    web_parser.add_argument("--allow-draft", action="store_true", help="允许运行未发布任务")
    web_parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    web_parser.set_defaults(func=_cmd_web)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AssessorError as exc:
        print(f"错误[{exc.execution_status.value}]：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

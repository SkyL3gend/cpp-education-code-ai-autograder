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

退出码：PASS=0、FAIL=1、NOT_ASSESSED=2、INCONCLUSIVE=3、基础设施错误=4、取消=130。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ..config import data_dir
from ..domain.enums import ComparisonStatus, ProtocolStatus
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
            "真实提交默认被拒绝（可用 --allow-unattested-backend 放行，但不得计入验收）。",
            file=sys.stderr,
        )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.submission and not args.variant:
        raise InvalidInputError("必须提供 --submission 或 --variant 之一")
    options = RunOptions(
        task_ref=args.task,
        submission=Path(args.submission) if args.submission else None,
        backend=args.backend,
        allow_draft=args.allow_draft,
        allow_unattested=args.allow_unattested_backend,
        target_before=Path(args.target_before) if args.target_before else None,
        variant=args.variant,
    )
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


def _cmd_replay(args: argparse.Namespace) -> int:
    report = _load_report(args.run_id)
    run_dir = _run_dir(args.run_id)
    task = load_task(
        f"{report.provenance.task_id}@{report.provenance.task_version}",
        allow_draft=True,
    )
    if task.package_hash != report.provenance.task_package_hash:
        print(
            "警告：当前任务包哈希与报告不一致，replay 使用当前任务包重新判定。",
            file=sys.stderr,
        )
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
    print(
        f"replay 一致：{len(report.case_results)} 个用例、{replayed} 条断言可由证据复现，"
        f"结论 {report.assessment_status.value}。"
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
    capabilities_parser.add_argument("--backend", default="local", choices=["local", "mock"])
    capabilities_parser.set_defaults(func=_cmd_sandbox_capabilities)

    run_parser = sub.add_parser("run", help="执行一次标准测评")
    run_parser.add_argument("--task", required=True, help="任务引用，如 t1_phone_validation@1.0.0")
    run_parser.add_argument("--submission", help="学生提交（目录、单文件或 zip）")
    run_parser.add_argument("--variant", help="参照版本 ID（correct/faulty），用于校准")
    run_parser.add_argument("--target-before", help="学生修改前的工程快照")
    run_parser.add_argument("--backend", default="local", choices=["local", "mock"])
    run_parser.add_argument("--allow-draft", action="store_true", help="允许运行未发布任务")
    run_parser.add_argument(
        "--allow-unattested-backend",
        action="store_true",
        help="显式放行未通过能力门禁的后端（报告标记 grade_eligible=false）",
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

    replay_parser = sub.add_parser("replay", help="用已保存的证据复现判定")
    replay_parser.add_argument("run_id")
    replay_parser.set_defaults(func=_cmd_replay)

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

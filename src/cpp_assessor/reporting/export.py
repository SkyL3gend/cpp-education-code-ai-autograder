# -*- coding: utf-8 -*-
"""报告导出（JSON / CSV）。

判定结论只来自 judge 包的客观比较结果；本模块只负责格式转换，不改变任何结论。
CSV 需要防止公式注入（§11）：以 = + - @ 开头的单元格一律加前缀单引号。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable

from ..domain.canonical import canonical_json
from ..domain.models import AssessmentReport, OverallStatus

CSV_COLUMNS = [
    "run_id",
    "task_id",
    "task_version",
    "mode",
    "execution_status",
    "assessment_status",
    "grade_eligible",
    "validation_level",
    "case_id",
    "purpose",
    "comparison",
    "termination",
    "protocol_status",
    "failed_assertions",
    "note",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def guard_cell(value: Any) -> str:
    """把单元格转成字符串，并阻断电子表格公式注入。"""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        text = ";".join(str(item) for item in value)
    else:
        text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def report_rows(report: AssessmentReport) -> list[list[str]]:
    rows: list[list[str]] = []
    provenance = report.provenance
    for case in report.case_results:
        rows.append(
            [
                report.run_id,
                provenance.task_id,
                provenance.task_version,
                report.mode.value,
                report.execution_status.value,
                report.assessment_status.value,
                str(report.grade_eligible).lower(),
                report.validation_level.value,
                case.case_id,
                case.purpose.value,
                case.comparison.value,
                case.termination.value,
                case.protocol_status.value,
                ";".join(case.failed_assertions),
                case.note or "",
            ]
        )
    return [[guard_cell(cell) for cell in row] for row in rows]


def write_csv(report: AssessmentReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(report_rows(report))
    # utf-8-sig：Excel 双击打开时才能正确识别中文；纯 UTF-8 会显示乱码。
    path.write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    return path


def write_json(report: AssessmentReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = report.model_dump(mode="json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def report_json(report: AssessmentReport) -> str:
    return canonical_json(report.model_dump(mode="json"))


def summary_lines(report: AssessmentReport) -> list[str]:
    """人类可读的结论摘要（不改变语义）。"""
    counts = report.counts
    lines = [
        f"run_id          : {report.run_id}",
        f"任务            : {report.provenance.task_id}@{report.provenance.task_version}",
        f"执行状态        : {report.execution_status.value}",
        f"判定结论        : {report.assessment_status.value}"
        + ("（计入正式成绩）" if report.grade_eligible else "（不计入正式成绩）"),
        f"校验等级        : {report.validation_level.value}",
        f"后端            : {report.provenance.execution_backend}"
        + ("（已通过能力门禁）" if report.provenance.sandbox_attested else "（未通过能力门禁）"),
        f"用例            : 必测 {counts.required_cases} / 已尝试 {counts.attempted_cases} / "
        f"完成 {counts.completed_cases} / 一致 {counts.matched_cases} / "
        f"不一致 {counts.mismatched_cases} / 未比较 {counts.not_compared_cases}",
        f"断言            : 必测 {counts.required_assertions} / 一致 {counts.matched_assertions} / "
        f"不一致 {counts.mismatched_assertions} / 未比较 {counts.not_compared_assertions}",
    ]
    if report.grade_eligible_reasons:
        lines.append("判定说明        : " + "；".join(report.grade_eligible_reasons))
    if report.observations_note:
        lines.append("执行说明        : " + report.observations_note)
    if report.coverage.missing_partitions or report.coverage.missing_assertions:
        lines.append(
            "覆盖缺口        : 分区 "
            + ", ".join(report.coverage.missing_partitions)
            + "；断言 "
            + ", ".join(report.coverage.missing_assertions)
        )
    return lines


def case_result_lines(report: AssessmentReport) -> Iterable[str]:
    for case in report.case_results:
        failed = ",".join(case.failed_assertions) or "-"
        note = f" ({case.note})" if case.note else ""
        yield (
            f"  {case.case_id:24s} {case.comparison.value:12s} "
            f"{case.termination.value:16s} 失败断言={failed}{note}"
        )


def is_pass(report: AssessmentReport) -> bool:
    return report.assessment_status is OverallStatus.PASS

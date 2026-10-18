"""端到端：把单文件完整程序（参考基准程序）直接交给题目测评。

提交物与任务基准工程没有交集时，不再拒绝，而是进入单文件适配（观察型）：
只编译该 .cpp，用固定菜单脚本观察一条规则，生成正式报告但标注不计入成绩。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cpp_assessor.domain.enums import CasePurpose, ExecutionStatus, OverallStatus, WorkflowState
from cpp_assessor.orchestrator.runner import RunOptions, execute_standard_run

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="需要 g++ 13 与本地 Win32 Job Object 后端"
)

REFERENCE_PROGRAM = Path(__file__).resolve().parents[2] / "参考基准程序" / "通讯录_v6最终版.cpp"


def _run(task_ref: str, tmp_path: Path):
    assert REFERENCE_PROGRAM.is_file(), f"缺少参考程序：{REFERENCE_PROGRAM}"
    return execute_standard_run(
        RunOptions(
            task_ref=task_ref,
            submission=REFERENCE_PROGRAM,
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path,
        )
    )


def test_single_file_submission_is_adapted_instead_of_rejected(tmp_path):
    result = _run("t1_phone_validation@1.0.0", tmp_path)

    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.grade_eligible is False
    assert (result.run_dir / "objective_report.json").is_file()

    report = result.report
    assert report is not None
    assert len(report.case_results) == 1

    case = report.case_results[0]
    assert case.case_id == "single_file_phone_non_digit"
    assert case.purpose is CasePurpose.ASSESSMENT
    assert case.assertion_results[0].assertion_id == "single_file_phone_non_digit"

    # 参考程序只检查“11 位、以 1 开头”，会存下 1380013800a，因此该规则被抓到。
    assert case.comparison.value == "MISMATCH"
    assert case.assertion_results[0].actual == {"invalid_phone_rejected": False}
    assert result.assessment_status is OverallStatus.FAIL

    # 报告必须如实说明这是观察型适配、不是完整评分。
    note = report.observations_note or ""
    assert "单文件适配（观察型）" in note
    assert "未执行" in note
    assert "execution_backend" not in note or "mock" not in note


def test_single_file_adaptation_can_also_pass(tmp_path):
    result = _run("t2_birthday_today@1.0.0", tmp_path)

    report = result.report
    assert report is not None
    case = report.case_results[0]
    assert case.case_id == "single_file_birthday_today"
    assert case.comparison.value == "MATCH"
    assert case.assertion_results[0].actual == {"today_birthday_reminded": True}
    assert result.assessment_status is OverallStatus.PASS
    assert result.grade_eligible is False


def test_multi_file_program_is_compiled_together(tmp_path):
    """一个文件夹里带依赖的多文件程序（zip 内含顶层目录）也要能直接测评。"""
    import zipfile

    package = tmp_path / "program.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "program/main.cpp",
            '#include <iostream>\n#include "greeting.h"\n'
            "int main() { std::cout << greeting() << \"\\n\"; return 0; }\n",
        )
        archive.writestr("program/greeting.h", "#pragma once\nconst char* greeting();\n")
        archive.writestr(
            "program/greeting.cpp",
            '#include "greeting.h"\nconst char* greeting() { return "hello"; }\n',
        )

    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            submission=package,
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path / "data",
        )
    )

    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.execution_status is ExecutionStatus.COMPLETED
    compile_record = (result.run_dir / "evidence" / "compile" / "compile.json").read_text(
        encoding="utf-8"
    )
    assert "SUCCESS" in compile_record

    build_plan = (result.run_dir / "plans" / "build_plan.json").read_text(encoding="utf-8")
    assert "generated/subject/main.cpp" in build_plan
    assert "generated/subject/greeting.cpp" in build_plan
    assert "generated/harness" not in build_plan

    report = result.report
    assert report is not None
    case = report.case_results[0]
    # 程序不是通讯录，探针脚本观察不到菜单事实；但编译与执行本身要成功。
    assert case.comparison.value == "NOT_COMPARED"
    assert case.note == "OBSERVATION_INCOMPLETE"
    assert "单文件适配（观察型）" in (report.observations_note or "")
    assert result.assessment_status is OverallStatus.INCONCLUSIVE

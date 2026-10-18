"""端到端：用真实 g++ 与本地受限后端跑标准测评（模式一）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cpp_assessor.domain.enums import (
    ExecutionStatus,
    OverallStatus,
    WorkflowState,
)
from cpp_assessor.orchestrator.runner import RunOptions, execute_standard_run

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="需要 g++ 13 与本地 Win32 Job Object 后端"
)


def test_correct_variant_passes_but_is_not_grade_eligible(tmp_path):
    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            variant="correct",
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path,
        )
    )
    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.assessment_status is OverallStatus.PASS
    assert result.grade_eligible is False
    assert result.exit_code == 0
    assert (result.run_dir / "objective_report.json").is_file()
    assert result.report is not None
    assert result.report.counts.matched_cases == 4


def test_faulty_variant_is_caught_by_non_digit_case(tmp_path):
    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            variant="faulty",
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path,
        )
    )
    assert result.assessment_status is OverallStatus.FAIL
    assert result.exit_code == 1
    failed_cases = {
        case.case_id
        for case in result.report.case_results
        if case.comparison.value == "MISMATCH"
    }
    assert failed_cases == {"c_non_digit_11"}


def test_compile_error_skips_cases_and_reports_submission_fault(tmp_path):
    submission = tmp_path / "student"
    submission.mkdir()
    (submission / "validators.cpp").write_text(
        "int main() { this is not valid c++ }\n", encoding="utf-8", newline="\n"
    )
    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            submission=submission,
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path / "run_data",
        )
    )
    assert result.execution_status is ExecutionStatus.SUBMISSION_COMPILE_ERROR
    assert result.assessment_status is OverallStatus.FAIL
    assert result.report.counts.attempted_cases == 0


def test_unrelated_source_file_falls_back_to_observation_instead_of_invalid_input(tmp_path):
    """提交与基准工程无关的单文件源码：编译运行后进入观察型适配。

    程序没有菜单输出，探针取不到可核对事实，因此只能 INCONCLUSIVE；
    关键是不能在入口处直接拒绝，也不能让运行卡在 REPORTING。"""
    submission = tmp_path / "student"
    submission.mkdir()
    (submission / "unrelated.cpp").write_text(
        "int main() { return 0; }\n", encoding="utf-8", newline="\n"
    )
    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            submission=submission,
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path / "run_data",
        )
    )
    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert result.grade_eligible is False
    assert (result.run_dir / "objective_report.json").is_file()
    assert result.report is not None
    assert result.report.counts.attempted_cases == 1
    case = result.report.case_results[0]
    assert case.case_id == "single_file_phone_non_digit"
    assert case.comparison.value == "NOT_COMPARED"
    assert case.note == "OBSERVATION_INCOMPLETE"
    assert "单文件适配（观察型）" in (result.report.observations_note or "")


def test_submission_with_wrapping_folder_still_matches_project(tmp_path):
    """学生把整个工程文件夹压缩上传时，去掉第一层目录后仍应匹配到基准工程，
    而不是被当成“无关程序”走观察型适配。"""
    project = (
        Path(__file__).resolve().parents[2]
        / "task_library"
        / "projects"
        / "contactbook_base"
        / "1.0.0"
    )
    submission = tmp_path / "student"
    (submission / "homework").mkdir(parents=True)
    shutil.copyfile(project / "validators.cpp", submission / "homework" / "validators.cpp")

    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            submission=submission,
            backend="local",
            allow_unattested=True,
            data_dir=tmp_path / "run_data",
        )
    )
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.report is not None
    assert result.report.counts.attempted_cases == 4
    assert not any(
        case.case_id.startswith("single_file_") for case in result.report.case_results
    )


def test_mock_backend_marks_execution_as_simulated(tmp_path):
    result = execute_standard_run(
        RunOptions(
            task_ref="t1_phone_validation@1.0.0",
            variant="correct",
            backend="mock",
            data_dir=tmp_path,
        )
    )
    assert result.assessment_status is OverallStatus.PASS
    assert result.grade_eligible is False
    assert result.report is not None
    assert "mock" in (result.report.observations_note or "")
    assert result.report.provenance.sandbox_attested is False

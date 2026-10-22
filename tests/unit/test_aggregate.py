"""整体判定聚合：PASS/FAIL/INCONCLUSIVE/NOT_ASSESSED 与 grade_eligible 正交。"""

from __future__ import annotations

from cpp_assessor.domain.enums import (
    ComparisonStatus,
    ExecutionStatus,
    OverallStatus,
    ProtocolStatus,
    Termination,
    ValidationLevel,
)
from cpp_assessor.judge.aggregate import aggregate


def run(**overrides):
    payload = {
        "execution_status": ExecutionStatus.COMPLETED,
        "case_results": [],
        "coverage": None,
        "validation_level": ValidationLevel.CALIBRATED,
        "original_build_confirmed": True,
        "task_published": True,
        "assertions_confirmed": True,
        "execution_backend_attested": True,
    }
    payload.update(overrides)
    return aggregate(**payload)


def test_all_cases_match_is_pass(make_case_result, make_coverage):
    result = run(
        case_results=[make_case_result()],
        coverage=make_coverage(),
    )
    assert result.assessment_status is OverallStatus.PASS
    assert result.grade_eligible is True
    assert result.counts.matched_cases == 1


def test_unattested_backend_keeps_conclusion_but_not_eligible(
    make_case_result, make_coverage
):
    result = run(
        case_results=[make_case_result()],
        coverage=make_coverage(),
        execution_backend_attested=False,
    )
    assert result.assessment_status is OverallStatus.PASS
    assert result.grade_eligible is False
    assert "EXECUTION_BACKEND_NOT_ATTESTED" in result.reasons


def test_assertion_mismatch_is_fail(make_case_result, make_coverage):
    result = run(
        case_results=[
            make_case_result(
                comparison=ComparisonStatus.MISMATCH,
                assertions=(ComparisonStatus.MISMATCH,),
            )
        ],
        coverage=make_coverage(),
    )
    assert result.assessment_status is OverallStatus.FAIL
    assert "ASSERTION_MISMATCH" in result.reasons


def test_compile_error_is_fail_without_running_cases(make_coverage):
    result = run(
        execution_status=ExecutionStatus.SUBMISSION_COMPILE_ERROR,
        case_results=[],
        coverage=make_coverage(),
    )
    assert result.assessment_status is OverallStatus.FAIL
    assert result.counts.attempted_cases == 0


def test_subject_crash_does_not_hide_incomplete_coverage(
    make_case_result, make_coverage
):
    """学生崩溃导致"未比较"时不能退化成 INCONCLUSIVE。"""
    result = run(
        case_results=[
            make_case_result(
                comparison=ComparisonStatus.NOT_COMPARED,
                termination=Termination.CRASH,
                protocol_status=ProtocolStatus.MISSING,
                note="SUBJECT_FAULT",
                assertions=(),
            )
        ],
        coverage=make_coverage(complete=False),
    )
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert "COVERAGE_INCOMPLETE" in result.reasons


def test_adapter_protocol_fault_is_inconclusive(make_case_result, make_coverage):
    result = run(
        case_results=[
            make_case_result(
                comparison=ComparisonStatus.NOT_COMPARED,
                protocol_status=ProtocolStatus.INVALID,
                note="ADAPTER_FAULT",
                assertions=(),
            )
        ],
        coverage=make_coverage(complete=False),
    )
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert "ADAPTER_OR_INFRA_FAULT" in result.reasons


def test_unpublished_task_is_inconclusive(make_case_result, make_coverage):
    result = run(
        case_results=[make_case_result()],
        coverage=make_coverage(),
        task_published=False,
    )
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert "TASK_NOT_PUBLISHED" in result.reasons


def test_unverified_validation_blocks_pass(make_case_result, make_coverage):
    result = run(
        case_results=[make_case_result()],
        coverage=make_coverage(),
        validation_level=ValidationLevel.UNVERIFIED,
    )
    assert result.assessment_status is OverallStatus.INCONCLUSIVE


def test_cancelled_run_is_not_assessed(make_coverage):
    result = run(
        execution_status=ExecutionStatus.CANCELLED,
        coverage=make_coverage(),
    )
    assert result.assessment_status is OverallStatus.NOT_ASSESSED
    assert result.grade_eligible is False


def test_no_cases_is_not_assessed(make_coverage):
    result = run(case_results=[], coverage=make_coverage(complete=True))
    assert result.assessment_status is OverallStatus.NOT_ASSESSED

def test_checked_adapter_is_only_exploratory(make_case_result, make_coverage):
    result = run(case_results=[make_case_result()], coverage=make_coverage(), validation_level=ValidationLevel.CHECKED)
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert result.grade_eligible is False

def test_unpublished_unverified_crash_cannot_acquire_qualification(make_case_result, make_coverage):
    result = run(case_results=[make_case_result(termination=Termination.CRASH, note="SUBJECT_FAULT")], coverage=make_coverage(), task_published=False, assertions_confirmed=False, validation_level=ValidationLevel.UNVERIFIED)
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert not result.grade_eligible

def test_generated_compile_failure_cannot_be_attributed_to_student(make_coverage):
    result = run(execution_status=ExecutionStatus.SUBMISSION_COMPILE_ERROR, coverage=make_coverage(), original_build_confirmed=False)
    assert result.assessment_status is OverallStatus.INCONCLUSIVE

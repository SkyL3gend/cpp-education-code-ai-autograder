"""判定聚合（架构实施方案 §7.3）。

严格按优先级得出结论，绝不把"没测到"当成"通过"：

1. 取消 / 导入非法 / 不支持 → NOT_ASSESSED
2. 已确认构建契约、归因 SUBMISSION 的编译失败 → FAIL（无需跑用例）
3. 适配、基础设施、未知故障，或规则未发布、断言未确认、覆盖不全、
   验证等级不足 → INCONCLUSIVE
4. 任一必需断言 MISMATCH，或合规调用中被测程序崩溃/超限 → FAIL
5. 全部 MATCH → PASS

`grade_eligible` 与 `assessment_status` 正交：结论可以明确是 FAIL，
但因为执行后端未通过能力门禁而不具备正式评分资格。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import (
    ComparisonStatus,
    ExecutionStatus,
    FaultOwner,
    OverallStatus,
    Termination,
    ValidationLevel,
)
from ..domain.models import CaseResult, CoverageReport, ReportCounts

LIMIT_TERMINATIONS = frozenset(
    {
        Termination.WALL_TIME_LIMIT,
        Termination.CPU_TIME_LIMIT,
        Termination.MEMORY_LIMIT,
        Termination.PROCESS_LIMIT,
        Termination.OUTPUT_LIMIT,
        Termination.DISK_LIMIT,
        Termination.CRASH,
    }
)

ASSESSED_STATUSES = frozenset({OverallStatus.PASS, OverallStatus.FAIL})


@dataclass(frozen=True)
class Aggregation:
    assessment_status: OverallStatus
    grade_eligible: bool
    reasons: list[str] = field(default_factory=list)
    counts: ReportCounts | None = None
    subject_limit_hit: bool = False
    adapter_issue: bool = False

    @property
    def is_pass(self) -> bool:
        return self.assessment_status is OverallStatus.PASS


def count_results(case_results: list[CaseResult], coverage: CoverageReport) -> ReportCounts:
    matched_cases = sum(
        1 for r in case_results if r.comparison is ComparisonStatus.MATCH
    )
    mismatched_cases = sum(
        1 for r in case_results if r.comparison is ComparisonStatus.MISMATCH
    )
    not_compared_cases = sum(
        1 for r in case_results if r.comparison is ComparisonStatus.NOT_COMPARED
    )
    matched_assertions = 0
    mismatched_assertions = 0
    not_compared_assertions = 0
    for result in case_results:
        for assertion in result.assertion_results:
            if assertion.comparison is ComparisonStatus.MATCH:
                matched_assertions += 1
            elif assertion.comparison is ComparisonStatus.MISMATCH:
                mismatched_assertions += 1
            else:
                not_compared_assertions += 1
    return ReportCounts(
        required_cases=len(coverage.planned_cases),
        attempted_cases=len(coverage.completed_cases),
        completed_cases=len(coverage.completed_cases),
        matched_cases=matched_cases,
        mismatched_cases=mismatched_cases,
        not_compared_cases=not_compared_cases,
        required_assertions=len(coverage.planned_assertions),
        matched_assertions=matched_assertions,
        mismatched_assertions=mismatched_assertions,
        not_compared_assertions=not_compared_assertions,
    )


def aggregate(
    *,
    execution_status: ExecutionStatus,
    case_results: list[CaseResult],
    coverage: CoverageReport,
    validation_level: ValidationLevel,
    task_published: bool,
    assertions_confirmed: bool,
    execution_backend_attested: bool,
) -> Aggregation:
    reasons: list[str] = []

    if execution_status is ExecutionStatus.CANCELLED:
        return Aggregation(OverallStatus.NOT_ASSESSED, False, ["RUN_CANCELLED"])
    if execution_status in (ExecutionStatus.INVALID_INPUT, ExecutionStatus.UNSUPPORTED):
        return Aggregation(
            OverallStatus.NOT_ASSESSED, False, [f"EXECUTION_{execution_status.value}"]
        )

    if execution_status is ExecutionStatus.SUBMISSION_COMPILE_ERROR:
        # 编译失败时没有任何用例被执行，覆盖统计必须如实清零。
        unattempted = coverage.model_copy(
            update={
                "completed_cases": [],
                "missed_cases": list(coverage.planned_cases),
                "covered_partitions": [],
                "missing_partitions": list(coverage.planned_partitions),
                "executed_assertions": [],
                "missing_assertions": list(coverage.planned_assertions),
                "complete": False,
            },
            deep=True,
        )
        return Aggregation(
            OverallStatus.FAIL,
            execution_backend_attested,
            ["SUBMISSION_COMPILE_ERROR"],
            count_results([], unattempted),
        )

    if execution_status is not ExecutionStatus.COMPLETED:
        return Aggregation(
            OverallStatus.INCONCLUSIVE,
            False,
            [f"EXECUTION_{execution_status.value}"],
            count_results(case_results, coverage),
        )

    if not case_results:
        return Aggregation(OverallStatus.NOT_ASSESSED, False, ["NO_CASES_EXECUTED"])

    subject_limit_hit = any(
        (
            result.termination in LIMIT_TERMINATIONS
            and result.note not in {"ADAPTER_FAULT", "INFRA_FAULT"}
        )
        or result.note == "SUBJECT_FAULT"
        for result in case_results
    )
    adapter_issue = any(
        result.protocol_status.value == "INVALID"
        or result.note in {"ADAPTER_FAULT", "INFRA_FAULT", "UNKNOWN_FAULT"}
        for result in case_results
    )
    mismatched = any(
        result.comparison is ComparisonStatus.MISMATCH for result in case_results
    )
    assertions_not_compared = any(
        assertion.comparison is ComparisonStatus.NOT_COMPARED
        for result in case_results
        for assertion in result.assertion_results
    )

    if subject_limit_hit:
        # 合规调用中被测程序崩溃/超限是确定的失败：即使因此缺少观测、
        # 覆盖不完整，也不能退化成 INCONCLUSIVE。
        reasons = ["SUBJECT_CRASH_OR_LIMIT"]
        eligible = execution_backend_attested
        if not eligible:
            reasons.append("EXECUTION_BACKEND_NOT_ATTESTED")
        return Aggregation(
            OverallStatus.FAIL,
            eligible,
            reasons,
            count_results(case_results, coverage),
            subject_limit_hit=True,
        )

    blockers: list[str] = []
    if not task_published:
        blockers.append("TASK_NOT_PUBLISHED")
    if not assertions_confirmed:
        blockers.append("ASSERTIONS_NOT_CONFIRMED")
    if validation_level is ValidationLevel.UNVERIFIED:
        blockers.append(f"VALIDATION_LEVEL_{validation_level.value}")
    if not coverage.complete:
        blockers.append("COVERAGE_INCOMPLETE")
    if adapter_issue:
        blockers.append("ADAPTER_OR_INFRA_FAULT")
    if assertions_not_compared:
        # 有必需断言没比成（观测缺字段/类型不符）：不能算通过。
        blockers.append("ASSERTIONS_NOT_COMPARED")

    if blockers:
        return Aggregation(
            OverallStatus.INCONCLUSIVE,
            False,
            blockers,
            count_results(case_results, coverage),
            subject_limit_hit=subject_limit_hit,
            adapter_issue=adapter_issue,
        )

    if mismatched:
        reasons.append("ASSERTION_MISMATCH")
        eligible = execution_backend_attested
        if not eligible:
            reasons.append("EXECUTION_BACKEND_NOT_ATTESTED")
        return Aggregation(
            OverallStatus.FAIL,
            eligible,
            reasons,
            count_results(case_results, coverage),
        )

    eligible = execution_backend_attested
    if not eligible:
        reasons.append("EXECUTION_BACKEND_NOT_ATTESTED")
    return Aggregation(
        OverallStatus.PASS,
        eligible,
        reasons,
        count_results(case_results, coverage),
    )

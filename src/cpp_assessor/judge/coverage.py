"""覆盖统计（架构实施方案 §3.7）。

覆盖由程序对 partition / assertion ID 求集合差，不接受 LLM 或学生自报。
"""

from __future__ import annotations

from ..domain.models import CaseResult, CoverageReport, TestMethodSpec, TaskCase


def compute_coverage(
    method: TestMethodSpec,
    cases: dict[str, TaskCase],
    case_results: list[CaseResult],
) -> CoverageReport:
    planned_cases = list(cases)
    completed = [
        result
        for result in case_results
        if result.note != "NOT_RUN"
    ]
    completed_ids = [result.case_id for result in completed]

    planned_partitions = list(method.coverage_requirements.required_partitions)
    covered: set[str] = set()
    for result in completed:
        case = cases.get(result.case_id)
        if case is not None:
            covered.update(case.partition_ids)

    planned_assertions = list(method.coverage_requirements.required_assertions)
    executed: set[str] = set()
    for result in completed:
        for assertion_result in result.assertion_results:
            executed.add(assertion_result.assertion_id)

    missing_partitions = [p for p in planned_partitions if p not in covered]
    missing_assertions = [a for a in planned_assertions if a not in executed]
    missed_cases = [c for c in planned_cases if c not in completed_ids]
    complete = not missing_partitions and not missing_assertions and not missed_cases

    return CoverageReport(
        planned_cases=planned_cases,
        completed_cases=completed_ids,
        missed_cases=missed_cases,
        planned_partitions=planned_partitions,
        covered_partitions=[p for p in planned_partitions if p in covered],
        missing_partitions=missing_partitions,
        planned_assertions=planned_assertions,
        executed_assertions=[a for a in planned_assertions if a in executed],
        missing_assertions=missing_assertions,
        complete=complete,
    )

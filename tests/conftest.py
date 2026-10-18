"""共用测试夹具：构造结果对象，避免每个测试重复拼装 pydantic 模型。"""

from __future__ import annotations

import pytest

from cpp_assessor.domain.enums import (
    CasePurpose,
    ComparisonStatus,
    Operator,
    ProtocolStatus,
    Termination,
)
from cpp_assessor.domain.models import (
    AssertionResult,
    CaseResult,
    CoverageReport,
)

SCHEMA_VERSION = "1.0"


@pytest.fixture
def make_assertion_result():
    def _make(
        comparison: ComparisonStatus = ComparisonStatus.MATCH,
        *,
        assertion_id: str = "a1",
        case_id: str = "c1",
    ) -> AssertionResult:
        return AssertionResult(
            schema_version=SCHEMA_VERSION,
            assertion_id=assertion_id,
            case_id=case_id,
            operator=Operator.EQUALS,
            comparison=comparison,
        )

    return _make


@pytest.fixture
def make_case_result(make_assertion_result):
    def _make(
        case_id: str = "c1",
        *,
        comparison: ComparisonStatus = ComparisonStatus.MATCH,
        termination: Termination = Termination.EXITED,
        protocol_status: ProtocolStatus = ProtocolStatus.OK,
        note: str | None = None,
        assertions: tuple[ComparisonStatus, ...] = (ComparisonStatus.MATCH,),
        purpose: CasePurpose = CasePurpose.ASSESSMENT,
    ) -> CaseResult:
        results = [
            make_assertion_result(
                item, assertion_id=f"a{index + 1}", case_id=case_id
            )
            for index, item in enumerate(assertions)
        ]
        return CaseResult(
            schema_version=SCHEMA_VERSION,
            case_id=case_id,
            attempt_id=f"attempt_{case_id}",
            purpose=purpose,
            comparison=comparison,
            termination=termination,
            protocol_status=protocol_status,
            assertion_results=results,
            failed_assertions=[
                item.assertion_id
                for item in results
                if item.comparison is ComparisonStatus.MISMATCH
            ],
            evidence_refs=[],
            note=note,
        )

    return _make


@pytest.fixture
def make_coverage():
    def _make(
        *,
        cases: tuple[str, ...] = ("c1",),
        assertions: tuple[str, ...] = ("a1",),
        complete: bool = True,
    ) -> CoverageReport:
        return CoverageReport(
            planned_cases=list(cases),
            completed_cases=list(cases),
            missed_cases=[],
            planned_partitions=["p1"],
            covered_partitions=["p1"],
            missing_partitions=[],
            planned_assertions=list(assertions),
            executed_assertions=list(assertions),
            missing_assertions=[],
            complete=complete,
        )

    return _make

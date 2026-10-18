# -*- coding: utf-8 -*-
"""异常类型与 execution_status 的一致性。

历史缺陷：异常类曾把 execution_status 写成普通字符串，收尾阶段调用
``.value`` 时抛 AttributeError，导致运行记录停在 REPORTING 且没有报告。
"""

from __future__ import annotations

import pytest

from cpp_assessor.domain.enums import ExecutionStatus
from cpp_assessor.domain.errors import (
    AssessorError,
    BackendNotAttestedError,
    BudgetExceededError,
    BuildUnresolvedError,
    CancelledError,
    InvalidInputError,
    ProtocolError,
    SandboxUnavailableError,
)


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (AssessorError, ExecutionStatus.INFRA_ERROR),
        (InvalidInputError, ExecutionStatus.INVALID_INPUT),
        (BuildUnresolvedError, ExecutionStatus.BUILD_UNRESOLVED),
        (SandboxUnavailableError, ExecutionStatus.UNSUPPORTED),
        (BackendNotAttestedError, ExecutionStatus.UNSUPPORTED),
        (BudgetExceededError, ExecutionStatus.BUDGET_EXCEEDED),
        (ProtocolError, ExecutionStatus.ADAPTATION_FAILED),
        (CancelledError, ExecutionStatus.CANCELLED),
    ],
)
def test_execution_status_is_enum_member(error_type, expected) -> None:
    assert isinstance(error_type.execution_status, ExecutionStatus)
    assert error_type.execution_status is expected
    assert error_type.execution_status.value == expected.value

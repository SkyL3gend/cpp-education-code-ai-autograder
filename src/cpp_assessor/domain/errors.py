"""领域异常。

每一类异常对应报告中一个明确的 execution_status，避免"笼统失败"。
"""

from __future__ import annotations


class AssessorError(Exception):
    """所有本系统异常的基类。"""

    execution_status = "INFRA_ERROR"
    exit_code = 1


class InvalidInputError(AssessorError):
    """提交内容、任务包或参数不合法。"""

    execution_status = "INVALID_INPUT"
    exit_code = 2


class TaskPackageError(InvalidInputError):
    """任务包结构或引用不合法。"""


class TaskNotPublishedError(InvalidInputError):
    """任务未发布，且未显式允许草稿执行。"""


class BuildUnresolvedError(AssessorError):
    """无法根据提交内容确定构建方案（缺文件、入口不明等）。"""

    execution_status = "BUILD_UNRESOLVED"


class SandboxUnavailableError(AssessorError):
    """所需隔离后端不可用。"""

    execution_status = "UNSUPPORTED"


class BackendNotAttestedError(AssessorError):
    """隔离能力未达门禁要求，拒绝执行真实提交。"""

    execution_status = "UNSUPPORTED"


class BudgetExceededError(AssessorError):
    """超出预算（总时长、用例数等）。"""

    execution_status = "BUDGET_EXCEEDED"
    exit_code = 4


class ProtocolError(AssessorError):
    """被测程序输出的观测协议不合法。"""

    execution_status = "ADAPTATION_FAILED"


class CancelledError(AssessorError):
    """运行被取消。"""

    execution_status = "CANCELLED"
    exit_code = 130

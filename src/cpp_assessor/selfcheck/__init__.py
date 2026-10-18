# -*- coding: utf-8 -*-
"""基准程序实测（观察型自检）。

用途：把教师手里的**原始基准程序**当作一个真实可执行程序，用固定脚本驱动它的
菜单，观察关键规则是否成立，并留下证据。它回答的是“这个程序现在表现如何”，
而不是“某道小改题是否通过签名断言”。

因此本模块明确不参与五道题的评分：它没有任务包、没有 oracle，也不产生
``grade_eligible``。结论只有三种取值：PASS（观察符合规则）、DEFECT（观察与规则
不符，记录证据）、NOT_OBSERVED（程序没按预期流程运行，无法判定）。
"""

from .checks import CHECKS
from .models import CheckOutcome, SelfCheckReport
from .runner import (
    SelfCheckResult,
    default_reference_source,
    list_recent,
    load_report,
    run_selfcheck,
)

__all__ = [
    "CHECKS",
    "CheckOutcome",
    "SelfCheckReport",
    "SelfCheckResult",
    "default_reference_source",
    "list_recent",
    "load_report",
    "run_selfcheck",
]

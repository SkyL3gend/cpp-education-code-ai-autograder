"""断言引擎（架构实施方案 §3.7）。

首批只实现三个白名单算子：

* ``EQUALS``：严格类型相等。bool 与 number 永不相等（Python 的
  ``True == 1`` 在这里是 False），number 内部 int/float 按数值比较。
* ``MULTISET_EQUALS``：多重集合相等，保留重复项计数，元素顺序无关。
* ``UNCHANGED``：与"动作前观测"里同一位置的取值相等，不允许缺省。

比较与解析分离：本模块只接收已经由可信解析器提取好的 ``actual``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.canonical import canonical_json
from ..domain.enums import ComparisonStatus, ExpectedSourceKind, Operator
from ..domain.errors import TaskPackageError
from ..domain.models import AssertionSpec
from ..domain.pointers import MISSING, resolve
from .normalizations import normalize


@dataclass(frozen=True)
class AssertionOutcome:
    comparison: ComparisonStatus
    expected: Any
    actual: Any
    detail: str | None = None


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def strict_equal(left: Any, right: Any) -> bool:
    """严格类型相等：bool 只等于 bool，且递归比较容器。"""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, bool):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, bool) or isinstance(right, bool):
            return False
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            strict_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(strict_equal(left[key], right[key]) for key in left)
    return False


def _multiset_counts(items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = canonical_json(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _describe_multiset_gap(expected: list[Any], actual: list[Any]) -> str:
    expected_counts = _multiset_counts(expected)
    actual_counts = _multiset_counts(actual)
    missing = {
        key: expected_counts[key] - actual_counts.get(key, 0)
        for key in expected_counts
        if expected_counts[key] > actual_counts.get(key, 0)
    }
    extra = {
        key: actual_counts[key] - expected_counts.get(key, 0)
        for key in actual_counts
        if actual_counts[key] > expected_counts.get(key, 0)
    }
    parts = []
    if missing:
        parts.append(f"缺少 {missing}")
    if extra:
        parts.append(f"多出 {extra}")
    return "；".join(parts) or "元素相同但计数不一致"


def _read_pointer(document: Any, pointer: str) -> Any:
    """读取观测字段：``/a/b`` 是 JSON Pointer，``a`` 也按顶层键处理。"""
    if pointer.startswith("/"):
        return resolve(document, pointer)
    return resolve(document, "/" + pointer)


def evaluate(
    spec: AssertionSpec,
    *,
    case_expected: Mapping[str, Any],
    actual_document: Any,
    pre_observations: Mapping[str, Any] | None = None,
) -> AssertionOutcome:
    """执行一条断言。任何"无法比较"都返回 NOT_COMPARED 并给出原因。"""
    pre_observations = pre_observations or {}
    actual_raw = _read_pointer(actual_document, spec.actual_observation_key)
    if actual_raw is MISSING:
        return AssertionOutcome(
            comparison=ComparisonStatus.NOT_COMPARED,
            expected=None,
            actual=None,
            detail=f"观测中缺少字段 {spec.actual_observation_key}",
        )

    source = spec.expected_source
    if source.kind is ExpectedSourceKind.CASE_EXPECTED:
        expected_raw = resolve(case_expected, source.pointer)
        if expected_raw is MISSING:
            return AssertionOutcome(
                comparison=ComparisonStatus.NOT_COMPARED,
                expected=None,
                actual=actual_raw,
                detail=f"用例期望中缺少 {source.pointer}",
            )
    else:
        key = source.observation_key
        if not key or key not in pre_observations:
            return AssertionOutcome(
                comparison=ComparisonStatus.NOT_COMPARED,
                expected=None,
                actual=actual_raw,
                detail=f"缺少动作前观测 {key!r}",
            )
        expected_raw = resolve(pre_observations[key], source.pointer)
        if expected_raw is MISSING:
            return AssertionOutcome(
                comparison=ComparisonStatus.NOT_COMPARED,
                expected=None,
                actual=actual_raw,
                detail=f"动作前观测缺少 {source.pointer}",
            )

    expected = normalize(spec.normalization_id, expected_raw)
    actual = normalize(spec.normalization_id, actual_raw)

    if spec.operator is Operator.EQUALS:
        matched = strict_equal(expected, actual)
        detail = None if matched else (
            f"类型 {_type_name(expected)} 与 {_type_name(actual)} 的取值不同"
            if _type_name(expected) != _type_name(actual)
            else "取值不同"
        )
    elif spec.operator is Operator.MULTISET_EQUALS:
        if not isinstance(expected, list) or not isinstance(actual, list):
            raise TaskPackageError(
                f"断言 {spec.assertion_id} 的 MULTISET_EQUALS 两侧必须都是数组"
            )
        matched = _multiset_counts(expected) == _multiset_counts(actual)
        detail = None if matched else _describe_multiset_gap(expected, actual)
    elif spec.operator is Operator.UNCHANGED:
        if source.kind is not ExpectedSourceKind.PRE_OBSERVATION:
            raise TaskPackageError(
                f"断言 {spec.assertion_id} 的 UNCHANGED 必须引用 PRE_OBSERVATION"
            )
        matched = strict_equal(expected, actual)
        detail = None if matched else "动作前观测与动作后不一致"
    else:  # pragma: no cover - 枚举已限定
        raise TaskPackageError(f"未知算子：{spec.operator}")

    return AssertionOutcome(
        comparison=ComparisonStatus.MATCH if matched else ComparisonStatus.MISMATCH,
        expected=expected,
        actual=actual,
        detail=detail,
    )

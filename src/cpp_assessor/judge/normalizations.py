"""观测值规范化（架构实施方案 §3.7）。

规范化只允许在"比较之前"做机械变换，且必须登记在白名单里；
不允许把不同语义的值折叠成相等（例如把 bool 变成 0/1）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.errors import TaskPackageError


def _identity(value: Any) -> Any:
    return value


def _lf_text(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_lf_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _lf_text(item) for key, item in value.items()}
    return value


NORMALIZATIONS: dict[str, Callable[[Any], Any]] = {
    "identity_v1": _identity,
    "lf_text_v1": _lf_text,
}

DEFAULT_NORMALIZATION = "identity_v1"


def normalize(normalization_id: str, value: Any) -> Any:
    try:
        transform = NORMALIZATIONS[normalization_id]
    except KeyError as exc:
        raise TaskPackageError(
            f"未知的规范化 ID：{normalization_id}（允许：{sorted(NORMALIZATIONS)}）"
        ) from exc
    return transform(value)

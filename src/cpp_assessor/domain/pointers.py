"""JSON Pointer（RFC 6901）实现。

断言引擎只允许通过指针取观测值，避免出现 "eval" 式取值。
"""

from __future__ import annotations

from typing import Any

from .errors import AssessorError

MISSING = object()


class PointerError(AssessorError):
    pass


def resolve(document: Any, pointer: str) -> Any:
    """按 RFC 6901 解析指针；不存在时返回 ``MISSING`` 哨兵而不是抛错。"""
    if not isinstance(pointer, str):
        raise PointerError("pointer 必须是字符串")
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise PointerError(f"指针必须以 '/' 开头：{pointer!r}")

    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                return MISSING
            index = int(token)
            if index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def exists(document: Any, pointer: str) -> bool:
    return resolve(document, pointer) is not MISSING


def format_pointer(tokens: list[str]) -> str:
    encoded = [t.replace("~", "~0").replace("/", "~1") for t in tokens]
    return "".join("/" + t for t in encoded)

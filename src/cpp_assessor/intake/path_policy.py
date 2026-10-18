"""提交内容的路径策略（架构实施方案 §11）。

只允许"根目录内的普通相对路径"。下列内容一律拒绝：
绝对路径、盘符、UNC、`..`/`.` 段、备用数据流（`:`）、Windows 设备名、
控制字符、Windows 非法字符、结尾空格或点、空段、超长路径。
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..domain.errors import InvalidInputError

MAX_TOTAL_PATH_LENGTH = 240
MAX_SEGMENT_LENGTH = 120

_INVALID_CHARS = set('<>:"|?*') | {chr(code) for code in range(32)}
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def validate_relative_path(raw: str) -> str:
    """校验并规范化相对路径；返回使用 `/` 分隔的规范形式。"""
    if not isinstance(raw, str) or raw.strip() == "":
        raise InvalidInputError("提交中存在空路径")
    if "\x00" in raw:
        raise InvalidInputError(f"路径包含 NUL 字符：{raw!r}")

    text = raw.replace("\\", "/")
    if text.startswith("/"):
        raise InvalidInputError(f"拒绝绝对路径：{raw!r}")
    if text.startswith("//"):
        raise InvalidInputError(f"拒绝 UNC 路径：{raw!r}")
    if _DRIVE_PREFIX.match(text):
        raise InvalidInputError(f"拒绝带盘符的路径：{raw!r}")
    if len(text) > MAX_TOTAL_PATH_LENGTH:
        raise InvalidInputError(f"路径过长（>{MAX_TOTAL_PATH_LENGTH}）：{raw!r}")

    parts = text.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise InvalidInputError(f"路径中出现非法段 {part!r}：{raw!r}")
        if len(part) > MAX_SEGMENT_LENGTH:
            raise InvalidInputError(f"路径段过长：{raw!r}")
        if artifact := (set(part) & _INVALID_CHARS):
            raise InvalidInputError(f"路径包含非法字符 {sorted(artifact)}：{raw!r}")
        if part.endswith(" ") or part.endswith("."):
            raise InvalidInputError(f"路径段不能以空格或点结尾：{raw!r}")
        stem = part.split(".")[0].upper()
        if stem in _RESERVED_NAMES:
            raise InvalidInputError(f"路径使用 Windows 保留设备名：{raw!r}")
    return "/".join(parts)


def ensure_unique_casefold(paths: list[str]) -> None:
    """Windows 文件系统大小写不敏感，折叠后重复即视为冲突。"""
    seen: dict[str, str] = {}
    for path in paths:
        folded = path.casefold()
        if folded in seen:
            raise InvalidInputError(
                f"提交中存在大小写冲突的路径：{seen[folded]!r} 与 {path!r}"
            )
        seen[folded] = path


def group_by_directory(paths: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        head, _, _ = path.rpartition("/")
        grouped[head].append(path)
    return dict(grouped)

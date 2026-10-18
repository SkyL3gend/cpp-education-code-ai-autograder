"""规范化序列化、哈希与时间工具。

所有需要哈希或比较的 JSON 都经由这里，保证键顺序、空白、
浮点表示和 Unicode 处理在进程间一致。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical_json(value: Any) -> str:
    """稳定序列化：键排序、紧凑分隔、禁止 NaN，保留非 ASCII 原字符。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def hash_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()

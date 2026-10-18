"""运行工件存储：``data/runs/<run_id>/`` 下的文件即证据。

写入一律"临时文件 + 原子替换"，避免中断留下半截 JSON。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.canonical import canonical_json, sha256_hex


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    rel_path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "rel_path": self.rel_path,
            "sha256": self.sha256,
            "size": self.size,
        }


class ArtifactStore:
    def __init__(self, root: str | Path, run_id: str):
        self.root = Path(root).resolve() / run_id
        self.run_id = run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str) -> Path:
        target = (self.root / relative).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"工件路径越界：{relative}")
        return target

    def write_bytes(self, kind: str, name: str, data: bytes) -> Artifact:
        relative = f"{kind}/{name}" if kind else name
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, prefix=".tmp-", suffix=".part"
        )
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(handle.name, target)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
        return Artifact(
            artifact_id=f"{kind}/{name}",
            kind=kind,
            rel_path=relative,
            sha256=sha256_hex(data),
            size=len(data),
        )

    def write_text(self, kind: str, name: str, text: str) -> Artifact:
        return self.write_bytes(kind, name, text.encode("utf-8"))

    def write_json(self, kind: str, name: str, payload: Any) -> Artifact:
        return self.write_text(kind, name, canonical_json(payload) + "\n")

    def read_json(self, relative: str) -> Any:
        return json.loads(self.path(relative).read_text(encoding="utf-8"))

    def exists(self, relative: str) -> bool:
        return self.path(relative).is_file()

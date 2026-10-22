"""Provide the full reference test method and real harness before target analysis."""
from __future__ import annotations

from ..domain.canonical import hash_json
from ..domain.errors import InvalidInputError


def reference_packet(task) -> dict:
    files = []
    # Drivers, protocol helpers and declarations explain setup/action/observation.
    paths = list(task.harness_files) + list(task.harness_dir.glob("*.h")) + list(task.project_root.glob("*.h"))
    for path in dict.fromkeys(paths):
        if path.stat().st_size > 160_000:
            raise InvalidInputError("基准判断程序超出上下文预算，不能静默省略")
        files.append({"path": path.name, "text": path.read_text(encoding="utf-8")})
    if not files:
        raise InvalidInputError("任务包缺少可阅读的基准判断程序")
    return {
        "method_hash": hash_json(task.method.model_dump(mode="json")),
        "test_method": task.method.model_dump(mode="json"),
        "io_template": task.template.model_dump(mode="json"),
        "reference_verifier_files": files,
        "instruction": "先解释基准如何初始化、触发业务、读取事实和执行每条断言，再迁移到目标；基准业务实现不得链接到目标构建。",
    }

"""Execute saved verifiers again, with new run IDs and no model requests."""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.enums import Mode
from ..domain.errors import InvalidInputError
from ..domain.models import AssessmentReport
from ..llm.provider import LLMSettings
from ..planning.bundle import TargetVerifierBundle
from ..tasks.loader import load_task
from .runner import RunOptions, execute_standard_run, make_backend


def replay_run(run_id: str, *, root: Path, library: Path | None = None, trusted_fixture: bool = False, allow_wasm_pilot: bool = False):
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in run_id) or ".." in run_id:
        raise InvalidInputError("非法运行 ID")
    old = root / "runs" / run_id
    if not (old / "objective_report.json").is_file():
        raise InvalidInputError("找不到原运行报告")
    if not (old / "plans/replay_manifest.json").is_file():
        raise InvalidInputError("旧运行没有冻结工件清单，无法精确重放；可用 rejudge 检查旧观测或创建新测评")
    report = AssessmentReport.model_validate_json((old / "objective_report.json").read_text(encoding="utf-8"))
    recorded_backend = report.provenance.execution_backend
    if recorded_backend == "wasm_pilot":
        if not allow_wasm_pilot:
            raise InvalidInputError("WebAssembly 试验重放需要显式启用 --allow-wasm-pilot")
        backend = "wasm_pilot"
    else:
        backend = "mock" if recorded_backend == "mock" else "local"
    task_ref = f"{report.provenance.task_id}@{report.provenance.task_version}"
    task = load_task(task_ref, task_library=library, allow_draft=True)
    if task.package_hash != report.provenance.task_package_hash:
        raise InvalidInputError("任务包已变化，拒绝称为原计划重放；新任务请创建新测评")
    saved = json.loads((old / "plans/replay_manifest.json").read_text(encoding="utf-8"))
    if saved["environment_digest"] != make_backend(backend).capabilities().environment_digest:
        raise InvalidInputError("环境摘要不同，拒绝精确重放")
    for relative, expected in saved["file_hashes"].items():
        import hashlib
        path = old / relative
        if not path.resolve().is_relative_to(old.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise InvalidInputError(f"原运行工件已变化：{relative}")
    if report.mode is Mode.LLM:
        from .mode_two import ModeTwoRun
        frozen = json.loads((old / "plans/frozen_plan.json").read_text(encoding="utf-8"))
        sid = frozen["submission"]["submission_id"]
        source = old / "submissions" / sid / "original"
        bundle = TargetVerifierBundle.model_validate(frozen["bundle"])
        options = RunOptions(task_ref=task_ref, submission=source, mode=Mode.LLM, backend=backend, data_dir=root, task_library=library, allow_draft=True, trusted_fixture=trusted_fixture, allow_unattested=trusted_fixture or (backend == "wasm_pilot" and allow_wasm_pilot))
        runner = ModeTwoRun(options, LLMSettings(api_key="replay-do-not-call-network"), replay_bundle=bundle)
        result = runner.execute()
    else:
        composition = json.loads((old / "evidence/composition.json").read_text(encoding="utf-8"))
        variant = composition.get("variant")
        source = root / "submissions" / report.provenance.submission_id / "original"
        import hashlib
        for path in (old / "generated/subject_source").rglob("*"):
            if path.is_file():
                target = source / path.relative_to(old / "generated/subject_source")
                if not target.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                    raise InvalidInputError("原始提交快照已变化，拒绝重放")
        result = execute_standard_run(RunOptions(task_ref=task_ref, submission=None if variant else source, variant=variant, backend=backend, allow_unattested=bool(variant), data_dir=root, task_library=library, allow_draft=True))
    (result.run_dir / "replay_of.json").write_text(json.dumps({"parent_run_id": run_id, "replay_of": run_id}), encoding="utf-8")
    return result

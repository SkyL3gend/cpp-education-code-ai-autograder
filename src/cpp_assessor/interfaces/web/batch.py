# -*- coding: utf-8 -*-
"""批量测评：一次上传多个程序，对每道勾选的题各跑一次标准测评。

设计约束：

- **不复制判定链路**：每个格子仍然是一次完整的 ``execute_standard_run``，
  报告、证据、退出码语义与单次运行完全一致；
- **串行**：复用进程内的单工作线程，不并发跑被测程序；
- **可恢复展示**：批次状态写在 ``data/batches/<batch_id>/batch.json``，
  页面只读这个文件与各运行目录的报告，不需要改 SQLite 表结构。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_PROGRAMS = 10
MAX_TASKS = 5
BATCH_SCHEMA_VERSION = "1.0"

BATCH_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def new_batch_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"batch_{stamp}_{uuid.uuid4().hex[:6]}"


def batch_directory(data_root: Path, batch_id: str) -> Path:
    if not BATCH_ID_PATTERN.fullmatch(batch_id) or ".." in batch_id:
        raise ValueError(f"非法的批次编号：{batch_id}")
    return data_root / "batches" / batch_id


def write_batch(directory: Path, data: dict[str, Any]) -> None:
    """原子写入批次状态。

    Windows 上同时有页面在读 ``batch.json`` 时，``os.replace`` 可能短暂地报
    PermissionError；批量测评会边跑边被轮询，因此这里做有限重试。
    """

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "batch.json"
    temporary = target.with_suffix(".json.tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    last_error: OSError | None = None
    for attempt in range(6):
        temporary.write_text(text, encoding="utf-8", newline="\n")
        try:
            temporary.replace(target)
            return
        except OSError as exc:  # 目标文件正被读取（Windows 文件锁）
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def read_batch(directory: Path) -> dict[str, Any] | None:
    path = directory / "batch.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_batches(data_root: Path, limit: int = 10) -> list[dict[str, Any]]:
    root = data_root / "batches"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if not directory.is_dir() or not BATCH_ID_PATTERN.fullmatch(directory.name):
            continue
        data = read_batch(directory)
        if data is None:
            continue
        rows.append(data)
        if len(rows) >= limit:
            break
    return rows


def count_progress(batch: dict[str, Any]) -> dict[str, int]:
    entries = batch.get("entries") or []
    done = sum(1 for entry in entries if entry.get("state") in {"done", "failed"})
    errors = sum(1 for entry in entries if entry.get("state") == "failed")
    passed = sum(1 for entry in entries if entry.get("assessment_status") == "PASS")
    rejected = sum(1 for entry in entries if entry.get("assessment_status") == "FAIL")
    return {
        "total": len(entries),
        "done": done,
        "errors": errors,
        "passed": passed,
        "rejected": rejected,
    }


def build_batch(
    *,
    batch_id: str,
    tasks: list[str],
    programs: list[dict[str, str]],
    run_id_factory: Any,
) -> dict[str, Any]:
    """生成扁平化的“程序 × 任务”条目，每条预分配 ``run_id`` 以便立即链接。"""
    entries: list[dict[str, Any]] = []
    index = 0
    for program in programs:
        for task_ref in tasks:
            index += 1
            entries.append(
                {
                    "entry_id": f"e{index:03d}",
                    "program_id": program["program_id"],
                    "filename": program["filename"],
                    "task_ref": task_ref,
                    "run_id": run_id_factory(),
                    "state": "queued",
                    "execution_status": None,
                    "assessment_status": None,
                    "error": None,
                }
            )
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "status": "queued",
        "programs": programs,
        "tasks": tasks,
        "entries": entries,
    }


def matrix(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """把条目整理成“行 = 程序、列 = 任务”的矩阵，供模板渲染。"""
    by_key = {
        (entry["program_id"], entry["task_ref"]): entry
        for entry in batch.get("entries") or []
    }
    rows: list[dict[str, Any]] = []
    for program in batch.get("programs") or []:
        cells = [
            {
                "task_ref": task_ref,
                "entry": by_key.get((program["program_id"], task_ref)),
            }
            for task_ref in batch.get("tasks") or []
        ]
        rows.append({"program": program, "cells": cells})
    return rows

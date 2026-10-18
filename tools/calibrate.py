# -*- coding: utf-8 -*-
"""对每道题运行 reference_suite 的正确/缺陷版本，确认判定结果符合预期。

用法：
    python tools/calibrate.py                 # 校准全部题目
    python tools/calibrate.py --task t1_phone_validation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cpp_assessor.config import data_dir, tasks_dir  # noqa: E402
from cpp_assessor.domain.canonical import utc_now  # noqa: E402
from cpp_assessor.domain.models import (  # noqa: E402
    CalibrationOutcome,
    CalibrationRecord,
    ComparisonStatus,
)
from cpp_assessor.orchestrator.runner import RunOptions, execute_standard_run  # noqa: E402
from cpp_assessor.storage.db import Database  # noqa: E402
from cpp_assessor.tasks.loader import load_task  # noqa: E402


def calibration_id(task_id: str) -> str:
    stamp = utc_now().replace("-", "").replace(":", "")
    return f"cal_{task_id}_{stamp}"


def failed_cases(report) -> list[str]:
    if report is None:
        return []
    return sorted(
        {
            case.case_id
            for case in report.case_results
            if case.comparison is ComparisonStatus.MISMATCH
        }
    )


def calibrate_task(task_id: str, version: str, *, backend: str) -> CalibrationRecord | None:
    task = load_task(f"{task_id}@{version}", allow_draft=True)
    suite = task.document.reference_suite
    db = Database(data_dir() / "app.sqlite3")
    db.upsert_task_version(
        task_id=task_id,
        version=version,
        package_hash=task.package_hash,
        publication_status=task.document.task_spec.publication_status.value,
        validation_level=task.validation.validation_level.value,
    )

    outcomes: list[CalibrationOutcome] = []
    for variant in suite.variants:
        result = execute_standard_run(
            RunOptions(
                task_ref=f"{task_id}@{version}",
                variant=variant.variant_id,
                backend=backend,
                allow_unattested=True,
                allow_draft=True,
            )
        )
        actual = (
            result.assessment_status.value if result.assessment_status is not None else "NOT_ASSESSED"
        )
        failed = failed_cases(result.report)
        expected = variant.expected_assessment_status.value
        matched = actual == expected
        if variant.role.value == "CORRECT":
            matched = matched and not failed
        else:
            matched = matched and set(variant.must_fail_cases) <= set(failed)
        outcomes.append(
            CalibrationOutcome(
                variant_id=variant.variant_id,
                role=variant.role,
                expected_assessment_status=variant.expected_assessment_status,
                actual_assessment_status=result.assessment_status or variant.expected_assessment_status,
                matched=matched,
                failed_cases=failed,
                expected_failed_cases=list(variant.must_fail_cases),
                run_id=result.run_id,
                detail=(result.error or None) if not matched else None,
            )
        )
        status = "OK  " if matched else "FAIL"
        print(
            f"[{status}] {task_id} {variant.variant_id:8s} "
            f"expected={expected:5s} actual={actual:12s} failed={failed}"
        )

    record = CalibrationRecord(
        schema_version="1.0",
        calibration_id=calibration_id(task_id),
        task_id=task_id,
        task_version=version,
        task_package_hash=task.package_hash,
        executed_at=utc_now(),
        outcomes=outcomes,
        passed=all(outcome.matched for outcome in outcomes),
    )
    out_dir = data_dir() / "calibrations"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{task_id}-{version}.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    db.save_calibration(
        calibration_id=record.calibration_id,
        task_id=task_id,
        task_version=version,
        package_hash=record.task_package_hash,
        passed=record.passed,
        detail=record.model_dump(mode="json"),
    )
    print(f"[{'OK  ' if record.passed else 'FAIL'}] {task_id} calibration -> {out_dir}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="校准题库：正确实现应 PASS，缺陷实现应 FAIL")
    parser.add_argument("--task", help="只校准指定题目（task_id）")
    parser.add_argument("--backend", default="local", choices=["local", "mock"])
    args = parser.parse_args()

    records: list[CalibrationRecord] = []
    for task_file in sorted(tasks_dir().glob("*/1.0.0/task.json")):
        task_id = task_file.parent.parent.name
        if args.task and task_id != args.task:
            continue
        record = calibrate_task(task_id, task_file.parent.name, backend=args.backend)
        if record is not None:
            records.append(record)
    if not records:
        print("没有找到可校准的题目。", file=sys.stderr)
        return 1
    return 0 if all(record.passed for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

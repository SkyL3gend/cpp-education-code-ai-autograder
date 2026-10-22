"""Evidence-backed validation of the built-in reference verifier.

Calibration reads recorded runs; it does not trust an LLM's confidence label.
Uncalibrated packages remain usable for exploratory runs.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.enums import ValidationLevel


def calibration_level(task, data_root: Path, environment_digest: str) -> ValidationLevel:
    path = data_root / "calibrations" / f"{task.task_id}-{task.version}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("task_package_hash") != task.package_hash or record.get("environment_digest") != environment_digest or not record.get("passed"):
            return ValidationLevel.CHECKED
        outcomes = {o["variant_id"]: o for o in record["outcomes"]}
        for variant in task.document.reference_suite.variants:
            outcome = outcomes[variant.variant_id]
            run_id = outcome["run_id"]
            if not isinstance(run_id, str) or "/" in run_id or "\\" in run_id or ".." in run_id:
                return ValidationLevel.CHECKED
            report = json.loads((data_root / "runs" / run_id / "objective_report.json").read_text(encoding="utf-8"))
            if report["provenance"]["task_package_hash"] != task.package_hash or report["provenance"]["execution_backend"] == "mock":
                return ValidationLevel.CHECKED
            rows = {c["case_id"]: c for c in report["case_results"]}
            if set(rows) != set(task.task_spec.case_ids) or report["execution_status"] != "COMPLETED":
                return ValidationLevel.CHECKED
            if variant.role.value == "CORRECT":
                if not all(row["comparison"] == "MATCH" for row in rows.values()):
                    return ValidationLevel.CHECKED
            elif not variant.must_fail_cases or not all(rows[c]["comparison"] == "MISMATCH" for c in variant.must_fail_cases):
                return ValidationLevel.CHECKED
        return ValidationLevel.CALIBRATED
    except (OSError, ValueError, KeyError, TypeError):
        return ValidationLevel.CHECKED

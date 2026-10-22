"""Verify saved real-model output under the current execution environment.

This creates a new exploratory run and makes no model requests. Unlike exact
replay it explicitly allows an environment upgrade, without reusing old facts.
Only use the trusted reference/example source snapshots created for this pilot.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cpp_assessor.domain.enums import Mode
from cpp_assessor.domain.models import SubmissionManifest
from cpp_assessor.llm.provider import LLMSettings
from cpp_assessor.orchestrator.mode_two import ModeTwoRun
from cpp_assessor.orchestrator.runner import RunOptions
from cpp_assessor.planning.bundle import TargetVerifierBundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("saved_run", type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--trusted-source", type=Path,
                        help="Explicit self-authored regression source; creates new facts without altering the saved run")
    args = parser.parse_args()
    saved = args.saved_run.resolve()
    manifest = SubmissionManifest.model_validate_json((saved / "plans/submission_manifest.json").read_text(encoding="utf-8"))
    source = saved / "submissions" / manifest.submission_id / "original"
    for entry in manifest.files:
        path = (source / entry.path).resolve()
        if not path.is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256:
            raise RuntimeError("The saved trusted source snapshot changed")
    bundle = TargetVerifierBundle.model_validate_json((saved / "plans/target_bundle.json").read_text(encoding="utf-8"))
    if args.trusted_source is not None:
        source = args.trusted_source.resolve()
    run = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=source,
        mode=Mode.LLM, backend="wasm_pilot", data_dir=args.data,
        allow_unattested=True), LLMSettings(api_key="offline-saved-bundle-no-network"), replay_bundle=bundle)
    started = time.perf_counter()
    result = run.execute()
    print(json.dumps({"run_id": result.run_id, "execution_status": result.execution_status.value,
        "error": result.error, "model_requests": 0, "grade_eligible": result.grade_eligible,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "source": str(source), "saved_bundle_run": saved.name,
        "calibration_passed": run.verifier_calibration.get("passed") if run.verifier_calibration else None,
        "supplemental_counts": run.target_challenges.get("challenge_counts") if run.target_challenges else None,
        "counts": result.report.counts.model_dump() if result.report else None}, ensure_ascii=False, indent=2))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

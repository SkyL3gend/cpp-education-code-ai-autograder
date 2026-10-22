"""Recompile and execute an existing successful WASM run; no model calls."""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpp_assessor.domain.enums import CompileStatus, Termination
from cpp_assessor.domain.models import BuildPlan
from cpp_assessor.sandbox.base import CompileRequest, RunRequest
from cpp_assessor.sandbox.wasm import WasmPilotBackend
from cpp_assessor.tasks.loader import load_task


def main(run_dir):
    run_dir = Path(run_dir).resolve()
    task = load_task("t1_phone_validation@1.0.0")
    plan = BuildPlan.model_validate_json((run_dir / "plans/build_plan.json").read_text(encoding="utf-8"))
    backend = WasmPilotBackend()
    destination = ROOT / "data/wasm-pilot" / ("benchmark-" + str(time.time_ns()))
    destination.mkdir(parents=True)
    compile_ms = []
    for index in range(3):
        work = destination / f"compile-{index}"
        work.mkdir()
        module = work / "subject.wasm"
        request = CompileRequest(plan, Path("unused"), [run_dir / unit.path for unit in plan.source_units],
            [run_dir / p for p in plan.include_dirs], ["-std=c++17", "-O2"], module, work, task.document.limits)
        start = time.perf_counter()
        outcome = backend.compile(request)
        compile_ms.append(round((time.perf_counter() - start) * 1000))
        if outcome.status != CompileStatus.SUCCESS:
            raise RuntimeError(outcome.stderr)
    case_dir = run_dir / "evidence/cases/c_valid_11_ascii"
    stdin = next(case_dir.rglob("request.json"), None) or next(case_dir.rglob("stdin.txt"), None)
    if stdin is None:
        raise RuntimeError("Cannot locate recorded stdin")
    wall_ms = []
    for index in range(10):
        work = destination / f"run-{index}"
        work.mkdir()
        outcome = backend.run(RunRequest(module, [], stdin, work / "stdout.txt", work / "stderr.txt", work, task.document.limits))
        if outcome.termination != Termination.EXITED:
            raise RuntimeError(str(outcome))
        # Require actual observed protocol output, not merely process exit.
        observed = json.loads((work / "stdout.txt").read_text(encoding="utf-8"))
        if observed.get("status") != "OK" or "actual" not in observed:
            raise RuntimeError("Invalid observation")
        wall_ms.append(outcome.wall_ms)
    result = {"source_run": str(run_dir), "compile_ms": compile_ms,
        "compile_median_ms": statistics.median(compile_ms), "cold_worker_run_ms": wall_ms,
        "cold_worker_median_ms": statistics.median(wall_ms), "module_bytes": module.stat().st_size,
        "environment_digest": backend.capabilities().environment_digest,
        "note": "Compilation includes disposable inputs, AppContainer, closed VFS and EH conversion. Every run starts a fresh worker and JIT. No model calls. Fixture calibration is measured separately by full pipeline runs."}
    (ROOT / "data/wasm-pilot/benchmark-isolated.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

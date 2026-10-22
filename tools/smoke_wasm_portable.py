"""Verify a separately extracted portable bundle using the real model service.

Explicit maintainer test: uploads the bundled trusted correct example. The
local user's model settings/key are used, never copied into a release file.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx


def main(folder):
    root = Path(folder).resolve()
    executable = root / "cpp-assessor-wasm-pilot.exe"
    if not executable.is_file():
        raise RuntimeError("Not a portable bundle directory")
    env = os.environ.copy()
    for key in list(env):
        if (key.upper().startswith(("ASSESSOR_", "PYTHON", "VIRTUAL_ENV"))
                and not key.upper().startswith("ASSESSOR_LLM_")):
            env.pop(key, None)
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    started = time.perf_counter()
    with (root / "smoke-server.log").open("wb") as log:
        process = subprocess.Popen([str(executable), "web", "--backend", "wasm_pilot",
            "--allow-unattested-backend", "--port", "8767", "--no-open"], cwd=root,
            env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with httpx.Client(trust_env=False, timeout=3) as client:
                for _ in range(100):
                    try:
                        response = client.get("http://127.0.0.1:8767/healthz")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError("Portable server exited; inspect smoke-server.log")
                    time.sleep(.1)
                else:
                    raise RuntimeError("Portable server startup timed out")
                startup_ms = round((time.perf_counter() - started) * 1000)
                source = root / "examples/wasm_phone/phone_correct.cpp"
                with source.open("rb") as content:
                    response = client.post("http://127.0.0.1:8767/llm/runs", timeout=30,
                        data={"task_ref": "t1_phone_validation@1.0.0"},
                        files={"submission": (source.name, content)})
                if response.status_code != 303:
                    raise RuntimeError("Upload failed: " + str(response.status_code))
                run_id = response.headers["location"].split("/")[-1]
                print("Portable upload started:", run_id, "startup_ms:", startup_ms, flush=True)
                run_started = time.perf_counter()
                for _ in range(600):
                    status = client.get(f"http://127.0.0.1:8767/api/runs/{run_id}").json()
                    if status["state"] in {"done", "failed"}:
                        break
                    time.sleep(.5)
                else:
                    raise RuntimeError("Portable assessment timed out")
                report_path = root / "data/runs" / run_id / "objective_report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if report["execution_status"] != "COMPLETED" or report["counts"]["matched_cases"] != 4:
                    raise RuntimeError("Assessment failed: " + json.dumps(status, ensure_ascii=False))
                run_root = report_path.parent
                calibration = json.loads((run_root / "plans/target_calibration.json").read_text(encoding="utf-8"))
                supplemental = json.loads((run_root / "evidence/target_challenges/target_challenges.json").read_text(encoding="utf-8"))
                if not calibration.get("passed") or not supplemental.get("passed"):
                    raise RuntimeError("Isolated pilot calibration or supplemental checks did not pass")
                page = client.get(f"http://127.0.0.1:8767/runs/{run_id}")
                if page.status_code != 200 or any(text not in page.text for text in ("WebAssembly", "补充规则检查", "固定工程回归校准通过")):
                    raise RuntimeError("Result page failed")
                logs = [json.loads(line) for line in (root / "data/logs/llm_calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                if sum(item.get("ok") is True for item in logs) < 2:
                    raise RuntimeError("No recorded successful model generation and review calls")
                result = {"root": str(root), "run_id": run_id, "startup_ms": startup_ms,
                    "assessment_seconds": round(time.perf_counter() - run_started, 2),
                    "execution_status": report["execution_status"], "counts": report["counts"],
                    "calibration_passed": calibration["passed"], "supplemental_counts": supplemental["challenge_counts"],
                    "recorded_model_calls": len(logs),
                    "successful_model_calls": sum(item.get("ok") is True for item in logs),
                    "path_contains_only_system32": True, "grade_eligible": report["grade_eligible"]}
                destination = Path(__file__).resolve().parents[1] / "data/wasm-pilot/portable-smoke-isolated.json"
                destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
                print(json.dumps(result, indent=2), flush=True)
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main(sys.argv[1])

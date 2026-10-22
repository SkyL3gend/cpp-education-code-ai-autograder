"""Maintainer acceptance of a trusted portable ZIP, without model requests.

Usage: python tools/verify_wasm_portable_replay.py dist/package.zip RUN_DIR...
       --destination data/wasm-pilot/fresh-portable-check --port 8767

Only use self-built archives and saved trusted pilot runs. This executes the
saved verifiers through the portable replay command; it never uploads sources
or starts a new LLM assessment. The destination must not already exist.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "data/wasm-pilot/portable-isolated-replay.json"
EXE_NAME = "cpp-assessor-wasm-pilot.exe"
RUN_ID = re.compile(r"[A-Za-z0-9_.-]+\Z")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_child(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (not normalized or pure.is_absolute() or PureWindowsPath(relative).drive
            or ".." in pure.parts or any(":" in part for part in pure.parts)):
        raise ValueError(f"Unsafe archive/artifact path: {relative!r}")
    result = root.joinpath(*pure.parts).resolve()
    if not result.is_relative_to(root.resolve()) or result == root.resolve():
        raise ValueError(f"Path escapes its destination: {relative!r}")
    return result


def extract_trusted_archive(archive: Path, destination: Path) -> tuple[Path, int]:
    seen = set()
    unpacked_bytes = 0
    with zipfile.ZipFile(archive) as source:
        # Validate every name before writing any member.
        for entry in source.infolist():
            checked_child(destination, entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Portable archive must not contain symbolic links")
            key = entry.filename.replace("\\", "/").rstrip("/").casefold()
            if key in seen:
                raise ValueError(f"Duplicate archive path: {entry.filename!r}")
            seen.add(key)
        for entry in source.infolist():
            target = checked_child(destination, entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1 << 20)
                unpacked_bytes += entry.file_size
    executables = list(destination.rglob(EXE_NAME))
    if len(executables) != 1:
        raise ValueError(f"Expected one portable executable, found {len(executables)}")
    package = executables[0].parent
    if (package / "data").exists():
        raise ValueError("Release archive unexpectedly includes runtime data")
    if any(path.name == ".env" or path.name == "llm.env" for path in destination.rglob("*")):
        raise ValueError("Release archive unexpectedly includes an environment/key file")
    return package, unpacked_bytes


def isolated_environment() -> dict[str, str]:
    forbidden = ("ASSESSOR_", "PYTHON", "VIRTUAL_ENV", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    result = {key: value for key, value in os.environ.items()
              if not key.upper().startswith(forbidden)}
    result["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    return result


def verify_replay(package: Path, saved: Path, environment: dict[str, str]) -> dict:
    original = read_json(saved / "objective_report.json")
    old_id = original["run_id"]
    if (not isinstance(old_id, str) or not RUN_ID.fullmatch(old_id)
            or ".." in old_id or saved.name != old_id):
        raise ValueError("Saved run directory must match its safe run_id")
    if original.get("execution_status") != "COMPLETED":
        raise ValueError(f"Saved run is not completed: {old_id}")
    runs = package / "data/runs"
    runs.mkdir(parents=True, exist_ok=True)
    # Reject reparse sources before copytree can follow them into host paths.
    for path in [saved, *saved.rglob("*")]:
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Saved run contains a reparse entry: {path}")
    shutil.copytree(saved, runs / old_id)
    before = {path.name for path in runs.iterdir() if path.is_dir()}
    started = time.perf_counter()
    command = [str(package / EXE_NAME), "replay", old_id, "--allow-wasm-pilot"]
    completed = subprocess.run(command, cwd=package, env=environment,
                               capture_output=True, encoding="utf-8", errors="replace",
                               timeout=240, creationflags=subprocess.CREATE_NO_WINDOW)
    elapsed = time.perf_counter() - started
    (runs / f"acceptance-{old_id}.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode not in {0, 3}:
        raise RuntimeError(f"Replay {old_id} failed with exit {completed.returncode}; see acceptance log")
    candidates = []
    for path in runs.iterdir():
        parent = path / "replay_of.json"
        if path.is_dir() and path.name not in before and parent.is_file():
            if read_json(parent).get("parent_run_id") == old_id:
                candidates.append(path)
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one new replay for {old_id}, found {len(candidates)}")
    new = candidates[0]
    report = read_json(new / "objective_report.json")
    if report.get("execution_status") != "COMPLETED" or report["counts"] != original["counts"]:
        raise RuntimeError(f"Replay failed to preserve completed formal counts: {new.name}")
    calibration = read_json(new / "plans/target_calibration.json")
    supplemental = read_json(new / "evidence/target_challenges/target_challenges.json")
    if calibration.get("passed") is not True:
        raise RuntimeError(f"Regression calibration did not pass: {new.name}")
    if supplemental.get("status") != "COMPLETED":
        raise RuntimeError(f"Supplemental checks did not complete: {new.name}")
    frozen = read_json(new / "plans/frozen_plan.json")
    module = checked_child(new / "generated/build", frozen["bundle"]["build"]["artifact_name"])
    with module.open("rb") as source:
        if source.read(4) != b"\x00asm":
            raise RuntimeError("Replay artifact is not a WebAssembly module")
    return {"saved_run_id": old_id, "run_id": new.name,
            "elapsed_seconds": round(elapsed, 3), "exit_code": completed.returncode,
            "counts": report["counts"], "calibration_passed": calibration["passed"],
            "supplemental_status": supplemental["status"],
            "supplemental_counts": supplemental["challenge_counts"],
            "module_sha256": digest(module), "module_magic": "0061736d",
            "model_requests": 0}


def verify_web(package: Path, replays: list[dict], environment: dict[str, str], port: int) -> dict:
    # Do not mistake another service already using this port for our child.
    with socket.socket() as reservation:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reservation.bind(("127.0.0.1", port))
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    command = [str(package / EXE_NAME), "web", "--backend", "wasm_pilot",
               "--allow-unattested-backend", "--no-open", "--port", str(port)]
    log = package / "data/acceptance-web.log"
    process = None
    started = time.perf_counter()
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(command, cwd=package, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = started + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Portable Web exited during startup; see acceptance-web.log")
                try:
                    with opener.open(base + "/healthz", timeout=1) as response:
                        health = json.load(response)
                    if health.get("ok") is True and health.get("backend") == "wasm_pilot":
                        break
                except (OSError, ValueError):
                    pass
                if time.perf_counter() >= deadline:
                    raise RuntimeError("Portable Web did not become healthy within 45 seconds")
                time.sleep(0.1)
            startup = time.perf_counter() - started
            for replay in replays:
                route = base + "/runs/" + replay["run_id"]
                with opener.open(route + "?lang=zh", timeout=15) as response:
                    html = response.read().decode("utf-8")
                for text in ("补充规则检查", "固定工程回归校准通过"):
                    if text not in html:
                        raise RuntimeError(f"Portable result page is missing {text!r}")
                with opener.open(route + "/report.json", timeout=15) as response:
                    exported = json.load(response)
                if exported["run_id"] != replay["run_id"] or exported["counts"] != replay["counts"]:
                    raise RuntimeError("JSON export differs from the saved replay")
                with opener.open(route + "/report.csv", timeout=15) as response:
                    rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
                if len(rows) != replay["counts"]["completed_cases"] or any(row["run_id"] != replay["run_id"] for row in rows):
                    raise RuntimeError("CSV export has unexpected run IDs or row counts")
                replay["web_result_and_exports_verified"] = True
            # An empty cross-site POST is rejected by middleware before any
            # form handler or model request can be reached.
            request = urllib.request.Request(base + "/llm/runs", data=b"", method="POST",
                headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
            try:
                with opener.open(request, timeout=10) as response:
                    status = response.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
            if status != 403:
                raise RuntimeError(f"Cross-origin POST was not rejected: HTTP {status}")
            return {"startup_seconds": round(startup, 3), "health": health,
                    "base_url": base, "cross_origin_post_status": status,
                    "server_pid": process.pid, "server_stopped_in_finally": True}
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("saved_runs", type=Path, nargs="+")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This portable acceptance tool requires Windows")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    archive = args.archive.resolve(strict=True)
    destination = args.destination.resolve()
    if args.destination.exists() or args.destination.is_symlink() or destination.exists():
        parser.error("Destination must be a new directory; existing directories are never reused")
    destination.mkdir(parents=True, exist_ok=False)
    environment = isolated_environment()
    metrics = {"status": "RUNNING", "archive": str(archive),
               "archive_bytes": archive.stat().st_size, "archive_sha256": digest(archive),
               "destination": str(destination), "path": environment["PATH"],
               "removed_environment_prefixes": ["ASSESSOR_", "PYTHON", "VIRTUAL_ENV", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"],
               "model_requests": 0, "replays": []}
    try:
        started = time.perf_counter()
        package, unpacked = extract_trusted_archive(archive, destination)
        metrics.update({"unpack_seconds": round(time.perf_counter() - started, 3),
                        "unpacked_bytes": unpacked, "package": str(package),
                        "exe_sha256": digest(package / EXE_NAME)})
        for saved in args.saved_runs:
            replay = verify_replay(package, saved.resolve(strict=True), environment)
            metrics["replays"].append(replay)
            print(f"Replay {replay['saved_run_id']} -> {replay['run_id']}: {replay['elapsed_seconds']}s", flush=True)
        metrics["web"] = verify_web(package, metrics["replays"], environment, args.port)
        metrics["status"] = "PASSED"
    except Exception as exc:
        metrics.update({"status": "FAILED", "error": str(exc), "error_type": type(exc).__name__})
        print(f"Portable acceptance failed: {exc}", file=sys.stderr)
        return 1
    finally:
        METRICS.parent.mkdir(parents=True, exist_ok=True)
        METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Portable acceptance passed; metrics: {METRICS}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

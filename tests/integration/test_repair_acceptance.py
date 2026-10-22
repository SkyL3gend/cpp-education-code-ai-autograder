"""CLI + Web use the same calibrated engine; no live model calls."""
from __future__ import annotations
import json
import time
import shutil
import zipfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from cpp_assessor.interfaces.cli import main
from cpp_assessor.interfaces.web.app import WebSettings, create_app
from cpp_assessor.storage.db import Database
from cpp_assessor.domain.enums import Mode
from cpp_assessor.orchestrator.runner import RunOptions
from cpp_assessor.orchestrator.mode_two import execute_llm_run
from cpp_assessor.orchestrator.replay import replay_run
from cpp_assessor.llm.provider import LLMSettings
from tests.target_verifier_fixture import create_target
from tests.integration.test_mode_two_llm import configure
from tools.calibrate import calibrate_task


def wait(client, run_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["state"] in {"done", "failed"}:
            return data
        time.sleep(.05)
    raise AssertionError("Web run did not finish")


@pytest.mark.skipif(shutil.which("g++") is None, reason="trusted fixture compiler required")
def test_calibration_cli_web_replay_agree(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    monkeypatch.setenv("ASSESSOR_HOME", str(root))
    record = calibrate_task("t1_phone_validation", "1.0.0", backend="local", root=root)
    assert record.passed
    capsys.readouterr()
    assert main(["run", "--task", "t1_phone_validation@1.0.0", "--variant", "correct", "--allow-unattested-backend", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    # CLI --json returns the summary rather than the full report on some versions.
    run_id = report["run_id"]
    objective = json.loads((root / "runs" / run_id / "objective_report.json").read_text(encoding="utf-8"))
    assert objective["assessment_status"] == "PASS"
    assert objective["validation_level"] == "CALIBRATED"
    assert not objective["grade_eligible"]  # local is never falsely called isolated
    assert main(["replay", run_id]) == 0
    capsys.readouterr()
    parents = list((root / "runs").glob("*/replay_of.json"))
    assert any(json.loads(p.read_text())["parent_run_id"] == run_id for p in parents)
    with TestClient(create_app(WebSettings(backend="local", allow_unattested=True, data_root=root))) as client:
        response = client.post("/runs", data={"task_ref": "t1_phone_validation@1.0.0", "variant": "faulty"}, follow_redirects=False)
        web_run = response.headers["location"].rsplit("/", 1)[-1]
        state = wait(client, web_run)
        assert state["assessment_status"] == "FAIL"
        web_report = client.get(f"/runs/{web_run}/report.json").json()
        assert web_report["validation_level"] == "CALIBRATED"
        assert not web_report["grade_eligible"]
        assert "c_non_digit_11" in client.get(f"/runs/{web_run}").text
        blocked = client.post("/reference", files={"source": ("uploaded.cpp", b"int main(){return 0;}")}, follow_redirects=False)
        assert blocked.status_code == 400
        assert "隔离" in blocked.text


@pytest.mark.skipif(shutil.which("g++") is None, reason="trusted fixture compiler required")
def test_llm_replay_does_not_call_model_and_cli_finds_history(tmp_path, monkeypatch, capsys):
    target = create_target(tmp_path / "target")
    calls = configure(monkeypatch, target, function=True)
    root = tmp_path / "data"
    result = execute_llm_run(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=target, mode=Mode.LLM, backend="local", data_dir=root, allow_unattested=True, trusted_fixture=True), LLMSettings(api_key="test-no-network"))
    assert result.report.counts.matched_cases == 4
    count = len(calls)
    replayed = replay_run(result.run_id, root=root, trusted_fixture=True)
    assert len(calls) == count
    assert replayed.run_id != result.run_id
    assert replayed.report.counts.matched_cases == 4
    monkeypatch.setenv("ASSESSOR_HOME", str(root))
    assert main(["status", result.run_id, "--json"]) == 0
    assert result.run_id in capsys.readouterr().out
    assert main(["rejudge", result.run_id]) == 0
    with TestClient(create_app(WebSettings(backend="mock", data_root=root))) as client:
        assert result.run_id in client.get("/runs").text
        assert client.get(f"/api/runs/{result.run_id}").json()["assessment_status"] == "INCONCLUSIVE"


def test_old_llm_database_migrates_without_removing_history(tmp_path):
    old = Database(tmp_path / "assessor.db")
    old.create_run(run_id="old_llm", task_id="t1", task_version="1.0.0", task_package_hash="0"*64, mode="llm", backend="mock", submission_id=None)
    current = Database(tmp_path / "app.sqlite3")
    assert current.get_run("old_llm") is not None
    assert old.get_run("old_llm") is not None


def test_cli_and_web_llm_upload_gate_refuses_before_calling_model(tmp_path, monkeypatch, capsys):
    target = create_target(tmp_path / "target")
    calls = configure(monkeypatch, target)
    root = tmp_path / "data"
    monkeypatch.setenv("ASSESSOR_HOME", str(root))
    monkeypatch.setenv("ASSESSOR_LLM_API_KEY", "test-no-network")
    assert main(["run", "--task", "t1_phone_validation@1.0.0", "--submission", str(target), "--llm", "--allow-unattested-backend", "--json"]) == 2
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["execution_status"] == "UNSUPPORTED"
    assert cli_result["assessment_status"] == "NOT_ASSESSED"
    with TestClient(create_app(WebSettings(backend="local", allow_unattested=True, data_root=root))) as client:
        response = client.post("/llm/runs", data={"task_ref": "t1_phone_validation@1.0.0", "api_key": "test-no-network"}, files={"submission": ("uploaded.cpp", b"int main(){return 0;}")}, follow_redirects=False)
        assert response.status_code == 303
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        status = wait(client, run_id)
        assert status["assessment_status"] == "NOT_ASSESSED"
        report = client.get(f"/runs/{run_id}/report.json").json()
        assert report["execution_status"] == cli_result["execution_status"]
        assert report["validation_level"] == "UNVERIFIED"
        assert not report["grade_eligible"]
    assert not calls


@pytest.mark.skipif(shutil.which("g++") is None, reason="trusted fixture compiler required")
def test_published_version_is_sealed_by_current_calibration(tmp_path, monkeypatch):
    from cpp_assessor.tasks.loader import load_task, publish_task
    from cpp_assessor.domain.errors import TaskPackageError
    original = load_task("t1_phone_validation@1.0.0")
    library = tmp_path / "library"
    shutil.copytree(original.task_dir.parents[2], library)
    monkeypatch.setenv("ASSESSOR_TASK_LIBRARY", str(library))
    monkeypatch.setenv("ASSESSOR_HOME", str(tmp_path / "data"))
    record = calibrate_task("t1_phone_validation", "1.0.0", backend="local", root=tmp_path / "data")
    assert record.passed
    task = load_task("t1_phone_validation@1.0.0")
    publish_task(task.task_dir, task_library=library)
    assert (library / "publications/t1_phone_validation/1.0.0.json").is_file()
    cases = task.task_dir / "cases.json"
    changed = json.loads(cases.read_text(encoding="utf-8"))
    changed["cases"][0]["expected"]["created"] = False
    cases.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(TaskPackageError, match="已发布版本"):
        load_task("t1_phone_validation@1.0.0")

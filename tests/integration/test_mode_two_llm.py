"""Actual compiler + deterministic model: method migration and new-layout observer."""
from __future__ import annotations
import json
import shutil
from types import SimpleNamespace
import pytest

from cpp_assessor.domain.enums import Mode, OverallStatus, ExecutionStatus
from cpp_assessor.domain.errors import InvalidInputError
from cpp_assessor.llm.provider import LLMSettings
from cpp_assessor.orchestrator import mode_two
from cpp_assessor.orchestrator.runner import RunOptions
from tests.target_verifier_fixture import create_target, bundle

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="requires known local compiler")


def configure(monkeypatch, target, *, function=False, broken=False):
    calls = []
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def complete_json(self, *, system, user, call_name, **kwargs):
            payload = json.JSONDecoder().raw_decode(user)[0]
            calls.append((call_name, payload))
            if call_name == "review_verifier":
                return SimpleNamespace(data={"issues": [], "method_hash": payload["reference"]["method_hash"]})
            result = bundle(target, function=function)
            if broken:
                result["driver"]["driver_source"] = result["driver"].get("driver_source", "") + "\nTHIS IS NOT CXX;"
            return SimpleNamespace(data=result)
    monkeypatch.setattr(mode_two, "LLMClient", FakeClient)
    return calls


@pytest.mark.parametrize("function", [False, True])
def test_full_method_migration_runs_and_preserves_checked_qualification(monkeypatch, tmp_path, function):
    target = create_target(tmp_path / "target")
    calls = configure(monkeypatch, target, function=function)
    options = RunOptions(task_ref="t1_phone_validation@1.0.0", submission=target, mode=Mode.LLM, backend="local", data_dir=tmp_path / "data", allow_unattested=True, trusted_fixture=True)
    result = mode_two.execute_llm_run(options, LLMSettings(api_key="test-no-network"))
    assert result.execution_status is ExecutionStatus.COMPLETED, result.error
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert not result.grade_eligible
    assert len(result.report.case_results) == 4
    assert all(c.comparison.value == "MATCH" for c in result.report.case_results)
    assert calls[0][1]["reference"]["test_method"]["setup_steps"]
    assert any("opCreateContact" in f["text"] for f in calls[0][1]["reference"]["reference_verifier_files"])
    assert any(name == "review_verifier" for name, _ in calls)
    assert (result.run_dir / "plans/frozen_plan.json").is_file()
    assert (result.run_dir / "evidence/cases/c_valid_11_ascii/observation.json").is_file()
    from cpp_assessor.storage.db import Database
    assert Database(tmp_path / "data/app.sqlite3").get_run(result.run_id) is not None
    inventory = json.loads((result.run_dir / "plans/subject_inventory.json").read_text(encoding="utf-8"))
    assert "demo.cpp" not in inventory["compile_units"]
    if not function:
        assert (result.run_dir / "generated/driver/observer.exe").is_file()


def test_generated_driver_compile_errors_never_blame_student(monkeypatch, tmp_path):
    target = create_target(tmp_path / "target")
    configure(monkeypatch, target, function=True, broken=True)
    result = mode_two.execute_llm_run(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=target, mode=Mode.LLM, backend="local", data_dir=tmp_path / "data", allow_unattested=True, trusted_fixture=True), LLMSettings(api_key="test-no-network"))
    assert result.execution_status is ExecutionStatus.ADAPTATION_FAILED
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert not result.grade_eligible
    assert list((result.run_dir / "evidence/revisions").rglob("*.json"))
    import json
    evidence = json.loads((result.run_dir / "evidence/compile/compile.json").read_text(encoding="utf-8"))
    assert evidence["fault_owner"] == "ADAPTER"
    assert evidence["backend_fault_owner"] == "SUBMISSION"  # raw compiler label is not final blame


def test_untrusted_upload_cannot_use_local_allow_flag(monkeypatch, tmp_path):
    target = create_target(tmp_path / "target")
    calls = configure(monkeypatch, target)
    result = mode_two.execute_llm_run(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=target, mode=Mode.LLM, data_dir=tmp_path / "data", allow_unattested=True), LLMSettings(api_key="test-no-network"))
    assert result.execution_status is ExecutionStatus.UNSUPPORTED
    assert result.assessment_status is OverallStatus.NOT_ASSESSED
    assert not calls  # refuse before spending API budget or compiling


def test_mode_two_requires_key(tmp_path):
    with pytest.raises(InvalidInputError):
        mode_two.execute_llm_run(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path, mode=Mode.LLM), LLMSettings(api_key=""))

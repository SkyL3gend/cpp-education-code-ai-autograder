"""Control-plane regressions: budgets, transport retries, cancellation, integrity."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

from cpp_assessor.llm.provider import LLMClient, LLMError, LLMSettings
from cpp_assessor.domain.enums import ExecutionStatus, Mode, WorkflowState
from cpp_assessor.orchestrator.mode_two import ModeTwoRun
from cpp_assessor.orchestrator.runner import RunOptions
from cpp_assessor.tasks.loader import load_task, publish_task
from cpp_assessor.domain.errors import TaskPackageError
from cpp_assessor.domain.errors import InvalidInputError


def test_authentication_failure_is_not_retried(monkeypatch):
    client = LLMClient(LLMSettings(api_key="test-no-network"))
    calls = []
    def rejected(*args):
        calls.append(1)
        raise LLMError("HTTP 401", retryable=False)
    monkeypatch.setattr(client, "_post", rejected)
    with pytest.raises(LLMError):
        client.complete_json(system="", user="{}")
    assert len(calls) == 1


def test_cancel_before_model_request_uses_no_budget(tmp_path):
    runner = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path, mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data"), LLMSettings(api_key="test-no-network"))
    (runner.state.run_dir / "CANCEL").write_text("cancel")
    with pytest.raises(Exception, match="取消"):
        runner._complete(system="", user="{}")
    assert runner.logical_calls == 0
    assert runner.state.execution_status is ExecutionStatus.CANCELLED


def test_deadline_covers_planning_not_only_formal_cases(tmp_path):
    runner = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path, mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data"), LLMSettings(api_key="test-no-network"))
    runner.deadline = time.monotonic() - 1
    with pytest.raises(Exception, match="预算"):
        runner._complete(system="", user="{}")
    assert runner.state.execution_status is ExecutionStatus.BUDGET_EXCEEDED
    assert runner.logical_calls == 0


def test_publish_requires_current_calibration(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSESSOR_HOME", str(tmp_path))
    task = load_task("t1_phone_validation@1.0.0")
    with pytest.raises(TaskPackageError, match="校准"):
        publish_task(task.task_dir)


@pytest.mark.parametrize("run_id", ["../escape", "x:stream", "x."])
def test_llm_run_rejects_unsafe_run_directory(tmp_path, run_id):
    with pytest.raises(InvalidInputError, match="run_id"):
        ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path,
                             mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data", run_id=run_id),
                   LLMSettings(api_key="test-no-network"))


def test_llm_run_preserves_existing_evidence(tmp_path):
    options = RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path,
                         mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data", run_id="existing")
    runner = ModeTwoRun(options, LLMSettings(api_key="test-no-network"))
    evidence = runner.state.run_dir / "evidence/original.txt"
    evidence.write_text("keep")
    with pytest.raises(InvalidInputError, match="拒绝覆盖"):
        ModeTwoRun(options, LLMSettings(api_key="test-no-network"))
    assert evidence.read_text() == "keep"


def test_seed_preserves_exact_input_and_rejects_missing_field():
    from cpp_assessor.orchestrator.mode_two import ModeTwoRun
    runner = object.__new__(ModeTwoRun)
    runner.state = SimpleNamespace(
        bundle=SimpleNamespace(driver=SimpleNamespace(seed_session=["{{ seed.id }}", "{{seed.name}}", "0", "0"])),
        warnings=[],
    )
    assert runner._seed_lines({"initial_contacts": [{"id": 0, "name": "原值"}]}) == ["0", "原值", "0", "0"]
    with pytest.raises(InvalidInputError, match="缺少实际输入"):
        runner._seed_lines({"initial_contacts": [{"name": "无编号"}]})


def test_standard_process_timeout_is_capped_by_global_budget():
    from cpp_assessor.orchestrator.runner import _StandardRun
    runner = object.__new__(_StandardRun)
    runner.run_dir = Path("not-a-real-run-directory")
    runner.deadline = time.monotonic() + 0.3
    limits = load_task("t1_phone_validation@1.0.0").document.limits
    remaining = runner._remaining_limits(limits)
    assert 0 < remaining.compile_wall_ms <= 300
    assert 0 < remaining.case_wall_ms <= 300


def test_cancelled_llm_run_preserves_cancel_state_and_cli_exit_code(tmp_path):
    runner = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path,
                                  mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data"),
                        LLMSettings(api_key="test-no-network"))
    (runner.state.run_dir / "CANCEL").write_text("cancel")
    result = runner.execute()
    assert result.workflow_state is WorkflowState.CANCELLED
    assert result.exit_code == 130
    assert result.execution_status is ExecutionStatus.CANCELLED
    assert result.report.provenance.submission_id is None
    assert "未完成计划冻结" in result.report.observations_note


def test_llm_ingest_failure_still_writes_truthful_report(tmp_path):
    runner = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=tmp_path/"missing.cpp",
                                  mode=Mode.LLM, backend="mock", data_dir=tmp_path/"data"),
                        LLMSettings(api_key="test-no-network"))
    result = runner.execute()
    assert result.execution_status is ExecutionStatus.INVALID_INPUT
    assert result.report.provenance.submission_id is None
    assert result.report.validation_level.value == "UNVERIFIED"
    assert (result.run_dir / "objective_report.json").is_file()

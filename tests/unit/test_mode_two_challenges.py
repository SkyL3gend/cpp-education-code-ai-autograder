"""Supplemental observations must remain separate from frozen case results."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpp_assessor.domain.enums import ExecutionStatus
from cpp_assessor.domain.errors import AssessorError
from cpp_assessor.orchestrator.mode_two import ModeTwoRun


def _runner(tmp_path):
    runner = object.__new__(ModeTwoRun)
    runner.state = SimpleNamespace(
        backend=SimpleNamespace(backend_id="wasm_pilot"), bundle=object(),
        exe=Path("subject.wasm"), task=SimpleNamespace(document=SimpleNamespace(limits=object())),
        run_id="run_supplemental", run_dir=tmp_path, evidence=[],
        execution_status=ExecutionStatus.COMPLETED, case_results=["four-frozen-cases"],
    )
    runner.deadline = 123
    runner._check_cancel = lambda: None
    runner._remaining_limits = lambda limits: limits
    runner._event = lambda *args: None
    return runner


def test_supplemental_mismatch_preserves_formal_case_results(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    calls = []
    record = {"status": "COMPLETED", "defect_detected": True,
              "challenge_counts": {"matched": 2, "mismatched": 1, "not_compared": 0}}
    def check(**kwargs):
        calls.append(kwargs)
        return record
    monkeypatch.setattr("cpp_assessor.planning.target_calibration.run_target_challenges", check)
    runner._challenge_target()
    assert calls[0]["executable"] == runner.state.exe
    assert calls[0]["run_id"] == runner.state.run_id
    assert runner.target_challenges is record
    assert runner.state.execution_status is ExecutionStatus.COMPLETED
    assert runner.state.case_results == ["four-frozen-cases"]
    assert runner.state.evidence == ["evidence/target_challenges/target_challenges.json"]


@pytest.mark.parametrize("status,execution", [
    ("INVALIDATED", ExecutionStatus.INFRA_ERROR),
    ("CANCELLED", ExecutionStatus.CANCELLED),
    ("BUDGET_EXCEEDED", ExecutionStatus.BUDGET_EXCEEDED),
])
def test_supplemental_interruption_never_leaves_completed_status(monkeypatch, tmp_path, status, execution):
    runner = _runner(tmp_path)
    monkeypatch.setattr("cpp_assessor.planning.target_calibration.run_target_challenges",
        lambda **kwargs: {"status": status, "defect_detected": False, "challenge_counts": {}})
    with pytest.raises(AssessorError):
        runner._challenge_target()
    assert runner.state.execution_status is execution

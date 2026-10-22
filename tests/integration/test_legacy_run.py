"""Standard mode must not silently replace a task with a one-case probe."""
from pathlib import Path
import pytest
from cpp_assessor.orchestrator.runner import RunOptions,execute_standard_run
from cpp_assessor.domain.enums import ExecutionStatus,OverallStatus

@pytest.mark.parametrize("task",["t1_phone_validation","t2_birthday_today"])
def test_unrelated_program_stays_outside_standard_contract(tmp_path,task):
    source=tmp_path/"unrelated.cpp"
    source.write_text("int main(){return 0;}",encoding="utf-8")
    result=execute_standard_run(RunOptions(task_ref=task+"@1.0.0",submission=source,backend="mock",data_dir=tmp_path/"data"))
    assert result.execution_status is ExecutionStatus.INVALID_INPUT
    assert result.assessment_status is OverallStatus.NOT_ASSESSED
    assert not result.grade_eligible
    assert result.report.counts.attempted_cases==0

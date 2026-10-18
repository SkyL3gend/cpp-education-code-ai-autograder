"""模式二（LLM 适配）端到端：假模型 + 真实编译/运行，不访问网络。"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpp_assessor.domain.enums import ExecutionStatus, Mode, OverallStatus
from cpp_assessor.llm import LLMSettings
from cpp_assessor.orchestrator import mode_two
from cpp_assessor.orchestrator.mode_two import execute_llm_run
from cpp_assessor.orchestrator.runner import RunOptions, new_run_id

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "参考基准程序" / "通讯录_v6最终版.cpp"

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None or not REFERENCE.is_file(),
    reason="需要 g++ 与参考基准程序",
)

SESSION = [
    "20",
    "2",
    "1",
    "1",
    "{{candidate.name}}",
    "{{candidate.phone}}",
    "{{candidate.birthday.year}} {{candidate.birthday.month}} {{candidate.birthday.day}}",
    "北京",
    "朝阳路1号",
    "0",
    "1",
    "0",
    "0",
    "2",
    "1",
    "2",
    "{{candidate.name}}",
    "{{candidate.phone}}",
    "{{candidate.birthday.year}} {{candidate.birthday.month}} {{candidate.birthday.day}}",
    "北京",
    "朝阳路1号",
    "0",
    "0",
    "1",
    "9",
    "1",
    "{{candidate.name}}",
    "0",
    "0",
]


def bundle(task_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "task_version": "1.0.0",
        "status": "READY",
        "target": {
            "subject_level": "PROGRAM",
            "encoding": "utf-8",
            "sources": [REFERENCE.name],
            "entry_files": [REFERENCE.name],
            "excluded_files": [],
            "include_dirs": [],
            "notes": "单文件",
        },
        "build": {
            "compiler_flags": ["-std=c++17", "-O2"],
            "defines": [],
            "libraries": [],
            "artifact_name": "subject.exe",
        },
        "driver": {
            "kind": "stdin_script",
            "session": SESSION,
            "eof": True,
            "notes": "添加成功后回到主菜单再搜索",
        },
        "observer": {
            "kind": "extractor",
            "extractor_id": "contactbook_menu_v1",
            "params": {
                "name": "{{candidate.name}}",
                "phone": "{{candidate.phone}}",
                "baseline_names": ["测试联系人1", "测试联系人2", "测试联系人3"],
            },
            "produces": ["created", "contacts_after"],
        },
        "assertion_bindings": [
            {"assertion_id": "a_created", "actual_key": "created", "status": "bound", "gap_reason": ""},
            {
                "assertion_id": "a_contacts",
                "actual_key": "contacts_after",
                "status": "bound",
                "gap_reason": "",
            },
        ],
        "migration_matrix": [
            {
                "partition_id": partition,
                "rule_ids": ["R1", "R2", "R3", "R4"],
                "driver_step": 2,
                "observation": "添加后搜索列表",
                "assertion_ids": ["a_created", "a_contacts"],
                "status": "covered",
            }
            for partition in ("p_valid_11_ascii", "p_short_10", "p_long_12", "p_non_digit_11")
        ],
        "coverage_gaps": [],
        "notes": "假模型回复，只用于验证编排链路",
    }


class FakeClient:
    def __init__(self, settings, log_dir=None):
        self.settings = settings
        self.calls: list[str] = []

    def complete_json(self, *, system: str, user: str, call_name: str, max_tokens: int):
        self.calls.append(call_name)
        return SimpleNamespace(data=bundle("t1_phone_validation"))


def test_mode_two_runs_reference_program_and_reports_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mode_two, "LLMClient", FakeClient)
    options = RunOptions(
        task_ref="t1_phone_validation@1.0.0",
        submission=REFERENCE,
        mode=Mode.LLM,
        backend="local",
        data_dir=tmp_path,
        run_id=new_run_id(),
    )
    options.allow_unattested = True
    result = execute_llm_run(options, LLMSettings(api_key="sk-test", model="fake"))

    report = result.report
    assert report.mode == Mode.LLM
    assert result.workflow_state.value == "COMPLETED"
    assert report.execution_status == ExecutionStatus.COMPLETED
    assert report.assessment_status in {OverallStatus.FAIL, OverallStatus.INCONCLUSIVE}
    assert len(report.case_results) == 4
    assert report.case_results[0].case_id.startswith("c_")
    assert report.grade_eligible is False
    assert "模式二" in (report.observations_note or "")
    assert (tmp_path / "runs" / options.run_id / "objective_report.json").is_file()


def test_mode_two_requires_api_key(tmp_path) -> None:
    from cpp_assessor.domain.errors import InvalidInputError

    options = RunOptions(
        task_ref="t1_phone_validation@1.0.0",
        submission=REFERENCE,
        backend="local",
        data_dir=tmp_path,
    )
    with pytest.raises(InvalidInputError):
        execute_llm_run(options, LLMSettings(api_key=""))


def test_mode_two_marks_unusable_program_as_not_compared(monkeypatch, tmp_path) -> None:
    class GarbageClient:
        def __init__(self, settings, log_dir=None):
            self.settings = settings

        def complete_json(self, *, system: str, user: str, call_name: str, max_tokens: int):
            payload = bundle("t1_phone_validation")
            payload["driver"]["session"] = ["0"]
            return SimpleNamespace(data=payload)

    monkeypatch.setattr(mode_two, "LLMClient", GarbageClient)
    submission = tmp_path / "empty.cpp"
    submission.write_text("int main() { return 0; }\n", encoding="utf-8")
    options = RunOptions(
        task_ref="t1_phone_validation@1.0.0",
        submission=submission,
        mode=Mode.LLM,
        backend="local",
        data_dir=tmp_path / "runs",
    )
    options.allow_unattested = True
    result = execute_llm_run(options, LLMSettings(api_key="sk-test", model="fake"))
    assert result.report.execution_status in {
        ExecutionStatus.ADAPTATION_FAILED,
        ExecutionStatus.COMPLETED,
    }
    assert result.report.assessment_status in {
        OverallStatus.INCONCLUSIVE,
        OverallStatus.NOT_ASSESSED,
    }
    assert result.report.grade_eligible is False

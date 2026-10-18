"""模式二适配清单的静态校验（不调用模型、不编译）。"""

from __future__ import annotations

import copy

from cpp_assessor.judge.extractors import extractor_ids
from cpp_assessor.planning.bundle import validate_bundle
from cpp_assessor.tasks.loader import load_task

TASK_REF = "t1_phone_validation@1.0.0"
SOURCE = "main.cpp"


def base_bundle(task_id: str = "t1_phone_validation") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "task_version": "1.0.0",
        "status": "READY",
        "target": {
            "subject_level": "PROGRAM",
            "encoding": "utf-8",
            "sources": [SOURCE],
            "entry_files": [SOURCE],
            "excluded_files": [],
            "include_dirs": [],
            "notes": "",
        },
        "build": {
            "compiler_flags": ["-std=c++17"],
            "defines": [],
            "libraries": [],
            "artifact_name": "subject.exe",
        },
        "driver": {
            "kind": "stdin_script",
            "session": ["20", "2", "1", "1", "{{candidate.name}}", "{{candidate.phone}}"],
            "eof": True,
            "driver_source": "",
            "notes": "",
        },
        "observer": {
            "kind": "extractor",
            "extractor_id": "contactbook_menu_v1",
            "params": {"name": "{{candidate.name}}", "phone": "{{candidate.phone}}"},
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
                "rule_ids": ["R1", "R2", "R3"],
                "driver_step": 2,
                "observation": "created / contacts_after",
                "assertion_ids": ["a_created", "a_contacts"],
                "status": "covered",
            }
            for partition in (
                "p_valid_11_ascii",
                "p_short_10",
                "p_long_12",
                "p_non_digit_11",
            )
        ],
        "coverage_gaps": [],
        "notes": "测试用",
    }


def validate(payload: dict):
    from cpp_assessor.planning.bundle import TargetVerifierBundle

    task = load_task(TASK_REF)
    bundle = TargetVerifierBundle.model_validate(payload)
    return validate_bundle(bundle, task, {SOURCE: "int main() { return 0; }"}, extractor_ids=extractor_ids())


def test_good_bundle_passes_static_validation() -> None:
    result = validate(base_bundle())
    assert result.problems == []
    assert result.passed


def test_unknown_task_and_unsupported_status_are_reported() -> None:
    payload = base_bundle(task_id="t9_unknown")
    payload["status"] = "UNSUPPORTED"
    result = validate(payload)
    assert any("任务不匹配" in problem for problem in result.problems)
    assert any("UNSUPPORTED" in problem for problem in result.problems)


def test_missing_required_binding_is_reported() -> None:
    payload = base_bundle()
    payload["assertion_bindings"] = [payload["assertion_bindings"][0]]
    result = validate(payload)
    assert any("a_contacts" in problem for problem in result.problems)


def test_session_placeholder_outside_schema_is_reported() -> None:
    payload = base_bundle()
    payload["driver"]["session"] = list(payload["driver"]["session"]) + ["{{candidate.nickname}}"]
    result = validate(payload)
    assert any("nickname" in problem for problem in result.problems)


def test_session_by_case_unknown_case_is_reported() -> None:
    payload = base_bundle()
    payload["driver"]["session_by_case"] = {"c_not_a_case": ["1"]}
    result = validate(payload)
    assert any("c_not_a_case" in problem for problem in result.problems)


def test_migration_matrix_must_cover_every_input_partition() -> None:
    payload = base_bundle()
    payload["migration_matrix"] = []
    result = validate(payload)
    assert any("迁移矩阵" in problem or "partition" in problem for problem in result.problems)


def test_enum_values_are_case_insensitive() -> None:
    payload = copy.deepcopy(base_bundle())
    payload["driver"]["kind"] = "STDIN_SCRIPT"
    payload["observer"]["kind"] = "TRUSTED_EXTRACTOR"
    result = validate(payload)
    assert result.problems == []


def test_prompt_text_in_session_is_rejected() -> None:
    payload = base_bundle()
    payload["driver"]["session_by_case"] = {
        "c_non_digit_11": ["1", "不合法的电话号码，请重新输入：", "13800138000"]
    }
    result = validate(payload)
    assert any("提示语" in problem for problem in result.problems)


def test_expected_values_are_rejected() -> None:
    payload = base_bundle()
    payload["observer"]["params"]["expected"] = "PASS"
    result = validate(payload)
    assert any("不允许出现" in problem and "/observer/params/expected" in problem for problem in result.problems)

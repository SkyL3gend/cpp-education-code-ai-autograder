"""T2's author-defined observations use the shared, model-free execution path.

The backend here is a unit-test double only. Its calendar oracle is independently
written in Python; none of these tests constitutes real WASM calibration.
"""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from cpp_assessor.domain.models import SandboxCapabilities
from cpp_assessor.judge.extractors import extractor_ids
from cpp_assessor.planning.bundle import TargetVerifierBundle, validate_bundle
from cpp_assessor.planning.pilot_tasks import PILOT_TASK_REFS, get_pilot_profile
from cpp_assessor.planning.target_calibration import (calibrate_target_verifier,
    calibration_binding_matches, merged_task_source, run_target_challenges,
    trusted_task_variants)
from cpp_assessor.sandbox.base import CompileOutcome, RunOutcome
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence


def birthday_inputs(*, modules=False):
    task = load_task("t2_birthday_today@1.0.0")
    trusted = trusted_task_variants(task)["correct"]
    files = trusted if modules else {"birthday.cpp": merged_task_source(trusted)}
    payload = {
        "task_id": task.task_id, "task_version": str(task.version),
        "target": {"sources": list(files)},
        "build": {"compiler_flags": ["-std=c++17", "-O2"], "artifact_name": "subject.wasm"},
        "driver": {"kind": "function_driver", "driver_source": "// protocol_version in the test protocol adapter\nvoid birthdayReminders() {}"},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1", "produces": ["names", "contacts_after"]},
        "assertion_bindings": [{"assertion_id": item.assertion_id, "actual_key": item.actual_observation_key} for item in task.assertions.values()],
        "migration_matrix": [{"partition_id": part.partition_id, "rule_ids": part.rule_ids, "observation": "actual names and full contacts_after", "assertion_ids": list(task.assertions)} for part in task.method.input_partitions],
    }
    attach_evidence(payload, task, files, "contactbook.cpp" if modules else "birthday.cpp", "ContactBook::birthdayReminders")
    return task, TargetVerifierBundle.model_validate(payload), files


class BirthdayBackend:
    backend_id = "wasm_pilot"
    simulated = False

    def __init__(self, *, faulty=False, conceal=False, unrelated=False,
                 renumber=False, drop_duplicate=False, mutate_today=False):
        self.faulty, self.conceal, self.unrelated = faulty, conceal, unrelated
        self.renumber, self.drop_duplicate, self.mutate_today = renumber, drop_duplicate, mutate_today
        self.compiles, self.requests = [], []

    def capabilities(self):
        return SandboxCapabilities(schema_version="1.0", backend_id=self.backend_id, environment_digest="a" * 64,
            isolation=IsolationLevel.WASM_RUNTIME, network_block=False, cpu_limit=True, memory_limit=True,
            process_limit=True, wall_timeout=True, tree_kill=True, clean_reset=True, filesystem_isolation=False)

    def compile(self, request):
        self.compiles.append(request)
        request.output.parent.mkdir(parents=True)
        request.output.write_bytes(b"\0asm-unit-test-only")
        return CompileOutcome(CompileStatus.SUCCESS, 0, "", "", 1, "b" * 64, FaultOwner.NONE)

    def run(self, request):
        envelope = json.loads(request.stdin_path.read_text(encoding="utf8"))
        self.requests.append(envelope)
        payload = envelope["input"]
        today = dt.date(**payload["today"])
        names, contacts = [], []
        faulty = (self.faulty or "faulty" in request.work_dir.parts) and not self.conceal
        for original in payload["initial_contacts"]:
            contact = copy.deepcopy(original)
            birth = contact["birthday"]
            # Match the fixed scaffold's day-of-year helper for its old formal
            # non-leap-year inputs; new challenges use valid current-year dates.
            next_birthday = dt.date(today.year, birth["month"], 1) + dt.timedelta(days=birth["day"] - 1)
            left = (next_birthday - today).days
            if left < 0 or (faulty and left == 0):
                left += 366 if today.year % 4 == 0 and (today.year % 100 or today.year % 400 == 0) else 365
            if left <= payload["days_ahead"]:
                names.append(contact["name"])
            contact["birthday"] = f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"
            if self.renumber:
                contact["id"] = len(contacts) + 1
            contacts.append(contact)
        if faulty and self.unrelated and envelope["case_id"] == "c_today_exact":
            names = ["无关错误"]
        if self.drop_duplicate:
            names = list(dict.fromkeys(names))
        if self.mutate_today and contacts:
            contacts[0]["birthday"] = "2000-01-01"
        actual = {"names": names, "contacts_after": contacts}
        request.stdout_path.write_text(json.dumps({**{key: value for key, value in envelope.items() if key != "input"}, "status": "OK", "actual": actual}), encoding="utf8")
        request.stderr_path.write_text("", encoding="utf8")
        return RunOutcome(Termination.EXITED, 0, 1, request.stdout_path.stat().st_size, 0, FaultOwner.NONE)


def test_inline_source_binding_requires_actual_header_and_explains_repair():
    task, bundle, files = birthday_inputs(modules=True)
    quote = "const std::vector<Contact>& contacts() const { return contacts_; }"
    assert quote in files["contactbook.h"]
    binding = next(item for item in bundle.source_bindings if item.key == "contacts_after")
    binding.source_quote, binding.symbol, binding.file = quote, "contacts", "contactbook.cpp"
    rejected = validate_bundle(bundle, task, files, extractor_ids=extractor_ids())
    assert not rejected.passed
    detail = next(item for item in rejected.problems if "源码映射引用不存在：contacts_after" in item)
    assert "声明文件 contactbook.cpp" in detail
    assert "相同原文所在文件：['contactbook.h']" in detail
    binding.file = "contactbook.h"
    assert validate_bundle(bundle, task, files, extractor_ids=extractor_ids()).passed


def calibration(tmp_path, *, modules=False, **options):
    task, bundle, files = birthday_inputs(modules=modules)
    backend = BirthdayBackend(**options)
    kwargs = dict(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend)
    return calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression"), kwargs


def test_profiles_are_precise_and_reject_unknown_versions():
    assert PILOT_TASK_REFS == {"t1_phone_validation@1.0.0", "t2_birthday_today@1.0.0"}
    assert get_pilot_profile("t2_birthday_today", "1.0.1") is None
    assert get_pilot_profile("unknown") is None
    profile = get_pilot_profile("t2_birthday_today")
    assert len(profile.formal_case_ids) == 6 and len(profile.challenge_labels) == 3
    assert profile.expected_assertions("faulty", "c_today_window") == {"a_names": "MISMATCH", "a_contacts": "MATCH"}
    assert profile.expected_assertions("faulty", "challenge_duplicate_birthdays") == {"a_names": "MATCH", "a_contacts": "MATCH"}
    with pytest.raises(ValueError):
        profile.expected_assertions("faulty", "invented_case")


def test_t2_migration_error_names_missing_original_operation_id():
    task, bundle, files = birthday_inputs()

    def validate():
        return validate_bundle(bundle, task, files, extractor_ids=extractor_ids(),
            case_inputs={cid: task.case(cid).input for cid in task.task_spec.case_ids})

    assert validate().passed
    for row in bundle.migration_matrix:
        # Generic IO-template wrappers do not substitute test-method action IDs.
        row.method_steps = ["seed_initial_contacts", "invoke_operation", "read_names", "read_contacts_after"]
    result = validate()
    assert not result.passed
    missing = [problem for problem in result.problems if "缺少基准操作步骤映射" in problem]
    assert len(missing) == len(bundle.migration_matrix)
    for row in bundle.migration_matrix:
        assert f"分区 {row.partition_id} 缺少基准操作步骤映射：['birthday_reminders']" in missing
        row.method_steps.append("birthday_reminders")
    assert validate().passed


@pytest.mark.parametrize("modules", [False, True])
def test_t2_independent_fixtures_and_exact_known_defects(tmp_path, modules):
    record, kwargs = calibration(tmp_path, modules=modules)
    assert record["passed"], record
    assert record["qualification"] == "T2_REGRESSION_CALIBRATED"
    assert record["policy_version"] == "t2-scaffold-regression-v1"
    assert record["grade_eligible"] is False and record["verifier_validation_level"] == "CHECKED"
    assert record["model_calls"] == 0 and record["gate_attested"] is False
    assert record["formal_case_ids"] == kwargs["task"].task_spec.case_ids
    assert len(record["supplemental_case_ids"]) == 3
    assert len(kwargs["backend"].compiles) == 2 and len(kwargs["backend"].requests) == 18
    assert all("expected" not in envelope for envelope in kwargs["backend"].requests)
    assert calibration_binding_matches(record, **kwargs)
    faulty = record["outcomes"][1]
    expected_names = {"c_today_exact": [], "c_today_window": ["周八"], "c_leap_day": []}
    for case_id, names in expected_names.items():
        row = next(row for row in faulty["cases"] if row["case_id"] == case_id)
        assert row["actual"]["names"] == names
        assert row["assertions"] == {"a_names": "MISMATCH", "a_contacts": "MATCH"}
        assert row["initial_contacts_preserved"] and row["defect_signature_verified"]
    rows = record["outcomes"][0]["cases"]
    duplicates = next(row for row in rows if row["case_id"] == "challenge_duplicate_birthdays")
    assert duplicates["actual"]["names"] == ["同名", "同名", "异名"]
    assert [contact["id"] for contact in duplicates["actual"]["contacts_after"]] == [7, 42, 99, 105]


@pytest.mark.parametrize("options", [{"conceal": True}, {"unrelated": True}, {"renumber": True}, {"drop_duplicate": True}, {"mutate_today": True}])
def test_false_calibration_or_state_corruption_is_rejected(tmp_path, options):
    record, _ = calibration(tmp_path, **options)
    assert record["status"] == "COMPLETED" and not record["passed"]
    assert record["qualification"] == "NOT_CALIBRATED" and not record["grade_eligible"]
    assert any(not row["passed"] for variant in record["outcomes"] for row in variant["cases"])


def test_t2_only_designated_function_body_may_change(tmp_path):
    task, bundle, files = birthday_inputs()
    files["birthday.cpp"] = files["birthday.cpp"].replace("return phone[0] == '1';", "return true;")
    backend = BirthdayBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "unsupported")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles
    assert "指定函数以外" in record["reason"] or "固定合并工程" in record["reason"]


def test_t2_bundle_changes_and_cross_task_policy_invalidate_binding(tmp_path):
    record, kwargs = calibration(tmp_path)
    changed = kwargs["bundle"].model_copy(deep=True)
    changed.driver.driver_source += "\n// changed adapter"
    assert not calibration_binding_matches(record, **{**kwargs, "bundle": changed})
    wrong_policy = copy.deepcopy(record)
    wrong_policy["policy_version"] = "t1-scaffold-regression-v1"
    wrong_policy["record_hash"] = hash_json({key: value for key, value in wrong_policy.items() if key != "record_hash"})
    assert not calibration_binding_matches(wrong_policy, **kwargs)


def challenge_args(tmp_path, **options):
    task, bundle, _ = birthday_inputs()
    executable = tmp_path / "birthday.wasm"
    executable.write_bytes(b"\0asm-unit-test-only")
    return dict(task=task, bundle=bundle, backend=BirthdayBackend(**options), executable=executable,
                run_id="run_birthday_student", work_dir=tmp_path / "challenges")


def test_t2_current_target_three_supplemental_checks_keep_official_cases_frozen(tmp_path):
    kwargs = challenge_args(tmp_path)
    original = copy.deepcopy(kwargs["task"].cases)
    record = run_target_challenges(**kwargs)
    assert record["passed"] and record["policy_version"] == "t2-target-challenges-v1"
    assert record["challenge_counts"] == {"required": 3, "attempted": 3, "matched": 3, "mismatched": 0, "not_compared": 0}
    assert not record["grade_eligible"] and not record["model_calls"]
    assert kwargs["task"].cases == original and len(original) == 6
    assert not kwargs["backend"].compiles
    assert all("expected" not in envelope for envelope in kwargs["backend"].requests)
    rows = record["challenge_results"]
    assert rows[0]["expected"]["names"] == ["岁末", "元旦"]
    assert rows[1]["expected"]["names"] == ["二月今日", "闰日"]
    assert rows[2]["expected"]["names"] == ["同名", "同名", "异名"]


def test_t2_supplemental_observes_today_defect_and_duplicate_loss(tmp_path):
    record = run_target_challenges(**challenge_args(tmp_path, faulty=True))
    assert record["defect_detected"] and record["challenge_counts"]["mismatched"] == 2
    assert record["challenge_counts"]["matched"] == 1
    # Re-use a separate evidence directory for a lossy observer/program.
    kwargs = challenge_args(tmp_path, drop_duplicate=True)
    kwargs["work_dir"] = tmp_path / "duplicates"
    record = run_target_challenges(**kwargs)
    assert record["defect_detected"] and record["challenge_counts"]["mismatched"] == 1
    assert record["challenge_results"][2]["actual"]["names"] == ["同名", "异名"]


@pytest.mark.parametrize("kind", ["simulation", "native_backend", "native_executable"])
def test_t2_never_accepts_native_or_simulated_calibration_evidence(tmp_path, kind):
    kwargs = challenge_args(tmp_path)
    if kind == "simulation":
        kwargs["backend"].simulated = True
    elif kind == "native_backend":
        kwargs["backend"].backend_id = "local"
    else:
        kwargs["executable"].write_bytes(b"MZ-native")
    record = run_target_challenges(**kwargs)
    assert record["status"] == "UNSUPPORTED" and not kwargs["backend"].requests

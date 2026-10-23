"""T3 rule evidence reuses the same bounded verifier/calibration pipeline.

The Python backend is an independent unit-test double, not real WASM evidence.
It never receives expected answers through the protocol request.
"""
from __future__ import annotations

import calendar
import copy
import datetime as dt
import json

import pytest
from jsonschema import Draft202012Validator

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from cpp_assessor.domain.models import SandboxCapabilities
from cpp_assessor.planning.bundle import TargetVerifierBundle
from cpp_assessor.planning.pilot_tasks import get_pilot_profile
from cpp_assessor.planning.target_calibration import (calibrate_target_verifier,
    calibration_binding_matches, merged_task_source, run_target_challenges,
    supplemental_cases, trusted_task_variants)
from cpp_assessor.sandbox.base import CompileOutcome, RunOutcome
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence


def date_inputs(*, modules=False, variant="correct", task_ref="t3_date_validation@1.0.0"):
    task = load_task(task_ref)
    trusted = trusted_task_variants(task)[variant]
    files = trusted if modules else {"date.cpp": merged_task_source(trusted)}
    payload = {
        "task_id": task.task_id, "task_version": str(task.version),
        "target": {"sources": list(files)},
        "build": {"compiler_flags": ["-std=c++17", "-O2"], "artifact_name": "subject.wasm"},
        "driver": {"kind": "function_driver", "driver_source": "// protocol_version in this unit-test adapter\nvoid " + ("birthdayReminders" if task.task_id == "t2_birthday_today" else "addContact") + "() {}"},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1", "produces": task.template.required_observations},
        "assertion_bindings": [{"assertion_id": item.assertion_id, "actual_key": item.actual_observation_key} for item in task.assertions.values()],
        "migration_matrix": [{"partition_id": part.partition_id, "rule_ids": part.rule_ids, "observation": "actual created and full contacts_after", "assertion_ids": list(task.assertions)} for part in task.method.input_partitions],
    }
    attach_evidence(payload, task, files, "contactbook.cpp" if modules else "date.cpp", "ContactBook::birthdayReminders" if task.task_id == "t2_birthday_today" else "ContactBook::addContact")
    return task, TargetVerifierBundle.model_validate(payload), files


class DateBackend:
    backend_id = "wasm_pilot"
    simulated = False

    def __init__(self, *, faulty=False, conceal=False, unrelated=False,
                 renumber=False, drop_seed=False, reject_endpoints=False):
        self.faulty, self.conceal, self.unrelated = faulty, conceal, unrelated
        self.renumber, self.drop_seed, self.reject_endpoints = renumber, drop_seed, reject_endpoints
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
        candidate = payload["candidate"]
        birth = candidate["birthday"]
        year, month, day = (birth[key] for key in ("year", "month", "day"))
        faulty = (self.faulty or "faulty" in request.work_dir.parts) and not self.conceal
        if faulty:
            created = 1 <= month <= 12 and 1 <= day <= (29 if month == 2 else calendar.monthrange(year, month)[1])
        else:
            try:
                dt.date(year, month, day)
                created = 1900 <= year <= 2100
            except ValueError:
                created = False
        if self.reject_endpoints and year in {1900, 2100}:
            created = False
        contacts = copy.deepcopy(payload["initial_contacts"])
        if created:
            contacts.append({"id": len(contacts) + 1, **copy.deepcopy(candidate)})
        for index, contact in enumerate(contacts):
            birthday = contact["birthday"]
            contact["birthday"] = f"{birthday['year']:04d}-{birthday['month']:02d}-{birthday['day']:02d}"
            if self.renumber:
                contact["id"] = index + 1
        if self.drop_seed and payload["initial_contacts"]:
            contacts = contacts[1:]
        if faulty and self.unrelated and envelope["case_id"] == "c_invalid_1900_02_29":
            contacts[-1]["phone"] = "unrelated-error"
        actual = {"created": created, "contacts_after": contacts}
        request.stdout_path.write_text(json.dumps({**{key: value for key, value in envelope.items() if key != "input"}, "status": "OK", "actual": actual}), encoding="utf8")
        request.stderr_path.write_text("", encoding="utf8")
        return RunOutcome(Termination.EXITED, 0, 1, request.stdout_path.stat().st_size, 0, FaultOwner.NONE)


def calibration(tmp_path, *, modules=False, variant="correct", **options):
    task, bundle, files = date_inputs(modules=modules, variant=variant)
    backend = DateBackend(**options)
    kwargs = dict(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend)
    return calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression"), kwargs


def test_t3_profile_declares_precise_formal_and_faulty_signatures():
    profile = get_pilot_profile("t3_date_validation")
    assert profile is not None and len(profile.formal_case_ids) == 6 and len(profile.challenge_labels) == 8
    assert profile.calibration_policy == "t3-scaffold-regression-v1"
    assert get_pilot_profile("t3_date_validation", "1.0.1") is None
    for case_id in profile.formal_case_ids + profile.supplemental_case_ids:
        expected = "MISMATCH" if case_id in profile.faulty_created_cases else "MATCH"
        assert profile.expected_assertions("faulty", case_id) == {"a_created": expected, "a_contacts": expected}
        assert profile.expected_assertions("correct", case_id) == {"a_created": "MATCH", "a_contacts": "MATCH"}


@pytest.mark.parametrize("case_id", ["challenge_2100_feb29", "challenge_common_feb29", "challenge_year_1900_valid"])
def test_t3_each_required_state_case_must_retain_nonempty_seed(tmp_path, monkeypatch, case_id):
    from cpp_assessor.planning import target_calibration as calibration_module

    task, bundle, files = date_inputs()
    cases = supplemental_cases(task)
    for case in cases:
        if case.case_id == case_id:
            case.input["initial_contacts"] = []
    monkeypatch.setattr(calibration_module, "supplemental_cases", lambda current: cases)
    backend = DateBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "missing_seed")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles
    assert "已有联系人" in record["reason"]


@pytest.mark.parametrize("task_ref,case_id", [("t1_phone_validation@1.0.0", "c_non_digit_11"), ("t2_birthday_today@1.0.0", "c_today_exact")])
def test_existing_tasks_keep_their_original_required_state_case(tmp_path, task_ref, case_id):
    task, bundle, files = date_inputs(task_ref=task_ref)
    assert get_pilot_profile(task.task_id).required_seed_case_ids == (case_id,)
    task.case(case_id).input["initial_contacts"] = []
    backend = DateBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "missing_seed")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles
    assert "已有联系人" in record["reason"]


def test_t3_supplemental_oracles_are_author_defined_and_within_sealed_schema():
    task, _, _ = date_inputs()
    original_cases = copy.deepcopy(task.cases)
    cases = supplemental_cases(task)
    profile = get_pilot_profile(task.task_id)
    assert tuple(case.case_id for case in cases) == profile.supplemental_case_ids
    expected = {
        "challenge_year_1900_valid": (1900, 1, 31, True),
        "challenge_year_2100_valid": (2100, 12, 31, True),
        "challenge_2100_feb29": (2100, 2, 29, False),
        "challenge_common_feb29": (2023, 2, 29, False),
        "challenge_leap_feb30": (2000, 2, 30, False),
        "challenge_april_30_valid": (2023, 4, 30, True),
        "challenge_january_31_valid": (2000, 1, 31, True),
        "challenge_february_31": (2000, 2, 31, False),
    }
    validator = Draft202012Validator(task.template.input_schema)
    for case in cases:
        year, month, day, created = expected[case.case_id]
        assert case.input["candidate"]["birthday"] == {"year": year, "month": month, "day": day}
        assert not list(validator.iter_errors(case.input))
        assert case.expected["created"] is created
        assert [contact["id"] for contact in case.input["initial_contacts"]] == [7, 42]
        assert [contact["id"] for contact in case.expected["contacts_after"]] == ([7, 42, 3] if created else [7, 42])
        assert case.expected["contacts_after"][0]["birthday"] == "1980-04-04"
    assert task.cases == original_cases
    assert "月份 0/13" in "".join(profile.coverage_limits)
    assert "日期 0 未执行" in "".join(profile.coverage_limits)


@pytest.mark.parametrize("modules,variant", [(False, "correct"), (False, "faulty"), (True, "correct"), (True, "faulty")])
def test_t3_calibration_reuses_current_adapter_on_two_independent_fixtures(tmp_path, modules, variant):
    record, kwargs = calibration(tmp_path, modules=modules, variant=variant)
    assert record["passed"], record
    assert record["qualification"] == "T3_REGRESSION_CALIBRATED"
    assert record["verifier_validation_level"] == "CHECKED" and record["grade_eligible"] is False
    assert record["model_calls"] == 0 and record["gate_attested"] is False
    assert len(kwargs["backend"].compiles) == 2 and len(kwargs["backend"].requests) == 28
    assert all("expected" not in envelope for envelope in kwargs["backend"].requests)
    assert [request.source_units[-1].read_text(encoding="utf8") for request in kwargs["backend"].compiles] == [kwargs["bundle"].driver.driver_source] * 2
    assert calibration_binding_matches(record, **kwargs)
    faulty = record["outcomes"][1]
    mismatches = {row["case_id"] for row in faulty["cases"] if row["expected_comparison"] == "MISMATCH"}
    assert mismatches == set(get_pilot_profile("t3_date_validation").faulty_created_cases)
    assert len(mismatches) == 5
    for row in faulty["cases"]:
        assert row["passed"] and row["initial_contacts_preserved"] and row["defect_signature_verified"]
        if row["case_id"] in {"challenge_2100_feb29", "challenge_common_feb29"}:
            assert row["actual"]["created"] is True
            assert [contact["id"] for contact in row["actual"]["contacts_after"]] == [7, 42, 3]


def test_t3_single_file_include_adapter_compiles_each_fixture_only_through_driver(tmp_path):
    task, bundle, files = date_inputs()
    bundle.driver.driver_source = '#include "date.cpp"\n' + bundle.driver.driver_source
    backend = DateBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "included_unit")
    assert record["passed"], record
    assert bundle.target.sources == ["date.cpp"]
    assert len(backend.compiles) == 2
    for request in backend.compiles:
        # Final link receives the driver once; its literal include resolves the
        # independent fixture's original source through the declared include dir.
        assert [source.name for source in request.source_units] == ["driver.cpp"]
        assert request.source_units[0].read_text(encoding="utf-8") == bundle.driver.driver_source
        assert (request.include_dirs[0] / "date.cpp").is_file()
        assert (request.include_dirs[0] / "date.cpp").read_text(encoding="utf-8") != ""
    assert len(backend.requests) == 28 and record["grade_eligible"] is False


@pytest.mark.parametrize("options", [{"conceal": True}, {"unrelated": True}, {"renumber": True}, {"drop_seed": True}, {"reject_endpoints": True}])
def test_t3_arbitrary_mismatch_missing_defect_or_damaged_state_is_not_calibration(tmp_path, options):
    record, _ = calibration(tmp_path, **options)
    assert record["status"] == "COMPLETED" and not record["passed"]
    assert record["qualification"] == "NOT_CALIBRATED" and not record["grade_eligible"]
    if options.get("unrelated"):
        row = next(row for row in record["outcomes"][1]["cases"] if row["case_id"] == "c_invalid_1900_02_29")
        assert row["assertions"] == {"a_created": "MISMATCH", "a_contacts": "MISMATCH"}
        assert not row["defect_signature_verified"]


def test_t3_only_current_function_body_may_vary_and_binding_cannot_cross_tasks(tmp_path):
    record, kwargs = calibration(tmp_path)
    changed_bundle = kwargs["bundle"].model_copy(deep=True)
    changed_bundle.driver.driver_source += "\n// changed driver"
    assert not calibration_binding_matches(record, **{**kwargs, "bundle": changed_bundle})
    files = dict(kwargs["target_files"])
    files["date.cpp"] = files["date.cpp"].replace("return phone[0] == '1';", "return true;")
    backend = DateBackend()
    unsupported = calibrate_target_verifier(**{**kwargs, "target_files": files, "source_snapshot_hash": hash_json(files), "backend": backend}, work_dir=tmp_path / "unsupported")
    assert unsupported["status"] == "UNSUPPORTED" and not backend.compiles
    altered = copy.deepcopy(record)
    altered["policy_version"] = "t1-scaffold-regression-v1"
    altered["record_hash"] = hash_json({key: value for key, value in altered.items() if key != "record_hash"})
    assert not calibration_binding_matches(altered, **kwargs)


def challenge_args(tmp_path, **options):
    task, bundle, _ = date_inputs()
    executable = tmp_path / "date.wasm"
    executable.write_bytes(b"\0asm-unit-test-only")
    return dict(task=task, bundle=bundle, backend=DateBackend(**options), executable=executable,
                run_id="run_date_student", work_dir=tmp_path / "challenges")


@pytest.mark.parametrize("options,matched,mismatched", [({}, 8, 0), ({"faulty": True}, 6, 2), ({"reject_endpoints": True}, 6, 2)])
def test_t3_current_student_observations_use_dynamic_count_and_preserve_formal_set(tmp_path, options, matched, mismatched):
    kwargs = challenge_args(tmp_path, **options)
    original = copy.deepcopy(kwargs["task"].cases)
    record = run_target_challenges(**kwargs)
    assert record["status"] == "COMPLETED"
    assert record["policy_version"] == "t3-target-challenges-v1"
    assert record["challenge_counts"] == {"required": 8, "attempted": 8, "matched": matched, "mismatched": mismatched, "not_compared": 0}
    assert record["defect_detected"] is (mismatched > 0)
    assert record["passed"] is (matched == 8)
    assert not record["grade_eligible"] and not record["model_calls"]
    assert kwargs["task"].cases == original and len(original) == 6
    assert not kwargs["backend"].compiles
    assert all("expected" not in envelope for envelope in kwargs["backend"].requests)


@pytest.mark.parametrize("kind", ["simulation", "native_backend", "native_executable"])
def test_t3_never_treats_native_or_simulated_runs_as_wasm_evidence(tmp_path, kind):
    kwargs = challenge_args(tmp_path)
    if kind == "simulation":
        kwargs["backend"].simulated = True
    elif kind == "native_backend":
        kwargs["backend"].backend_id = "local"
    else:
        kwargs["executable"].write_bytes(b"MZ-native")
    record = run_target_challenges(**kwargs)
    assert record["status"] == "UNSUPPORTED" and not kwargs["backend"].requests


@pytest.mark.parametrize("cancelled,deadline,status", [(lambda: True, None, "CANCELLED"), (None, 0, "BUDGET_EXCEEDED")])
def test_t3_supplemental_checks_respect_existing_cancellation_and_budget(tmp_path, cancelled, deadline, status):
    kwargs = challenge_args(tmp_path)
    record = run_target_challenges(**kwargs, cancelled=cancelled, deadline=deadline)
    assert record["status"] == status and not kwargs["backend"].requests
    assert record["challenge_counts"] == {"required": 8, "attempted": 0, "matched": 0, "mismatched": 0, "not_compared": 8}

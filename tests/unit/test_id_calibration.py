"""T5 preserves ordered identifiers and only legitimately surviving seed state.

The independent Python backend is a unit-test double, never real WASM evidence.
Expected records come from fixed author scenarios, never from this backend.
"""
from __future__ import annotations

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from cpp_assessor.domain.models import SandboxCapabilities
from cpp_assessor.planning.bundle import TargetVerifierBundle
from cpp_assessor.planning.pilot_tasks import get_pilot_profile
from cpp_assessor.planning.target_calibration import (calibrate_target_verifier,
    calibration_binding_matches, merged_task_source, reference_observation,
    run_target_challenges, state_evidence, supplemental_cases, trusted_task_variants)
from cpp_assessor.sandbox.base import CompileOutcome, RunOutcome
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence


def id_inputs(*, modules=False, variant="correct", include_unit=False):
    task = load_task("t5_id_allocation@1.0.0")
    trusted = trusted_task_variants(task)[variant]
    files = trusted if modules else {"id.cpp": merged_task_source(trusted)}
    driver = "// protocol_version in this unit-test adapter\nvoid addContact() {}"
    if include_unit:
        driver = '#include "id.cpp"\n' + driver
    payload = {
        "task_id": task.task_id, "task_version": str(task.version),
        "target": {"sources": list(files)},
        "build": {"compiler_flags": ["-std=c++17", "-O2"], "artifact_name": "subject.wasm"},
        "driver": {"kind": "function_driver", "driver_source": driver},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1", "produces": ["id_list", "contacts_after"]},
        "assertion_bindings": [{"assertion_id": item.assertion_id, "actual_key": item.actual_observation_key} for item in task.assertions.values()],
        "migration_matrix": [{"partition_id": part.partition_id, "rule_ids": part.rule_ids, "observation": "actual ordered identifiers and full contacts_after", "assertion_ids": list(task.assertions)} for part in task.method.input_partitions],
    }
    attach_evidence(payload, task, files, "contactbook.cpp" if modules else "id.cpp", "ContactBook::addContact")
    return task, TargetVerifierBundle.model_validate(payload), files


class IdBackend:
    backend_id = "wasm_pilot"
    simulated = False

    def __init__(self, *, faulty=False, conceal=False, unrelated=False,
                 reorder_known_bad=False, sort_ids=False, reverse_contacts=False,
                 corrupt_survivor=False, retain_deleted=False, historical_counter=False):
        self.faulty, self.conceal, self.unrelated = faulty, conceal, unrelated
        self.reorder_known_bad, self.sort_ids, self.reverse_contacts = reorder_known_bad, sort_ids, reverse_contacts
        self.corrupt_survivor, self.retain_deleted, self.historical_counter = corrupt_survivor, retain_deleted, historical_counter
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
        contacts = copy.deepcopy(payload["initial_contacts"])
        faulty = (self.faulty or "faulty" in request.work_dir.parts) and not self.conceal
        historical = max([0] + [contact["id"] for contact in contacts])
        for operation in payload["ops"]:
            if operation["kind"] == "delete":
                if not self.retain_deleted:
                    found = next((index for index, contact in enumerate(contacts) if contact["id"] == operation["id"]), None)
                    if found is not None:
                        contacts.pop(found)
            else:
                # All fixed inputs use nonempty names, valid phones and dates.
                identifier = len(contacts) + 1 if faulty else max([0] + [contact["id"] for contact in contacts]) + 1
                if self.historical_counter and not faulty:
                    historical += 1
                    identifier = historical
                contacts.append({"id": identifier, **{key: copy.deepcopy(operation[key]) for key in ("name", "phone", "birthday")}})
        for contact in contacts:
            birth = contact["birthday"]
            contact["birthday"] = f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"
        if self.corrupt_survivor and contacts:
            contacts[0]["phone"] = "changed-in-place"
        identifiers = [contact["id"] for contact in contacts]
        if self.sort_ids:
            identifiers.sort()
        if faulty and self.reorder_known_bad and envelope["case_id"] == "c_max_seed":
            identifiers.reverse()
        if faulty and self.unrelated and envelope["case_id"] == "c_max_seed":
            contacts[-1]["phone"] = "unrelated-error"
        if self.reverse_contacts:
            contacts.reverse()
        actual = {"id_list": identifiers, "contacts_after": contacts}
        request.stdout_path.write_text(json.dumps({**{key: value for key, value in envelope.items() if key != "input"}, "status": "OK", "actual": actual}), encoding="utf8")
        request.stderr_path.write_text("", encoding="utf8")
        return RunOutcome(Termination.EXITED, 0, 1, request.stdout_path.stat().st_size, 0, FaultOwner.NONE)


def calibration(tmp_path, *, modules=False, variant="correct", include_unit=False, **options):
    task, bundle, files = id_inputs(modules=modules, variant=variant, include_unit=include_unit)
    backend = IdBackend(**options)
    kwargs = dict(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend)
    return calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression"), kwargs


def test_t5_profile_has_separate_survivor_policy_and_ordered_identifier_contract():
    profile = get_pilot_profile("t5_id_allocation")
    assert profile is not None and profile.mutable_file == "contactbook.cpp"
    assert profile.state_preservation_policy == "surviving_initial_contacts_v1"
    assert profile.ordered_observation_keys == ("id_list",)
    assert len(profile.formal_case_ids) == 5 and len(profile.challenge_labels) == 8
    assert get_pilot_profile("t5_id_allocation", "1.0.1") is None
    for identifier in profile.formal_case_ids + profile.supplemental_case_ids:
        expected = "MISMATCH" if identifier in profile.faulty_id_lists else "MATCH"
        assert profile.expected_assertions("faulty", identifier) == {"a_id_list": expected, "a_contacts": expected}
        assert profile.expected_assertions("correct", identifier) == {"a_id_list": "MATCH", "a_contacts": "MATCH"}
    for task_id in ("t1_phone_validation", "t2_birthday_today", "t3_date_validation", "t4_name_search"):
        assert get_pilot_profile(task_id).state_preservation_policy == "all_initial_contacts_v1"
        assert get_pilot_profile(task_id).ordered_observation_keys == ()


def test_t5_author_oracles_are_explicit_ordered_and_allow_valid_reuse():
    task, _, _ = id_inputs()
    original = copy.deepcopy(task.cases)
    cases = supplemental_cases(task)
    correct = [[7, 42, 3, 43], [42, 9, 43], [7, 9, 10], [3, 1, 4], [1, 2, 3, 4], [9, 2, 10, 11], [42, 7], [1]]
    faulty = [[7, 42, 3, 4], [42, 9, 3], [7, 9, 3], [3, 1, 3], [1, 2, 3, 4], [9, 2, 3, 4], [42, 7], [1]]
    validator = Draft202012Validator(task.template.input_schema)
    for index, case in enumerate(cases):
        assert not list(validator.iter_errors(case.input))
        for variant, ids in (("correct", correct[index]), ("faulty", faulty[index])):
            actual = reference_observation(task, case, variant)
            assert actual["id_list"] == ids
            assert [contact["id"] for contact in actual["contacts_after"]] == ids
            assert all(set(contact) == {"id", "name", "phone", "birthday"} for contact in actual["contacts_after"])
            evidence = state_evidence(task, case, actual, variant)
            assert evidence == {"state_preservation_policy": "surviving_initial_contacts_v1", "surviving_initial_contacts_preserved": True, "state_transition_verified": True}
        assert case.expected == reference_observation(task, case)
    assert cases[4].input["ops"][0] == {"kind": "delete", "id": 3}
    assert cases[4].expected["contacts_after"][2]["name"] == "新增联系人0"
    assert cases[7].expected["contacts_after"][0]["name"] == "新增联系人0"
    assert task.cases == original


@pytest.mark.parametrize("modules,variant,include_unit", [(False, "correct", False), (False, "faulty", False), (True, "correct", False), (True, "faulty", False), (False, "correct", True)])
def test_t5_same_adapter_two_independent_fixtures_and_legal_deletions_calibrate(tmp_path, modules, variant, include_unit):
    record, kwargs = calibration(tmp_path, modules=modules, variant=variant, include_unit=include_unit)
    assert record["passed"], record
    assert record["qualification"] == "T5_REGRESSION_CALIBRATED"
    assert record["state_preservation_policy"] == "surviving_initial_contacts_v1"
    assert record["verifier_validation_level"] == "CHECKED" and record["grade_eligible"] is False
    assert record["model_calls"] == 0 and record["gate_attested"] is False
    assert len(kwargs["backend"].compiles) == 2 and len(kwargs["backend"].requests) == 26
    assert all("expected" not in request for request in kwargs["backend"].requests)
    assert calibration_binding_matches(record, **kwargs)
    for variant in record["outcomes"]:
        for row in variant["cases"]:
            assert "initial_contacts_preserved" not in row
            assert row["surviving_initial_contacts_preserved"] and row["state_transition_verified"]
            assert row["defect_signature_verified"] and row["passed"]
    faulty = record["outcomes"][1]["cases"]
    assert sum(row["expected_comparison"] == "MISMATCH" for row in faulty if row["purpose"] == "REFERENCE_REGRESSION") == 3
    assert sum(row["expected_comparison"] == "MISMATCH" for row in faulty if row["purpose"] == "SUPPLEMENTAL_CALIBRATION") == 5
    if include_unit:
        assert all([source.name for source in request.source_units] == ["driver.cpp"] for request in kwargs["backend"].compiles)


def test_contacts_are_still_multiset_while_identifier_sequence_is_ordered(tmp_path):
    record, _ = calibration(tmp_path, reverse_contacts=True)
    assert record["passed"], record
    task, _, _ = id_inputs()
    case = next(case for case in supplemental_cases(task) if case.case_id == "challenge_delete_noop_only")
    actual = reference_observation(task, case)
    actual["id_list"].reverse()
    evidence = state_evidence(task, case, actual)
    assert evidence["surviving_initial_contacts_preserved"] is True
    assert evidence["state_transition_verified"] is False


@pytest.mark.parametrize("options", [{"conceal": True}, {"unrelated": True}, {"reorder_known_bad": True}, {"sort_ids": True}, {"corrupt_survivor": True}, {"retain_deleted": True}, {"historical_counter": True}])
def test_wrong_complete_state_or_order_cannot_receive_calibration(tmp_path, options):
    record, _ = calibration(tmp_path, **options)
    assert record["status"] == "COMPLETED" and not record["passed"]
    assert record["qualification"] == "NOT_CALIBRATED" and not record["grade_eligible"]
    if options.get("unrelated") or options.get("reorder_known_bad"):
        row = next(row for row in record["outcomes"][1]["cases"] if row["case_id"] == "c_max_seed")
        assert row["assertions"] == {"a_id_list": "MISMATCH", "a_contacts": "MISMATCH"}
        assert not row["defect_signature_verified"] and not row["state_transition_verified"]


@pytest.mark.parametrize("case_id", ["c_delete_then_add", "challenge_unsorted_seed", "challenge_delete_noop_only", "challenge_clear_then_add"])
def test_required_seed_cases_cannot_be_emptied(tmp_path, monkeypatch, case_id):
    from cpp_assessor.planning import target_calibration as module

    task, bundle, files = id_inputs()
    if case_id in task.task_spec.case_ids:
        task.case(case_id).input["initial_contacts"] = []
    else:
        cases = supplemental_cases(task)
        next(case for case in cases if case.case_id == case_id).input["initial_contacts"] = []
        monkeypatch.setattr(module, "supplemental_cases", lambda current: cases)
    backend = IdBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "missing_seed")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles


def test_only_t5_add_function_body_can_change_and_state_policy_is_bound(tmp_path):
    record, kwargs = calibration(tmp_path)
    altered = copy.deepcopy(record)
    del altered["state_preservation_policy"]
    altered["record_hash"] = hash_json({key: value for key, value in altered.items() if key != "record_hash"})
    assert not calibration_binding_matches(altered, **kwargs)
    files = dict(kwargs["target_files"])
    files["id.cpp"] = files["id.cpp"].replace("if (keyword.empty())", "if (keyword.size() <= 1)")
    backend = IdBackend()
    unsupported = calibrate_target_verifier(**{**kwargs, "target_files": files, "source_snapshot_hash": hash_json(files), "backend": backend}, work_dir=tmp_path / "unsupported")
    assert unsupported["status"] == "UNSUPPORTED" and not backend.compiles


def challenge_args(tmp_path, **options):
    task, bundle, _ = id_inputs()
    executable = tmp_path / "id.wasm"
    executable.write_bytes(b"\0asm-unit-test-only")
    return dict(task=task, bundle=bundle, backend=IdBackend(**options), executable=executable,
        run_id="run_id_student", work_dir=tmp_path / "challenges")


@pytest.mark.parametrize("options,matched,mismatched", [({}, 8, 0), ({"faulty": True}, 3, 5), ({"sort_ids": True}, 3, 5)])
def test_current_target_checks_actual_order_and_surviving_records(tmp_path, options, matched, mismatched):
    kwargs = challenge_args(tmp_path, **options)
    original = copy.deepcopy(kwargs["task"].cases)
    record = run_target_challenges(**kwargs)
    assert record["status"] == "COMPLETED" and record["policy_version"] == "t5-target-challenges-v1"
    assert record["state_preservation_policy"] == "surviving_initial_contacts_v1"
    assert record["challenge_counts"] == {"required": 8, "attempted": 8, "matched": matched, "mismatched": mismatched, "not_compared": 0}
    assert record["defect_detected"] is (mismatched > 0)
    assert record["passed"] is (matched == 8)
    assert all("initial_contacts_preserved" not in row and row["surviving_initial_contacts_preserved"] for row in record["challenge_results"])
    assert sum(row["state_transition_verified"] for row in record["challenge_results"]) == matched
    assert not record["grade_eligible"] and record["model_calls"] == 0
    assert kwargs["task"].cases == original and len(original) == 5
    assert not kwargs["backend"].compiles
    assert all("expected" not in request for request in kwargs["backend"].requests)


@pytest.mark.parametrize("kind", ["simulation", "native_backend", "native_executable"])
def test_no_native_or_simulated_current_target_evidence(tmp_path, kind):
    kwargs = challenge_args(tmp_path)
    if kind == "simulation":
        kwargs["backend"].simulated = True
    elif kind == "native_backend":
        kwargs["backend"].backend_id = "local"
    else:
        kwargs["executable"].write_bytes(b"MZ-native")
    record = run_target_challenges(**kwargs)
    assert record["status"] == "UNSUPPORTED" and not kwargs["backend"].requests

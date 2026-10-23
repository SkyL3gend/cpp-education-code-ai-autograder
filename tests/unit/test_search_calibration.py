"""T4 reference signatures use actual records and unchanged source scaffolds.

The independent Python search backend is a unit-test double. These tests never
claim native/simulated output is real WASM evidence and never contact a model.
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from cpp_assessor.domain.models import SandboxCapabilities
from cpp_assessor.planning.bundle import TargetVerifierBundle
from cpp_assessor.planning.pilot_tasks import get_pilot_profile
from cpp_assessor.planning.target_calibration import (calibrate_target_verifier,
    calibration_binding_matches, merged_task_source, reference_observation,
    run_target_challenges, supplemental_cases, trusted_task_variants)
from cpp_assessor.sandbox.base import CompileOutcome, RunOutcome
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence


def search_inputs(*, modules=False, variant="correct", include_unit=False):
    task = load_task("t4_name_search@1.0.0")
    trusted = trusted_task_variants(task)[variant]
    files = trusted if modules else {"search.cpp": merged_task_source(trusted)}
    driver = "// protocol_version in this unit-test adapter\nvoid searchByName() {}"
    if include_unit:
        driver = '#include "search.cpp"\n' + driver
    payload = {
        "task_id": task.task_id, "task_version": str(task.version),
        "target": {"sources": list(files)},
        "build": {"compiler_flags": ["-std=c++17", "-O2"], "artifact_name": "subject.wasm"},
        "driver": {"kind": "function_driver", "driver_source": driver},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1", "produces": ["matches", "contacts_after"]},
        "assertion_bindings": [{"assertion_id": item.assertion_id, "actual_key": item.actual_observation_key} for item in task.assertions.values()],
        "migration_matrix": [{"partition_id": part.partition_id, "rule_ids": part.rule_ids, "observation": "actual full matched contacts and full contacts_after", "assertion_ids": list(task.assertions)} for part in task.method.input_partitions],
    }
    attach_evidence(payload, task, files, "contactbook.cpp" if modules else "search.cpp", "ContactBook::searchByName")
    return task, TargetVerifierBundle.model_validate(payload), files


class SearchBackend:
    backend_id = "wasm_pilot"
    simulated = False
    _ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

    def __init__(self, *, faulty=False, conceal=False, unrelated=False,
                 renumber=False, deduplicate_names=False, trim_keyword=False, regex_keyword=False):
        self.faulty, self.conceal, self.unrelated = faulty, conceal, unrelated
        self.renumber, self.deduplicate_names = renumber, deduplicate_names
        self.trim_keyword, self.regex_keyword = trim_keyword, regex_keyword
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
        keyword = payload["keyword"].strip() if self.trim_keyword else payload["keyword"]
        faulty = (self.faulty or "faulty" in request.work_dir.parts) and not self.conceal
        contacts, matches = [], []
        for index, original in enumerate(payload["initial_contacts"]):
            contact = copy.deepcopy(original)
            birth = contact["birthday"]
            contact["birthday"] = f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"
            if self.renumber:
                contact["id"] = index + 1
            contacts.append(contact)
            needle = keyword if faulty else keyword.translate(self._ASCII_LOWER)
            haystack = contact["name"] if faulty else contact["name"].translate(self._ASCII_LOWER)
            found = bool(needle) and (bool(re.search(needle, haystack)) if self.regex_keyword else needle in haystack)
            if found and (not faulty or not matches):
                if not self.deduplicate_names or not any(item["name"] == contact["name"] for item in matches):
                    matches.append(copy.deepcopy(contact))
        if faulty and self.unrelated and envelope["case_id"] == "c_case_insensitive":
            matches[0]["phone"] = "unrelated-error"
        actual = {"matches": matches, "contacts_after": contacts}
        request.stdout_path.write_text(json.dumps({**{key: value for key, value in envelope.items() if key != "input"}, "status": "OK", "actual": actual}), encoding="utf8")
        request.stderr_path.write_text("", encoding="utf8")
        return RunOutcome(Termination.EXITED, 0, 1, request.stdout_path.stat().st_size, 0, FaultOwner.NONE)


def calibration(tmp_path, *, modules=False, variant="correct", include_unit=False, **options):
    task, bundle, files = search_inputs(modules=modules, variant=variant, include_unit=include_unit)
    backend = SearchBackend(**options)
    kwargs = dict(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend)
    return calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression"), kwargs


def test_t4_profile_changes_only_designated_business_module():
    profile = get_pilot_profile("t4_name_search")
    assert profile.mutable_file == "contactbook.cpp" and profile.fixture_overrides == ("contactbook.cpp",)
    assert profile.calibration_policy == "t4-scaffold-regression-v1"
    assert len(profile.formal_case_ids) == 5 and len(profile.challenge_labels) == 8
    assert get_pilot_profile("t4_name_search", "1.0.1") is None
    for case_id in profile.formal_case_ids + profile.supplemental_case_ids:
        assert profile.expected_assertions("correct", case_id) == {"a_matches": "MATCH", "a_contacts": "MATCH"}
        expected = "MISMATCH" if case_id in profile.faulty_match_ids else "MATCH"
        assert profile.expected_assertions("faulty", case_id) == {"a_matches": expected, "a_contacts": "MATCH"}


def test_t4_explicit_reference_records_preserve_duplicates_ids_and_raw_keywords():
    task, _, _ = search_inputs()
    original = copy.deepcopy(task.cases)
    cases = supplemental_cases(task)
    assert tuple(case.case_id for case in cases) == get_pilot_profile(task.task_id).supplemental_case_ids
    correct_ids = [[7], [7, 42, 99], [7, 42], [7, 42, 99], [], [], [7, 42, 105], [7, 99]]
    faulty_ids = [[7], [7], [], [7], [], [], [7], [7]]
    validator = Draft202012Validator(task.template.input_schema)
    for index, case in enumerate(cases):
        assert not list(validator.iter_errors(case.input))
        correct, faulty = (reference_observation(task, case, variant) for variant in ("correct", "faulty"))
        assert correct == case.expected
        assert [contact["id"] for contact in correct["matches"]] == correct_ids[index]
        assert [contact["id"] for contact in faulty["matches"]] == faulty_ids[index]
        assert correct["contacts_after"] == faulty["contacts_after"]
        assert [contact["id"] for contact in correct["contacts_after"]] == [contact["id"] for contact in case.input["initial_contacts"]]
        for contact in correct["matches"] + faulty["matches"]:
            assert contact in correct["contacts_after"] and set(contact) == {"id", "name", "phone", "birthday"}
    assert cases[5].input["keyword"] == "" and cases[6].input["keyword"] == " " and cases[7].input["keyword"] == "."
    duplicates = cases[1].expected["matches"]
    assert duplicates[0]["name"] == duplicates[1]["name"] == "anna"
    assert duplicates[0]["id"] != duplicates[1]["id"] and duplicates[0]["phone"] != duplicates[1]["phone"]
    assert task.cases == original
    assert [item["id"] for item in reference_observation(task, task.case("c_case_insensitive"), "faulty")["matches"]] == [2]
    assert reference_observation(task, task.case("c_upper_keyword"), "faulty")["matches"] == []


@pytest.mark.parametrize("modules,variant,include_unit", [(False, "correct", False), (False, "faulty", False), (True, "correct", False), (True, "faulty", False), (False, "correct", True)])
def test_t4_two_exact_author_scaffolds_and_original_adapter_calibrate(tmp_path, modules, variant, include_unit):
    record, kwargs = calibration(tmp_path, modules=modules, variant=variant, include_unit=include_unit)
    assert record["passed"], record
    assert record["qualification"] == "T4_REGRESSION_CALIBRATED" and record["verifier_validation_level"] == "CHECKED"
    assert record["grade_eligible"] is False and record["gate_attested"] is False and record["model_calls"] == 0
    assert len(kwargs["backend"].compiles) == 2 and len(kwargs["backend"].requests) == 26
    assert all("expected" not in request for request in kwargs["backend"].requests)
    assert calibration_binding_matches(record, **kwargs)
    assert record["fixture_hashes"]["correct"] != record["fixture_hashes"]["faulty"]
    faulty = record["outcomes"][1]["cases"]
    assert sum(row["expected_comparison"] == "MISMATCH" for row in faulty if row["purpose"] == "REFERENCE_REGRESSION") == 2
    assert sum(row["expected_comparison"] == "MISMATCH" for row in faulty if row["purpose"] == "SUPPLEMENTAL_CALIBRATION") == 5
    assert all(row["assertions"]["a_contacts"] == "MATCH" and row["initial_contacts_preserved"] for row in faulty)
    if include_unit:
        assert all([source.name for source in request.source_units] == ["driver.cpp"] for request in kwargs["backend"].compiles)


@pytest.mark.parametrize("modules,change", [(False, "extra_include"), (True, "extra_include"), (False, "move_include"), (True, "other_business"), (True, "other_module")])
def test_t4_never_strips_arbitrary_includes_or_accepts_unrelated_source_changes(tmp_path, modules, change):
    task, bundle, files = search_inputs(modules=modules)
    path = "contactbook.cpp" if modules else "search.cpp"
    if change == "extra_include":
        files[path] = "#include <cstring>\n" + files[path]
    elif change == "move_include":
        files[path] = files[path].replace("#include <cctype>\n#include <cstdio>", "#include <cstdio>\n#include <cctype>")
    elif change == "other_business":
        files[path] = files[path].replace("if (name.empty())", "if (name.size() <= 1)")
    else:
        files["validators.cpp"] = files["validators.cpp"].replace("return phone[0] == '1';", "return true;")
    backend = SearchBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "unsupported")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles


@pytest.mark.parametrize("options", [{"conceal": True}, {"unrelated": True}, {"renumber": True}, {"deduplicate_names": True}, {"trim_keyword": True}, {"regex_keyword": True}])
def test_t4_missing_specific_defect_or_corrupted_records_never_calibrate(tmp_path, options):
    record, _ = calibration(tmp_path, **options)
    assert record["status"] == "COMPLETED" and not record["passed"]
    assert record["qualification"] == "NOT_CALIBRATED" and record["grade_eligible"] is False
    if options.get("unrelated"):
        row = next(row for row in record["outcomes"][1]["cases"] if row["case_id"] == "c_case_insensitive")
        assert row["assertions"] == {"a_matches": "MISMATCH", "a_contacts": "MATCH"}
        assert not row["defect_signature_verified"]


@pytest.mark.parametrize("case_id", ["c_case_insensitive", "challenge_all_duplicates", "challenge_infix"])
def test_t4_required_nonempty_state_cases_cannot_be_dropped(tmp_path, monkeypatch, case_id):
    from cpp_assessor.planning import target_calibration as module

    task, bundle, files = search_inputs()
    if case_id in task.task_spec.case_ids:
        task.case(case_id).input["initial_contacts"] = []
    else:
        cases = supplemental_cases(task)
        next(case for case in cases if case.case_id == case_id).input["initial_contacts"] = []
        monkeypatch.setattr(module, "supplemental_cases", lambda current: cases)
    backend = SearchBackend()
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files,
        source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "missing_seed")
    assert record["status"] == "UNSUPPORTED" and not backend.compiles
    assert "已有联系人" in record["reason"]


def test_t4_adapter_and_policy_changes_invalidate_bound_calibration(tmp_path):
    record, kwargs = calibration(tmp_path)
    changed = kwargs["bundle"].model_copy(deep=True)
    changed.driver.driver_source += "\n// different driver"
    assert not calibration_binding_matches(record, **{**kwargs, "bundle": changed})
    changed_record = copy.deepcopy(record)
    changed_record["policy_version"] = "t3-scaffold-regression-v1"
    changed_record["record_hash"] = hash_json({key: value for key, value in changed_record.items() if key != "record_hash"})
    assert not calibration_binding_matches(changed_record, **kwargs)


def challenge_args(tmp_path, **options):
    task, bundle, _ = search_inputs()
    executable = tmp_path / "search.wasm"
    executable.write_bytes(b"\0asm-unit-test-only")
    return dict(task=task, bundle=bundle, backend=SearchBackend(**options), executable=executable,
        run_id="run_search_student", work_dir=tmp_path / "challenges")


@pytest.mark.parametrize("options,matched,mismatched", [({}, 8, 0), ({"faulty": True}, 3, 5), ({"trim_keyword": True}, 7, 1), ({"regex_keyword": True}, 7, 1)])
def test_t4_current_target_checks_full_records_and_raw_keyword_independently(tmp_path, options, matched, mismatched):
    kwargs = challenge_args(tmp_path, **options)
    original = copy.deepcopy(kwargs["task"].cases)
    record = run_target_challenges(**kwargs)
    assert record["status"] == "COMPLETED" and record["policy_version"] == "t4-target-challenges-v1"
    assert record["challenge_counts"] == {"required": 8, "attempted": 8, "matched": matched, "mismatched": mismatched, "not_compared": 0}
    assert record["defect_detected"] is (mismatched > 0)
    assert record["passed"] is (matched == 8)
    assert not record["grade_eligible"] and record["model_calls"] == 0
    assert kwargs["task"].cases == original and len(original) == 5
    assert not kwargs["backend"].compiles
    assert all("expected" not in request for request in kwargs["backend"].requests)


@pytest.mark.parametrize("kind", ["simulation", "native_backend", "native_executable"])
def test_t4_rejects_native_or_simulated_supplemental_evidence(tmp_path, kind):
    kwargs = challenge_args(tmp_path)
    if kind == "simulation":
        kwargs["backend"].simulated = True
    elif kind == "native_backend":
        kwargs["backend"].backend_id = "local"
    else:
        kwargs["executable"].write_bytes(b"MZ-native")
    record = run_target_challenges(**kwargs)
    assert record["status"] == "UNSUPPORTED" and not kwargs["backend"].requests

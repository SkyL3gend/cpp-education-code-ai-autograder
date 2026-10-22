"""Regression evidence cannot become a self-awarded scoring qualification."""
from __future__ import annotations

import copy
import json

import pytest

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from cpp_assessor.domain.models import SandboxCapabilities
from cpp_assessor.planning.bundle import TargetVerifierBundle
from cpp_assessor.planning.target_calibration import (calibrate_target_verifier,
    calibration_binding_matches, merged_t1_source, run_target_challenges,
    trusted_t1_variants)
from cpp_assessor.sandbox.base import CompileOutcome, RunOutcome
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence


def calibration_inputs():
    task = load_task("t1_phone_validation@1.0.0")
    files = {"phone.cpp": merged_t1_source(trusted_t1_variants(task)["correct"])}
    payload = {
        "task_id": task.task_id, "task_version": str(task.version),
        "target": {"sources": ["phone.cpp"]},
        "build": {"compiler_flags": ["-std=c++17", "-O2"], "artifact_name": "subject.wasm"},
        "driver": {"kind": "function_driver", "driver_source": "// protocol_version is returned by this regression test adapter.\nvoid addContact() {}"},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1", "produces": ["created", "contacts_after"]},
        "assertion_bindings": [{"assertion_id": item.assertion_id, "actual_key": item.actual_observation_key} for item in task.assertions.values()],
        "migration_matrix": [{"partition_id": part.partition_id, "rule_ids": part.rule_ids, "observation": "actual created and full contacts_after", "assertion_ids": list(task.assertions)} for part in task.method.input_partitions],
    }
    attach_evidence(payload, task, files, "phone.cpp", "ContactBook::addContact")
    return task, TargetVerifierBundle.model_validate(payload), files


class EvidenceBackend:
    """Only a test double: never used as actual calibration evidence."""
    backend_id = "wasm_pilot"
    simulated = False

    def __init__(self, task, *, conceal_defect=False, drop_seed=False, unrelated_mismatch=False):
        self.task, self.conceal_defect, self.drop_seed = task, conceal_defect, drop_seed
        self.unrelated_mismatch = unrelated_mismatch
        self.compiles, self.requests = [], []

    def capabilities(self):
        return SandboxCapabilities(schema_version="1.0", backend_id=self.backend_id, environment_digest="a" * 64,
            isolation=IsolationLevel.WASM_RUNTIME, network_block=False, cpu_limit=True, memory_limit=True,
            process_limit=True, wall_timeout=True, tree_kill=True, clean_reset=True, filesystem_isolation=False)

    def compile(self, request):
        self.compiles.append(request)
        request.output.parent.mkdir(parents=True)
        request.output.write_bytes(b"fake-test-only")
        return CompileOutcome(CompileStatus.SUCCESS, 0, "", "", 1, "b" * 64, FaultOwner.NONE)

    def run(self, request):
        envelope = json.loads(request.stdin_path.read_text(encoding="utf8"))
        self.requests.append(envelope)
        case_id = envelope["case_id"]
        case = self.task.case(case_id if not case_id.startswith("challenge_") else "c_non_digit_11")
        actual = copy.deepcopy(case.expected)
        if "faulty" in request.work_dir.parts and case_id == "c_non_digit_11" and not self.conceal_defect:
            candidate = envelope["input"]["candidate"]
            birth = candidate["birthday"]
            actual["created"] = True
            actual["contacts_after"].append({"id": 2, "name": candidate["name"], "phone": "unrelated" if self.unrelated_mismatch else candidate["phone"],
                "birthday": f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"})
        if self.drop_seed and envelope["input"]["initial_contacts"]:
            actual["contacts_after"] = [item for item in actual["contacts_after"] if item["id"] != 7]
        request.stdout_path.write_text(json.dumps({**{key: value for key, value in envelope.items() if key != "input"}, "status": "OK", "actual": actual}), encoding="utf8")
        request.stderr_path.write_text("", encoding="utf8")
        return RunOutcome(Termination.EXITED, 0, 1, request.stdout_path.stat().st_size, 0, FaultOwner.NONE)


def run_calibration(tmp_path, **backend_options):
    task, bundle, files = calibration_inputs()
    backend = EvidenceBackend(task, **backend_options)
    kwargs = dict(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend)
    return calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression"), kwargs


def test_independent_fixtures_same_adapter_and_no_scoring_upgrade(tmp_path):
    record, kwargs = run_calibration(tmp_path)
    assert record["passed"], record
    assert record["qualification"] == "T1_REGRESSION_CALIBRATED"
    assert record["verifier_validation_level"] == "CHECKED"
    assert record["grade_eligible"] is False and record["gate_attested"] is False
    assert "filesystem_isolation" in record["gate_missing"]
    assert record["model_calls"] == 0
    backend = kwargs["backend"]
    assert len(backend.compiles) == 2
    assert [request.source_units[-1].read_text(encoding="utf8") for request in backend.compiles] == [kwargs["bundle"].driver.driver_source] * 2
    assert record["fixture_hashes"]["correct"] != record["fixture_hashes"]["faulty"]
    for variant in record["outcomes"]:
        assert len(variant["cases"]) == 7
        non_digit = next(row for row in variant["cases"] if row["case_id"] == "c_non_digit_11")
        assert non_digit["initial_contacts_preserved"]
        assert non_digit["actual"]["contacts_after"][0]["id"] == 7
    assert all("expected" not in request for request in backend.requests)
    assert calibration_binding_matches(record, **kwargs)


def test_student_all_match_is_not_independent_defect_detection(tmp_path):
    record, _ = run_calibration(tmp_path, conceal_defect=True)
    assert not record["passed"]
    assert record["qualification"] == "NOT_CALIBRATED"
    assert not record["outcomes"][1]["passed"]


def test_dropping_or_renumbering_initial_contacts_fails(tmp_path):
    record, _ = run_calibration(tmp_path, drop_seed=True)
    assert not record["passed"]
    assert any(not row["initial_contacts_preserved"] for variant in record["outcomes"] for row in variant["cases"])


def test_unrelated_mismatch_is_not_the_known_defect(tmp_path):
    record, _ = run_calibration(tmp_path, unrelated_mismatch=True)
    assert not record["passed"]
    row = next(row for row in record["outcomes"][1]["cases"] if row["case_id"] == "c_non_digit_11")
    assert row["assertions"] == {"a_created": "MISMATCH", "a_contacts": "MISMATCH"}
    assert not row["defect_signature_verified"]


def test_source_and_adapter_and_environment_changes_invalidate_binding(tmp_path):
    record, kwargs = run_calibration(tmp_path)
    assert not calibration_binding_matches(record, **{**kwargs, "source_snapshot_hash": "c" * 64})
    assert not calibration_binding_matches(record, **{**kwargs, "target_files": {"phone.cpp": "different"}})
    changed = kwargs["bundle"].model_copy(deep=True)
    changed.driver.driver_source += "\n// changed"
    assert not calibration_binding_matches(record, **{**kwargs, "bundle": changed})
    tampered = copy.deepcopy(record)
    tampered["outcomes"][0]["cases"][0]["actual"]["created"] = False
    assert not calibration_binding_matches(tampered, **kwargs)


def test_unknown_business_layout_stays_checked_without_execution(tmp_path):
    task, bundle, files = calibration_inputs()
    files["phone.cpp"] = files["phone.cpp"].replace("contacts_.push_back(c);", "contacts_.push_back(c); contacts_.clear();")
    backend = EvidenceBackend(task)
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "unsupported")
    assert record["status"] == "UNSUPPORTED"
    assert record["verifier_validation_level"] == "CHECKED"
    assert not backend.compiles


def test_original_four_module_shape_is_supported_without_rewriting_student_files(tmp_path):
    task, bundle, _ = calibration_inputs()
    files = trusted_t1_variants(task)["faulty"]
    original = copy.deepcopy(files)
    payload = bundle.model_dump(mode="json")
    payload["target"]["sources"] = list(files)
    attach_evidence(payload, task, files, "contactbook.cpp", "ContactBook::addContact")
    bundle = TargetVerifierBundle.model_validate(payload)
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=EvidenceBackend(task), work_dir=tmp_path / "modules")
    assert record["passed"] and record["build_shape"] == "four_modules"
    assert files == original


@pytest.mark.parametrize("cancelled,deadline,status", [(lambda: True, None, "CANCELLED"), (None, 0, "BUDGET_EXCEEDED")])
def test_existing_cancellation_and_total_budget_are_respected(tmp_path, cancelled, deadline, status):
    task, bundle, files = calibration_inputs()
    backend = EvidenceBackend(task)
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "bounded", cancelled=cancelled, deadline=deadline)
    assert record["status"] == status and not backend.compiles


def test_simulation_never_counts_as_calibration(tmp_path):
    task, bundle, files = calibration_inputs()
    backend = EvidenceBackend(task)
    backend.simulated = True
    record = calibrate_target_verifier(task=task, bundle=bundle, target_files=files, source_snapshot_hash=hash_json(files), backend=backend, work_dir=tmp_path / "mock")
    assert record["status"] == "UNSUPPORTED" and not record["passed"]


def test_rejects_overwriting_evidence(tmp_path):
    _, kwargs = run_calibration(tmp_path)
    with pytest.raises(ValueError, match="全新"):
        calibrate_target_verifier(**kwargs, work_dir=tmp_path / "regression")


class ChallengeBackend(EvidenceBackend):
    def __init__(self, task, *, accepts_prefix_2=False, malformed=False, change_executable=False):
        super().__init__(task)
        self.accepts_prefix_2 = accepts_prefix_2
        self.malformed = malformed
        self.change_executable = change_executable

    def run(self, request):
        outcome = super().run(request)
        payload = json.loads(request.stdout_path.read_text(encoding="utf8"))
        if self.accepts_prefix_2 and payload["case_id"] == "challenge_prefix_2":
            candidate = self.requests[-1]["input"]["candidate"]
            payload["actual"]["created"] = True
            payload["actual"]["contacts_after"].append({"id": 2, "name": candidate["name"], "phone": candidate["phone"], "birthday": "2000-04-04"})
        request.stdout_path.write_text("not an observation" if self.malformed else json.dumps(payload), encoding="utf8")
        if self.change_executable:
            request.executable.write_bytes(b"\0asmchanged")
        return outcome


def challenge_inputs(tmp_path, **backend_options):
    task, bundle, _ = calibration_inputs()
    module = tmp_path / "current_student.wasm"
    module.write_bytes(b"\0asm-test-only")
    backend = ChallengeBackend(task, **backend_options)
    return dict(task=task, bundle=bundle, backend=backend, executable=module, run_id="run_actual_student", work_dir=tmp_path / "supplemental")


def test_challenges_run_current_target_without_changing_formal_cases(tmp_path):
    kwargs = challenge_inputs(tmp_path)
    task = kwargs["task"]
    original_cases = copy.deepcopy(task.cases)
    record = run_target_challenges(**kwargs)
    assert record["passed"] and record["status"] == "COMPLETED"
    assert not record["grade_eligible"] and record["model_calls"] == 0
    assert record["challenge_counts"] == {"required": 3, "attempted": 3, "matched": 3, "mismatched": 0, "not_compared": 0}
    assert task.cases == original_cases and len(task.cases) == 4
    backend = kwargs["backend"]
    assert not backend.compiles and len(backend.requests) == 3
    assert all("expected" not in envelope for envelope in backend.requests)
    assert all(envelope["input"]["initial_contacts"][0]["id"] == 7 for envelope in backend.requests)
    for row in record["challenge_results"]:
        assert row["assertion_results"] and row["actual"] == row["expected"]
        assert row["protocol_status"] == "OK" and row["termination"] == "EXITED"
    assert len(record["executable_hash"]) == 64
    assert record["verifier_bundle_hash"] == hash_json(kwargs["bundle"].model_dump(mode="json"))


def test_extra_prefix_defect_is_detected_separately_from_four_formal_counts(tmp_path):
    record = run_target_challenges(**challenge_inputs(tmp_path, accepts_prefix_2=True))
    assert record["status"] == "COMPLETED" and record["defect_detected"]
    assert not record["passed"] and not record["grade_eligible"]
    assert record["challenge_counts"]["mismatched"] == 1
    assert record["challenge_counts"]["matched"] == 2
    row = record["challenge_results"][0]
    assert row["case_id"] == "challenge_prefix_2" and row["comparison"] == "MISMATCH"
    assert row["expected"]["created"] is False and row["actual"]["created"] is True
    assert {item["comparison"] for item in row["assertion_results"]} == {"MISMATCH"}


def test_unusable_challenge_protocol_is_not_a_student_defect(tmp_path):
    record = run_target_challenges(**challenge_inputs(tmp_path, malformed=True))
    assert record["status"] == "COMPLETED" and not record["passed"]
    assert not record["defect_detected"]
    assert record["challenge_counts"]["not_compared"] == 3
    assert all(row["comparison"] == "NOT_COMPARED" for row in record["challenge_results"])


@pytest.mark.parametrize("cancelled,deadline,status", [(lambda: True, None, "CANCELLED"), (None, 0, "BUDGET_EXCEEDED")])
def test_challenges_share_cancellation_and_budget(tmp_path, cancelled, deadline, status):
    kwargs = challenge_inputs(tmp_path)
    record = run_target_challenges(**kwargs, cancelled=cancelled, deadline=deadline)
    assert record["status"] == status and not kwargs["backend"].requests
    assert not record["defect_detected"]


def test_changed_executable_invalidates_target_challenge_results(tmp_path):
    record = run_target_challenges(**challenge_inputs(tmp_path, change_executable=True))
    assert record["status"] == "INVALIDATED" and not record["passed"]
    assert not record["defect_detected"]


def test_native_target_is_not_run_by_challenges(tmp_path):
    kwargs = challenge_inputs(tmp_path)
    kwargs["executable"].write_bytes(b"MZnative executable")
    record = run_target_challenges(**kwargs)
    assert record["status"] == "UNSUPPORTED" and not kwargs["backend"].requests

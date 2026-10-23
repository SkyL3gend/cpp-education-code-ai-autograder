"""Trusted local compiler regression for a driver including an uploaded CPP.

The model is a deterministic substitute; only this checked-in fixture is allowed
to execute on the non-isolated local backend. No API or network is involved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from cpp_assessor.domain.enums import ExecutionStatus, Mode, OverallStatus
from cpp_assessor.llm.provider import LLMSettings
from cpp_assessor.orchestrator import mode_two
from cpp_assessor.orchestrator.replay import replay_run
from cpp_assessor.orchestrator.runner import RunOptions
from cpp_assessor.tasks.loader import load_task
from tests.target_verifier_fixture import LIB, bundle
from tests.verifier_fixtures import attach_evidence


pytestmark = pytest.mark.skipif(shutil.which("g++") is None,
                               reason="requires trusted fixture local compiler")

TARGET_SOURCE = LIB.replace("#pragma once\n", "").replace("inline Value createEntry", "Value createEntry") + r'''
class EntryBook {
    Value latest_;
public:
    Value create(const Value& input);
    const Value& contacts() const { return *latest_.find("contacts_after"); }
};
Value EntryBook::create(const Value& input) {
    latest_ = createEntry(input);
    return latest_;
}
'''

DRIVER_SOURCE = r'''
#include <iostream>
#include <sstream>
#include "contact_target.cpp"
int main() {
    std::ostringstream input; input << std::cin.rdbuf();
    auto request = jsonlite::parse(input.str());
    EntryBook book;
    auto actual = book.create(*request.find("input"));
    actual["contacts_after"] = book.contacts();
    request.objValue.erase("input");
    request["protocol_version"] = Value::of(request.stringOr("protocol_version", ""));
    request["status"] = Value::of(std::string("OK"));
    request["actual"] = actual;
    std::cout << jsonlite::dump(request) << std::endl;
}
'''


def test_direct_cpp_include_compiles_original_once_and_replays_without_model(tmp_path, monkeypatch):
    task = load_task("t1_phone_validation@1.0.0")
    target = tmp_path / "trusted_target"
    target.mkdir()
    (target / "contact_target.cpp").write_text(TARGET_SOURCE, encoding="utf-8")
    original_source_bytes = (target / "contact_target.cpp").read_bytes()
    (target / "json_lite.h").write_bytes((task.harness_dir / "json_lite.h").read_bytes())
    response = bundle(target, function=True)
    response["target"] = {"sources": ["contact_target.cpp"], "entry_files": [],
                          "excluded_files": [], "include_dirs": []}
    response["driver"]["driver_source"] = DRIVER_SOURCE
    attach_evidence(response, task, {"contact_target.cpp": TARGET_SOURCE}, "contact_target.cpp", "EntryBook::create")
    for binding in response["source_bindings"]:
        if binding["role"] == "OUTPUT":
            binding.update(symbol="contacts", source_quote='const Value& contacts() const { return *latest_.find("contacts_after"); }')
    calls = []

    class DeterministicClient:
        def __init__(self, *args, **kwargs):
            pass

        def complete_json(self, *, system, user, call_name, **kwargs):
            calls.append(call_name)
            payload = json.JSONDecoder().raw_decode(user)[0]
            if call_name == "review_verifier":
                return SimpleNamespace(data={"issues": [], "method_hash": payload["reference"]["method_hash"]})
            assert call_name == "bundle_t1_phone_validation", "A valid direct include must not need repair"
            return SimpleNamespace(data=copy.deepcopy(response))

    monkeypatch.setattr(mode_two, "LLMClient", DeterministicClient)
    root = tmp_path / "data"
    result = mode_two.execute_llm_run(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=target,
        mode=Mode.LLM, backend="local", data_dir=root, allow_unattested=True, trusted_fixture=True),
        LLMSettings(api_key="test-no-network"))
    assert result.execution_status is ExecutionStatus.COMPLETED, result.error
    assert result.assessment_status is OverallStatus.INCONCLUSIVE
    assert not result.grade_eligible
    assert result.report.counts.matched_cases == result.report.counts.required_cases == 4
    assert calls == ["bundle_t1_phone_validation", "review_verifier"]

    def check_artifacts(run_dir):
        original = json.loads((run_dir / "evidence/compile/original_units.json").read_text(encoding="utf-8"))
        assert [(item["source"], item["status"], item["scope"]) for item in original] == [
            ("contact_target.cpp", "SUCCESS", "ORIGINAL_TRANSLATION_UNIT_ONLY")]
        plan = json.loads((run_dir / "plans/build_plan.json").read_text(encoding="utf-8"))
        assert [unit["path"] for unit in plan["source_units"]] == ["generated/driver/driver.cpp"]
        assert plan["source_units"][0]["origin"] == "GENERATED"
        effective = json.loads((run_dir / "plans/effective_build_sources.json").read_text(encoding="utf-8"))
        assert effective == {"original_compile_units": ["contact_target.cpp"], "linked_target_units": [],
                             "driver_included_units": ["contact_target.cpp"], "generated_driver": True}
        frozen = json.loads((run_dir / "plans/frozen_plan.json").read_text(encoding="utf-8"))
        assert frozen["bundle"]["target"]["sources"] == ["contact_target.cpp"]
        assert frozen["bundle"]["driver"]["driver_source"] == DRIVER_SOURCE
        manifest = json.loads((run_dir / "plans/replay_manifest.json").read_text(encoding="utf-8"))
        assert manifest["file_hashes"]["generated/subject/contact_target.cpp"] == hashlib.sha256(
            original_source_bytes).hexdigest()
        assert (run_dir / "generated/subject/contact_target.cpp").read_bytes() == original_source_bytes
        assert (run_dir / "submissions" / frozen["submission"]["submission_id"] /
                "original/contact_target.cpp").read_bytes() == original_source_bytes
        assert (run_dir / "generated/driver/driver.cpp").read_text(encoding="utf-8") == DRIVER_SOURCE

    check_artifacts(result.run_dir)

    class NoModelDuringReplay:
        def __init__(self, *args, **kwargs):
            pass

        def complete_json(self, *args, **kwargs):
            pytest.fail("Frozen verifier replay must not request a model response")

    monkeypatch.setattr(mode_two, "LLMClient", NoModelDuringReplay)
    replayed = replay_run(result.run_id, root=root, trusted_fixture=True)
    assert replayed.run_id != result.run_id
    assert replayed.execution_status is ExecutionStatus.COMPLETED, replayed.error
    assert replayed.report.counts == result.report.counts
    check_artifacts(replayed.run_dir)
    assert json.loads((replayed.run_dir / "replay_of.json").read_text(encoding="utf-8"))["parent_run_id"] == result.run_id

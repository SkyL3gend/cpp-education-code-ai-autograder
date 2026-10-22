"""Read-only review reproductions. No LLM requests or C++ execution.

Run with Python >= 3.11 and pydantic >= 2.6. The checks report current
unexpected behavior; they are not intended to bless that behavior as correct.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from cpp_assessor.domain.enums import (  # noqa: E402
    CasePurpose, ComparisonStatus, ExecutionStatus, IsolationLevel,
    Operator, ProtocolStatus, Termination, ValidationLevel,
)
from cpp_assessor.domain.models import (  # noqa: E402
    AssertionResult, CaseResult, CapabilityRequirements, CoverageReport, IOTemplate,
    SandboxCapabilities, TaskCase, TaskDocument, TestMethodSpec,
)
from cpp_assessor.judge.aggregate import aggregate  # noqa: E402
from cpp_assessor.judge.assertions import evaluate  # noqa: E402
from cpp_assessor.judge.extractors import (  # noqa: E402
    contactbook_menu_v1, extractor_ids, keyword_search_v1,
)
from cpp_assessor.planning.bundle import TargetVerifierBundle, validate_bundle  # noqa: E402
from cpp_assessor.sandbox.gate import evaluate as evaluate_gate  # noqa: E402


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


task_dir = ROOT / "task_library/tasks/t1_phone_validation/1.0.0"
document = TaskDocument.model_validate(read(task_dir / "task.json"))
method = TestMethodSpec.model_validate(read(task_dir / "test_method.json"))
template = IOTemplate.model_validate(read(task_dir / "io_template.json"))
cases = {c.case_id: c for c in map(TaskCase.model_validate, read(task_dir / "cases.json")["cases"])}
assertions = {a.assertion_id: a for a in method.assertions}
task = SimpleNamespace(document=document, method=method, template=template, assertions=assertions)
results = {}


def compare(case, facts):
    return {
        aid: evaluate(assertions[aid], case_expected=case.expected, actual_document=facts).comparison.value
        for aid in case.assertion_ids
    }


# A name-only row does not reveal the stored phone, birthday or identifier.
case = cases["c_valid_11_ascii"]
facts = contactbook_menu_v1(
    "4.[普通] 张三\n",
    {**case.input["candidate"], "initial_contacts": case.input["initial_contacts"]},
)
results["R1_unobserved_phone_birthday_id_filled_from_input"] = {
    "actual": facts, "comparisons": compare(case, facts),
    "bug_reproduced": all(x == "MATCH" for x in compare(case, facts).values()),
}

# The initial record is absent from stdout but still appears in actual.
case = cases["c_non_digit_11"]
facts = contactbook_menu_v1(
    "1.[普通] Demo\n",
    {**case.input["candidate"], "initial_contacts": case.input["initial_contacts"], "baseline_names": ["Demo"]},
)
results["R2_unobserved_initial_state_accepted_as_actual"] = {
    "actual": facts, "comparisons": compare(case, facts),
    "bug_reproduced": all(x == "MATCH" for x in compare(case, facts).values()),
}

# A visibly incorrect phone is replaced with the declared initial phone.
initial = [{"id": 1, "name": "Anna", "phone": "13800138000", "birthday": "2000-01-01"}]
facts = keyword_search_v1("1.[普通] Anna 99999999999\n", {"initial_contacts": initial})
results["R3_search_output_phone_overwritten"] = {
    "actual": facts,
    "bug_reproduced": facts["matches"][0]["phone"] == "13800138000",
}

coverage = CoverageReport(
    planned_cases=["c1"], completed_cases=["c1"], missed_cases=[],
    planned_partitions=["p1"], covered_partitions=["p1"], missing_partitions=[],
    planned_assertions=["a1"], executed_assertions=["a1"], missing_assertions=[], complete=True,
)
case_result = CaseResult(
    schema_version="1.0", case_id="c1", attempt_id="a1", purpose=CasePurpose.ASSESSMENT,
    comparison=ComparisonStatus.MATCH, termination=Termination.EXITED,
    protocol_status=ProtocolStatus.OK, assertion_results=[AssertionResult(
        schema_version="1.0", assertion_id="a1", case_id="c1", operator=Operator.EQUALS,
        comparison=ComparisonStatus.MATCH, expected=True, actual=True,
    )], failed_assertions=[], evidence_refs=[],
)
outcome = aggregate(
    execution_status=ExecutionStatus.COMPLETED, case_results=[case_result], coverage=coverage,
    validation_level=ValidationLevel.CHECKED, task_published=True, assertions_confirmed=True,
    execution_backend_attested=True,
)
results["R4_checked_adapter_becomes_grade_eligible"] = {
    "assessment_status": outcome.assessment_status.value, "grade_eligible": outcome.grade_eligible,
    "bug_reproduced": outcome.grade_eligible,
}
crash = case_result.model_copy(update={
    "comparison": ComparisonStatus.NOT_COMPARED, "termination": Termination.CRASH,
    "protocol_status": ProtocolStatus.MISSING, "note": "SUBJECT_FAULT",
})
outcome = aggregate(
    execution_status=ExecutionStatus.COMPLETED, case_results=[crash], coverage=coverage,
    validation_level=ValidationLevel.UNVERIFIED, task_published=False, assertions_confirmed=False,
    execution_backend_attested=True,
)
results["R5_unpublished_unverified_crash_becomes_grade_eligible"] = {
    "assessment_status": outcome.assessment_status.value, "grade_eligible": outcome.grade_eligible,
    "bug_reproduced": outcome.grade_eligible,
}

base_bundle = {
    "task_id": document.task_spec.task_id, "task_version": document.task_spec.version,
    "target": {"sources": ["student.cpp"]},
    "driver": {"kind": "stdin_script", "session": ["0"]},
    "observer": {"kind": "extractor", "extractor_id": "contactbook_menu_v1"},
    "assertion_bindings": [
        {"assertion_id": a.assertion_id, "actual_key": a.actual_observation_key} for a in method.assertions
    ],
    "migration_matrix": [{"partition_id": p.partition_id, "status": "gap"} for p in method.input_partitions],
    "coverage_gaps": ["The test did not reach the required action or observe stored records."],
}
bundle = TargetVerifierBundle.model_validate(base_bundle)
validation = validate_bundle(bundle, task, {"student.cpp": "int main(){return 0;}"}, extractor_ids=extractor_ids())
results["R6_explicit_migration_gaps_pass_static_validation"] = {
    "passed": validation.passed, "problems": validation.problems,
    "bug_reproduced": validation.passed,
}
unsafe = dict(base_bundle)
unsafe["build"] = {"artifact_name": "C:/review-canary.exe", "compiler_flags": ["-fplugin=C:/review-plugin.dll"]}
validation = validate_bundle(
    TargetVerifierBundle.model_validate(unsafe), task,
    {"student.cpp": "int main(){return 0;}"}, extractor_ids=extractor_ids(),
)
results["R7_unrestricted_output_path_and_compiler_flags"] = {
    "passed": validation.passed, "problems": validation.problems,
    "bug_reproduced": validation.passed,
}

no_call = dict(base_bundle)
no_call["driver"] = {
    "kind": "function_driver",
    "driver_source": '#include <iostream>\nint main(){std::cout << R"({"protocol_version":"1.0","status":"OK","actual":{"created":false,"contacts_after":[]}})";}',
}
no_call["observer"] = {"kind": "json_stdout", "extractor_id": "json_stdout_v1"}
validation = validate_bundle(
    TargetVerifierBundle.model_validate(no_call), task,
    {"student.cpp": "bool add_contact(){return true;}"}, extractor_ids=extractor_ids(),
)
results["R9_generated_driver_never_calls_student_implementation"] = {
    "passed": validation.passed, "problems": validation.problems,
    "bug_reproduced": validation.passed,
}

capabilities = SandboxCapabilities(
    schema_version="1.0", backend_id="review_only", environment_digest="0" * 64,
    isolation=IsolationLevel.HOST_PROCESS, network_block=True, cpu_limit=True,
    memory_limit=True, process_limit=False, wall_timeout=True, tree_kill=True,
    clean_reset=False, filesystem_isolation=False,
)
gate = evaluate_gate(capabilities, CapabilityRequirements(), allow_unattested=False)
results["R8_missing_process_reset_filesystem_limits_still_attested"] = {
    "allowed": gate.allowed, "attested": gate.attested, "missing": gate.missing,
    "bug_reproduced": gate.attested,
}

payload = json.dumps(results, ensure_ascii=False, indent=2)
if "--write-results" in sys.argv:
    Path(__file__).with_suffix(".json").write_text(payload + "\n", encoding="utf-8")
print(payload)

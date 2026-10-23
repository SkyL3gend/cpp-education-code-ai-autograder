"""Bounded, model-free regression calibration of supported target verifiers.

This is deliberately narrower than authoritative CALIBRATED/REVIEWED status.
The same adapter is exercised against independent known-correct and known-bad
business implementations, never against the student's own results as an oracle.
Only exact registered task scaffolds are supported. Unknown layouts stay CHECKED.
Compilation and runtime isolation remain separate from verifier correctness.
"""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from ..domain.canonical import canonical_json, hash_json
from ..domain.enums import (BuildRole, CompileStatus,
                            ProtocolStatus, SourceOrigin, Termination)
from ..domain.models import BuildPlan, ResourceLimits, SourceUnit
from ..judge.assertions import evaluate, strict_equal
from ..judge.extractors import extractor_ids
from ..orchestrator.protocol import build_request, parse_response
from ..sandbox.base import CompileRequest, RunRequest
from ..sandbox.gate import evaluate as evaluate_gate
from .bundle import TargetVerifierBundle, validate_bundle
from .pilot_tasks import PilotTaskProfile, get_pilot_profile

POLICY_VERSION = "t1-scaffold-regression-v1"
_FILES = ("validators.h", "contactbook.h", "validators.cpp", "contactbook.cpp")


def _text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    # The checked-in pilot amalgamation differs only in empty line counts.
    return re.sub(r"\n(?:[ \t]*\n)+", "\n\n", value)


def _scaffold(source: str, signature: str) -> str | None:
    """Match the exact published scaffold except the single function body.

    This checks calibration applicability, not C++ safety. Conservative lexical
    handling declines raw strings, preprocessing and unusual function spelling.
    No student source is rewritten or replaced during actual assessment.
    """
    source = _text(source)
    if source.count(signature) != 1:
        return None
    begin = source.index(signature) + len(signature) - 1
    tail = source[begin:]
    if 'R"' in tail:
        return None
    tokens = re.finditer(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', tail)
    depth = 0
    for token in tokens:
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                end = begin + token.end()
                if re.search(r"^\s*#", source[begin:end], re.M):
                    return None
                return source[:begin] + "{TARGET_IMPLEMENTATION}" + source[end:]
    return None


def trusted_task_variants(task) -> dict[str, dict[str, str]]:
    """Read author-controlled fixtures from the loaded package, not a request."""
    profile = get_pilot_profile(task.task_id, str(task.version))
    if profile is None:
        raise ValueError("该任务版本未登记固定工程校准")
    if tuple(task.task_spec.case_ids) != profile.formal_case_ids:
        raise ValueError("固定任务的正式用例清单发生变化")
    variants = {item.variant_id: item for item in task.document.reference_suite.variants}
    if set(variants) != {"correct", "faulty"} or variants["correct"].role.value != "CORRECT" or variants["faulty"].role.value != "FAULTY":
        raise ValueError("缺少独立的已知正确和已知缺陷夹具")
    if tuple(variants["faulty"].must_fail_cases) != profile.declared_faulty_cases or variants["correct"].must_fail_cases:
        raise ValueError("固定任务的参照缺陷声明发生变化")
    result = {}
    for name, variant in variants.items():
        if variant.overrides != ["validators.cpp"]:
            raise ValueError("不支持的固定夹具构建形态")
        result[name] = {filename: (task.task_dir / "variants" / name / filename if filename in variant.overrides else task.project_root / filename).read_text(encoding="utf-8") for filename in _FILES}
    return result


def trusted_t1_variants(task) -> dict[str, dict[str, str]]:
    """Compatibility entry point for the original T1 pilot."""
    if (task.task_id, str(task.version)) != ("t1_phone_validation", "1.0.0"):
        raise ValueError("只支持 T1@1.0.0 的固定工程校准")
    return trusted_task_variants(task)


def merged_task_source(files: dict[str, str]) -> str:
    """Reproduce the pilot's transparent amalgamation of the trusted modules."""
    parts = []
    for name in _FILES:
        source = files[name]
        for directive in ('#pragma once', '#include "validators.h"', '#include "contactbook.h"'):
            source = source.replace(directive, "")
        parts.append(source)
    return "\n".join(parts).rstrip() + "\n"


merged_t1_source = merged_task_source


def _fixtures_for_target(task, bundle, files):
    profile = get_pilot_profile(task.task_id, str(task.version))
    if profile is None:
        raise ValueError("该任务版本未登记固定工程校准")
    trusted = trusted_task_variants(task)
    if bundle.driver.kind != "function_driver" or bundle.observer.kind not in {"json_stdout", "extractor"} or bundle.observer.extractor_id != "json_stdout_v1" or bundle.observer.params or bundle.observer.artifact_paths:
        raise ValueError("当前校准仅支持函数驱动和原始 JSON 观测；不支持菜单或生成观测器")
    if bundle.target.entry_files or bundle.target.excluded_files or bundle.target.include_dirs or bundle.target.runtime_files or bundle.build.defines or bundle.build.libraries:
        raise ValueError("当前校准仅支持固定无 main 工程、默认 include 和无额外构建选项")
    if bundle.target.encoding.lower() not in {"utf-8", "utf8"}:
        raise ValueError("当前固定校准夹具仅支持 UTF-8")
    if len(files) == 1:
        filename, actual = next(iter(files.items()))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(cpp|cc|cxx)", filename) or bundle.target.sources != [filename]:
            raise ValueError("合并工程必须是唯一的相对 C++ 编译单元")
        expected = merged_task_source(trusted["correct"])
        if _scaffold(actual, profile.mutable_signature) is None or _scaffold(actual, profile.mutable_signature) != _scaffold(expected, profile.mutable_signature):
            raise ValueError("目标不属于该题固定合并工程；不能迁移独立夹具校准")
        return "single_amalgamation", {name: {filename: merged_task_source(value)} for name, value in trusted.items()}
    if set(files) != set(_FILES) or set(bundle.target.sources) - set(_FILES) or not {"validators.cpp", "contactbook.cpp"} <= set(bundle.target.sources):
        raise ValueError("当前校准仅支持单合并文件或原始四模块工程")
    for filename in _FILES:
        actual, expected = _text(files[filename]), _text(trusted["correct"][filename])
        if filename == "validators.cpp":
            if _scaffold(actual, profile.mutable_signature) is None or _scaffold(actual, profile.mutable_signature) != _scaffold(expected, profile.mutable_signature):
                raise ValueError("validators.cpp 在当前题指定函数以外的结构发生变化")
        elif actual != expected:
            raise ValueError(f"固定工程的 {filename} 发生变化，无法迁移校准")
    return "four_modules", trusted


def _supplemental_cases(task):
    profile = get_pilot_profile(task.task_id, str(task.version))
    if profile is None:
        raise ValueError("该任务版本未登记补充规则检查")
    if profile.task_id == "t2_birthday_today":
        return _birthday_supplemental_cases(task)
    seed = task.case("c_non_digit_11")
    challenges = []
    for identifier, phone in (("challenge_prefix_2", "23800138000"), ("challenge_empty", ""), ("challenge_fullwidth", "１３８００１３８０００")):
        payload = copy.deepcopy(seed.input)
        payload["candidate"]["phone"] = phone
        challenges.append(seed.model_copy(update={"case_id": identifier, "input": payload}))
    return challenges


def _project_contacts(contacts):
    projected = copy.deepcopy(contacts)
    for contact in projected:
        birth = contact["birthday"]
        contact["birthday"] = f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"
    return projected


def _birthday_supplemental_cases(task):
    """Author-derived short-window boundaries; no unspecified non-leap policy.

    Neither arbitrary civil-calendar inputs nor non-leap 2/29 conventions are
    introduced. Distinct IDs and duplicate names expose lossy seeding/observers.
    """
    seed = task.case("c_today_window")

    def contact(identifier, name, year, month, day):
        return {"id": identifier, "name": name, "phone": f"13800138{identifier:03d}", "birthday": {"year": year, "month": month, "day": day}}

    definitions = (
        ("challenge_year_boundary", {"year": 2026, "month": 12, "day": 31},
         [contact(7, "岁末", 1990, 12, 31), contact(42, "元旦", 2000, 1, 1), contact(99, "窗外", 1990, 1, 2), contact(105, "昨日", 1990, 12, 30)], ["岁末", "元旦"]),
        ("challenge_leap_boundary", {"year": 2024, "month": 2, "day": 28},
         [contact(7, "二月今日", 1990, 2, 28), contact(42, "闰日", 1992, 2, 29), contact(99, "三月", 1990, 3, 1), contact(105, "昨日", 1990, 2, 27)], ["二月今日", "闰日"]),
        ("challenge_duplicate_birthdays", {"year": 2026, "month": 10, "day": 18},
         [contact(7, "同名", 1990, 10, 19), contact(42, "同名", 1992, 10, 19), contact(99, "异名", 1994, 10, 19), contact(105, "窗外", 1990, 10, 20)], ["同名", "同名", "异名"]),
    )
    return [seed.model_copy(update={"case_id": identifier,
        "input": {"initial_contacts": contacts, "today": today, "days_ahead": 1},
        "expected": {"names": names, "contacts_after": _project_contacts(contacts)}})
        for identifier, today, contacts, names in definitions]


def _fixture_expectation(profile: PilotTaskProfile, case, variant: str):
    """Independently specified defect observations, never executable inputs."""
    actual = copy.deepcopy(case.expected)
    if variant == "faulty":
        if profile.task_id == "t1_phone_validation" and case.case_id == "c_non_digit_11":
            candidate = case.input["candidate"]
            birth = candidate["birthday"]
            actual["created"] = True
            actual["contacts_after"] = _project_contacts(case.input["initial_contacts"]) + [{"id": len(case.input["initial_contacts"]) + 1,
                "name": candidate["name"], "phone": candidate["phone"], "birthday": f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"}]
        for excluded in profile.faulty_name_exclusions.get(case.case_id, ()):
            actual["names"].remove(excluded)
    return actual


def _same_observation(actual, expected):
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    return all(Counter(map(canonical_json, actual[key])) == Counter(map(canonical_json, expected[key]))
               if isinstance(expected[key], list) and isinstance(actual[key], list)
               else strict_equal(actual[key], expected[key]) for key in expected)


def _write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def calibrate_target_verifier(*, task, bundle: TargetVerifierBundle,
                              target_files: dict[str, str], source_snapshot_hash: str,
                              backend, work_dir: Path, limits: ResourceLimits | None = None,
                              cancelled: Callable[[], bool] | None = None,
                              deadline: float | None = None) -> dict[str, Any]:
    """Run independent task fixtures with *exactly* the selected verifier bundle.

    Call once after build/probe and before freezing the actual assessment. Supply
    the original manifest snapshot hash and the existing monotonic deadline.
    Output is evidence, not permission to run and not formal scoring authority.
    It always preserves CHECKED and grade_eligible=False. No model is contacted.
    ``work_dir`` must be a fresh directory outside the student's subject tree.
    """
    started = time.monotonic()
    profile = get_pilot_profile(task.task_id, str(task.version))
    limits = limits or task.document.limits
    deadline = min(deadline if deadline is not None else float("inf"), started + limits.total_wall_ms / 1000)
    capabilities = backend.capabilities()
    gate = evaluate_gate(capabilities, task.template.capability_requirements, allow_unattested=False)
    record: dict[str, Any] = {
        "schema_version": "1.0", "policy_version": profile.calibration_policy if profile else "unsupported-task",
        "task_package_hash": task.package_hash,
        "source_snapshot_hash": source_snapshot_hash,
        "target_files_hash": hash_json(target_files),
        "verifier_bundle_hash": hash_json(bundle.model_dump(mode="json")),
        "driver_hash": hashlib.sha256(bundle.driver.driver_source.encode()).hexdigest(),
        "observer_hash": hash_json(bundle.observer.model_dump(mode="json")),
        "migration_matrix_hash": hash_json([row.model_dump(mode="json") for row in bundle.migration_matrix]),
        "backend_id": backend.backend_id, "environment_digest": capabilities.environment_digest,
        "backend_capabilities_hash": hash_json(capabilities.model_dump(mode="json")),
        "gate_attested": gate.attested, "gate_missing": gate.missing,
        "verifier_validation_level": "CHECKED", "grade_eligible": False,
        "qualification": "NOT_CALIBRATED", "passed": False,
        "status": "UNSUPPORTED", "outcomes": [], "model_calls": 0,
        "scope": profile.scope if profile else "未登记的任务版本不能进行固定工程回归校准",
        "coverage_limits": list(profile.coverage_limits) if profile else [],
    }

    def finish(status, reason=None):
        record["status"] = status
        if reason:
            record["reason"] = reason
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        record["record_hash"] = hash_json({key: value for key, value in record.items() if key != "record_hash"})
        _write(work_dir / "calibration.json", record)
        return record

    def remaining():
        if cancelled and cancelled():
            raise InterruptedError("校准已取消")
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise TimeoutError("校准总预算耗尽")
        return limits.model_copy(update={"compile_wall_ms": min(milliseconds, limits.compile_wall_ms), "case_wall_ms": min(milliseconds, limits.case_wall_ms)})

    if work_dir.exists():
        raise ValueError("校准目录必须全新，拒绝覆盖旧证据")
    work_dir.mkdir(parents=True)
    if profile is None:
        return finish("UNSUPPORTED", "该任务版本未登记固定工程校准")
    if not re.fullmatch(r"[a-f0-9]{64}", source_snapshot_hash):
        return finish("UNSUPPORTED", "缺少可信原始源码快照 hash")
    if getattr(backend, "simulated", False) or backend.backend_id != "wasm_pilot":
        return finish("UNSUPPORTED", "当前实现只在真实 wasm_pilot 后端上执行可信夹具；模拟结果不构成校准")
    try:
        remaining()
        validation = validate_bundle(bundle, task, target_files, extractor_ids=extractor_ids(), case_inputs={cid: task.case(cid).input for cid in task.task_spec.case_ids})
        if not validation.passed:
            return finish("UNSUPPORTED", "适配声明静态检查未通过：" + "; ".join(validation.problems))
        shape, fixtures = _fixtures_for_target(task, bundle, target_files)
        record["build_shape"] = shape
        compiler_flags = list(bundle.build.compiler_flags) or list(task.document.build.compiler_flags)
        compiler_flags = [flag for flag in compiler_flags if not flag.startswith("-finput-charset=")]
        compiler_flags.append("-finput-charset=UTF-8")
        record["compiler_flags"] = compiler_flags
        record["fixture_hashes"] = {name: hash_json(files) for name, files in fixtures.items()}
        cases = [task.case(cid) for cid in task.task_spec.case_ids] + _supplemental_cases(task)
        record["case_inputs_hash"] = hash_json({case.case_id: case.input for case in cases})
        record["formal_case_ids"] = list(task.task_spec.case_ids)
        record["supplemental_case_ids"] = [case.case_id for case in cases if case.case_id not in task.task_spec.case_ids]
        required = set(profile.assertion_ids)
        if set(task.assertions) != required or not all(task.case(cid).input.get("initial_contacts") for cid in profile.declared_faulty_cases):
            return finish("UNSUPPORTED", "固定工程缺少完整状态断言或独立用例的已有联系人")
        input_validator = Draft202012Validator(task.template.input_schema)
        output_validator = Draft202012Validator(task.template.output_schema)
        if any(list(input_validator.iter_errors(case.input)) or list(output_validator.iter_errors(case.expected)) for case in cases):
            return finish("UNSUPPORTED", "固定回归用例不符合封存任务输入/输出契约")
        for name, files in fixtures.items():
            variant_dir = work_dir / name
            subject = variant_dir / "subject"
            subject.mkdir(parents=True)
            for filename, source in files.items():
                (subject / filename).write_text(source, encoding="utf-8")
            driver_dir = variant_dir / "driver"
            driver_dir.mkdir()
            driver = driver_dir / "driver.cpp"
            driver.write_text(bundle.driver.driver_source, encoding="utf-8")
            for helper in task.harness_dir.glob("*.h"):
                (driver_dir / helper.name).write_bytes(helper.read_bytes())
            sources = [subject / path for path in bundle.target.sources if Path(path).suffix in {".cpp", ".cc", ".cxx"}] + [driver]
            plan = BuildPlan(schema_version="1.0", build_plan_id="target_calibration", role=BuildRole.ORIGINAL,
                subject_snapshot_hash=hash_json(files), toolchain_id="wasi_sdk", source_units=[SourceUnit(unit_id=f"unit_{i}", origin=SourceOrigin.GENERATED if path == driver else SourceOrigin.SUBMISSION, role=BuildRole.DRIVER if path == driver else BuildRole.ORIGINAL, path=path.name) for i, path in enumerate(sources)],
                include_dirs=[str(subject), str(driver_dir)], defines=[], libraries=[], entrypoint=None,
                artifact_name="subject.wasm", build_hash=hash_json({"fixture": files, "bundle": record["verifier_bundle_hash"]}))
            build = variant_dir / "build"
            outcome = backend.compile(CompileRequest(plan, Path("unused"), sources, [subject, driver_dir], compiler_flags, build / "subject.wasm", build, remaining(), cancelled=cancelled))
            _write(variant_dir / "compile.json", {"status": outcome.status.value, "stderr": outcome.stderr, "duration_ms": outcome.duration_ms, "artifact_hash": outcome.artifact_hash})
            remaining()
            variant = {"variant_id": name, "compile_status": outcome.status.value, "cases": [], "passed": False}
            record["outcomes"].append(variant)
            if outcome.status is not CompileStatus.SUCCESS:
                return finish("FAILED", f"独立 {name} 夹具编译未完成；不修复或替换业务实现")
            for case in cases:
                case_dir = variant_dir / "cases" / case.case_id
                case_dir.mkdir(parents=True)
                request = build_request(run_id="target_calibration", attempt_id="attempt_" + name, case_id=case.case_id, operation_id=task.template.operation_id, case_input=copy.deepcopy(case.input))
                _write(case_dir / "request.json", request)
                run = backend.run(RunRequest(build / "subject.wasm", [], case_dir / "request.json", case_dir / "stdout.txt", case_dir / "stderr.txt", case_dir, remaining(), cancelled=cancelled))
                remaining()
                stdout = (case_dir / "stdout.txt").read_text(encoding="utf-8", errors="replace") if (case_dir / "stdout.txt").exists() else ""
                parsed = parse_response(stdout_text=stdout, run_id=request["run_id"], attempt_id=request["attempt_id"], case_id=case.case_id, operation_id=task.template.operation_id, termination=run.termination)
                schema_errors = list(Draft202012Validator(task.template.output_schema).iter_errors(parsed.actual)) if parsed.status is ProtocolStatus.OK else []
                results = {identifier: evaluate(task.assertions[identifier], case_expected=case.expected, actual_document=parsed.actual).comparison.value for identifier in required} if parsed.status is ProtocolStatus.OK and not schema_errors and run.termination is Termination.EXITED and run.exit_code == 0 else {}
                expected_actual = _fixture_expectation(profile, case, name)
                expected_assertions = profile.expected_assertions(name, case.case_id)
                derived_assertions = {identifier: evaluate(task.assertions[identifier], case_expected=case.expected, actual_document=expected_actual).comparison.value for identifier in required}
                if derived_assertions != expected_assertions:
                    return finish("UNSUPPORTED", "固定夹具的精确缺陷签名与任务断言不一致")
                expected_comparison = "MISMATCH" if "MISMATCH" in expected_assertions.values() else "MATCH"
                good = results == expected_assertions
                # Keep complete initial state, including author-assigned IDs.
                # The bad fixture must preserve seeds too, even though it adds
                # a candidate that the correct fixture must reject.
                initial = _project_contacts(case.input["initial_contacts"])
                observed_contacts = (parsed.actual or {}).get("contacts_after", [])
                preserved = isinstance(observed_contacts, list) and all(any(strict_equal(contact, item) for item in observed_contacts) for contact in initial)
                defect_verified = _same_observation(parsed.actual, expected_actual)
                row = {"case_id": case.case_id, "purpose": "SUPPLEMENTAL_CALIBRATION" if case.case_id not in task.task_spec.case_ids else "REFERENCE_REGRESSION", "termination": run.termination.value, "protocol_status": parsed.status.value, "assertions": results, "expected_assertions": expected_assertions, "expected_comparison": expected_comparison, "initial_contacts_preserved": preserved, "defect_signature_verified": defect_verified, "actual": parsed.actual, "passed": good and preserved and defect_verified, "wall_ms": run.wall_ms, "request_hash": hash_json(request), "stdout_hash": hashlib.sha256(stdout.encode()).hexdigest()}
                variant["cases"].append(row)
                _write(case_dir / "observation.json", row)
            variant["passed"] = all(row["passed"] for row in variant["cases"]) and len(variant["cases"]) == len(cases)
        record["passed"] = len(record["outcomes"]) == 2 and all(item["passed"] for item in record["outcomes"])
        record["qualification"] = profile.qualification if record["passed"] else "NOT_CALIBRATED"
        return finish("COMPLETED", None if record["passed"] else "独立夹具或初始状态保留检查失败；仍保持 CHECKED")
    except InterruptedError as exc:
        return finish("CANCELLED", str(exc))
    except TimeoutError as exc:
        return finish("BUDGET_EXCEEDED", str(exc))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return finish("UNSUPPORTED", str(exc))


def calibration_binding_matches(record: dict[str, Any], *, task, bundle: TargetVerifierBundle,
                                target_files: dict[str, str], source_snapshot_hash: str, backend) -> bool:
    """Reject reuse after any bound input or recorded content changes.

    Hashes protect accidental stale reuse; this is not a signature or authority
    boundary. The host must only consume its own newly generated evidence.
    """
    capabilities = backend.capabilities()
    profile = get_pilot_profile(task.task_id, str(task.version))
    if profile is None:
        return False
    bindings = {"policy_version": profile.calibration_policy, "task_package_hash": task.package_hash,
                "source_snapshot_hash": source_snapshot_hash, "target_files_hash": hash_json(target_files),
                "verifier_bundle_hash": hash_json(bundle.model_dump(mode="json")),
                "environment_digest": capabilities.environment_digest,
                "backend_capabilities_hash": hash_json(capabilities.model_dump(mode="json")),
                "backend_id": backend.backend_id}
    return bool(record.get("passed") and record.get("status") == "COMPLETED" and
                record.get("qualification") == profile.qualification and
                record.get("record_hash") == hash_json({key: value for key, value in record.items() if key != "record_hash"}) and
                all(record.get(key) == value for key, value in bindings.items()))


def run_target_challenges(*, task, bundle: TargetVerifierBundle, backend,
                          executable: Path, run_id: str, work_dir: Path,
                          limits: ResourceLimits | None = None,
                          cancelled: Callable[[], bool] | None = None,
                          deadline: float | None = None) -> dict[str, Any]:
    """Observe registered extra rules on the actual frozen student executable.

    Independent of the published assessment cases and of verifier
    calibration: no official counts, validation level or grade are changed.
    The host alone compares expectations; only inputs enter the executable.
    """
    if work_dir.exists():
        raise ValueError("补充检查目录必须全新，拒绝覆盖旧证据")
    work_dir.mkdir(parents=True)
    started = time.monotonic()
    profile = get_pilot_profile(task.task_id, str(task.version))
    required_count = len(profile.challenge_labels) if profile else 0
    limits = limits or task.document.limits
    deadline = min(deadline if deadline is not None else float("inf"), started + limits.total_wall_ms / 1000)
    capabilities = backend.capabilities()
    record: dict[str, Any] = {
        "schema_version": "1.0", "policy_version": profile.challenge_policy if profile else "unsupported-task",
        "run_id": run_id, "purpose": "SUPPLEMENTAL_RULE_CHECK",
        "task_package_hash": task.package_hash,
        "verifier_bundle_hash": hash_json(bundle.model_dump(mode="json")),
        "backend_id": backend.backend_id, "environment_digest": capabilities.environment_digest,
        "backend_capabilities_hash": hash_json(capabilities.model_dump(mode="json")),
        "executable_hash": None, "status": "UNSUPPORTED", "passed": False,
        "defect_detected": False, "grade_eligible": False, "model_calls": 0,
        "scope": profile.challenge_scope if profile else "该任务版本未登记补充规则检查",
        "challenge_results": [],
    }

    def finish(status, reason=None):
        record["status"] = status
        if reason:
            record["reason"] = reason
        rows = record["challenge_results"]
        record["challenge_counts"] = {
            "required": required_count, "attempted": len(rows),
            "matched": sum(row["comparison"] == "MATCH" for row in rows),
            "mismatched": sum(row["comparison"] == "MISMATCH" for row in rows),
            "not_compared": required_count - sum(row["comparison"] in {"MATCH", "MISMATCH"} for row in rows),
        }
        record["passed"] = status == "COMPLETED" and required_count > 0 and record["challenge_counts"]["matched"] == required_count
        record["defect_detected"] = status == "COMPLETED" and record["challenge_counts"]["mismatched"] > 0
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        record["record_hash"] = hash_json({key: value for key, value in record.items() if key != "record_hash"})
        _write(work_dir / "target_challenges.json", record)
        return record

    def remaining():
        if cancelled and cancelled():
            raise InterruptedError("补充规则检查已取消")
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise TimeoutError("补充规则检查总预算耗尽")
        return limits.model_copy(update={"case_wall_ms": min(milliseconds, limits.case_wall_ms)})

    if (profile is None or
            tuple(task.task_spec.case_ids) != profile.formal_case_ids or
            bundle.task_id != task.task_id or bundle.task_version != str(task.version) or
            bundle.status != "READY" or bundle.driver.kind != "function_driver" or
            bundle.observer.kind not in {"json_stdout", "extractor"} or
            bundle.observer.extractor_id != "json_stdout_v1" or bundle.observer.params or
            bundle.observer.artifact_paths or getattr(backend, "simulated", False) or
            backend.backend_id != "wasm_pilot"):
        return finish("UNSUPPORTED", "补充规则检查仅支持已登记题目的真实 WASM 函数驱动与原始 JSON 观测")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        return finish("UNSUPPORTED", "补充规则检查的运行标识不合法")
    try:
        remaining()
        if set(task.assertions) != set(profile.assertion_ids):
            return finish("UNSUPPORTED", "缺少该题完整结果/状态断言")
        module_bytes = executable.read_bytes()
        if not module_bytes.startswith(b"\0asm"):
            return finish("UNSUPPORTED", "目标不是 WebAssembly，禁止回退本机运行")
        record["executable_hash"] = hashlib.sha256(module_bytes).hexdigest()
        cases = _supplemental_cases(task)
        record["case_inputs_hash"] = hash_json({case.case_id: case.input for case in cases})
        labels = profile.challenge_labels
        if tuple(case.case_id for case in cases) != profile.supplemental_case_ids:
            return finish("UNSUPPORTED", "补充检查清单与任务版本配置不一致")
        validator = Draft202012Validator(task.template.output_schema)
        input_validator = Draft202012Validator(task.template.input_schema)
        if any(list(input_validator.iter_errors(case.input)) or list(validator.iter_errors(case.expected)) for case in cases):
            return finish("UNSUPPORTED", "补充检查不符合封存任务输入/输出契约")
        for case in cases:
            if hashlib.sha256(executable.read_bytes()).hexdigest() != record["executable_hash"]:
                return finish("INVALIDATED", "补充检查过程中可执行文件发生变化")
            case_dir = work_dir / case.case_id
            case_dir.mkdir()
            request = build_request(run_id=run_id, attempt_id="supplemental_" + case.case_id, case_id=case.case_id, operation_id=task.template.operation_id, case_input=copy.deepcopy(case.input))
            _write(case_dir / "request.json", request)
            outcome = backend.run(RunRequest(executable, [], case_dir / "request.json", case_dir / "stdout.txt", case_dir / "stderr.txt", case_dir, remaining(), cancelled=cancelled))
            remaining()
            stdout = (case_dir / "stdout.txt").read_text(encoding="utf-8", errors="replace") if (case_dir / "stdout.txt").is_file() else ""
            parsed = parse_response(stdout_text=stdout, run_id=run_id, attempt_id=request["attempt_id"], case_id=case.case_id, operation_id=task.template.operation_id, termination=outcome.termination)
            errors = [error.message for error in validator.iter_errors(parsed.actual)] if parsed.status is ProtocolStatus.OK else []
            usable = parsed.status is ProtocolStatus.OK and outcome.termination is Termination.EXITED and outcome.exit_code == 0 and not errors
            results = []
            if usable:
                for identifier in case.assertion_ids:
                    comparison = evaluate(task.assertions[identifier], case_expected=case.expected, actual_document=parsed.actual)
                    results.append({"assertion_id": identifier, "comparison": comparison.comparison.value, "expected": comparison.expected, "actual": comparison.actual, "detail": comparison.detail})
            comparisons = {item["comparison"] for item in results}
            overall = "NOT_COMPARED" if not results or "NOT_COMPARED" in comparisons else "MISMATCH" if "MISMATCH" in comparisons else "MATCH"
            row = {"case_id": case.case_id, "label": labels[case.case_id], "purpose": "SUPPLEMENTAL_RULE_CHECK",
                "input": case.input, "expected": case.expected, "actual": parsed.actual,
                "comparison": overall, "assertion_results": results,
                "termination": outcome.termination.value, "exit_code": outcome.exit_code,
                "protocol_status": parsed.status.value, "detail": "; ".join(errors) if errors else parsed.detail,
                "wall_ms": outcome.wall_ms, "request_hash": hash_json(request),
                "stdout_hash": hashlib.sha256(stdout.encode()).hexdigest()}
            record["challenge_results"].append(row)
            _write(case_dir / "observation.json", row)
        if hashlib.sha256(executable.read_bytes()).hexdigest() != record["executable_hash"] or backend.capabilities().environment_digest != record["environment_digest"]:
            return finish("INVALIDATED", "目标或后端环境在补充检查过程中变化，结果不可复用")
        return finish("COMPLETED")
    except InterruptedError as exc:
        return finish("CANCELLED", str(exc))
    except TimeoutError as exc:
        return finish("BUDGET_EXCEEDED", str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return finish("UNSUPPORTED", str(exc))

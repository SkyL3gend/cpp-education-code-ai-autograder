"""Bounded, model-free regression calibration of a T1 target verifier.

This is deliberately narrower than authoritative CALIBRATED/REVIEWED status.
The same adapter is exercised against independent known-correct and known-bad
business implementations, never against the student's own results as an oracle.
Only the published T1 scaffold is supported. Unknown layouts stay CHECKED.
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
from ..domain.enums import (BuildRole, ComparisonStatus, CompileStatus,
                            ProtocolStatus, SourceOrigin, Termination)
from ..domain.models import BuildPlan, ResourceLimits, SourceUnit
from ..judge.assertions import evaluate, strict_equal
from ..judge.extractors import extractor_ids
from ..orchestrator.protocol import build_request, parse_response
from ..sandbox.base import CompileRequest, RunRequest
from ..sandbox.gate import evaluate as evaluate_gate
from .bundle import TargetVerifierBundle, validate_bundle

POLICY_VERSION = "t1-scaffold-regression-v1"
_FILES = ("validators.h", "contactbook.h", "validators.cpp", "contactbook.cpp")
_SIGNATURE = "bool isValidPhone(const std::string& phone) {"


def _text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    # The checked-in pilot amalgamation differs only in empty line counts.
    return re.sub(r"\n(?:[ \t]*\n)+", "\n\n", value)


def _scaffold(source: str) -> str | None:
    """Match the exact published scaffold except the single function body.

    This checks calibration applicability, not C++ safety. Conservative lexical
    handling declines raw strings, preprocessing and unusual function spelling.
    No student source is rewritten or replaced during actual assessment.
    """
    source = _text(source)
    if source.count(_SIGNATURE) != 1:
        return None
    begin = source.index(_SIGNATURE) + len(_SIGNATURE) - 1
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
                return source[:begin] + "{PHONE_IMPLEMENTATION}" + source[end:]
    return None


def trusted_t1_variants(task) -> dict[str, dict[str, str]]:
    """Read author-controlled fixtures from the loaded package, not a request."""
    if (task.task_id, str(task.version)) != ("t1_phone_validation", "1.0.0"):
        raise ValueError("只支持 T1@1.0.0 的固定工程校准")
    variants = {item.variant_id: item for item in task.document.reference_suite.variants}
    if set(variants) != {"correct", "faulty"} or variants["correct"].role.value != "CORRECT" or variants["faulty"].role.value != "FAULTY":
        raise ValueError("缺少独立的已知正确和已知缺陷夹具")
    if variants["faulty"].must_fail_cases != ["c_non_digit_11"]:
        raise ValueError("T1 缺陷夹具必须指定 c_non_digit_11")
    result = {}
    for name, variant in variants.items():
        if variant.overrides != ["validators.cpp"]:
            raise ValueError("不支持的 T1 夹具构建形态")
        result[name] = {filename: (task.task_dir / "variants" / name / filename if filename in variant.overrides else task.project_root / filename).read_text(encoding="utf-8") for filename in _FILES}
    return result


def merged_t1_source(files: dict[str, str]) -> str:
    """Reproduce the pilot's transparent amalgamation of the trusted modules."""
    parts = []
    for name in _FILES:
        source = files[name]
        for directive in ('#pragma once', '#include "validators.h"', '#include "contactbook.h"'):
            source = source.replace(directive, "")
        parts.append(source)
    return "\n".join(parts).rstrip() + "\n"


def _fixtures_for_target(task, bundle, files):
    trusted = trusted_t1_variants(task)
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
        expected = merged_t1_source(trusted["correct"])
        if _scaffold(actual) is None or _scaffold(actual) != _scaffold(expected):
            raise ValueError("目标不属于 T1 固定合并工程；不能迁移独立夹具校准")
        return "single_amalgamation", {name: {filename: merged_t1_source(value)} for name, value in trusted.items()}
    if set(files) != set(_FILES) or set(bundle.target.sources) - set(_FILES) or not {"validators.cpp", "contactbook.cpp"} <= set(bundle.target.sources):
        raise ValueError("当前校准仅支持单合并文件或 T1 原始四模块工程")
    for filename in _FILES:
        actual, expected = _text(files[filename]), _text(trusted["correct"][filename])
        if filename == "validators.cpp":
            if _scaffold(actual) is None or _scaffold(actual) != _scaffold(expected):
                raise ValueError("validators.cpp 在手机号函数以外的结构发生变化")
        elif actual != expected:
            raise ValueError(f"固定工程的 {filename} 发生变化，无法迁移校准")
    return "four_modules", trusted


def _supplemental_cases(task):
    seed = task.case("c_non_digit_11")
    challenges = []
    for identifier, phone in (("challenge_prefix_2", "23800138000"), ("challenge_empty", ""), ("challenge_fullwidth", "１３８００１３８０００")):
        payload = copy.deepcopy(seed.input)
        payload["candidate"]["phone"] = phone
        challenges.append(seed.model_copy(update={"case_id": identifier, "input": payload}))
    return challenges


def _write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def calibrate_target_verifier(*, task, bundle: TargetVerifierBundle,
                              target_files: dict[str, str], source_snapshot_hash: str,
                              backend, work_dir: Path, limits: ResourceLimits | None = None,
                              cancelled: Callable[[], bool] | None = None,
                              deadline: float | None = None) -> dict[str, Any]:
    """Run independent T1 fixtures with *exactly* the selected verifier bundle.

    Call once after build/probe and before freezing the actual assessment. Supply
    the original manifest snapshot hash and the existing monotonic deadline.
    Output is evidence, not permission to run and not formal scoring authority.
    It always preserves CHECKED and grade_eligible=False. No model is contacted.
    ``work_dir`` must be a fresh directory outside the student's subject tree.
    """
    started = time.monotonic()
    limits = limits or task.document.limits
    deadline = min(deadline if deadline is not None else float("inf"), started + limits.total_wall_ms / 1000)
    capabilities = backend.capabilities()
    gate = evaluate_gate(capabilities, task.template.capability_requirements, allow_unattested=False)
    record: dict[str, Any] = {
        "schema_version": "1.0", "policy_version": POLICY_VERSION,
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
        "scope": "T1 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        "coverage_limits": ["封存 task@1.0.0 的 4 用例未单独覆盖 R2 的错误首位；本次独立挑战补充验证，不改写任务包", "有限回归不能证明对所有程序、所有输入和所有适配形态正确"],
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
        required = {"a_created", "a_contacts"}
        if set(task.assertions) != required or not task.case("c_non_digit_11").input.get("initial_contacts"):
            return finish("UNSUPPORTED", "T1 缺少完整状态断言或非数字用例的已有联系人")
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
                must_fail = name == "faulty" and case.case_id == "c_non_digit_11"
                expected_comparison = ComparisonStatus.MISMATCH.value if must_fail else ComparisonStatus.MATCH.value
                good = set(results) == required and all(value == expected_comparison for value in results.values())
                # Keep complete initial state, including author-assigned IDs.
                # The bad fixture must preserve seeds too, even though it adds
                # a candidate that the correct fixture must reject.
                initial = copy.deepcopy(case.input["initial_contacts"])
                for contact in initial:
                    birthday = contact["birthday"]
                    contact["birthday"] = f"{birthday['year']:04d}-{birthday['month']:02d}-{birthday['day']:02d}"
                observed_contacts = (parsed.actual or {}).get("contacts_after", [])
                preserved = isinstance(observed_contacts, list) and all(any(strict_equal(contact, item) for item in observed_contacts) for contact in initial)
                defect_verified = True
                if must_fail:
                    # Independently authored signature of this specific fixture
                    # defect, not a generic "any mismatch counts" criterion.
                    # These values validate recorded observations only; they are
                    # never supplied to a driver or used as student actuals.
                    candidate = case.input["candidate"]
                    birth = candidate["birthday"]
                    faulty_contacts = initial + [{"id": len(initial) + 1, "name": candidate["name"], "phone": candidate["phone"], "birthday": f"{birth['year']:04d}-{birth['month']:02d}-{birth['day']:02d}"}]
                    defect_verified = (parsed.actual or {}).get("created") is True and isinstance(observed_contacts, list) and Counter(map(canonical_json, observed_contacts)) == Counter(map(canonical_json, faulty_contacts))
                row = {"case_id": case.case_id, "purpose": "SUPPLEMENTAL_CALIBRATION" if case.case_id not in task.task_spec.case_ids else "REFERENCE_REGRESSION", "termination": run.termination.value, "protocol_status": parsed.status.value, "assertions": results, "expected_comparison": expected_comparison, "initial_contacts_preserved": preserved, "defect_signature_verified": defect_verified, "actual": parsed.actual, "passed": good and preserved and defect_verified, "wall_ms": run.wall_ms, "request_hash": hash_json(request), "stdout_hash": hashlib.sha256(stdout.encode()).hexdigest()}
                variant["cases"].append(row)
                _write(case_dir / "observation.json", row)
            variant["passed"] = all(row["passed"] for row in variant["cases"]) and len(variant["cases"]) == len(cases)
        record["passed"] = len(record["outcomes"]) == 2 and all(item["passed"] for item in record["outcomes"])
        record["qualification"] = "T1_REGRESSION_CALIBRATED" if record["passed"] else "NOT_CALIBRATED"
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
    bindings = {"policy_version": POLICY_VERSION, "task_package_hash": task.package_hash,
                "source_snapshot_hash": source_snapshot_hash, "target_files_hash": hash_json(target_files),
                "verifier_bundle_hash": hash_json(bundle.model_dump(mode="json")),
                "environment_digest": capabilities.environment_digest,
                "backend_capabilities_hash": hash_json(capabilities.model_dump(mode="json")),
                "backend_id": backend.backend_id}
    return bool(record.get("passed") and record.get("status") == "COMPLETED" and
                record.get("qualification") == "T1_REGRESSION_CALIBRATED" and
                record.get("record_hash") == hash_json({key: value for key, value in record.items() if key != "record_hash"}) and
                all(record.get(key) == value for key, value in bindings.items()))


def run_target_challenges(*, task, bundle: TargetVerifierBundle, backend,
                          executable: Path, run_id: str, work_dir: Path,
                          limits: ResourceLimits | None = None,
                          cancelled: Callable[[], bool] | None = None,
                          deadline: float | None = None) -> dict[str, Any]:
    """Observe three extra T1 rules on the actual frozen student executable.

    Independent of the four published assessment cases and of verifier
    calibration: no official counts, validation level or grade are changed.
    The host alone compares expectations; only inputs enter the executable.
    """
    if work_dir.exists():
        raise ValueError("补充检查目录必须全新，拒绝覆盖旧证据")
    work_dir.mkdir(parents=True)
    started = time.monotonic()
    limits = limits or task.document.limits
    deadline = min(deadline if deadline is not None else float("inf"), started + limits.total_wall_ms / 1000)
    capabilities = backend.capabilities()
    record: dict[str, Any] = {
        "schema_version": "1.0", "policy_version": "t1-target-challenges-v1",
        "run_id": run_id, "purpose": "SUPPLEMENTAL_RULE_CHECK",
        "task_package_hash": task.package_hash,
        "verifier_bundle_hash": hash_json(bundle.model_dump(mode="json")),
        "backend_id": backend.backend_id, "environment_digest": capabilities.environment_digest,
        "backend_capabilities_hash": hash_json(capabilities.model_dump(mode="json")),
        "executable_hash": None, "status": "UNSUPPORTED", "passed": False,
        "defect_detected": False, "grade_eligible": False, "model_calls": 0,
        "scope": "当前目标程序的额外规则观测；不改写封存任务或四项正式用例计数，不授予正式评分资格",
        "challenge_results": [],
    }

    def finish(status, reason=None):
        record["status"] = status
        if reason:
            record["reason"] = reason
        rows = record["challenge_results"]
        record["challenge_counts"] = {
            "required": 3, "attempted": len(rows),
            "matched": sum(row["comparison"] == "MATCH" for row in rows),
            "mismatched": sum(row["comparison"] == "MISMATCH" for row in rows),
            "not_compared": 3 - sum(row["comparison"] in {"MATCH", "MISMATCH"} for row in rows),
        }
        record["passed"] = status == "COMPLETED" and record["challenge_counts"]["matched"] == 3
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

    if ((task.task_id, str(task.version)) != ("t1_phone_validation", "1.0.0") or
            bundle.task_id != task.task_id or bundle.task_version != str(task.version) or
            bundle.status != "READY" or bundle.driver.kind != "function_driver" or
            bundle.observer.kind not in {"json_stdout", "extractor"} or
            bundle.observer.extractor_id != "json_stdout_v1" or bundle.observer.params or
            bundle.observer.artifact_paths or getattr(backend, "simulated", False) or
            backend.backend_id != "wasm_pilot"):
        return finish("UNSUPPORTED", "补充规则检查仅支持 T1 的真实 WASM 函数驱动与原始 JSON 观测")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        return finish("UNSUPPORTED", "补充规则检查的运行标识不合法")
    try:
        remaining()
        if set(task.assertions) != {"a_created", "a_contacts"}:
            return finish("UNSUPPORTED", "缺少 T1 完整创建/状态断言")
        module_bytes = executable.read_bytes()
        if not module_bytes.startswith(b"\0asm"):
            return finish("UNSUPPORTED", "目标不是 WebAssembly，禁止回退本机运行")
        record["executable_hash"] = hashlib.sha256(module_bytes).hexdigest()
        cases = _supplemental_cases(task)
        record["case_inputs_hash"] = hash_json({case.case_id: case.input for case in cases})
        labels = {"challenge_prefix_2": "11 位号码以 2 开头", "challenge_empty": "空手机号", "challenge_fullwidth": "全角数字手机号"}
        validator = Draft202012Validator(task.template.output_schema)
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

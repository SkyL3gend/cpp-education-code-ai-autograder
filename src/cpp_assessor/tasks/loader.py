"""任务包加载与静态校验（架构实施方案 §3.2、§7.4）。

任务包是纯数据：不在这里执行任何提交内容，也不调用 LLM。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .. import config
from ..domain.canonical import canonical_json, sha256_hex
from ..domain.enums import (
    ComparisonStatus,
    ExpectedSourceKind,
    Operator,
    PublicationStatus,
    ValidationLevel,
    VariantRole,
)
from ..domain.errors import InvalidInputError, TaskPackageError
from ..domain.models import (
    AssertionSpec,
    IOTemplate,
    ReferenceSuite,
    TaskCase,
    TaskDocument,
    TaskSpec,
    TestMethodSpec,
)
from ..domain.pointers import MISSING, resolve
from ..judge.normalizations import NORMALIZATIONS

SCHEMA_VERSION = "1.0"
_ALLOWED_OPERATORS = {operator.value for operator in Operator}
_REQUIRED_FILES = (
    "task.json",
    "io_template.json",
    "test_method.json",
    "cases.json",
    "reference_suite.json",
)


@dataclass
class Check:
    check_id: str
    description: str
    passed: bool
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "description": self.description,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class LoadedTask:
    document: TaskDocument
    task_dir: Path
    task_spec: TaskSpec
    template: IOTemplate
    method: TestMethodSpec
    cases: dict[str, TaskCase]
    assertions: dict[str, AssertionSpec]
    package_hash: str
    project_root: Path
    harness_dir: Path
    harness_files: list[Path]
    validation: "TaskValidation"
    ref_label: str

    @property
    def task_id(self) -> str:
        return self.task_spec.task_id

    @property
    def version(self) -> str:
        return self.task_spec.version

    def case(self, case_id: str) -> TaskCase:
        return self.cases[case_id]

    def assertion(self, assertion_id: str) -> AssertionSpec:
        return self.assertions[assertion_id]


@dataclass
class TaskValidation:
    task_dir: Path
    package_hash: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks) and bool(self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    @property
    def validation_level(self) -> ValidationLevel:
        return ValidationLevel.CHECKED if self.passed else ValidationLevel.UNVERIFIED

    def summary(self) -> dict[str, Any]:
        return {
            "task_dir": str(self.task_dir),
            "package_hash": self.package_hash,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
        }


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise TaskPackageError(f"任务包缺少文件：{path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TaskPackageError(f"{path.name} 不是合法 UTF-8：{exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskPackageError(f"{path.name} 不是合法 JSON：{exc}") from exc


def _file_sha256(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def _check_ref_text(ref_label: str, filename: str, ref, checks: list[Check]) -> None:
    path = Path(ref_label) / filename
    expected = ref.sha256
    actual = _file_sha256(path) if path.is_file() else ""
    checks.append(
        Check(
            f"ref_sha256_{filename.replace('.', '_')}",
            f"{filename} 的 sha256 与引用一致",
            bool(actual) and actual == expected,
            f"expected={expected} actual={actual or 'MISSING'}",
        )
    )


def _scan_remote_refs(schema: Any, trail: str = "#") -> list[str]:
    found: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str) and (
                "://" in value or value.startswith("file:")
            ):
                found.append(f"{trail}/$ref={value}")
            found.extend(_scan_remote_refs(value, f"{trail}/{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found.extend(_scan_remote_refs(value, f"{trail}/{index}"))
    return found


def _validate_json_schema(schema: dict[str, Any], name: str, checks: list[Check]) -> None:
    problems: list[str] = []
    version = schema.get("$schema")
    if version not in (None, "https://json-schema.org/draft/2020-12/schema"):
        problems.append(f"$schema 固定为 2020-12，实际 {version!r}")
    if schema.get("type") != "object":
        problems.append("根 schema 必须是 object")
    if schema.get("additionalProperties") is not False:
        problems.append("根 schema 必须设置 additionalProperties=false")
    remote = _scan_remote_refs(schema)
    if remote:
        problems.append(f"禁止远程 $ref：{remote[:3]}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        problems.append(f"schema 非法：{exc.message}")
    checks.append(
        Check(
            f"schema_{name}",
            f"{name} 是合法的 Draft 2020-12 schema 且关闭未声明字段",
            not problems,
            "；".join(problems) or None,
        )
    )


def _collect_case_problems(
    case: TaskCase,
    validator: Draft202012Validator,
    method: TestMethodSpec,
    spec: TaskSpec,
    checks: list[Check],
) -> None:
    errors = sorted(validator.iter_errors(case.input), key=lambda e: list(e.path))
    checks.append(
        Check(
            f"input_schema_{case.case_id}",
            f"用例 {case.case_id} 的输入符合 input_schema",
            not errors,
            "；".join(error.message for error in errors[:3]) or None,
        )
    )
    rule_ids = {rule.rule_id for rule in spec.rules}
    unknown_rules = [r for r in case.rule_ids if r not in rule_ids]
    checks.append(
        Check(
            f"rules_{case.case_id}",
            f"用例 {case.case_id} 的 rule_ids 都能回溯到规则",
            not unknown_rules,
            f"未知规则：{unknown_rules}" if unknown_rules else None,
        )
    )
    partition_ids = {partition.partition_id for partition in method.input_partitions}
    unknown_partitions = [p for p in case.partition_ids if p not in partition_ids]
    checks.append(
        Check(
            f"partitions_{case.case_id}",
            f"用例 {case.case_id} 的 partition_ids 都在测试方法中声明",
            not unknown_partitions,
            f"未知分区：{unknown_partitions}" if unknown_partitions else None,
        )
    )
    assertion_ids = {assertion.assertion_id for assertion in method.assertions}
    unknown_assertions = [a for a in case.assertion_ids if a not in assertion_ids]
    checks.append(
        Check(
            f"assertions_{case.case_id}",
            f"用例 {case.case_id} 引用的断言都已声明",
            not unknown_assertions,
            f"未知断言：{unknown_assertions}" if unknown_assertions else None,
        )
    )
    if case.limits_override:
        allowed = set(config_resource_limit_fields())
        unknown_limits = [k for k in case.limits_override if k not in allowed]
        checks.append(
            Check(
                f"limits_{case.case_id}",
                f"用例 {case.case_id} 的 limits_override 字段合法",
                not unknown_limits,
                f"未知字段：{unknown_limits}" if unknown_limits else None,
            )
        )


def config_resource_limit_fields() -> set[str]:
    from ..domain.models import ResourceLimits

    return set(ResourceLimits.model_fields)


def _validate_assertion_pointers(
    method: TestMethodSpec,
    cases: Iterable[TaskCase],
    template: IOTemplate,
    checks: list[Check],
) -> None:
    pre_keys = {pre.observation_key for pre in template.pre_operations}
    problems: list[str] = []
    for assertion in method.assertions:
        if assertion.normalization_id not in NORMALIZATIONS:
            problems.append(f"{assertion.assertion_id}: 未知规范化 {assertion.normalization_id}")
        if assertion.operator.value not in _ALLOWED_OPERATORS:
            problems.append(f"{assertion.assertion_id}: 未知算子 {assertion.operator}")
        if assertion.operator is Operator.UNCHANGED and (
            assertion.expected_source.kind is not ExpectedSourceKind.PRE_OBSERVATION
        ):
            problems.append(f"{assertion.assertion_id}: UNCHANGED 必须引用 PRE_OBSERVATION")
        if assertion.expected_source.kind is ExpectedSourceKind.PRE_OBSERVATION:
            key = assertion.expected_source.observation_key
            if key not in pre_keys:
                problems.append(f"{assertion.assertion_id}: 未声明的动作前观测 {key!r}")
    checks.append(
        Check(
            "assertion_definitions",
            "断言算子、规范化与期望来源合法",
            not problems,
            "；".join(problems[:5]) or None,
        )
    )

    pointer_problems: list[str] = []
    for case in cases:
        by_id = {a.assertion_id: a for a in method.assertions}
        for assertion_id in case.assertion_ids:
            assertion = by_id.get(assertion_id)
            if assertion is None:
                continue
            if assertion.expected_source.kind is ExpectedSourceKind.CASE_EXPECTED:
                if resolve(case.expected, assertion.expected_source.pointer) is MISSING:
                    pointer_problems.append(
                        f"{case.case_id}/{assertion_id}: expected {assertion.expected_source.pointer} 不存在"
                    )
    checks.append(
        Check(
            "assertion_pointers",
            "每条断言都能在用例期望中解析到目标值",
            not pointer_problems,
            "；".join(pointer_problems[:5]) or None,
        )
    )


def validate_task(task_dir: Path, task_library: Path | None = None) -> TaskValidation:
    task_library = task_library or config.task_library_dir()
    task_dir = Path(task_dir).resolve()
    checks: list[Check] = []
    package_hash = ""

    def add(check_id: str, description: str, passed: bool, detail: str | None = None) -> None:
        checks.append(Check(check_id, description, passed, detail))

    missing = [name for name in _REQUIRED_FILES if not (task_dir / name).is_file()]
    add("required_files", "任务包必需文件齐全", not missing, f"缺少：{missing}" if missing else None)
    if missing:
        return TaskValidation(task_dir=task_dir, package_hash=package_hash, checks=checks)

    try:
        document = TaskDocument.model_validate(_read_json(task_dir / "task.json"))
        template = IOTemplate.model_validate(_read_json(task_dir / "io_template.json"))
        method = TestMethodSpec.model_validate(_read_json(task_dir / "test_method.json"))
        raw_cases = _read_json(task_dir / "cases.json")
    except Exception as exc:  # pydantic / JSON 错误统一转成检查项
        add("document_models", "任务包文档通过严格模型校验", False, str(exc).splitlines()[0])
        return TaskValidation(task_dir=task_dir, package_hash=package_hash, checks=checks)
    add("document_models", "任务包文档通过严格模型校验", True)

    spec = document.task_spec
    try:
        cases_list = [TaskCase.model_validate(item) for item in raw_cases["cases"]]
    except Exception as exc:
        add("cases_model", "cases.json 通过严格模型校验", False, f"{exc}".splitlines()[0])
        return TaskValidation(task_dir=task_dir, package_hash=package_hash, checks=checks)
    add("cases_model", "cases.json 通过严格模型校验", True)

    add(
        "schema_version",
        "所有文档 schema_version 一致",
        {document.schema_version, template.schema_version, method.schema_version}
        == {SCHEMA_VERSION}
        and all(case.schema_version == SCHEMA_VERSION for case in cases_list),
        f"{document.schema_version}/{template.schema_version}/{method.schema_version}",
    )

    case_ids = [case.case_id for case in cases_list]
    add(
        "case_ids_unique",
        "用例 ID 唯一",
        len(case_ids) == len(set(case_ids)),
        f"重复：{[c for c in case_ids if case_ids.count(c) > 1]}",
    )
    add(
        "case_ids_match",
        "task.json 的 case_ids 与 cases.json 一致",
        set(spec.case_ids) == set(case_ids),
        f"task={sorted(set(spec.case_ids) - set(case_ids))} cases={sorted(set(case_ids) - set(spec.case_ids))}",
    )
    assertion_ids = [assertion.assertion_id for assertion in method.assertions]
    add(
        "assertion_ids_unique",
        "断言 ID 唯一",
        len(assertion_ids) == len(set(assertion_ids)),
    )
    partition_ids = [partition.partition_id for partition in method.input_partitions]
    add(
        "partition_ids_unique",
        "输入分区 ID 唯一",
        len(partition_ids) == len(set(partition_ids)),
    )

    rule_ids = {rule.rule_id for rule in spec.rules}
    add(
        "rules_defined",
        "规则条目 id 唯一且非空",
        len(rule_ids) == len(spec.rules) and bool(rule_ids),
    )
    rule_problems: list[str] = []
    for item in list(method.assertions):
        rule_problems.extend(
            f"断言 {item.assertion_id} 引用未知规则 {rule}"
            for rule in item.rule_ids
            if rule not in rule_ids
        )
    for item in method.input_partitions:
        rule_problems.extend(
            f"分区 {item.partition_id} 引用未知规则 {rule}"
            for rule in item.rule_ids
            if rule not in rule_ids
        )
    add("rules_traceable", "断言与分区都能回溯到规则", not rule_problems, "；".join(rule_problems[:5]) or None)

    coverage = method.coverage_requirements
    unknown_required_partitions = [
        p for p in coverage.required_partitions if p not in partition_ids
    ]
    unknown_required_assertions = [
        a for a in coverage.required_assertions if a not in assertion_ids
    ]
    add(
        "coverage_requirements_valid",
        "覆盖要求引用的分区与断言都已声明",
        not unknown_required_partitions and not unknown_required_assertions,
        f"分区 {unknown_required_partitions} 断言 {unknown_required_assertions}" if (unknown_required_partitions or unknown_required_assertions) else None,
    )
    covered_partitions = {p for case in cases_list for p in case.partition_ids}
    covered_assertions = {a for case in cases_list for a in case.assertion_ids}
    add(
        "coverage_requirements_met",
        "必测分区与必测断言都有用例覆盖",
        set(coverage.required_partitions) <= covered_partitions
        and set(coverage.required_assertions) <= covered_assertions,
        f"缺分区 {sorted(set(coverage.required_partitions) - covered_partitions)} "
        f"缺断言 {sorted(set(coverage.required_assertions) - covered_assertions)}",
    )
    add(
        "required_cases_nonempty",
        "必测集合非空（禁止真空 PASS）",
        bool(case_ids) and bool(coverage.required_assertions) and bool(coverage.required_partitions),
        f"cases={len(case_ids)} partitions={len(coverage.required_partitions)} assertions={len(coverage.required_assertions)}",
    )
    business_invalid = [
        partition for partition in method.input_partitions if partition.kind == "business_invalid"
    ]
    add(
        "business_invalid_partition",
        "测试方法包含业务非法输入分区",
        bool(business_invalid)
        and any(p.partition_id in covered_partitions for p in business_invalid),
        "缺少 kind=business_invalid 的分区或被覆盖" if not business_invalid else None,
    )

    _validate_json_schema(template.input_schema, "input", checks)
    _validate_json_schema(template.output_schema, "output", checks)
    add(
        "output_schema_required_observations",
        "required_observations 都在 output_schema 中声明",
        all(key in template.output_schema.get("properties", {}) for key in template.required_observations),
        f"未声明：{[k for k in template.required_observations if k not in template.output_schema.get('properties', {})]}",
    )
    add(
        "template_operation_matches_method",
        "IO 模板与测试方法描述同一操作层次",
        template.subject_level == method.subject_level and template.operation_id
        == (method.action_steps[0] if method.action_steps else ""),
        f"template={template.operation_id} method_action={method.action_steps[:1]}",
    )

    validator = Draft202012Validator(template.input_schema)
    for case in cases_list:
        _collect_case_problems(case, validator, method, spec, checks)
    _validate_assertion_pointers(method, cases_list, template, checks)

    _check_ref_text(str(task_dir), "io_template.json", spec.io_template_ref, checks)
    _check_ref_text(str(task_dir), "test_method.json", spec.test_method_ref, checks)
    _check_ref_text(
        str(task_dir / "reference_verifier"),
        "assertions.json",
        method.reference_verifier_ref,
        checks,
    )
    add(
        "reference_verifier_dir",
        "参照判断程序目录 reference_verifier/assertions.json 存在",
        (task_dir / "reference_verifier" / "assertions.json").is_file(),
        None,
    )
    add(
        "verifier_ref_consistent",
        "TaskSpec 与 TestMethod 指向同一个参照判断程序",
        spec.reference_verifier_ref.id == method.reference_verifier_ref.id
        and spec.reference_verifier_ref.version == method.reference_verifier_ref.version
        and spec.reference_verifier_ref.sha256 == method.reference_verifier_ref.sha256,
        f"{spec.reference_verifier_ref.id}@{spec.reference_verifier_ref.version}",
    )
    add(
        "refs_versions",
        "内容引用的版本号与文件内版本一致",
        spec.io_template_ref.version == template.version
        and spec.test_method_ref.version == method.version,
        f"{spec.io_template_ref.version}/{template.version} {spec.test_method_ref.version}/{method.version}",
    )

    project_root = (task_library / document.project_ref.root).resolve()
    add(
        "project_root_exists",
        "基准工程目录存在",
        project_root.is_dir(),
        str(project_root),
    )
    if project_root.is_dir():
        add(
            "project_id_path",
            "基准工程路径包含 project_id 与 version",
            document.project_ref.project_id in document.project_ref.root
            and document.project_ref.version in document.project_ref.root,
            document.project_ref.root,
        )
        cpp_files = list(project_root.glob("*.cpp"))
        add("project_has_sources", "基准工程包含 .cpp 源文件", bool(cpp_files), f"{len(cpp_files)} 个")
    harness_paths = [task_library / item for item in document.build.harness_files]
    add(
        "harness_files_exist",
        "测试驱动文件存在",
        all(path.is_file() for path in harness_paths) and bool(harness_paths),
        f"{[str(p) for p in harness_paths if not p.is_file()]}",
    )
    add(
        "harness_include_dir",
        "测试驱动头文件目录存在",
        (task_library / document.build.harness_include_dir).is_dir(),
        document.build.harness_include_dir,
    )

    suite = document.reference_suite
    correct = [v for v in suite.variants if v.role is VariantRole.CORRECT]
    faulty = [v for v in suite.variants if v.role is VariantRole.FAULTY]
    add(
        "suite_correct_variant",
        "参照套件有且仅有一个正确实现版本且期望通过",
        len(correct) == 1 and correct[0].expected_assessment_status.value == "PASS",
        f"correct={[v.variant_id for v in correct]}",
    )
    add(
        "suite_faulty_variants",
        "参照套件至少有一个缺陷实现版本且期望不通过",
        bool(faulty)
        and all(v.expected_assessment_status.value in {"FAIL", "INCONCLUSIVE"} for v in faulty),
        f"faulty={[(v.variant_id, v.expected_assessment_status.value) for v in faulty]}",
    )
    override_problems: list[str] = []
    for variant in suite.variants:
        for relative in variant.overrides:
            path = task_dir / "variants" / variant.variant_id / relative
            if not path.is_file():
                override_problems.append(f"{variant.variant_id}: 缺少 {relative}")
    add(
        "suite_overrides_exist",
        "参照版本的覆盖文件存在",
        not override_problems,
        "；".join(override_problems[:5]) or None,
    )
    add(
        "suite_task_id",
        "参照套件的 task_id 与任务一致",
        suite.task_id == spec.task_id,
        suite.task_id,
    )

    package_hash = compute_package_hash(task_dir, project_root, task_library)
    return TaskValidation(task_dir=task_dir, package_hash=package_hash, checks=checks)


def compute_package_hash(task_dir: Path, project_root: Path | None, task_library: Path) -> str:
    """任务包哈希：任务目录 + 基准工程 + 测试驱动的全部文件。"""
    entries: list[dict[str, Any]] = []
    roots: list[tuple[str, Path]] = [("task", Path(task_dir))]
    if project_root is not None and project_root.is_dir():
        roots.append(("project", project_root))
    harness = task_library / "shared" / "harness"
    if harness.is_dir():
        roots.append(("harness", harness))
    for label, root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith("variants/") and label == "task":
                pass
            digest = _file_sha256(path)
            if label == "task" and relative == "task.json":
                content = _read_json(path)
                content["task_spec"].pop("publication_status", None)
                digest = sha256_hex(canonical_json(content).encode("utf-8"))
            entries.append(
                {
                    "scope": label,
                    "path": relative,
                    "sha256": digest,
                }
            )
    return sha256_hex(canonical_json(entries).encode("utf-8"))


def _check_publication(spec: TaskSpec, allow_draft: bool) -> None:
    if spec.publication_status is PublicationStatus.DRAFT and not allow_draft:
        raise InvalidInputError(
            f"任务 {spec.task_id}@{spec.version} 尚未发布，"
            "正式测评拒绝使用 DRAFT 任务；如仅调试请加 --allow-draft"
        )


def load_task(
    ref: str,
    *,
    task_library: Path | None = None,
    allow_draft: bool = False,
) -> LoadedTask:
    task_library = (task_library or config.task_library_dir()).resolve()
    if "@" in ref:
        task_id, version = ref.split("@", 1)
    else:
        task_id, version = ref, None
    if version is None:
        candidates = sorted((task_library / "tasks" / task_id).glob("*"))
        if not candidates:
            raise InvalidInputError(f"题库中没有任务 {task_id}")
        version = candidates[-1].name
    task_dir = (task_library / "tasks" / task_id / version).resolve()
    if not task_dir.is_dir():
        raise InvalidInputError(f"任务包不存在：{task_dir}")

    validation = validate_task(task_dir, task_library)
    if not validation.passed:
        detail = "；".join(
            f"{check.check_id}: {check.detail}" for check in validation.failures[:5]
        )
        raise TaskPackageError(f"任务包校验未通过（{task_id}@{version}）：{detail}")

    document = TaskDocument.model_validate(_read_json(task_dir / "task.json"))
    template = IOTemplate.model_validate(_read_json(task_dir / "io_template.json"))
    method = TestMethodSpec.model_validate(_read_json(task_dir / "test_method.json"))
    raw_cases = _read_json(task_dir / "cases.json")
    cases = {
        case.case_id: case
        for case in (TaskCase.model_validate(item) for item in raw_cases["cases"])
    }
    _check_publication(document.task_spec, allow_draft)
    publication = task_library / "publications" / document.task_spec.task_id / f"{document.task_spec.version}.json"
    if publication.is_file() and _read_json(publication).get("package_hash") != validation.package_hash:
        raise TaskPackageError("已发布版本内容已变化；请创建新版本，不能覆盖原发布资产")

    project_root = (task_library / document.project_ref.root).resolve()
    harness_dir = (task_library / document.build.harness_include_dir).resolve()
    harness_files = [(task_library / item).resolve() for item in document.build.harness_files]
    return LoadedTask(
        document=document,
        task_dir=task_dir,
        task_spec=document.task_spec,
        template=template,
        method=method,
        cases=cases,
        assertions={a.assertion_id: a for a in method.assertions},
        package_hash=validation.package_hash,
        project_root=project_root,
        harness_dir=harness_dir,
        harness_files=harness_files,
        validation=validation,
        ref_label=f"{task_id}@{version}",
    )


def list_tasks(task_library: Path | None = None) -> list[dict[str, Any]]:
    task_library = (task_library or config.task_library_dir()).resolve()
    found: list[dict[str, Any]] = []
    root = task_library / "tasks"
    if not root.is_dir():
        return found
    for task_json in sorted(root.glob("*/*/task.json")):
        try:
            payload = json.loads(task_json.read_text(encoding="utf-8"))
            spec = TaskSpec.model_validate(payload["task_spec"])
        except Exception as exc:
            found.append(
                {
                    "task_id": task_json.parent.parent.name,
                    "version": task_json.parent.name,
                    "status": "BROKEN",
                    "error": str(exc).splitlines()[0],
                }
            )
            continue
        found.append(
            {
                "task_id": spec.task_id,
                "version": spec.version,
                "title": spec.title,
                "publication_status": spec.publication_status.value,
                "case_count": len(spec.case_ids),
                "path": str(task_json.parent),
            }
        )
    return found


def publish_task(task_dir: Path, task_library: Path | None = None) -> dict[str, Any]:
    """把任务标记为 PUBLISHED（校验不通过则拒绝发布）。"""
    task_library = (task_library or config.task_library_dir()).resolve()
    validation = validate_task(task_dir, task_library)
    if not validation.passed:
        raise TaskPackageError(
            "任务包校验未通过，拒绝发布："
            + "；".join(f"{c.check_id}: {c.detail}" for c in validation.failures[:5])
        )
    path = Path(task_dir).resolve() / "task.json"
    payload = _read_json(path)
    from .calibration import calibration_level
    from ..orchestrator.runner import make_backend
    task = load_task(f"{payload['task_spec']['task_id']}@{payload['task_spec']['version']}", task_library=task_library, allow_draft=True)
    backend = make_backend("local")
    if calibration_level(task, config.data_dir(), backend.capabilities().environment_digest) is not ValidationLevel.CALIBRATED:
        raise TaskPackageError("发布需要当前内容哈希和环境的正确/缺陷版本校准记录；先运行 tools/calibrate.py")
    publication = task_library / "publications" / task.task_id / f"{task.version}.json"
    publication.parent.mkdir(parents=True, exist_ok=True)
    if not publication.exists():
        with publication.open("x", encoding="utf-8") as handle:
            json.dump({"task_id": task.task_id, "version": task.version, "package_hash": task.package_hash, "environment_digest": backend.capabilities().environment_digest}, handle, ensure_ascii=False, indent=2)
    payload["task_spec"]["publication_status"] = PublicationStatus.PUBLISHED.value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return validation.summary()


def reference_variant_dir(task: LoadedTask, variant_id: str) -> Path:
    return task.task_dir / "variants" / variant_id


def excluded(relative: Path, patterns: Iterable[str]) -> bool:
    text = relative.as_posix()
    return any(fnmatch(text, pattern) or fnmatch(relative.name, pattern) for pattern in patterns)


def subject_source_units(task: LoadedTask, subject_root: Path) -> list[Path]:
    """被测工程的编译单元：项目内 .cpp，扣除 exclude_globs。"""
    units: list[Path] = []
    for path in sorted(subject_root.rglob("*.cpp")):
        relative = path.relative_to(subject_root)
        if excluded(relative, task.document.build.exclude_globs):
            continue
        units.append(path)
    return units


def compose_subject(task: LoadedTask, overrides_dir: Path | None, destination: Path) -> list[str]:
    """把基准工程与覆盖文件合成到目的地，返回被覆盖的相对路径。"""
    import shutil

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(task.project_root, destination)
    overridden: list[str] = []
    if overrides_dir is not None and overrides_dir.is_dir():
        for path in sorted(overrides_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(overrides_dir)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            overridden.append(relative.as_posix())
    return overridden

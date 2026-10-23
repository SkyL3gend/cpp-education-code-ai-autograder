"""模式二的适配声明（TargetVerifierBundle）与宿主校验。

LLM 只被允许填写这份声明里的字段：

- 这份程序怎么编译（哪些文件、什么编码、include 目录）；
- 用哪种驱动去触发业务入口（``stdin_script`` 菜单剧本 / ``function_driver`` 驱动源码）；
- 用哪个宿主注册的观测提取器把原始输出变成事实；
- 每个任务断言对应哪个观测键；
- 每个输入分区由剧本的第几步触发。

**断言本身（operator、期望值来源、用例输入与期望）不属于 bundle 的字段**：
它们来自已发布的测试方法，LLM 改不了。宿主校验会拒绝任何试图携带
"expected/答案/PASS/FAIL"的声明。
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.enums import DriverKind
from ..domain.models import AssertionSpec, TaskDocument
from .policy import validate_build_options, relative_path
from ..domain.canonical import hash_json

SCHEMA_VERSION = "1.0"

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_FORBIDDEN_KEYS = {"expected", "expected_value", "answer", "answers", "verdict", "评分", "答案"}
_FORBIDDEN_DRIVER_MARKERS = (
    "system(",
    "popen(",
    "http://",
    "https://",
    "std::filesystem",
    "ifstream",
    "fopen(",
    "remove(",
    "rename(",
)
SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"}

#: ``driver.seed_session`` 里允许的占位符：宿主会对用例声明的每个初始联系人重复执行。
SEED_PLACEHOLDERS = {
    "seed.id",
    "seed.name",
    "seed.phone",
    "seed.birthday.year",
    "seed.birthday.month",
    "seed.birthday.day",
}

_DRIVER_KIND_ALIASES = {
    "stdin": "stdin_script",
    "menu": "stdin_script",
    "script": "stdin_script",
    "stdin_script": "stdin_script",
    "function": "function_driver",
    "function_driver": "function_driver",
    "driver": "function_driver",
    "unsupported": "unsupported",
}
_OBSERVER_KIND_ALIASES = {
    "extractor": "extractor",
    "trusted_extractor": "extractor",
    "json": "json_stdout",
    "json_stdout": "json_stdout",
}


def _lower(value: Any) -> Any:
    return value.strip().lower().replace("-", "_") if isinstance(value, str) else value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetSpec(_Model):
    subject_level: Literal["FUNCTION", "PROGRAM"] = "PROGRAM"
    encoding: str = "utf-8"
    sources: list[str] = Field(min_length=1)
    entry_files: list[str] = Field(default_factory=list)
    excluded_files: list[str] = Field(default_factory=list)
    include_dirs: list[str] = Field(default_factory=list)
    runtime_files: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("include_dirs", mode="before")
    @classmethod
    def _root_include_is_implicit(cls, value):
        # The subject root is always included by the host. Models commonly
        # spell that root as '.', which is not a submitted relative path.
        if isinstance(value, list):
            return [item for item in value if item != "."]
        return value


class BuildSpec(_Model):
    compiler_flags: list[str] = Field(default_factory=list)
    defines: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    artifact_name: str = "subject.exe"


class DriverSpec(_Model):
    kind: Literal["stdin_script", "function_driver", "unsupported"] = "stdin_script"
    session: list[str] = Field(default_factory=list)
    session_by_case: dict[str, list[str]] = Field(default_factory=dict)
    seed_session: list[str] = Field(default_factory=list)
    startup_session: list[str] = Field(default_factory=list)
    eof: bool = True
    driver_source: str = ""
    notes: str = ""

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: Any) -> str:
        text = _lower(value)
        return _DRIVER_KIND_ALIASES.get(text, text)

    @field_validator("session", mode="before")
    @classmethod
    def _session(cls, value: Any) -> list[Any]:
        return list(value or [])

    @field_validator("session_by_case", mode="before")
    @classmethod
    def _session_by_case(cls, value: Any) -> dict[str, list[Any]]:
        if not isinstance(value, dict):
            return {}
        return {str(key): list(item or []) for key, item in value.items()}

    @field_validator("seed_session", mode="before")
    @classmethod
    def _seed_session(cls, value: Any) -> list[Any]:
        return list(value or [])

    @field_validator("driver_source", mode="before")
    @classmethod
    def _driver_source(cls, value: Any) -> str:
        return "" if value is None else str(value)


class ObserverSpec(_Model):
    kind: Literal["extractor", "json_stdout", "generated_observer"] = "extractor"
    extractor_id: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    produces: list[str] = Field(default_factory=list)
    observer_source: str = ""
    artifact_paths: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: Any) -> str:
        text = _lower(value)
        return _OBSERVER_KIND_ALIASES.get(text, text)


class AssertionBinding(_Model):
    assertion_id: str
    actual_key: str = ""
    status: Literal["bound", "gap"] = "bound"
    gap_reason: str = ""

    @field_validator("actual_key", mode="before")
    @classmethod
    def _actual_key(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> str:
        return _lower(value) or "bound"

    @field_validator("gap_reason", mode="before")
    @classmethod
    def _gap_reason(cls, value: Any) -> str:
        return "" if value is None else str(value)


class MigrationRow(_Model):
    partition_id: str
    rule_ids: list[str] = Field(default_factory=list)
    driver_step: int | None = None
    observation: str = ""
    assertion_ids: list[str] = Field(default_factory=list)
    status: Literal["covered", "gap"] = "covered"
    method_steps: list[str] = Field(default_factory=list)

    @field_validator("driver_step", mode="before")
    @classmethod
    def _driver_step(cls, value: Any) -> int | None:
        return value if isinstance(value, int) else None

    @field_validator("observation", mode="before")
    @classmethod
    def _observation(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> str:
        return _lower(value) or "covered"


class SourceBinding(_Model):
    role: Literal["ACTION", "OUTPUT"]
    key: str
    file: str
    source_quote: str = Field(min_length=4, max_length=1000)
    symbol: str = Field(min_length=1)


class ReferenceLearning(_Model):
    method_hash: str
    setup_steps: list[str]
    action_steps: list[str]
    observations: list[str]
    assertion_ids: list[str]
    partition_ids: list[str]
    explanation: str = Field(min_length=20)


class TargetVerifierBundle(_Model):
    schema_version: str = SCHEMA_VERSION
    task_id: str
    task_version: str
    status: Literal["READY", "NEEDS_INFORMATION", "UNSUPPORTED"] = "READY"
    target: TargetSpec
    build: BuildSpec = Field(default_factory=BuildSpec)
    driver: DriverSpec
    observer: ObserverSpec
    assertion_bindings: list[AssertionBinding] = Field(default_factory=list)
    migration_matrix: list[MigrationRow] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    notes: str = ""
    reference_learning: ReferenceLearning | None = None
    source_bindings: list[SourceBinding] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> str:
        text = str(value or "READY").strip().upper().replace("-", "_").replace(" ", "_")
        return text or "READY"

    @field_validator("coverage_gaps", mode="before")
    @classmethod
    def _coverage_gaps(cls, value: Any) -> list[str]:
        gaps: list[str] = []
        for item in value or []:
            if isinstance(item, dict):
                kind = str(item.get("kind") or "GAP")
                detail = str(item.get("detail") or "").strip()
                gaps.append(f"{kind}: {detail}" if detail else kind)
            elif item is not None:
                gaps.append(str(item))
        return gaps

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @property
    def driver_kind(self) -> DriverKind:
        if self.driver.kind == "function_driver":
            return DriverKind.FUNCTION_DRIVER
        if self.driver.kind == "stdin_script":
            return DriverKind.STDIN_SCRIPT
        return DriverKind.STDIN_SCRIPT


@dataclass
class BundleValidation:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    level: str = "static"

    @property
    def passed(self) -> bool:
        return not self.problems

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "level": self.level,
            "problems": self.problems,
            "warnings": self.warnings,
        }


def _walk(node: Any, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((f"{path}/{key}", key))
            found.extend(_walk(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk(value, f"{path}/{index}"))
    return found


def schema_paths(schema: dict[str, Any]) -> set[str]:
    """把 JSON Schema 的属性路径摊平成 ``candidate.birthday.year`` 形式。"""
    paths: set[str] = set()

    def visit(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return
        for name, child in properties.items():
            current = f"{prefix}.{name}" if prefix else name
            paths.add(current)
            visit(child, current)
            if isinstance(child, dict):
                visit(child.get("items"), f"{current}.0")

    visit(schema, "")
    return paths


def _placeholder_known(placeholder: str, known_inputs: set[str]) -> bool:
    """数组下标的占位符（``initial_contacts.3.name``）按 ``.0`` 归一后再比对。"""
    if placeholder in known_inputs:
        return True
    normalized = re.sub(r"\.\d+(?=\.|$)", ".0", placeholder)
    return normalized in known_inputs


def _placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text or ""))


def validate_bundle(
    bundle: TargetVerifierBundle,
    task: Any,
    files: dict[str, str],
    *,
    extractor_ids: set[str],
    case_inputs: dict[str, dict[str, Any]] | None = None,
) -> BundleValidation:
    """宿主静态校验：LLM 的声明必须先过关，才允许编译/试跑。"""
    result = BundleValidation()
    document: TaskDocument = task.document
    spec = document.task_spec
    result.problems.extend(validate_build_options(bundle.build))
    for artifact_path in bundle.observer.artifact_paths:
        try:
            relative_path(artifact_path)
        except Exception:
            result.problems.append(f"观测文件路径非法：{artifact_path}")
    for path in bundle.target.sources + bundle.target.entry_files + bundle.target.excluded_files + bundle.target.include_dirs + bundle.target.runtime_files:
        try:
            relative_path(path)
        except Exception as exc:
            result.problems.append(f"目标路径非法：{path}: {exc}")
    for path in bundle.target.runtime_files:
        if path not in files or Path(path).name in {"stdout.txt", "stderr.txt", "stdin.txt", "request.json", "observer_request.json", "observer_stdout.txt", "observer_stderr.txt"}:
            result.problems.append(f"运行期文件缺失或与协议工件冲突：{path}")
    if bundle.coverage_gaps:
        result.problems.append("coverage_gaps 非空；不能将覆盖缺口声明为完整适配")
    learning = bundle.reference_learning
    if learning is None:
        result.problems.append("缺少 reference_learning：必须先阅读基准测试方法和判断程序")
    else:
        expected_learning = {
            "setup_steps": task.method.setup_steps,
            "action_steps": task.method.action_steps,
            "observations": task.method.observations,
            "assertion_ids": [a.assertion_id for a in task.method.assertions if a.required],
            "partition_ids": task.method.coverage_requirements.required_partitions,
        }
        if learning.method_hash != hash_json(task.method.model_dump(mode="json")):
            result.problems.append("reference_learning.method_hash 不匹配基准测试方法")
        for field, required in expected_learning.items():
            if set(getattr(learning, field)) != set(required):
                result.problems.append(f"基准方法学习遗漏/改写了 {field}")
    bindings = {(b.role, b.key): b for b in bundle.source_bindings}
    required_bindings = [("ACTION", key) for key in task.method.action_steps] + [("OUTPUT", key) for key in task.method.observations]
    for binding_key in required_bindings:
        binding = bindings.get(binding_key)
        if binding is None:
            result.problems.append(f"缺少目标源码证据映射：{binding_key}")
        elif binding.file not in files:
            result.problems.append(f"源码映射文件不存在：{binding.key} -> {binding.file}；可用文件：{sorted(files)}")
        elif binding.source_quote not in files[binding.file]:
            matches = sorted(path for path, text in files.items() if binding.source_quote in text)
            result.problems.append(
                f"源码映射引用不存在：{binding.key}；source_quote 不在声明文件 {binding.file} 中；"
                f"相同原文所在文件：{matches}。必须声明实际包含该原文的文件，头文件内联实现不能映射到 .cpp。"
            )
        elif binding.symbol not in files[binding.file]:
            result.problems.append(f"源码映射符号不存在：{binding.symbol}")

    if bundle.schema_version != SCHEMA_VERSION:
        result.problems.append(f"schema_version 必须是 {SCHEMA_VERSION}")
    if bundle.task_id != spec.task_id or bundle.task_version != spec.version:
        result.problems.append(
            f"任务不匹配：声明 {bundle.task_id}@{bundle.task_version}，实际 {spec.task_id}@{spec.version}"
        )
    if bundle.status != "READY":
        result.problems.append(f"适配状态是 {bundle.status}，只有 READY 才能进入测评")

    # 目标源码
    normalized_files = {path.replace("\\", "/"): text for path, text in files.items()}
    for source in bundle.target.sources:
        clean = source.replace("\\", "/")
        if clean not in normalized_files:
            result.problems.append(f"target.sources 里的文件不存在：{source}")
        elif not any(clean.lower().endswith(suffix) for suffix in SOURCE_SUFFIXES):
            result.problems.append(f"target.sources 里不是源码/头文件：{source}")
    for entry in bundle.target.entry_files:
        if entry.replace("\\", "/") not in normalized_files:
            result.problems.append(f"target.entry_files 里的文件不存在：{entry}")
    for path in bundle.target.excluded_files:
        if path not in normalized_files:
            result.problems.append(f"排除文件不存在：{path}")
    if set(bundle.target.sources) & set(bundle.target.excluded_files):
        result.problems.append("被测源码不能同时包含于 sources 与 excluded_files")
    for directory in bundle.target.include_dirs:
        if not any(p.startswith(directory.rstrip("/") + "/") for p in normalized_files):
            result.problems.append(f"include 目录不存在：{directory}")
    if bundle.target.encoding.lower() not in {"utf-8", "utf8", "gbk"}:
        result.problems.append(f"不支持的编码声明：{bundle.target.encoding}")

    # 驱动
    known_inputs = schema_paths(task.template.input_schema)
    known_inputs.add("__input_json__")

    def _check_script_lines(script: list[str], where: str) -> None:
        for index, step in enumerate(script):
            text = str(step).strip()
            if not text:
                continue
            looks_like_prompt = text.startswith("*") or "请输入" in text or "Make your choice" in text
            looks_like_prompt = looks_like_prompt or text.endswith("：") or text.endswith(":")
            if looks_like_prompt:
                result.problems.append(
                    f"剧本第 {index + 1} 行像是程序打印的提示语而不是输入：{text[:40]}（{where}）；"
                    "剧本只放要输入的值，不要复制程序输出"
                )

    if bundle.driver.kind == "stdin_script":
        if not bundle.driver.session and not bundle.driver.session_by_case:
            result.problems.append("stdin_script 驱动必须给出非空的 session 剧本")
        _check_script_lines(bundle.driver.session, "session")
        _check_script_lines(bundle.driver.startup_session, "startup_session")
        for step in bundle.driver.session + bundle.driver.startup_session:
            for placeholder in _placeholders(step):
                if not _placeholder_known(placeholder, known_inputs):
                    result.problems.append(f"剧本占位符不在输入模板里：{{{{{placeholder}}}}}")
        known_cases = set(spec.case_ids)
        for case_id, script in bundle.driver.session_by_case.items():
            if case_id not in known_cases:
                result.problems.append(f"session_by_case 里的用例不存在：{case_id}")
            if not script:
                result.problems.append(f"session_by_case[{case_id}] 是空剧本")
            _check_script_lines(script, case_id)
            for step in script:
                for placeholder in _placeholders(step):
                    if not _placeholder_known(placeholder, known_inputs):
                        result.problems.append(
                            f"剧本占位符不在输入模板里：{{{{{placeholder}}}}}（{case_id}）"
                        )
        if bundle.driver.seed_session:
            for index, step in enumerate(bundle.driver.seed_session):
                for placeholder in _placeholders(str(step)):
                    if placeholder not in SEED_PLACEHOLDERS:
                        result.problems.append(
                            f"seed_session 第 {index + 1} 行占位符只允许 {sorted(SEED_PLACEHOLDERS)}："
                            f"{{{{{placeholder}}}}}"
                        )
        if bundle.observer.extractor_id in {"birthday_menu_v1", "keyword_search_v1", "contact_ids_v1"}:
            for case_id, payload in (case_inputs or {}).items():
                initial = payload.get("initial_contacts") or []
                names = [str(item.get("name") or "") for item in initial if isinstance(item, dict)]
                names = [name for name in names if name]
                if not names:
                    continue
                script = bundle.driver.session_by_case.get(case_id) or bundle.driver.session
                text = "\n".join(str(line) for line in script)
                if "initial_contacts" in text:
                    continue
                if bundle.driver.seed_session:
                    continue
                missing = [name for name in names if name not in text]
                if missing:
                    result.problems.append(
                        f"用例 {case_id} 声明了初始联系人 {missing}，剧本没有先建出来；"
                        "菜单程序无法导入初始数据，请用 driver.seed_session 写一段“建一个联系人”"
                        "的剧本（占位符用 {{seed.name}} / {{seed.phone}} / "
                        "{{seed.birthday.year}} 等），宿主会对每个初始联系人重复执行"
                    )
    elif bundle.driver.kind == "function_driver":
        source = bundle.driver.driver_source or ""
        if len(source) < 60:
            result.problems.append("function_driver 的 driver_source 太短，无法编译")
        for marker in _FORBIDDEN_DRIVER_MARKERS:
            if marker in source:
                result.problems.append(f"driver_source 含被禁止的调用：{marker}")
        if "protocol_version" not in source:
            result.problems.append("driver_source 没有输出 protocol_version 观测信封")
    else:
        result.problems.append("driver.kind=unsupported：这份程序不适合自动适配")

    # 观测
    if bundle.driver.kind == "stdin_script":
        if bundle.observer.kind == "generated_observer":
            if len(bundle.observer.observer_source) < 60:
                result.problems.append("generated_observer 必须给出 C++ observer_source")
        elif bundle.observer.kind != "extractor":
            result.problems.append("菜单剧本需要可信提取器或隔离内 generated_observer")
        elif bundle.observer.extractor_id not in extractor_ids:
            result.problems.append(f"提取器未注册：{bundle.observer.extractor_id!r}")
    for key, value in bundle.observer.params.items():
        if key in {"baseline_names", "renumber_ids", "id_offset"}:
            result.problems.append(f"禁止观测转换 {key}：不能过滤实际记录或改写编号")
        if isinstance(value, str):
            for placeholder in _placeholders(value):
                if not _placeholder_known(placeholder, known_inputs):
                    result.problems.append(f"观测参数占位符不在输入模板里：{placeholder}")

    # 断言绑定：必需断言必须一条不漏，且不能改名
    required_assertions = {a.assertion_id: a for a in task.assertions.values() if a.required}
    bound = {binding.assertion_id: binding for binding in bundle.assertion_bindings}
    for assertion_id, assertion in required_assertions.items():
        binding = bound.get(assertion_id)
        if binding is None:
            result.problems.append(f"断言未绑定：{assertion_id}")
            continue
        if binding.status != "bound":
            result.problems.append(f"必需断言 {assertion_id} 被标成 gap：{binding.gap_reason}")
        elif binding.actual_key != assertion.actual_observation_key:
            result.problems.append(
                f"断言 {assertion_id} 的观测键不能改：应为 {assertion.actual_observation_key!r}，声明为 {binding.actual_key!r}"
            )
    for assertion_id in bound:
        if assertion_id not in required_assertions:
            result.warnings.append(f"声明了任务里不存在的断言：{assertion_id}")

    # 迁移矩阵：输入分区必须全覆盖
    required_partitions = {partition.partition_id for partition in task.method.input_partitions}
    declared = {row.partition_id for row in bundle.migration_matrix}
    for partition in sorted(required_partitions - declared):
        result.problems.append(f"迁移矩阵缺分区：{partition}")
    for partition in sorted(declared - required_partitions):
        result.warnings.append(f"迁移矩阵多出未定义分区：{partition}")
    if len(declared) != len(bundle.migration_matrix):
        result.problems.append("迁移矩阵有重复分区")
    required_steps = set(task.method.setup_steps + task.method.action_steps)
    for row in bundle.migration_matrix:
        if row.status != "covered" or not row.observation.strip():
            result.problems.append(f"分区 {row.partition_id} 未覆盖观测")
        if not required_steps <= set(row.method_steps):
            missing_steps = sorted(required_steps - set(row.method_steps))
            result.problems.append(f"分区 {row.partition_id} 缺少基准操作步骤映射：{missing_steps}")
        if not set(required_assertions) <= set(row.assertion_ids):
            result.problems.append(f"分区 {row.partition_id} 缺少必需断言")
        if bundle.driver.kind == "stdin_script":
            scripts = [bundle.driver.session] + list(bundle.driver.session_by_case.values())
            if row.driver_step is None or row.driver_step < 0 or not any(row.driver_step < len(s) for s in scripts):
                result.problems.append(f"分区 {row.partition_id} 的 driver_step 无效（零起始下标）")
    if bundle.driver.kind == "function_driver":
        actions = [b.symbol for b in bundle.source_bindings if b.role == "ACTION"]
        if not actions or not any(re.search(r"\b" + re.escape(symbol.split("::")[-1]) + r"\s*\(", bundle.driver.driver_source) for symbol in actions):
            result.problems.append("函数驱动没有调用已映射的目标业务符号")

    # 严禁夹带期望值/结论
    for location, key in _walk(bundle.model_dump()):
        if str(key).lower() in _FORBIDDEN_KEYS:
            result.problems.append(f"bundle 不允许出现期望值/结论字段：{location}")

    result.level = "static"
    return result


def assertion_ids_for_case(bundle: TargetVerifierBundle, case: Any) -> list[str]:
    bound = {binding.assertion_id for binding in bundle.assertion_bindings if binding.status == "bound"}
    return [assertion_id for assertion_id in case.assertion_ids if assertion_id in bound]


def binding_map(bundle: TargetVerifierBundle) -> dict[str, AssertionBinding]:
    return {binding.assertion_id: binding for binding in bundle.assertion_bindings}


def spec_of(task: Any, assertion_id: str) -> AssertionSpec:
    return task.assertions[assertion_id]

"""领域模型（架构实施方案 §7 的数据契约）。

约定：
- 所有持久化文档都带 ``schema_version``；
- 模型一律 ``extra="forbid"``，未知字段直接拒绝；
- 枚举字段保存字符串值，便于 SQLite / JSON / CSV 共用。
本阶段（模式一 CLI 原型）实现的是模式一所需子集：
任务包、构建计划、执行观测、断言结果、客观报告、验证记录。
模式二所需的 AdapterBundle / TargetVerifierBundle / ProjectAnalysis
在后续阶段补充，不在本阶段伪造。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BindingRole,
    BindingStatus,
    BuildRole,
    CasePurpose,
    ComparisonStatus,
    CompileStatus,
    DriverKind,
    ExecutionStatus,
    ExpectedSourceKind,
    FaultOwner,
    IsolationLevel,
    Mode,
    ObserverKind,
    Operator,
    OracleStatus,
    OverallStatus,
    ProtocolStatus,
    PublicationStatus,
    SourceOrigin,
    SubjectLevel,
    Termination,
    ValidationLevel,
    VariantRole,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Version = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
JsonPointer = Annotated[str, Field(pattern=r"^(/.*)?$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 任务包
# ---------------------------------------------------------------------------


class ContentRef(StrictModel):
    id: Identifier
    version: Version
    sha256: Sha256


class FileEntry(StrictModel):
    file_id: Identifier
    path: str
    size: int = Field(ge=0)
    sha256: Sha256
    encoding: str = "utf-8"


class SubmissionManifest(StrictModel):
    schema_version: str
    submission_id: Identifier
    files: list[FileEntry]
    snapshot_hash: Sha256
    target_before_id: Identifier | None = None


class RuleStatement(StrictModel):
    """任务规则条目。断言、用例都通过 ``rule_id`` 回溯到规则原文。"""

    rule_id: Identifier
    statement: str


class TaskSpec(StrictModel):
    schema_version: str
    task_id: Identifier
    version: Version
    title: str
    description: str
    rules: list[RuleStatement]
    case_ids: list[str]
    io_template_ref: ContentRef
    test_method_ref: ContentRef
    reference_verifier_ref: ContentRef
    publication_status: PublicationStatus
    oracle_provenance: str


class TemplateStep(StrictModel):
    step_id: Identifier
    action: Identifier
    pointer: JsonPointer | None = None
    output_key: Identifier | None = None


class TemplateSteps(StrictModel):
    setup: list[TemplateStep] = []
    action: list[TemplateStep] = []
    observe: list[TemplateStep] = []
    teardown: list[TemplateStep] = []


class PreOperation(StrictModel):
    step_id: Identifier
    operation_id: Identifier
    observation_key: Identifier


class ExitContract(StrictModel):
    success_exit_codes: list[int]
    observation_channel: str


class CapabilityRequirements(StrictModel):
    needs_cpu_limit: bool = True
    needs_memory_limit: bool = True
    needs_wall_timeout: bool = True
    needs_process_tree_kill: bool = True
    needs_network_block: bool = True


class IOTemplate(StrictModel):
    schema_version: str
    template_id: Identifier
    version: Version
    operation_id: Identifier
    subject_level: SubjectLevel
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    field_semantics: dict[str, str]
    preconditions: list[str]
    steps: TemplateSteps
    pre_operations: list[PreOperation] = []
    required_observations: list[str]
    allowed_transforms: list[str]
    encoding: str
    exit_contract: ExitContract
    capability_requirements: CapabilityRequirements


class ExpectedSource(StrictModel):
    kind: ExpectedSourceKind
    pointer: JsonPointer
    observation_key: Identifier | None = None


class AssertionSpec(StrictModel):
    schema_version: str
    assertion_id: Identifier
    rule_ids: list[str]
    operator: Operator
    actual_observation_key: str
    expected_source: ExpectedSource
    normalization_id: Identifier
    required: bool = True


class InputPartition(StrictModel):
    partition_id: Identifier
    description: str
    kind: str
    rule_ids: list[str]


class BoundaryRequirement(StrictModel):
    boundary_id: Identifier
    description: str
    required_partitions: list[str]


class CoverageRequirements(StrictModel):
    required_partitions: list[str]
    required_assertions: list[str]


class KnownFault(StrictModel):
    fault_id: Identifier
    description: str
    detected_by_cases: list[str]


class TestMethodSpec(StrictModel):
    schema_version: str
    method_id: Identifier
    version: Version
    subject_level: SubjectLevel
    rule_refs: list[str]
    input_partitions: list[InputPartition]
    boundary_requirements: list[BoundaryRequirement]
    setup_steps: list[str]
    action_steps: list[str]
    observations: list[str]
    assertions: list[AssertionSpec]
    coverage_requirements: CoverageRequirements
    reference_verifier_ref: ContentRef
    known_fault_matrix: list[KnownFault]


class TaskCase(StrictModel):
    schema_version: str
    case_id: Identifier
    rule_ids: list[str]
    partition_ids: list[str]
    assertion_ids: list[str]
    purpose: CasePurpose
    input: dict[str, Any]
    expected: dict[str, Any]
    fixture_ids: list[str] = []
    limits_override: dict[str, int] | None = None
    oracle_status: OracleStatus


class BindingEntry(StrictModel):
    binding_id: Identifier
    template_pointer: JsonPointer
    role: BindingRole
    target_locator: str
    transform_id: Identifier
    evidence_refs: list[str] = []
    observation_source: str
    status: BindingStatus


class BindingMap(StrictModel):
    schema_version: str
    binding_map_id: Identifier
    template_ref: ContentRef
    entries: list[BindingEntry]
    coverage_gaps: list[str] = []


# ---------------------------------------------------------------------------
# 构建与执行
# ---------------------------------------------------------------------------


class SourceUnit(StrictModel):
    unit_id: Identifier
    origin: SourceOrigin
    role: BuildRole
    path: str


class BuildPlan(StrictModel):
    schema_version: str
    build_plan_id: Identifier
    role: BuildRole
    subject_snapshot_hash: Sha256
    toolchain_id: Identifier
    source_units: list[SourceUnit]
    include_dirs: list[str]
    defines: list[str]
    libraries: list[str]
    entrypoint: str | None
    artifact_name: str
    build_hash: Sha256


class ResourceLimits(StrictModel):
    limits_id: Identifier
    compile_wall_ms: int = Field(gt=0)
    case_wall_ms: int = Field(gt=0)
    total_wall_ms: int = Field(gt=0)
    compile_cpu_ms: int = Field(gt=0)
    case_cpu_ms: int = Field(gt=0)
    cpu_rate_percent: int = Field(gt=0, le=100)
    compile_memory_bytes: int = Field(gt=0)
    case_memory_bytes: int = Field(gt=0)
    compile_process_count: int = Field(gt=0)
    case_process_count: int = Field(gt=0)
    output_bytes: int = Field(gt=0)
    workdir_bytes: int = Field(gt=0)


class SandboxCapabilities(StrictModel):
    schema_version: str
    backend_id: Identifier
    environment_digest: Sha256
    isolation: IsolationLevel
    network_block: bool
    cpu_limit: bool
    memory_limit: bool
    process_limit: bool
    wall_timeout: bool
    tree_kill: bool
    clean_reset: bool
    filesystem_isolation: bool = False
    notes: list[str] = []


class CompileResult(StrictModel):
    schema_version: str
    compile_id: Identifier
    build_hash: Sha256
    status: CompileStatus
    exit_code: int | None
    stdout_ref: str | None
    stderr_ref: str | None
    duration_ms: int
    artifact_hash: Sha256 | None = None
    fault_owner: FaultOwner
    issue_code: str | None = None


class Observation(StrictModel):
    schema_version: str
    observation_id: Identifier
    run_id: Identifier
    phase: str
    execution_manifest_hash: Sha256
    plan_hash: Sha256 | None = None
    case_id: Identifier
    attempt_id: Identifier
    termination: Termination
    exit_code: int | None
    stdout_ref: str | None
    stderr_ref: str | None
    files_refs: list[str] = []
    wall_ms: int
    resource_events: list[str] = []
    protocol_status: ProtocolStatus
    actual: Any = None
    fault_owner: FaultOwner
    protocol_detail: str | None = None


class AssertionResult(StrictModel):
    schema_version: str
    assertion_id: Identifier
    case_id: Identifier
    operator: Operator
    comparison: ComparisonStatus
    expected_source: ExpectedSource | None = None
    expected: Any = None
    actual: Any = None
    detail: str | None = None
    evidence_ref: str | None = None


class CaseResult(StrictModel):
    schema_version: str
    case_id: Identifier
    attempt_id: Identifier
    purpose: CasePurpose
    comparison: ComparisonStatus
    termination: Termination
    protocol_status: ProtocolStatus
    assertion_results: list[AssertionResult]
    failed_assertions: list[str]
    evidence_refs: list[str]
    observation_id: str | None = None
    note: str | None = None


class CoverageReport(StrictModel):
    planned_cases: list[str]
    completed_cases: list[str]
    missed_cases: list[str]
    planned_partitions: list[str]
    covered_partitions: list[str]
    missing_partitions: list[str]
    planned_assertions: list[str]
    executed_assertions: list[str]
    missing_assertions: list[str]
    complete: bool


class ReportCounts(StrictModel):
    required_cases: int = Field(ge=0)
    attempted_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    matched_cases: int = Field(ge=0)
    mismatched_cases: int = Field(ge=0)
    not_compared_cases: int = Field(ge=0)
    required_assertions: int = Field(ge=0)
    matched_assertions: int = Field(ge=0)
    mismatched_assertions: int = Field(ge=0)
    not_compared_assertions: int = Field(ge=0)


class ReportProvenance(StrictModel):
    execution_backend: Identifier
    sandbox_attested: bool
    task_id: Identifier
    task_version: Version
    task_package_hash: Sha256
    submission_id: Identifier | None = None
    toolchain_id: Identifier
    started_at: str
    finished_at: str
    assessor_version: str
    protocol_version: str


class AssessmentReport(StrictModel):
    schema_version: str
    run_id: Identifier
    mode: Mode
    execution_status: ExecutionStatus
    assessment_status: OverallStatus
    grade_eligible: bool
    grade_eligible_reasons: list[str] = []
    validation_level: ValidationLevel
    coverage: CoverageReport
    case_results: list[CaseResult]
    counts: ReportCounts
    evidence_refs: list[str]
    provenance: ReportProvenance
    observations_note: str | None = None


class PlannedCase(StrictModel):
    schema_version: str
    case_id: Identifier
    operation_id: Identifier
    input_hash: Sha256
    assertion_ids: list[str]
    limits: ResourceLimits


class TestPlan(StrictModel):
    schema_version: str
    plan_id: Identifier
    run_id: Identifier
    task_ref: ContentRef
    method_ref: ContentRef
    template_ref: ContentRef
    verifier_ref: ContentRef
    subject_snapshot_hash: Sha256
    operation_id: Identifier
    driver_kind: DriverKind
    observer_kind: ObserverKind
    cases: list[PlannedCase]
    build_plan: BuildPlan
    plan_hash: Sha256


class ValidationCheck(StrictModel):
    check_id: Identifier
    description: str
    passed: bool
    detail: str | None = None


class ValidationRecord(StrictModel):
    schema_version: str
    validation_id: Identifier
    run_id: Identifier
    plan_hash: Sha256
    checks: list[ValidationCheck]
    passed: bool
    validation_level: ValidationLevel


# ---------------------------------------------------------------------------
# 任务包整体（task.json 的包装）
# ---------------------------------------------------------------------------


class TaskProjectRef(StrictModel):
    project_id: Identifier
    version: Version
    root: str


class TaskBuildSpec(StrictModel):
    toolchain_id: Identifier
    exclude_globs: list[str]
    harness_files: list[str]
    harness_include_dir: str
    compiler_flags: list[str]


class ReferenceVariant(StrictModel):
    variant_id: Identifier
    role: VariantRole
    description: str
    overrides: list[str] = []
    expected_assessment_status: OverallStatus
    must_fail_cases: list[str] = []


class ReferenceSuite(StrictModel):
    schema_version: str
    suite_id: Identifier
    version: Version
    task_id: Identifier
    variants: list[ReferenceVariant]


class TaskDocument(StrictModel):
    schema_version: str
    task_spec: TaskSpec
    project_ref: TaskProjectRef
    build: TaskBuildSpec
    limits: ResourceLimits
    reference_suite: ReferenceSuite
    student_entry_note: str = ""


class CalibrationOutcome(StrictModel):
    variant_id: Identifier
    role: VariantRole
    expected_assessment_status: OverallStatus
    actual_assessment_status: OverallStatus
    matched: bool
    failed_cases: list[str]
    expected_failed_cases: list[str]
    run_id: Identifier | None = None
    detail: str | None = None


class CalibrationRecord(StrictModel):
    schema_version: str
    calibration_id: Identifier
    task_id: Identifier
    task_version: Version
    task_package_hash: Sha256
    executed_at: str
    outcomes: list[CalibrationOutcome]
    passed: bool

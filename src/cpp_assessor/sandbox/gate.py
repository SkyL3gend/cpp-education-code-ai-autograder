"""能力门禁（架构实施方案 §9.3）。

CPU、内存、时间、进程数、网络隔离、进程树终止、环境清理中
任何一项"必要能力"缺失，就拒绝真实提交执行。
被显式放行时，报告必须标明后端未通过门禁，且不得计入正式验收。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import IsolationLevel
from ..domain.models import CapabilityRequirements, SandboxCapabilities

# 模板能力需求字段 → SandboxCapabilities 字段
_CAPABILITY_MAP = {
    "needs_cpu_limit": "cpu_limit",
    "needs_memory_limit": "memory_limit",
    "needs_wall_timeout": "wall_timeout",
    "needs_process_tree_kill": "tree_kill",
    "needs_network_block": "network_block",
}


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    attested: bool
    required: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "attested": self.attested,
            "required": self.required,
            "missing": self.missing,
            "reasons": self.reasons,
        }


def evaluate(
    capabilities: SandboxCapabilities,
    requirements: CapabilityRequirements,
    *,
    allow_unattested: bool,
) -> GateDecision:
    required: list[str] = []
    missing: list[str] = []
    for requirement_field, capability_field in _CAPABILITY_MAP.items():
        if getattr(requirements, requirement_field):
            required.append(capability_field)
            if not getattr(capabilities, capability_field):
                missing.append(capability_field)
    reasons: list[str] = []
    for field in ("process_limit", "clean_reset", "filesystem_isolation", "network_block"):
        if field not in required:
            required.append(field)
        if not getattr(capabilities, field) and field not in missing:
            missing.append(field)
    if capabilities.isolation not in {IsolationLevel.WINDOWS_SANDBOX, IsolationLevel.VM}:
        missing.append("isolation")
        reasons.append("真实提交需要虚拟化隔离；宿主进程资源限制不构成隔离")
    if missing:
        reasons.append(f"缺少必要能力：{', '.join(missing)}")
    attested = not missing
    if attested:
        return GateDecision(True, True, required, missing, reasons)
    if allow_unattested:
        reasons.append("已显式放行：本次执行不得计入正式验收")
        return GateDecision(True, False, required, missing, reasons)
    reasons.append(
        "拒绝执行：请改用通过门禁的后端，或显式加 --allow-unattested-backend"
    )
    return GateDecision(False, False, required, missing, reasons)

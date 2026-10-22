"""能力门禁：缺任一必要能力即拒绝真实执行，显式放行也不得算验收。"""

from __future__ import annotations

from cpp_assessor.domain.enums import IsolationLevel
from cpp_assessor.domain.models import CapabilityRequirements, SandboxCapabilities
from cpp_assessor.sandbox.gate import evaluate


def capabilities(**overrides) -> SandboxCapabilities:
    payload = {
        "schema_version": "1.0",
        "backend_id": "test_backend",
        "environment_digest": "0" * 64,
        "isolation": IsolationLevel.HOST_PROCESS,
        "network_block": False,
        "cpu_limit": True,
        "memory_limit": True,
        "process_limit": True,
        "wall_timeout": True,
        "tree_kill": True,
        "clean_reset": True,
        "filesystem_isolation": False,
        "notes": [],
    }
    payload.update(overrides)
    return SandboxCapabilities(**payload)


ALL_REQUIREMENTS = CapabilityRequirements(
    needs_cpu_limit=True,
    needs_memory_limit=True,
    needs_wall_timeout=True,
    needs_process_tree_kill=True,
    needs_network_block=True,
)


def test_missing_network_isolation_denies_execution():
    decision = evaluate(capabilities(), ALL_REQUIREMENTS, allow_unattested=False)
    assert decision.allowed is False
    assert decision.attested is False
    assert "network_block" in decision.missing


def test_explicit_allow_keeps_execution_untested():
    decision = evaluate(capabilities(), ALL_REQUIREMENTS, allow_unattested=True)
    assert decision.allowed is True
    assert decision.attested is False
    assert any("不得计入正式验收" in reason for reason in decision.reasons)


def test_no_isolation_backend_is_never_attested():
    mock = capabilities(backend_id="mock", isolation=IsolationLevel.NONE)
    decision = evaluate(mock, ALL_REQUIREMENTS, allow_unattested=True)
    assert decision.allowed is True
    assert decision.attested is False
    assert "isolation" in decision.missing


def test_all_capabilities_present_is_attested():
    full = capabilities(network_block=True, filesystem_isolation=True, isolation=IsolationLevel.VM)
    decision = evaluate(full, ALL_REQUIREMENTS, allow_unattested=False)
    assert decision.allowed is True
    assert decision.attested is True
    assert decision.missing == []

def test_process_reset_and_filesystem_are_mandatory():
    result = evaluate(capabilities(network_block=True, process_limit=False, clean_reset=False), ALL_REQUIREMENTS, allow_unattested=False)
    assert not result.attested
    assert {"process_limit", "clean_reset", "filesystem_isolation", "isolation"} <= set(result.missing)

"""标准输入输出协议：请求信封构造与观测解析（架构实施方案 §7.2）。

约定：

- 请求信封只含协议元数据与 ``input``，**绝不包含 expected/答案**；
- 被测程序向 stdout 输出**一行**观测信封；
- 观测信封不含 PASS/FAIL，判定完全由宿主完成；
- 解析与比较严格分离：本模块只把原始输出变成 ``actual``，不比较。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import PROTOCOL_VERSION
from ..domain.enums import FaultOwner, ProtocolStatus, Termination

#: 这些终止状态意味着"被测程序没有正常跑完"，此时协议文件缺失是学生侧问题。
LIMIT_TERMINATIONS = {
    Termination.WALL_TIME_LIMIT,
    Termination.CPU_TIME_LIMIT,
    Termination.MEMORY_LIMIT,
    Termination.PROCESS_LIMIT,
    Termination.OUTPUT_LIMIT,
    Termination.DISK_LIMIT,
    Termination.CRASH,
}


def build_request(
    *,
    run_id: str,
    attempt_id: str,
    case_id: str,
    operation_id: str,
    case_input: dict[str, Any],
) -> dict[str, Any]:
    """构造请求信封。

    只放协议元数据与输入。任何期望值都不得出现在这里——否则被测程序
    可以读走答案。写入磁盘的请求文件也用于事后复核。
    """
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "case_id": case_id,
        "operation_id": operation_id,
        "input": case_input,
    }


@dataclass(frozen=True)
class ParsedObservation:
    status: ProtocolStatus
    actual: Any
    detail: str | None
    fault_owner: FaultOwner


def _fail(
    status: ProtocolStatus,
    detail: str,
    fault: FaultOwner,
) -> ParsedObservation:
    return ParsedObservation(status=status, actual=None, detail=detail, fault_owner=fault)


def parse_response(
    *,
    stdout_text: str,
    run_id: str,
    attempt_id: str,
    case_id: str,
    operation_id: str,
    termination: Termination,
) -> ParsedObservation:
    """把被测进程的 stdout 解析成观测。

    归因原则：

    - 崩溃/超限导致没有输出 → 归因 SUBMISSION（学生侧），不是适配器故障；
    - 输出不是合法 JSON 或 ID 不匹配 → 归因 UNKNOWN，结论只能是 INCONCLUSIVE，
      不能据此判学生失败；
    - 驱动自己报告 INVALID/ADAPTER_ERROR → 归因 ADAPTER（宿主侧问题）；
    - OBSERVATION_UNAVAILABLE 表示业务调用抛异常 → 归因 SUBMISSION。
    """
    if termination in LIMIT_TERMINATIONS:
        return _fail(
            ProtocolStatus.MISSING,
            f"被测程序未正常结束（{termination.value}），没有可解析的观测",
            FaultOwner.SUBMISSION,
        )

    text = stdout_text.strip()
    if not text:
        return _fail(
            ProtocolStatus.MISSING,
            "被测程序没有向标准输出写出观测（可能提前退出或崩溃）",
            FaultOwner.SUBMISSION,
        )

    last_line = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line.strip()
            break

    try:
        envelope = json.loads(last_line)
    except json.JSONDecodeError as exc:
        return _fail(
            ProtocolStatus.MISSING,
            f"标准输出最后一行不是合法 JSON 观测：{exc.msg}",
            FaultOwner.UNKNOWN,
        )

    if not isinstance(envelope, dict):
        return _fail(ProtocolStatus.INVALID, "观测信封不是 JSON 对象", FaultOwner.ADAPTER)

    if envelope.get("protocol_version") != PROTOCOL_VERSION:
        return _fail(
            ProtocolStatus.INVALID,
            f"协议版本不匹配：期望 {PROTOCOL_VERSION}，实际 {envelope.get('protocol_version')!r}",
            FaultOwner.ADAPTER,
        )

    expected_ids = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "case_id": case_id,
        "operation_id": operation_id,
    }
    for key, expected in expected_ids.items():
        actual = envelope.get(key)
        if actual != expected:
            return _fail(
                ProtocolStatus.INVALID,
                f"观测信封字段 {key} 不匹配：期望 {expected!r}，实际 {actual!r}",
                FaultOwner.ADAPTER,
            )

    status = envelope.get("status")
    if status == "OK":
        actual = envelope.get("actual")
        if not isinstance(actual, dict):
            return _fail(ProtocolStatus.INVALID, "status=OK 但缺少对象型 actual", FaultOwner.ADAPTER)
        return ParsedObservation(
            status=ProtocolStatus.OK,
            actual=actual,
            detail=None,
            fault_owner=FaultOwner.NONE,
        )
    if status == "INVALID":
        return _fail(
            ProtocolStatus.INVALID,
            f"驱动程序拒绝请求：{envelope.get('error', '未提供原因')}",
            FaultOwner.ADAPTER,
        )
    if status == "OBSERVATION_UNAVAILABLE":
        return _fail(
            ProtocolStatus.MISSING,
            f"被测业务调用未能产生观测：{envelope.get('error', '未提供原因')}",
            FaultOwner.SUBMISSION,
        )
    if status == "ADAPTER_ERROR":
        return _fail(
            ProtocolStatus.INVALID,
            f"驱动程序故障：{envelope.get('error', '未提供原因')}",
            FaultOwner.ADAPTER,
        )
    return _fail(
        ProtocolStatus.INVALID,
        f"未知的观测状态 {status!r}",
        FaultOwner.ADAPTER,
    )

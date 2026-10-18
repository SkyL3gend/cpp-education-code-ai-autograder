"""标准输入输出协议解析：合法观测、ID 不匹配、非法 JSON、驱动/被测故障归因。"""

from __future__ import annotations

import json

from cpp_assessor.domain.enums import FaultOwner, ProtocolStatus, Termination
from cpp_assessor.orchestrator.protocol import build_request, parse_response

RUN_ID = "run_test"
ATTEMPT_ID = "attempt_1"
CASE_ID = "c1"
OPERATION_ID = "create_contact"


def envelope(**overrides):
    payload = {
        "protocol_version": "1.0",
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "case_id": CASE_ID,
        "operation_id": OPERATION_ID,
        "status": "OK",
        "actual": {"created": True},
    }
    payload.update(overrides)
    return payload


def parse(text: str, termination: Termination = Termination.EXITED):
    return parse_response(
        stdout_text=text,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        case_id=CASE_ID,
        operation_id=OPERATION_ID,
        termination=termination,
    )


def test_build_request_contains_no_expected_values():
    request = build_request(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        case_id=CASE_ID,
        operation_id=OPERATION_ID,
        case_input={"candidate": {"name": "x"}},
    )
    assert set(request) == {
        "protocol_version",
        "run_id",
        "attempt_id",
        "case_id",
        "operation_id",
        "input",
    }
    assert "expected" not in json.dumps(request)


def test_valid_observation_takes_last_json_line():
    text = "调试输出\n" + json.dumps(envelope()) + "\n"
    parsed = parse(text)
    assert parsed.status is ProtocolStatus.OK
    assert parsed.actual == {"created": True}
    assert parsed.fault_owner is FaultOwner.NONE


def test_invalid_json_is_unknown_fault():
    parsed = parse("这不是 JSON\n")
    assert parsed.status is ProtocolStatus.MISSING
    assert parsed.fault_owner is FaultOwner.UNKNOWN


def test_empty_output_is_submission_fault():
    parsed = parse("   \n")
    assert parsed.status is ProtocolStatus.MISSING
    assert parsed.fault_owner is FaultOwner.SUBMISSION


def test_case_id_mismatch_is_adapter_fault():
    parsed = parse(json.dumps(envelope(case_id="other_case")))
    assert parsed.status is ProtocolStatus.INVALID
    assert parsed.fault_owner is FaultOwner.ADAPTER


def test_ok_without_actual_object_is_adapter_fault():
    parsed = parse(json.dumps(envelope(actual=[1, 2])))
    assert parsed.status is ProtocolStatus.INVALID
    assert parsed.fault_owner is FaultOwner.ADAPTER


def test_observation_unavailable_is_submission_fault():
    parsed = parse(json.dumps(envelope(status="OBSERVATION_UNAVAILABLE")))
    assert parsed.status is ProtocolStatus.MISSING
    assert parsed.fault_owner is FaultOwner.SUBMISSION


def test_adapter_error_is_adapter_fault():
    parsed = parse(json.dumps(envelope(status="ADAPTER_ERROR")))
    assert parsed.status is ProtocolStatus.INVALID
    assert parsed.fault_owner is FaultOwner.ADAPTER


def test_timeout_without_output_is_submission_fault():
    parsed = parse("", termination=Termination.WALL_TIME_LIMIT)
    assert parsed.status is ProtocolStatus.MISSING
    assert parsed.fault_owner is FaultOwner.SUBMISSION


def test_unknown_status_is_adapter_fault():
    parsed = parse(json.dumps(envelope(status="WEIRD")))
    assert parsed.status is ProtocolStatus.INVALID
    assert parsed.fault_owner is FaultOwner.ADAPTER

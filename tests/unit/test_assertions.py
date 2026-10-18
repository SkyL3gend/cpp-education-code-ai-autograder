"""断言引擎：严格相等、多重集合比较、未变化断言、缺失观测。"""

from __future__ import annotations

import pytest

from cpp_assessor.domain.enums import (
    ComparisonStatus,
    ExpectedSourceKind,
    Operator,
)
from cpp_assessor.domain.errors import TaskPackageError
from cpp_assessor.domain.models import AssertionSpec, ExpectedSource
from cpp_assessor.judge.assertions import evaluate
from cpp_assessor.judge.normalizations import normalize


def make_spec(
    operator: Operator,
    *,
    actual_key: str = "/value",
    pointer: str = "/value",
    kind: ExpectedSourceKind = ExpectedSourceKind.CASE_EXPECTED,
    observation_key: str | None = None,
    normalization: str = "identity_v1",
) -> AssertionSpec:
    return AssertionSpec(
        schema_version="1.0",
        assertion_id="a1",
        rule_ids=["r1"],
        operator=operator,
        actual_observation_key=actual_key,
        expected_source=ExpectedSource(
            kind=kind, pointer=pointer, observation_key=observation_key
        ),
        normalization_id=normalization,
    )


def test_equals_deep_object():
    spec = make_spec(Operator.EQUALS)
    outcome = evaluate(
        spec,
        case_expected={"value": {"ok": True, "n": 2}},
        actual_document={"value": {"ok": True, "n": 2}},
    )
    assert outcome.comparison is ComparisonStatus.MATCH


def test_boolean_is_not_number():
    """Python 中 True == 1，判定必须区分类型。"""
    spec = make_spec(Operator.EQUALS)
    outcome = evaluate(
        spec,
        case_expected={"value": True},
        actual_document={"value": 1},
    )
    assert outcome.comparison is ComparisonStatus.MISMATCH


def test_int_and_float_numeric_equality():
    spec = make_spec(Operator.EQUALS)
    outcome = evaluate(
        spec,
        case_expected={"value": 2},
        actual_document={"value": 2.0},
    )
    assert outcome.comparison is ComparisonStatus.MATCH


def test_multiset_ignores_order_but_keeps_duplicates():
    spec = make_spec(Operator.MULTISET_EQUALS)
    matched = evaluate(
        spec,
        case_expected={"value": [1, 2, 1]},
        actual_document={"value": [2, 1, 1]},
    )
    assert matched.comparison is ComparisonStatus.MATCH

    mismatched = evaluate(
        spec,
        case_expected={"value": [1, 2, 2]},
        actual_document={"value": [2, 1, 1]},
    )
    assert mismatched.comparison is ComparisonStatus.MISMATCH
    assert "缺少" in (mismatched.detail or "")


def test_multiset_requires_lists():
    """算子与观测类型不符属于任务包/适配契约问题，直接报错而不是静默通过。"""
    spec = make_spec(Operator.MULTISET_EQUALS)
    with pytest.raises(TaskPackageError):
        evaluate(
            spec,
            case_expected={"value": [1]},
            actual_document={"value": 1},
        )


def test_missing_actual_field_is_not_compared():
    spec = make_spec(Operator.EQUALS)
    outcome = evaluate(spec, case_expected={"value": 1}, actual_document={})
    assert outcome.comparison is ComparisonStatus.NOT_COMPARED
    assert "缺少" in (outcome.detail or "")


def test_unchanged_compares_against_pre_observation():
    spec = make_spec(
        Operator.UNCHANGED,
        actual_key="/contacts_after",
        kind=ExpectedSourceKind.PRE_OBSERVATION,
        pointer="/contacts_after",
        observation_key="before",
    )
    same = evaluate(
        spec,
        case_expected={},
        actual_document={"contacts_after": []},
        pre_observations={"before": {"contacts_after": []}},
    )
    assert same.comparison is ComparisonStatus.MATCH

    changed = evaluate(
        spec,
        case_expected={},
        actual_document={"contacts_after": [{"id": 1}]},
        pre_observations={"before": {"contacts_after": []}},
    )
    assert changed.comparison is ComparisonStatus.MISMATCH


def test_unchanged_without_pre_observation_is_not_compared():
    spec = make_spec(
        Operator.UNCHANGED,
        kind=ExpectedSourceKind.PRE_OBSERVATION,
        observation_key="missing_before",
    )
    outcome = evaluate(
        spec,
        case_expected={},
        actual_document={"value": 1},
        pre_observations={},
    )
    assert outcome.comparison is ComparisonStatus.NOT_COMPARED


def test_unknown_normalization_is_package_error():
    with pytest.raises(TaskPackageError):
        normalize("no_such_normalization", 1)


def test_lf_text_normalization_flattens_newlines():
    assert normalize("lf_text_v1", "a\r\nb\rc") == "a\nb\nc"
    assert normalize("identity_v1", "a\r\nb") == "a\r\nb"

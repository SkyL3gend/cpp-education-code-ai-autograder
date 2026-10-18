"""JSON Pointer 解析（RFC 6901 子集，拒绝宽松匹配）。"""

from __future__ import annotations

from cpp_assessor.domain.pointers import MISSING, format_pointer, resolve


def test_root_pointer_returns_whole_document():
    document = {"a": 1}
    assert resolve(document, "") == document


def test_nested_object_and_array():
    document = {"a": {"b": [10, 20, {"c": "值"}]}}
    assert resolve(document, "/a/b/0") == 10
    assert resolve(document, "/a/b/2/c") == "值"


def test_escape_sequences():
    document = {"a/b": {"c~d": 7}}
    assert resolve(document, "/a~1b/c~0d") == 7


def test_missing_key_and_out_of_range():
    document = {"items": [1, 2]}
    assert resolve(document, "/items/2") is MISSING
    assert resolve(document, "/absent") is MISSING


def test_array_index_rejects_leading_zero_and_non_numeric():
    document = {"items": [1, 2, 3]}
    assert resolve(document, "/items/01") is MISSING
    assert resolve(document, "/items/-") is MISSING
    assert resolve(document, "/items/x") is MISSING


def test_scalar_has_no_children():
    assert resolve({"a": 1}, "/a/b") is MISSING


def test_format_pointer_escapes_tokens():
    assert format_pointer(["a/b", "c~d"]) == "/a~1b/c~0d"

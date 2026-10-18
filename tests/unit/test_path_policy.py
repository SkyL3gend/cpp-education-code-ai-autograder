"""提交路径策略：拒绝逃逸出根目录、设备名、大小写冲突等。"""

from __future__ import annotations

import pytest

from cpp_assessor.domain.errors import InvalidInputError
from cpp_assessor.intake.path_policy import (
    ensure_unique_casefold,
    validate_relative_path,
)


def test_accepts_nested_relative_path():
    assert validate_relative_path("src/sub/a.cpp") == "src/sub/a.cpp"
    assert validate_relative_path("a\\b.h") == "a/b.h"


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.cpp",
        "a/../../b.cpp",
        "./a.cpp",
        "a//b.cpp",
        "/etc/passwd",
        "//server/share/a.cpp",
        "C:/tmp/a.cpp",
        "c:relative.cpp",
        "a/b:c.cpp",
        "CON",
        "sub/NUL.txt",
        "a/ ",
        "a/b.",
        "a/\x00b",
    ],
)
def test_rejects_invalid_paths(raw):
    with pytest.raises(InvalidInputError):
        validate_relative_path(raw)


def test_rejects_overlong_path_and_segment():
    with pytest.raises(InvalidInputError):
        validate_relative_path("a" * 121 + ".cpp")
    with pytest.raises(InvalidInputError):
        validate_relative_path("/".join(["dir"] * 80) + "/a.cpp")


def test_casefold_duplicates_are_rejected():
    with pytest.raises(InvalidInputError):
        ensure_unique_casefold(["Src/A.cpp", "src/a.cpp"])
    ensure_unique_casefold(["src/a.cpp", "src/b.cpp"])

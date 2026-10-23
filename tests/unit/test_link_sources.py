"""Include-aware final linking must never guess which business units to omit."""
from __future__ import annotations

import pytest

from cpp_assessor.planning.link_sources import select_link_sources


def test_direct_declared_unit_is_linked_only_through_driver():
    original = ["date.cpp", "other.cc", "src/detail.cxx", "contactbook.h"]
    driver = '#include "date.cpp"\n#include "src/detail.cxx"\n#include "contactbook.h"\n'
    assert select_link_sources(original, driver) == ["other.cc", "contactbook.h"]
    assert original == ["date.cpp", "other.cc", "src/detail.cxx", "contactbook.h"]


def test_whitespace_and_real_comments_around_directive_are_accepted():
    driver = ' /* prefix */ # /* separator */ include /* operand */ "date.cpp" // trailing\n'
    assert select_link_sources(["date.cpp", "other.cpp"], driver) == ["other.cpp"]


@pytest.mark.parametrize("driver", [
    '// #include "date.cpp"\n',
    '/*\n#include "date.cpp"\n*/\n',
    'const char* message = "#include \\\"date.cpp\\\"";\n',
    'const char quote = \'"\'; // #include "date.cpp"\n',
    '#define UNIT "date.cpp"\n#include UNIT\n',
    '#include <date.cpp>\n',
    '#include_next "date.cpp"\n',
    '#include "date.cpp" unexpected_extra_token\n',
    '#include "undeclared.cpp"\n',
    '#include "DATE.cpp"\n',
])
def test_pseudo_macro_or_nonexact_includes_do_not_remove_source(driver):
    assert select_link_sources(["date.cpp"], driver) == ["date.cpp"]


@pytest.mark.parametrize("driver", [
    '#if 1\n#include "date.cpp"\n#endif\n',
    '#if 0\n#else\n#include "date.cpp"\n#endif\n',
    '#ifdef FEATURE\n#ifndef SECOND\n#include "date.cpp"\n#endif\n#elif ALTERNATIVE\n#include "date.cpp"\n#endif\n',
])
def test_even_apparently_true_or_else_conditions_are_not_evaluated(driver):
    assert select_link_sources(["date.cpp"], driver) == ["date.cpp"]


def test_unconditional_after_balanced_condition_only_removes_its_exact_unit():
    driver = '#if 1\n#include "date.cpp"\n#endif\n#include "other.cpp"\n'
    assert select_link_sources(["date.cpp", "other.cpp"], driver) == ["date.cpp"]


@pytest.mark.parametrize("path", ["../date.cpp", "sub/../date.cpp", "/date.cpp", "C:/date.cpp",
    r"C:\date.cpp", r"sub\date.cpp", "./date.cpp", "sub//date.cpp"])
def test_non_normalized_or_absolute_paths_are_not_deduced_even_when_declared(path):
    assert select_link_sources([path, "date.cpp"], f'#include "{path}"\n') == [path, "date.cpp"]


@pytest.mark.parametrize("driver", [
    '#include "date.cpp"\nR"tag(raw text)tag";\n',
    '#include \\\n"date.cpp"\n',
    '#include "date.cpp"\n// continued comment\\\n',
    '#if 1\n#include "date.cpp"\n',
    '#endif\n#include "date.cpp"\n',
    '#else\n#include "date.cpp"\n',
    '%:if 1\n#include "date.cpp"\n%:endif\n',
    '??=if 1\n#include "date.cpp"\n??=endif\n',
    '# /* split\ncomment */ if 0\n#include "date.cpp"\n# /* split\ncomment */ endif\n',
    '/* unterminated comment\n#include "date.cpp"\n',
])
def test_ambiguous_or_unbalanced_preprocessing_keeps_all_units(driver):
    assert select_link_sources(["date.cpp", "other.cpp"], driver) == ["date.cpp", "other.cpp"]


def test_fake_conditionals_in_comments_or_strings_do_not_hide_real_direct_include():
    driver = '// #if 1\nconst char* message = "#ifdef FEATURE";\n#include "date.cpp"\n'
    assert select_link_sources(["date.cpp"], driver) == []

"""Conservative source selection when a driver directly includes a .cpp file.

This only prevents the same declared unit from being compiled twice at final
link time. Original source inventory, snapshots and independent ``-c`` checks
must remain intact. It is not a preprocessor, dependency resolver or safety gate.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Sequence


_CPP_SUFFIXES = {".cpp", ".cc", ".cxx"}
_DIRECTIVE = re.compile(r"^[ \t\v\f]*#[ \t\v\f]*([A-Za-z_][A-Za-z_0-9]*)\b")
_QUOTED_INCLUDE = re.compile(r'^[ \t\v\f]*#[ \t\v\f]*include[ \t\v\f]*"([^"\r\n]+)"[ \t\v\f]*$')


def _safe_unit(path: str) -> bool:
    if not isinstance(path, str) or not path or any(char in path for char in ("\\", ":")) or any(ord(char) < 32 for char in path):
        return False
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    return PurePosixPath(path).suffix.lower() in _CPP_SUFFIXES


def _directive_views(source: str) -> tuple[str, str] | None:
    """Mask comments in both views and string/character literals in code only.

    The original quoted include operand survives in the second view. Newlines
    are retained so a literal/comment cannot invent a preprocessing line.
    Ambiguous raw literals or line splices deliberately decline optimization.
    """
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    if "\\\n" in source:
        return None
    code, uncommented = list(source), list(source)

    def mask(begin: int, end: int, *, comment: bool):
        for index in range(begin, end):
            if source[index] != "\n":
                code[index] = " "
                if comment:
                    uncommented[index] = " "

    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = len(source) if end < 0 else end
            mask(index, end, comment=True)
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                return None
            end += 2
            prefix = "".join(code[source.rfind("\n", 0, index) + 1:index])
            if "\n" in source[index:end] and re.match(r"^[ \t\v\f]*#", prefix):
                # Do not resolve preprocessing-line boundaries inside a
                # multi-line comment after '#'; hidden conditional directives
                # must never make an include appear unconditional.
                return None
            mask(index, end, comment=True)
            index = end
        elif source.startswith('R"', index):
            return None
        elif source[index] in {"\"", "'"}:
            quote, end = source[index], index + 1
            while end < len(source):
                if source[end] == "\\":
                    end += 2
                elif source[end] == quote:
                    end += 1
                    break
                else:
                    end += 1
            else:
                return None
            mask(index, end, comment=False)
            index = end
        else:
            index += 1
    masked = "".join(code)
    # Alternate preprocessing spellings are not interpreted by this helper.
    # Decline instead of misclassifying a conditional using a digraph/trigraph.
    if any(token in masked for token in ("%:", "??=", "??/")):
        return None
    return masked, "".join(uncommented)


def select_link_sources(target_sources: Sequence[str], driver_source: str) -> list[str]:
    """Drop only exact declared C++ units directly included without condition.

    Recognize full literal ``#include "relative/unit.cpp"`` lines, allowing
    ordinary whitespace/comments. Ignore all conditional branches, macros,
    headers, non-normalized/absolute paths and undeclared files. Preserve source
    order and input values. Unknown syntax keeps every source for the compiler
    to diagnose; this helper never guesses or rewrites submitted business code.
    """
    original = list(target_sources)
    views = _directive_views(driver_source)
    if views is None:
        return original
    declared = {path for path in original if _safe_unit(path)}
    included, depth = set(), 0
    for code, literal in zip(views[0].split("\n"), views[1].split("\n")):
        directive = _DIRECTIVE.match(code)
        if directive is None:
            continue
        kind = directive.group(1)
        if kind in {"if", "ifdef", "ifndef"}:
            depth += 1
        elif kind == "endif":
            if depth == 0:
                return original
            depth -= 1
        elif kind in {"else", "elif"}:
            if depth == 0:
                return original
        elif kind == "include" and depth == 0:
            match = _QUOTED_INCLUDE.fullmatch(literal)
            if match and match.group(1) in declared:
                included.add(match.group(1))
    if depth:
        return original
    return [path for path in original if path not in included]

"""Compiler requests are data, never unrestricted compiler argv or host paths."""
from __future__ import annotations

from ..intake.path_policy import validate_relative_path

SAFE_FLAGS = {
    "-std=c++17", "-std=c++20", "-O0", "-O1", "-O2", "-g", "-Wall", "-Wextra",
    "-pedantic", "-finput-charset=UTF-8", "-finput-charset=GBK",
    "-fexec-charset=UTF-8", "-fexec-charset=GBK",
}
SAFE_LIBRARIES = {"user32", "kernel32"}


def validate_build_options(build) -> list[str]:
    problems = []
    if build.artifact_name not in {"subject.exe", "subject.wasm"}:
        problems.append("artifact_name 必须是固定文件名 subject.exe 或 subject.wasm，不能提供路径")
    for flag in build.compiler_flags:
        if flag not in SAFE_FLAGS:
            problems.append(f"编译选项未注册：{flag}")
    if build.defines:
        problems.append("第一版不允许模型设置预处理宏改变目标业务逻辑")
    for library in build.libraries:
        if library not in SAFE_LIBRARIES:
            problems.append(f"链接库未注册：{library}")
    return problems


def relative_path(raw: str) -> str:
    return validate_relative_path(raw)

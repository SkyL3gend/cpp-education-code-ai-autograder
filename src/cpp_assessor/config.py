"""运行时路径与默认配置。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

ASSESSOR_VERSION = "0.3.0"
SCHEMA_VERSION = "1.0"
PROTOCOL_VERSION = "1.0"


def data_dir() -> Path:
    override = os.environ.get("ASSESSOR_HOME")
    return Path(override).resolve() if override else PROJECT_ROOT / "data"


def task_library_dir() -> Path:
    override = os.environ.get("ASSESSOR_TASK_LIBRARY")
    return Path(override).resolve() if override else PROJECT_ROOT / "task_library"


def tasks_dir() -> Path:
    return task_library_dir() / "tasks"


def projects_dir() -> Path:
    return task_library_dir() / "projects"


def shared_dir() -> Path:
    return task_library_dir() / "shared"


def toolchain_id() -> str:
    compiler = shutil.which("g++")
    if compiler is None:
        return "gxx-unavailable"
    return "gxx_minqw64_ucrt_x64"


def compiler_path() -> str | None:
    return shutil.which("g++")


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

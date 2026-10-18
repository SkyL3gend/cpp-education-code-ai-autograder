# -*- coding: utf-8 -*-
"""基准程序实测：真实编译 + 真实运行（需要 g++ 与随附的参考程序）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cpp_assessor.selfcheck import default_reference_source, run_selfcheck

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None or default_reference_source() is None,
    reason="需要 g++ 与 参考基准程序/通讯录_v6最终版.cpp",
)

EXPECTED = {
    "smoke_menu": "PASS",
    "phone_digits": "DEFECT",
    "birthday_today": "PASS",
    "name_search_case": "DEFECT",
    "date_validation": "PASS",
}


def test_selfcheck_reference_program(tmp_path: Path) -> None:
    source = default_reference_source()
    assert source is not None
    result = run_selfcheck(source, data_root=tmp_path, source_kind="reference")
    report = result.report

    assert report.compile.status.value == "SUCCESS", report.compile.stderr_ref
    assert result.report_path.is_file()
    assert len(report.checks) == 5
    verdicts = {check.check_id: check.verdict for check in report.checks}
    assert verdicts == EXPECTED
    assert report.summary.defects == 2
    assert report.summary.not_observed == 0
    assert report.sandbox_attested is False, "本地后端缺网络/文件系统隔离，必须如实标注"

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["selfcheck_id"] == report.selfcheck_id
    for check in report.checks:
        artifact = result.run_dir / check.stdout_ref
        assert artifact.is_file(), f"缺少证据文件：{artifact}"

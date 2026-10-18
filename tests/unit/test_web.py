# -*- coding: utf-8 -*-
"""本地 Web 界面测试：只验证“页面能展示、提交能跑通、导出可用”，判定逻辑由其他测试覆盖。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cpp_assessor.interfaces.web.app import WebSettings, create_app

TASK_REF = "t1_phone_validation@1.0.0"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(WebSettings(backend="mock", data_root=tmp_path / "data"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def local_client(tmp_path: Path) -> TestClient:
    """真实 g++ 后端：mock 后端按 expected 合成观测，测不出缺陷版本。"""
    if shutil.which("g++") is None:
        pytest.skip("未找到 g++，跳过真实后端用例")
    app = create_app(
        WebSettings(
            backend="local",
            allow_unattested=True,
            data_root=tmp_path / "data",
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def _wait_for_run(client: TestClient, run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/runs/{run_id}").json()
        if payload["state"] in {"done", "failed"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"运行未在 {timeout}s 内结束：{run_id}")


def _start_variant_run(client: TestClient, variant: str = "correct") -> str:
    response = client.post(
        "/runs",
        data={"task_ref": TASK_REF, "variant": variant},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    return location.rsplit("/", 1)[-1]


def test_index_lists_tasks(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "t1_phone_validation" in response.text
    assert "t3_date_validation" in response.text
    assert "模拟后端已启用" in response.text


def test_task_page_hides_case_inputs_and_expected(client: TestClient) -> None:
    response = client.get(f"/tasks/{TASK_REF}")
    assert response.status_code == 200
    body = response.text
    assert "功能要求" in body
    assert "c_valid_11_ascii" in body
    # 输入与预期值不下发，避免学生针对用例硬编码。
    assert "13800138000" not in body
    assert "1380013800a" not in body


def test_variant_run_reports_pass_and_exports(client: TestClient) -> None:
    run_id = _start_variant_run(client)
    status = _wait_for_run(client, run_id)
    assert status["state"] == "done"
    assert status["assessment_status"] == "PASS"

    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "PASS" in page.text
    assert "c_non_digit_11" in page.text

    report_json = client.get(f"/runs/{run_id}/report.json")
    assert report_json.status_code == 200
    assert report_json.json()["assessment_status"] == "PASS"

    report_csv = client.get(f"/runs/{run_id}/report.csv")
    assert report_csv.status_code == 200
    assert report_csv.text.startswith("\ufeff")
    assert "run_id,task_id" in report_csv.text


def test_faulty_variant_reports_fail(local_client: TestClient) -> None:
    run_id = _start_variant_run(local_client, variant="faulty")
    status = _wait_for_run(local_client, run_id, timeout=120.0)
    assert status["assessment_status"] == "FAIL"
    page = local_client.get(f"/runs/{run_id}")
    assert "FAIL" in page.text


def test_create_run_requires_submission_or_variant(client: TestClient) -> None:
    response = client.post(
        "/runs", data={"task_ref": TASK_REF}, follow_redirects=False
    )
    assert response.status_code == 400
    assert "请上传提交文件" in response.text


def test_unknown_task_returns_404(client: TestClient) -> None:
    assert client.get("/tasks/t999_missing@1.0.0").status_code == 404
    assert client.get("/runs/run_does_not_exist").status_code == 404


def test_cancel_writes_flag_file(client: TestClient, tmp_path: Path) -> None:
    run_id = _start_variant_run(client)
    _wait_for_run(client, run_id)
    response = client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
    assert response.status_code == 303
    assert (tmp_path / "data" / "runs" / run_id / "CANCEL").is_file()

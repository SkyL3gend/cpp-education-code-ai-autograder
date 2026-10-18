# -*- coding: utf-8 -*-
"""本地 Web 界面测试：只验证“页面能展示、提交能跑通、导出可用”，判定逻辑由其他测试覆盖。"""

from __future__ import annotations

import io
import shutil
import time
import zipfile
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


def test_modes_page_offers_standard_and_disabled_llm(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "基准模式" in body
    assert "LLM 模式" in body
    assert "暂未开放" in body
    assert 'href="/standard"' in body
    # LLM 模式按钮是禁用状态，没有可用链接。
    assert 'href="/llm"' not in body


def test_index_lists_tasks(client: TestClient) -> None:
    response = client.get("/standard")
    assert response.status_code == 200
    assert "t1_phone_validation" in response.text
    assert "t3_date_validation" in response.text
    assert "模拟后端已启用" in response.text


def test_language_switch_renders_english(client: TestClient) -> None:
    response = client.get("/?lang=en")
    assert response.status_code == 200
    assert "lang=\"en\"" in response.text
    assert "Choose an assessment mode" in response.text
    assert "Standard mode" in response.text
    # 语言只影响展示：任务编号与枚举值仍是英文代码。
    assert "Accepted" not in response.text or "PASS" in response.text


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
    assert "Accepted" in page.text
    assert "通过" in page.text
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
    assert "Rejected" in page.text
    assert "未通过" in page.text


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


def test_queued_run_page_is_not_404(client: TestClient) -> None:
    """303 重定向可能早于后台线程建库：已排队的 run 不能报“找不到”。"""
    client.app.state.pending["run_queued_demo"] = "queued"
    response = client.get("/runs/run_queued_demo")
    assert response.status_code == 200
    assert "任务已排队" in response.text


def test_invalid_submission_renders_report_instead_of_crashing(
    client: TestClient, tmp_path: Path
) -> None:
    submission = tmp_path / "unrelated.cpp"
    submission.write_text("int main() { return 0; }\n", encoding="utf-8", newline="\n")
    response = client.post(
        "/runs",
        data={"task_ref": TASK_REF},
        files={"submission": ("unrelated.cpp", submission.read_bytes())},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    run_id = response.headers["location"].rsplit("/", 1)[-1]

    status = _wait_for_run(client, run_id)
    assert status["state"] == "done"
    assert status["execution_status"] == "INVALID_INPUT"
    assert status["assessment_status"] == "NOT_ASSESSED"

    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "INVALID_INPUT" in page.text
    assert "参考基准程序" in page.text


def test_run_id_with_dotdot_is_rejected(client: TestClient) -> None:
    assert client.get("/runs/bad..id").status_code == 400


def test_base_project_download_and_submission_guidance(client: TestClient) -> None:
    page = client.get(f"/tasks/{TASK_REF}")
    assert "/base.zip" in page.text
    assert "不能直接提交" in page.text

    archive = client.get(f"/tasks/{TASK_REF}/base.zip")
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as handle:
        names = handle.namelist()
    assert any(name.endswith("validators.cpp") for name in names)
    assert any(name.endswith("contactbook.h") for name in names)


def _wait_for_batch(client: TestClient, batch_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/batches/{batch_id}").json()
        if payload["status"] in {"completed", "cancelled"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"批次未在 {timeout}s 内结束：{batch_id}")


def _zip_project(tmp_path: Path, name: str) -> Path:
    from cpp_assessor.tasks.loader import load_task

    project_root = load_task(TASK_REF).project_root
    target = tmp_path / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(project_root.iterdir()):
            if path.is_file() and path.suffix.lower() in {".cpp", ".h", ".hpp"}:
                archive.write(path, path.name)
    return target


def test_batch_runs_programs_times_tasks(client: TestClient, tmp_path: Path) -> None:
    zips = [_zip_project(tmp_path, "p1.zip"), _zip_project(tmp_path, "p2.zip")]
    files = [("submissions", (path.name, path.read_bytes(), "application/zip")) for path in zips]
    response = client.post(
        "/batch",
        data={"tasks": [TASK_REF, "t2_birthday_today@1.0.0"]},
        files=files,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    batch_id = response.headers["location"].rsplit("/", 1)[-1]

    status = _wait_for_batch(client, batch_id)
    assert status["status"] == "completed"
    assert status["progress"]["total"] == 4
    assert status["progress"]["done"] == 4
    assert status["progress"]["errors"] == 0

    page = client.get(f"/batch/{batch_id}")
    assert page.status_code == 200
    assert "t1_phone_validation@1.0.0" in page.text
    assert "t2_birthday_today@1.0.0" in page.text
    assert "Accepted" in page.text
    # 每个格子都能点进单次运行报告。
    entries = client.get(f"/batch/{batch_id}").text.count('href="/runs/run_')
    assert entries >= 4

    listing = client.get("/batch")
    assert listing.status_code == 200
    assert batch_id in listing.text


def test_batch_rejects_non_zip(client: TestClient) -> None:
    response = client.post(
        "/batch",
        data={"tasks": [TASK_REF]},
        files={"submissions": ("p1.cpp", b"int main(){return 0;}\n", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert ".zip" in response.text


def test_reference_selfcheck_page_and_run(local_client: TestClient) -> None:
    """基准程序实测页：GET 可打开，POST 用默认参考程序跑通并渲染结论。"""
    from cpp_assessor.selfcheck import default_reference_source

    if default_reference_source() is None:
        pytest.skip("缺少随附的参考基准程序")

    page = local_client.get("/reference")
    assert page.status_code == 200
    assert "基准程序实测" in page.text
    assert "不产生" in page.text

    response = local_client.post(
        "/reference", files={"source": (None, "")}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/reference/sc_")

    result = local_client.get(location)
    assert result.status_code == 200
    assert "手机号必须为 11 位数字" in result.text
    assert "不符" in result.text

    report = local_client.get(f"{location}/report.json")
    assert report.status_code == 200
    payload = report.json()
    verdicts = {item["check_id"]: item["verdict"] for item in payload["checks"]}
    assert verdicts["phone_digits"] == "DEFECT"
    assert verdicts["name_search_case"] == "DEFECT"
    assert verdicts["smoke_menu"] == "PASS"

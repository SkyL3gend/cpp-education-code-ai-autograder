# -*- coding: utf-8 -*-
"""本地 Web 界面测试：只验证“页面能展示、提交能跑通、导出可用”，判定逻辑由其他测试覆盖。"""

from __future__ import annotations

import io
import copy
import csv
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cpp_assessor.domain.canonical import hash_json
from cpp_assessor.domain.models import AssessmentReport
from cpp_assessor.interfaces.web.app import (WebSettings, _read_target_challenges,
                                           _read_verifier_calibration, create_app)
from cpp_assessor.planning.pilot_tasks import get_pilot_profile
from cpp_assessor.tasks.loader import load_task

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


def test_home_offers_only_llm_assessment(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "AI 代码测评" in body
    assert f'href="/llm/tasks/{TASK_REF}"' in body
    for hidden in ('href="/standard"', 'href="/batch"', 'href="/reference"', '基准模式'):
        assert hidden not in body
    task_page = client.get(f"/llm/tasks/{TASK_REF}").text
    assert 'action="/llm/runs"' in task_page
    assert 'action="/runs"' not in task_page
    assert '#demo' not in task_page
    assert 'name="variant"' not in task_page
    assert '参照版本' not in task_page


def test_llm_page_explains_key_and_lists_tasks(client: TestClient) -> None:
    response = client.get("/llm")
    assert response.status_code == 200
    body = response.text
    assert "API Key" in body
    assert "t1_phone_validation" in body
    page = client.get("/llm/tasks/t1_phone_validation@1.0.0")
    assert page.status_code == 200
    assert 'name="api_key"' in page.text
    assert 'action="/llm/runs"' in page.text
    assert 'type="password"' in page.text


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
    assert "Choose a task for AI code assessment" in response.text
    assert "Standard mode" not in response.text
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


def test_variant_run_retains_trial_status_and_exports(client: TestClient) -> None:
    run_id = _start_variant_run(client)
    status = _wait_for_run(client, run_id)
    assert status["state"] == "done"
    assert status["assessment_status"] == "INCONCLUSIVE"

    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "Inconclusive" in page.text
    assert "尚未完成权威目标校准或人工签审" in page.text
    assert "c_non_digit_11" in page.text

    report_json = client.get(f"/runs/{run_id}/report.json")
    assert report_json.status_code == 200
    assert report_json.json()["assessment_status"] == "INCONCLUSIVE"
    assert report_json.json()["grade_eligible"] is False

    report_csv = client.get(f"/runs/{run_id}/report.csv")
    assert report_csv.status_code == 200
    assert report_csv.text.startswith("\ufeff")
    assert "run_id,task_id" in report_csv.text


def test_faulty_variant_reports_fail(local_client: TestClient) -> None:
    run_id = _start_variant_run(local_client, variant="faulty")
    status = _wait_for_run(local_client, run_id, timeout=120.0)
    assert status["assessment_status"] == "INCONCLUSIVE"
    page = local_client.get(f"/runs/{run_id}")
    assert "Inconclusive" in page.text
    assert "不确定" in page.text


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


def test_unmatched_upload_is_rejected_without_legacy_pass(local_client,tmp_path):
    response=local_client.post("/runs",data={"task_ref":TASK_REF},files={"submission":("unrelated.cpp",b"int main(){return 0;}")},follow_redirects=False)
    run_id=response.headers["location"].rsplit("/",1)[-1]
    status=_wait_for_run(local_client,run_id)
    assert status["assessment_status"]=="NOT_ASSESSED"
    assert status["execution_status"]=="INVALID_INPUT"
    report=local_client.get(f"/runs/{run_id}/report.json").json()
    assert report["counts"]["attempted_cases"]==0


def test_run_id_with_dotdot_is_rejected(client: TestClient) -> None:
    assert client.get("/runs/bad..id").status_code == 400


def test_base_project_download_and_submission_guidance(client: TestClient) -> None:
    page = client.get(f"/tasks/{TASK_REF}")
    assert "/base.zip" in page.text
    assert "LLM" in page.text

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
    assert "Inconclusive" in page.text
    # 每个格子都能点进单次运行报告。
    entries = client.get(f"/batch/{batch_id}").text.count('href="/runs/run_')
    assert entries >= 4

    listing = client.get("/batch")
    assert listing.status_code == 200
    assert batch_id in listing.text


def test_batch_accepts_zip_and_single_file_programs(client: TestClient, tmp_path: Path) -> None:
    """批量不再只收 zip：一个程序可以是压缩包（多文件带依赖），也可以是单个源码文件。

    每个程序落成独立文件夹：source/ 放被测代码、verifier/ 留给该程序专属判定代码。
    """
    archive = tmp_path / "nested.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("src/main.cpp", "int main(){return 0;}\n")
        handle.writestr("src/util.h", "#pragma once\n")
    single = tmp_path / "standalone.cpp"
    single.write_text("int main(){return 0;}\n", encoding="utf-8", newline="\n")

    response = client.post(
        "/batch",
        data={"tasks": [TASK_REF]},
        files=[
            ("submissions", (archive.name, archive.read_bytes(), "application/zip")),
            ("submissions", (single.name, single.read_bytes(), "text/plain")),
        ],
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    batch_id = response.headers["location"].rsplit("/", 1)[-1]

    status = _wait_for_batch(client, batch_id)
    assert status["status"] == "completed"
    assert status["progress"]["total"] == 2
    assert status["progress"]["done"] == 2
    assert status["progress"]["errors"] == 0

    root = tmp_path / "data" / "batches" / batch_id
    assert (root / "programs" / "p1" / "source" / "src" / "main.cpp").is_file()
    assert (root / "programs" / "p1" / "source" / "src" / "util.h").is_file()
    assert (root / "programs" / "p1" / "original.zip").is_file()
    assert (root / "programs" / "p2" / "source" / "standalone.cpp").is_file()
    for program_id in ("p1", "p2"):
        assert (root / "programs" / program_id / "verifier").is_dir()
        assert (root / "programs" / program_id / "program.json").is_file()
    assert not (root / "programs" / "p1" / "incoming").exists()

    batch = json.loads((root / "batch.json").read_text(encoding="utf-8"))
    assert [item["source"] for item in batch["programs"]] == [
        "programs/p1/source",
        "programs/p2/source",
    ]
    assert [item["kind"] for item in batch["programs"]] == ["archive", "source"]

    page = client.get(f"/batch/{batch_id}")
    assert page.status_code == 200
    assert "nested.zip" in page.text
    assert "standalone.cpp" in page.text

    # 单文件程序走观察型适配：报告里出现 single_file 用例。
    single_entry = next(
        entry for entry in batch["entries"] if entry["program_id"] == "p2"
    )
    run_page = client.get(f"/runs/{single_entry['run_id']}")
    assert run_page.status_code == 200
    assert "未测评" in run_page.text


def test_batch_rejects_unsupported_file(client: TestClient) -> None:
    response = client.post(
        "/batch",
        data={"tasks": [TASK_REF]},
        files={"submissions": ("notes.txt", b"hello\n", "text/plain")},
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


def _supplemental_web_record(tmp_path: Path, *, defect: bool = False):
    """Synthetic saved evidence: no model, compiler or assessment is invoked."""
    run_id = "run_saved_supplemental"
    settings = WebSettings(backend="mock", data_root=tmp_path / "data")
    root = settings.data_root / "runs" / run_id
    (root / "plans").mkdir(parents=True)
    (root / "evidence" / "target_challenges").mkdir(parents=True)
    formal_cases = ["c_valid_11_ascii", "c_short_10", "c_long_12", "c_non_digit_11"]
    report = AssessmentReport.model_validate({
        "schema_version": "1.0", "run_id": run_id, "mode": "llm",
        "execution_status": "COMPLETED", "assessment_status": "INCONCLUSIVE",
        "grade_eligible": False, "grade_eligible_reasons": ["VALIDATION_LEVEL_CHECKED"],
        "validation_level": "CHECKED",
        "coverage": {"planned_cases": formal_cases, "completed_cases": formal_cases,
            "missed_cases": [], "planned_partitions": [], "covered_partitions": [],
            "missing_partitions": [], "planned_assertions": [], "executed_assertions": [],
            "missing_assertions": [], "complete": True},
        "case_results": [],
        "counts": {"required_cases": 4, "attempted_cases": 4, "completed_cases": 4,
            "matched_cases": 4, "mismatched_cases": 0, "not_compared_cases": 0,
            "required_assertions": 0, "matched_assertions": 0,
            "mismatched_assertions": 0, "not_compared_assertions": 0},
        "evidence_refs": [],
        "provenance": {"execution_backend": "wasm_pilot", "sandbox_attested": False,
            "task_id": "t1_phone_validation", "task_version": "1.0.0",
            "task_package_hash": "c" * 64, "submission_id": "sub_saved",
            "toolchain_id": "wasi_test", "started_at": "2026-10-22T12:00:00Z",
            "finished_at": "2026-10-22T12:00:01Z", "assessor_version": "test",
            "protocol_version": "1.0"},
    })
    frozen = {"task_hash": "c" * 64, "submission": {"submission_id": "sub_saved"},
        "bundle": {"task_id": "t1_phone_validation", "task_version": "1.0.0",
            "build": {"artifact_name": "subject.wasm"}}}
    replay = {"environment_digest": "a" * 64,
        "file_hashes": {"generated/build/subject.wasm": "b" * 64}}
    labels = [("challenge_prefix_2", "11 位号码以 2 开头", "23800138000"),
              ("challenge_empty", "空手机号", ""),
              ("challenge_fullwidth", "全角数字手机号", "１３８００１３８０００")]
    rows = [{"case_id": identifier, "label": label,
        "input": {"candidate": {"phone": phone}}, "expected": {"created": False},
        "actual": {"created": defect and index == 0},
        "comparison": "MISMATCH" if defect and index == 0 else "MATCH",
        "termination": "EXITED", "protocol_status": "OK", "detail": None}
        for index, (identifier, label, phone) in enumerate(labels)]
    candidate = {"schema_version": "1.0", "policy_version": "t1-target-challenges-v1",
        "run_id": run_id, "purpose": "SUPPLEMENTAL_RULE_CHECK", "backend_id": "wasm_pilot",
        "task_package_hash": frozen["task_hash"], "verifier_bundle_hash": hash_json(frozen["bundle"]),
        "environment_digest": replay["environment_digest"], "executable_hash": "b" * 64,
        "status": "COMPLETED", "passed": not defect, "defect_detected": defect,
        "grade_eligible": False, "model_calls": 0, "challenge_results": rows,
        "challenge_counts": {"required": 3, "attempted": 3, "matched": 2 if defect else 3,
            "mismatched": 1 if defect else 0, "not_compared": 0}}
    (root / "objective_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    _write_supplemental_web_record(root, candidate, frozen, replay)
    return settings, report, root, candidate, frozen, replay


def _write_supplemental_web_record(root, candidate, frozen, replay):
    candidate["record_hash"] = hash_json({key: value for key, value in candidate.items() if key != "record_hash"})
    frozen_bytes = json.dumps(frozen, ensure_ascii=False).encode("utf-8")
    replay["file_hashes"]["plans/frozen_plan.json"] = hashlib.sha256(frozen_bytes).hexdigest()
    (root / "plans/frozen_plan.json").write_bytes(frozen_bytes)
    (root / "plans/replay_manifest.json").write_text(json.dumps(replay), encoding="utf-8")
    (root / "evidence/target_challenges/target_challenges.json").write_text(json.dumps(candidate), encoding="utf-8")


def test_target_challenges_display_saved_environment_without_current_backend(tmp_path, monkeypatch):
    settings, report, _, candidate, _, _ = _supplemental_web_record(tmp_path)
    def no_current_backend(*args, **kwargs):
        raise AssertionError("Historical evidence must not consult the current backend")
    monkeypatch.setattr("cpp_assessor.interfaces.web.app.make_backend", no_current_backend)
    assert _read_target_challenges(settings, report.run_id, report) == candidate
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}")
    assert page.status_code == 200
    assert "3 项补充规则均符合预期" in page.text
    assert "11 位号码以 2 开头" in page.text
    assert "空手机号" in page.text and "全角数字手机号" in page.text


@pytest.mark.parametrize("binding", ["run", "task", "bundle", "environment", "executable", "backend", "submission", "task_version", "frozen_bytes", "record_hash"])
def test_target_challenges_reject_changed_saved_binding(tmp_path, binding):
    settings, report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path)
    if binding in {"run", "task", "bundle", "environment", "executable", "backend"}:
        key = {"run": "run_id", "task": "task_package_hash", "bundle": "verifier_bundle_hash",
            "environment": "environment_digest", "executable": "executable_hash", "backend": "backend_id"}[binding]
        candidate[key] = "run_other" if binding == "run" else "local" if binding == "backend" else "d" * 64
    elif binding == "submission":
        frozen["submission"]["submission_id"] = "sub_other"
    elif binding == "task_version":
        frozen["bundle"]["task_version"] = "2.0.0"
        candidate["verifier_bundle_hash"] = hash_json(frozen["bundle"])
    _write_supplemental_web_record(root, candidate, frozen, replay)
    if binding == "frozen_bytes":
        with (root / "plans/frozen_plan.json").open("ab") as handle:
            handle.write(b" ")
    elif binding == "record_hash":
        candidate["challenge_results"][0]["actual"]["created"] = True
        (root / "evidence/target_challenges/target_challenges.json").write_text(json.dumps(candidate), encoding="utf-8")
    assert _read_target_challenges(settings, report.run_id, report) is None


@pytest.mark.parametrize("malformation", ["counts", "passed", "defect_detected", "duplicate_case", "unknown_case", "unknown_status"])
def test_target_challenges_reject_inconsistent_summary(tmp_path, malformation):
    settings, report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path)
    if malformation == "counts":
        candidate["challenge_counts"]["mismatched"] = 1
    elif malformation in {"passed", "defect_detected"}:
        candidate[malformation] = not candidate[malformation]
    elif malformation == "duplicate_case":
        candidate["challenge_results"][1] = copy.deepcopy(candidate["challenge_results"][0])
    elif malformation == "unknown_case":
        candidate["challenge_results"][0]["case_id"] = "challenge_other"
    else:
        candidate["status"] = "TRUSTED"
    _write_supplemental_web_record(root, candidate, frozen, replay)
    assert _read_target_challenges(settings, report.run_id, report) is None


@pytest.mark.parametrize("key", ["environment_digest", "executable_hash"])
def test_target_challenges_reject_missing_digest_even_if_manifest_agrees(tmp_path, key):
    settings, report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path)
    candidate[key] = None
    if key == "environment_digest":
        replay[key] = None
    else:
        replay["file_hashes"]["generated/build/subject.wasm"] = None
    _write_supplemental_web_record(root, candidate, frozen, replay)
    assert _read_target_challenges(settings, report.run_id, report) is None


def test_target_challenges_new_defect_is_separate_from_four_formal_cases(tmp_path):
    settings, report, _, _, _, _ = _supplemental_web_record(tmp_path, defect=True)
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}")
        exported = client.get(f"/runs/{report.run_id}/report.json").json()
    assert page.status_code == 200
    assert "请先查看“补充规则检查”中的输入与实际结果" in page.text
    assert "发现 1 项必测用例之外的差异" in page.text
    assert "4 项符合预期，0 项不符合预期" in page.text
    assert "不改变下方 4 项必测用例计数" in page.text
    assert 'id="supplemental-checks"' in page.text
    assert "23800138000" in page.text
    assert exported["counts"] == report.counts.model_dump(mode="json")
    assert exported["grade_eligible"] is False


def test_target_challenges_partial_result_cannot_claim_a_defect(tmp_path):
    settings, report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path, defect=True)
    candidate.update(status="CANCELLED", passed=False, defect_detected=False)
    candidate["challenge_results"] = candidate["challenge_results"][:1]
    candidate["challenge_counts"] = {"required": 3, "attempted": 1, "matched": 0, "mismatched": 1, "not_compared": 2}
    _write_supplemental_web_record(root, candidate, frozen, replay)
    assert _read_target_challenges(settings, report.run_id, report) == candidate
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}")
    assert "补充检查尚未获得完整可比较的结果" in page.text
    assert "发现 1 项必测用例之外的差异" not in page.text


def _birthday_web_record(tmp_path: Path, *, defect: bool = False):
    settings, old_report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path)
    task = load_task("t2_birthday_today@1.0.0")
    profile = get_pilot_profile(task.task_id, task.version)
    payload = old_report.model_dump(mode="json")
    payload["provenance"].update(task_id=task.task_id, task_version=task.version,
                                 task_package_hash=task.package_hash)
    payload["counts"].update(required_cases=6, attempted_cases=6, completed_cases=6, matched_cases=6)
    payload["coverage"].update(planned_cases=list(profile.formal_case_ids), completed_cases=list(profile.formal_case_ids))
    payload["case_results"] = [{
        "schema_version": "1.0", "case_id": case.case_id, "attempt_id": "attempt_" + case.case_id,
        "purpose": "ASSESSMENT", "comparison": "MATCH", "termination": "EXITED", "protocol_status": "OK",
        "failed_assertions": [], "evidence_refs": [], "assertion_results": [
            {"schema_version": "1.0", "assertion_id": assertion_id, "case_id": case.case_id,
             "operator": "MULTISET_EQUALS", "comparison": "MATCH", "expected": case.expected[field],
             "actual": case.expected[field]} for assertion_id, field in (("a_names", "names"), ("a_contacts", "contacts_after"))
        ]} for case in task.cases.values()]
    report = AssessmentReport.model_validate(payload)
    frozen["task_hash"] = task.package_hash
    frozen["bundle"].update(task_id=task.task_id, task_version=task.version)
    frozen["submission"]["snapshot_hash"] = "e" * 64
    rows = []
    for index, (case_id, title) in enumerate(profile.challenge_labels.items()):
        expected = {"names": ["同名", "同名"], "contacts_after": [{"id": 7, "name": "原联系人"}]}
        actual = copy.deepcopy(expected)
        if defect and index == 0:
            actual["names"] = []
        rows.append({"case_id": case_id, "label": title,
            "input": {"today": {"year": 2026, "month": 12, "day": 31}, "days_ahead": 1},
            "expected": expected, "actual": actual,
            "assertion_results": [{"assertion_id": "a_contacts", "comparison": "MATCH"}],
            "comparison": "MISMATCH" if defect and index == 0 else "MATCH",
            "termination": "EXITED", "protocol_status": "OK", "detail": None})
    candidate.update(policy_version=profile.challenge_policy, task_package_hash=task.package_hash,
        verifier_bundle_hash=hash_json(frozen["bundle"]), challenge_results=rows, passed=not defect,
        defect_detected=defect, challenge_counts={"required": 3, "attempted": 3,
            "matched": 2 if defect else 3, "mismatched": 1 if defect else 0, "not_compared": 0})
    (root / "objective_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    _write_supplemental_web_record(root, candidate, frozen, replay)
    return settings, report, root, candidate, frozen, replay


def _date_web_record(tmp_path: Path, *, defect: bool = False):
    """Saved T3 presentation evidence; the assertions come from the task profile."""
    from cpp_assessor.planning.target_calibration import supplemental_cases

    settings, old_report, root, candidate, frozen, replay = _supplemental_web_record(tmp_path)
    task = load_task("t3_date_validation@1.0.0")
    profile = get_pilot_profile(task.task_id, task.version)
    variant = "faulty" if defect else "correct"

    def observations(case):
        signatures = profile.expected_assertions(variant, case.case_id)
        actual = copy.deepcopy(case.expected)
        if signatures["a_created"] == "MISMATCH":
            actual["created"] = True
            contact = copy.deepcopy(case.input["candidate"])
            date = contact["birthday"]
            contact["birthday"] = f"{date['year']:04d}-{date['month']:02d}-{date['day']:02d}"
            contact["id"] = len(actual["contacts_after"]) + 1
            actual["contacts_after"].append(contact)
        assertions = [{"schema_version": "1.0", "assertion_id": assertion_id, "case_id": case.case_id,
            "operator": "EQUALS" if field == "created" else "MULTISET_EQUALS",
            "comparison": signatures[assertion_id], "expected": case.expected[field], "actual": actual[field]}
            for assertion_id, field in (("a_created", "created"), ("a_contacts", "contacts_after"))]
        return actual, assertions, "MISMATCH" if "MISMATCH" in signatures.values() else "MATCH"

    payload = old_report.model_dump(mode="json")
    payload["provenance"].update(task_id=task.task_id, task_version=task.version,
                                 task_package_hash=task.package_hash)
    formal = []
    for case in task.cases.values():
        _, assertions, comparison = observations(case)
        formal.append({"schema_version": "1.0", "case_id": case.case_id, "attempt_id": "attempt_" + case.case_id,
            "purpose": "ASSESSMENT", "comparison": comparison, "termination": "EXITED", "protocol_status": "OK",
            "failed_assertions": [item["assertion_id"] for item in assertions if item["comparison"] == "MISMATCH"],
            "evidence_refs": [], "assertion_results": assertions})
    mismatch = sum(case["comparison"] == "MISMATCH" for case in formal)
    payload["counts"].update(required_cases=6, attempted_cases=6, completed_cases=6,
        matched_cases=6 - mismatch, mismatched_cases=mismatch, required_assertions=12,
        matched_assertions=12 - mismatch * 2, mismatched_assertions=mismatch * 2)
    payload["coverage"].update(planned_cases=list(profile.formal_case_ids), completed_cases=list(profile.formal_case_ids))
    payload["case_results"] = formal
    report = AssessmentReport.model_validate(payload)
    frozen["task_hash"] = task.package_hash
    frozen["bundle"].update(task_id=task.task_id, task_version=task.version)
    frozen["submission"]["snapshot_hash"] = "e" * 64
    rows = []
    for case in supplemental_cases(task):
        actual, assertions, comparison = observations(case)
        rows.append({"case_id": case.case_id, "label": profile.challenge_labels[case.case_id],
            "input": case.input, "expected": case.expected, "actual": actual, "assertion_results": assertions,
            "comparison": comparison, "termination": "EXITED", "protocol_status": "OK", "detail": None})
    mismatch = sum(row["comparison"] == "MISMATCH" for row in rows)
    required = len(profile.supplemental_case_ids)
    candidate.update(policy_version=profile.challenge_policy, task_package_hash=task.package_hash,
        verifier_bundle_hash=hash_json(frozen["bundle"]), challenge_results=rows,
        passed=not mismatch, defect_detected=bool(mismatch), challenge_counts={"required": required,
        "attempted": required, "matched": required - mismatch, "mismatched": mismatch, "not_compared": 0})
    (root / "objective_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    _write_supplemental_web_record(root, candidate, frozen, replay)
    return settings, report, root, candidate, frozen, replay


def _add_web_calibration(report, root, challenges, frozen, replay, *, passed=True):
    profile = get_pilot_profile(report.provenance.task_id, report.provenance.task_version)
    frozen["submission"]["snapshot_hash"] = "e" * 64
    calibration = {"schema_version": "1.0", "policy_version": profile.calibration_policy,
        "backend_id": "wasm_pilot", "task_package_hash": report.provenance.task_package_hash,
        "source_snapshot_hash": "e" * 64, "verifier_bundle_hash": hash_json(frozen["bundle"]),
        "environment_digest": replay["environment_digest"], "passed": passed,
        "status": "COMPLETED", "qualification": profile.qualification if passed else "NOT_CALIBRATED",
        "formal_case_ids": list(profile.formal_case_ids), "supplemental_case_ids": list(profile.challenge_labels),
        "grade_eligible": False, "verifier_validation_level": "CHECKED",
        "outcomes": [{"variant_id": variant, "compile_status": "SUCCESS", "passed": passed,
            "cases": [{"case_id": case_id, "passed": passed, "termination": "EXITED", "protocol_status": "OK",
                       "assertions": profile.expected_assertions(variant, case_id),
                       "initial_contacts_preserved": True, "defect_signature_verified": True}
                      for case_id in profile.formal_case_ids + profile.supplemental_case_ids]}
                     for variant in ("correct", "faulty")],
        "scope": profile.scope, "reason": None if passed else "独立正确夹具结果不符合预期"}
    _write_web_calibration(root, calibration, challenges, frozen, replay)
    return calibration


def _write_web_calibration(root, calibration, challenges, frozen, replay):
    calibration["record_hash"] = hash_json({key: value for key, value in calibration.items() if key != "record_hash"})
    raw = json.dumps(calibration, ensure_ascii=False).encode("utf-8")
    (root / "plans/target_calibration.json").write_bytes(raw)
    replay["file_hashes"]["plans/target_calibration.json"] = hashlib.sha256(raw).hexdigest()
    _write_supplemental_web_record(root, challenges, frozen, replay)


def test_pilot_ui_offers_exact_registered_uploads_without_development_choices(tmp_path):
    settings = WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        page = client.get("/").text
        assert "/llm/tasks/t1_phone_validation@1.0.0" in page
        assert "/llm/tasks/t2_birthday_today@1.0.0" in page
        assert "/llm/tasks/t3_date_validation@1.0.0" in page
        for task_id in ("t1_phone_validation", "t2_birthday_today", "t3_date_validation"):
            form = client.get(f"/llm/tasks/{task_id}@1.0.0").text
            assert 'action="/llm/runs"' in form
            assert 'name="submission"' in form
            assert 'name="variant"' not in form and 'href="/standard"' not in form
            assert "上传测评暂不可用" not in form
            assert 'fieldset class="form-fields" disabled' not in form
        unsupported = client.get("/llm/tasks/t4_name_search@1.0.0").text
        assert "当前环境暂不支持上传测评" in unsupported
        assert 'fieldset class="form-fields" disabled' in unsupported


@pytest.mark.parametrize("task_ref", ["t2_birthday_today", "t2_birthday_today@1.0.0"])
def test_birthday_upload_queues_exact_version_without_model_or_compiler(tmp_path, monkeypatch, task_ref):
    seen = []
    monkeypatch.setattr("cpp_assessor.interfaces.web.app.execute_llm_run", lambda options, settings: seen.append(options))
    settings = WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": task_ref, "api_key": "synthetic-test-key"},
                               files={"submission": ("birthday.cpp", b"int main(){}")}, follow_redirects=False)
        assert response.status_code == 303
        app.state.executor.shutdown(wait=True)
    assert len(seen) == 1
    assert seen[0].task_ref == "t2_birthday_today@1.0.0"
    assert seen[0].submission.name == "birthday.cpp"


@pytest.mark.parametrize("task_ref", ["t2_birthday_today@2.0.0", "t2_birthday_today@1.0.1",
                                      "t3_date_validation@1.0.1", "t3_date_validation@2.0.0",
                                      "t4_name_search@1.0.0", "t2_birthday_today@"])
def test_pilot_rejects_unopened_task_or_version_before_queueing(tmp_path, task_ref):
    settings = WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": task_ref, "api_key": "test"},
                               files={"submission": ("birthday.cpp", b"int main(){}")})
        assert response.status_code == 400
        assert "当前仅开放" in response.text
        assert not client.app.state.pending


def test_birthday_result_shows_six_formal_and_three_supplemental_observations(tmp_path, monkeypatch):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path, defect=True)
    calibration = _add_web_calibration(report, root, candidate, frozen, replay)
    monkeypatch.setattr("cpp_assessor.interfaces.web.app.make_backend", lambda *_: pytest.fail("Must use saved environment"))
    assert _read_target_challenges(settings, report.run_id, report) == candidate
    assert _read_verifier_calibration(settings, report.run_id, report) == calibration
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}")
        exported = client.get(f"/runs/{report.run_id}/report.json").json()
    assert page.status_code == 200
    assert "6 项符合预期，0 项不符合预期" in page.text
    assert "不改变下方 6 项必测用例计数" in page.text
    assert "发现 1 项必测用例之外的差异" in page.text
    assert "跨年提醒窗口" in page.text and "闰年二月边界" in page.text
    assert "2026-10-18" in page.text and "2026-12-31" in page.text
    assert "预期生日名单：" in page.text and "实际生日名单：" in page.text
    assert "张伟、周八" in page.text and "同名、同名" in page.text
    assert "内容完整保留" in page.text
    assert "查看完整断言与 JSON" in page.text
    assert "输入手机号" not in page.text and "空号码" not in page.text
    assert exported["counts"] == report.counts.model_dump(mode="json")
    assert exported["grade_eligible"] is False


@pytest.mark.parametrize("field", ["policy", "case_id", "task", "task_version", "report_version", "assertions"])
def test_birthday_supplemental_rejects_cross_task_or_version_record(tmp_path, field):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path)
    if field == "policy":
        candidate["policy_version"] = "t1-target-challenges-v1"
    elif field == "case_id":
        candidate["challenge_results"][0]["case_id"] = "challenge_prefix_2"
    elif field in {"task", "task_version"}:
        frozen["bundle"]["task_id" if field == "task" else "task_version"] = "t3_date_validation" if field == "task" else "1.0.1"
        candidate["verifier_bundle_hash"] = hash_json(frozen["bundle"])
    elif field == "report_version":
        report.provenance.task_version = "2.0.0"
    else:
        candidate["challenge_results"][0]["assertion_results"] = None
    _write_supplemental_web_record(root, candidate, frozen, replay)
    assert _read_target_challenges(settings, report.run_id, report) is None


@pytest.mark.parametrize("task_id", ["t1_phone_validation", "t2_birthday_today", "t3_date_validation"])
def test_calibration_display_keeps_task_specific_scope(tmp_path, task_id):
    factory = {"t1_phone_validation": _supplemental_web_record,
               "t2_birthday_today": _birthday_web_record, "t3_date_validation": _date_web_record}[task_id]
    settings, report, root, candidate, frozen, replay = factory(tmp_path)
    calibration = _add_web_calibration(report, root, candidate, frozen, replay)
    assert _read_verifier_calibration(settings, report.run_id, report) == calibration
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "固定工程回归校准通过" in page
    if task_id == "t2_birthday_today":
        assert "跨年提醒窗口、闰年二月边界、同日生日与重复姓名" in page
        assert "错误首位、空号码" not in page
    elif task_id == "t1_phone_validation":
        assert "11 位号码以 2 开头、空手机号、全角数字手机号" in page
    else:
        profile = get_pilot_profile(task_id)
        assert "、".join(profile.challenge_labels.values()) in page
        assert "错误首位、空号码" not in page and "跨年提醒窗口" not in page


@pytest.mark.parametrize("binding", ["policy", "task", "task_version", "snapshot", "environment", "qualification", "cases", "frozen_bytes"])
def test_birthday_calibration_rejects_stale_bindings(tmp_path, binding):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path)
    calibration = _add_web_calibration(report, root, candidate, frozen, replay)
    if binding in {"policy", "snapshot", "environment", "qualification"}:
        key = {"policy": "policy_version", "snapshot": "source_snapshot_hash", "environment": "environment_digest",
               "qualification": "qualification"}[binding]
        calibration[key] = "t1-scaffold-regression-v1" if binding == "policy" else "d" * 64
    elif binding in {"task", "task_version"}:
        frozen["bundle"]["task_id" if binding == "task" else "task_version"] = "t3_date_validation" if binding == "task" else "2.0.0"
        calibration["verifier_bundle_hash"] = hash_json(frozen["bundle"])
    elif binding == "cases":
        calibration["formal_case_ids"] = ["c_today_exact"]
    _write_web_calibration(root, calibration, candidate, frozen, replay)
    if binding == "frozen_bytes":
        with (root / "plans/frozen_plan.json").open("ab") as handle:
            handle.write(b" ")
    assert _read_verifier_calibration(settings, report.run_id, report) is None


def test_failed_birthday_calibration_does_not_blame_student_for_supplemental_difference(tmp_path):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path, defect=True)
    _add_web_calibration(report, root, candidate, frozen, replay, passed=False)
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "判断程序校准未通过，以下差异待核实，不能据此归责学生" in page
    assert "这属于判断程序可靠性限制，不能据此认定学生程序错误" in page


@pytest.mark.parametrize("malformation", ["missing_case", "duplicate_case", "assertions", "expected_assertions", "preservation", "signature", "compile", "qualification"])
def test_birthday_calibration_rejects_inconsistent_passed_summary(tmp_path, malformation):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path)
    calibration = _add_web_calibration(report, root, candidate, frozen, replay)
    outcome = calibration["outcomes"][1]
    if malformation == "missing_case":
        outcome["cases"].pop()
    elif malformation == "duplicate_case":
        outcome["cases"][0] = copy.deepcopy(outcome["cases"][1])
    elif malformation in {"assertions", "expected_assertions"}:
        outcome["cases"][0][malformation] = {"a_names": "MATCH", "a_contacts": "MATCH"}
    elif malformation in {"preservation", "signature"}:
        outcome["cases"][0]["initial_contacts_preserved" if malformation == "preservation" else "defect_signature_verified"] = False
    elif malformation == "compile":
        outcome["compile_status"] = "INFRA_ERROR"
    else:
        calibration["grade_eligible"] = True
    _write_web_calibration(root, calibration, candidate, frozen, replay)
    assert _read_verifier_calibration(settings, report.run_id, report) is None


def test_birthday_formal_inputs_hidden_if_task_package_changed(tmp_path):
    settings, report, root, candidate, frozen, replay = _birthday_web_record(tmp_path)
    report.provenance.task_package_hash = "d" * 64
    (root / "objective_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "测试日期：" not in page
    assert "查看完整断言与 JSON" not in page


@pytest.mark.parametrize("execution_status,completed", [("ADAPTATION_FAILED", 0), ("CANCELLED", 0), ("CANCELLED", 2)])
def test_birthday_incomplete_run_displays_unexecuted_separately_from_saved_counts(tmp_path, execution_status, completed):
    settings, report, root, _, _, _ = _birthday_web_record(tmp_path)
    payload = report.model_dump(mode="json")
    payload["execution_status"] = execution_status
    payload["counts"].update(attempted_cases=completed, completed_cases=completed,
                             matched_cases=completed, mismatched_cases=0, not_compared_cases=0)
    payload["case_results"] = payload["case_results"][:completed]
    case_ids = payload["coverage"]["planned_cases"]
    payload["coverage"].update(completed_cases=case_ids[:completed], missed_cases=case_ids[completed:], complete=False)
    (root / "objective_report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (root / "evidence/target_challenges/target_challenges.json").unlink()
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
        exported = client.get(f"/runs/{report.run_id}/report.json").json()
    assert f"另有 {6 - completed} 项必测用例尚未执行" in page
    assert "未执行不代表程序通过或失败" in page
    assert f"{completed} 项符合预期，0 项不符合预期，0 项未比较" in page
    if execution_status == "CANCELLED":
        assert "本次测评已取消，仅保留已完成的观测结果" in page
    else:
        assert "测试适配尚未完成" in page
        assert "不能据此判断学生程序正确或错误" in page
    assert exported["execution_status"] == execution_status
    assert exported["counts"] == payload["counts"]


def test_completed_birthday_run_does_not_display_unexecuted_warning(tmp_path):
    settings, report, _, _, _, _ = _birthday_web_record(tmp_path)
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "必测用例尚未执行" not in page


@pytest.mark.parametrize("task_ref", ["t3_date_validation", "t3_date_validation@1.0.0"])
@pytest.mark.parametrize("filename", ["date.cpp", "date.zip"])
def test_date_upload_queues_exact_version_for_both_source_forms(tmp_path, monkeypatch, task_ref, filename):
    seen = []
    monkeypatch.setattr("cpp_assessor.interfaces.web.app.execute_llm_run", lambda options, settings: seen.append(options))
    settings = WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path)
    app = create_app(settings)
    source = b"int example() { return 0; }"
    if filename.endswith(".zip"):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("validators.cpp", source)
        source = stream.getvalue()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": task_ref, "api_key": "synthetic-test-key"},
            files={"submission": (filename, source)}, follow_redirects=False)
        assert response.status_code == 303
        app.state.executor.shutdown(wait=True)
    assert len(seen) == 1
    assert seen[0].task_ref == "t3_date_validation@1.0.0"
    assert seen[0].submission.name == filename
    assert seen[0].submission.read_bytes() == source


@pytest.mark.parametrize("defect", [False, True])
def test_date_results_show_add_contact_and_all_eight_supplemental_dates(tmp_path, monkeypatch, defect):
    settings, report, root, challenges, frozen, replay = _date_web_record(tmp_path, defect=defect)
    calibration = _add_web_calibration(report, root, challenges, frozen, replay)
    monkeypatch.setattr("cpp_assessor.interfaces.web.app.make_backend", lambda *_: pytest.fail("Use only saved evidence"))
    assert _read_target_challenges(settings, report.run_id, report) == challenges
    assert _read_verifier_calibration(settings, report.run_id, report) == calibration
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/runs/{report.run_id}")
        exported = client.get(f"/runs/{report.run_id}/report.json").json()
        csv_report = client.get(f"/runs/{report.run_id}/report.csv")
    page = response.text
    assert response.status_code == 200
    assert f"{3 if defect else 6} 项符合预期，{3 if defect else 0} 项不符合预期" in page
    assert "另外检查 8 种输入" in page and "不改变下方 6 项必测用例计数" in page
    assert ("发现 2 项必测用例之外的差异" if defect else "8 项补充规则均符合预期") in page
    assert "固定工程回归校准通过" in page
    for date in ("1900-01-31", "2100-12-31", "1900-02-29", "2100-02-29", "2023-02-29", "2000-02-30",
                 "2023-04-30", "2000-01-31", "2000-02-31"):
        assert date in page
    assert "待校验生日：" in page and "预期新增结果：" in page and "实际新增结果：" in page
    assert "新增后内容符合预期" in page and "拒绝后内容保持不变" in page
    assert "查看完整断言与 JSON" in page
    assert "输入手机号" not in page and "空号码" not in page and "预期生日名单" not in page
    assert exported["counts"] == report.counts.model_dump(mode="json")
    assert exported["grade_eligible"] is False and csv_report.status_code == 200
    csv_rows = list(csv.DictReader(io.StringIO(csv_report.content.decode("utf-8-sig"))))
    assert len(csv_rows) == 6
    assert {row["case_id"] for row in csv_rows} == set(get_pilot_profile("t3_date_validation").formal_case_ids)
    assert sum(row["comparison"] == "MISMATCH" for row in csv_rows) == (3 if defect else 0)


@pytest.mark.parametrize("malformation", ["wrong_policy", "extra_case", "three_case_summary", "wrong_task"])
def test_date_saved_supplemental_binding_rejects_wrong_task_or_count(tmp_path, malformation):
    settings, report, root, candidate, frozen, replay = _date_web_record(tmp_path)
    if malformation == "wrong_policy":
        candidate["policy_version"] = "t2-target-challenges-v1"
    elif malformation == "extra_case":
        candidate["challenge_results"][0]["case_id"] = "challenge_outside_profile"
    elif malformation == "three_case_summary":
        candidate["challenge_counts"] = {"required": 3, "attempted": 3, "matched": 3, "mismatched": 0, "not_compared": 0}
    else:
        frozen["bundle"]["task_id"] = "t1_phone_validation"
        candidate["verifier_bundle_hash"] = hash_json(frozen["bundle"])
    _write_supplemental_web_record(root, candidate, frozen, replay)
    assert _read_target_challenges(settings, report.run_id, report) is None


def test_date_formal_inputs_hidden_when_task_package_hash_changed(tmp_path):
    settings, report, root, _, _, _ = _date_web_record(tmp_path)
    report.provenance.task_package_hash = "d" * 64
    (root / "objective_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "待校验生日：" not in page and "输入手机号" not in page
    assert "查看完整断言与 JSON" not in page


def test_failed_date_calibration_warns_before_explaining_student_difference(tmp_path):
    settings, report, root, candidate, frozen, replay = _date_web_record(tmp_path, defect=True)
    _add_web_calibration(report, root, candidate, frozen, replay, passed=False)
    with TestClient(create_app(settings)) as client:
        page = client.get(f"/runs/{report.run_id}").text
    assert "判断程序校准未通过，以下差异待核实，不能据此归责学生" in page
    assert "这属于判断程序可靠性限制，不能据此认定学生程序错误" in page

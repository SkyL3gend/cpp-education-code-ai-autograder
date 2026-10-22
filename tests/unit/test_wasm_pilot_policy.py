from pathlib import Path

from fastapi.testclient import TestClient

from cpp_assessor.domain.enums import Mode
from cpp_assessor.interfaces.web.app import WebSettings, create_app
from cpp_assessor.llm.provider import LLMSettings
from cpp_assessor.orchestrator.mode_two import ModeTwoRun
from cpp_assessor.orchestrator.runner import RunOptions
from cpp_assessor.planning.bundle import TargetSpec, BuildSpec
from cpp_assessor.planning.policy import validate_build_options


def test_dot_include_normalized_but_parent_not_hidden():
    target = TargetSpec(sources=["main.cpp"], include_dirs=[".", "include", ".."])
    assert target.include_dirs == ["include", ".."]


def test_wasm_artifact_is_fixed_name_only():
    assert not validate_build_options(BuildSpec(artifact_name="subject.wasm"))
    assert validate_build_options(BuildSpec(artifact_name="../subject.wasm"))


def test_crlf_source_quotes_use_universal_newlines(tmp_path):
    source = tmp_path / "phone.cpp"
    original = b"int example() {\r\n  return 1;\r\n}\r\n"
    source.write_bytes(original)
    run = ModeTwoRun(RunOptions(task_ref="t1_phone_validation@1.0.0", submission=source,
        mode=Mode.LLM, backend="mock", data_dir=tmp_path / "data"), LLMSettings(api_key="test"))
    # Initialize persistent state in the same order as execute(), without model calls.
    run.db.create_run(run_id=run.state.run_id, task_id=run.state.task.task_id,
        task_version=str(run.state.task.version), task_package_hash=run.state.task.package_hash,
        mode=Mode.LLM, backend="mock", submission_id=None)
    run._ingest()
    assert run.state.files["phone.cpp"] == "int example() {\n  return 1;\n}\n"
    originals = list(run.state.run_dir.glob("submissions/**/original/phone.cpp"))
    assert originals and originals[0].read_bytes() == original


def test_pilot_upload_is_explicit_and_task_scoped(tmp_path):
    with TestClient(create_app(WebSettings(backend="wasm_pilot", data_root=tmp_path / "closed")), base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": "t1_phone_validation@1.0.0"})
        assert response.status_code == 403
    with TestClient(create_app(WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path / "pilot")), base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": "t2_birthday_today@1.0.0"})
        assert response.status_code == 400
        page = client.get("/").text
        assert "/llm/tasks/t1_phone_validation@1.0.0" in page
        assert "/llm/tasks/t2_birthday_today@1.0.0" not in page


def test_pilot_rejects_cross_origin_uploads_and_foreign_hosts(tmp_path):
    app = create_app(WebSettings(backend="wasm_pilot", allow_unattested=True, data_root=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/llm/runs", data={"task_ref": "t1_phone_validation@1.0.0"},
            headers={"Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 403

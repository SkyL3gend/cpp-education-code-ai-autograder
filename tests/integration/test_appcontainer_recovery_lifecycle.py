"""Host-only profile/journal lifecycle; no submitted program/compiler executes."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows recovery lifecycle")

if os.name == "nt":
    from cpp_assessor.sandbox import appcontainer_recovery as recovery
    from cpp_assessor.sandbox.appcontainer import AppContainerCompiler, advapi32, kernel32


@pytest.fixture(autouse=True)
def normal_parent():
    token = wintypes.HANDLE()
    assert advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 8, ctypes.byref(token))
    try:
        result, size = wintypes.DWORD(), wintypes.DWORD()
        assert advapi32.GetTokenInformation(token, 29, ctypes.byref(result), 4, ctypes.byref(size))
        if result.value:
            pytest.skip("Host-only lifecycle requires a normal parent, not nested AppContainer")
    finally:
        kernel32.CloseHandle(token)


def paths(tmp_path, monkeypatch, name="compile-lifecycle123"):
    monkeypatch.setenv("ASSESSOR_HOME", str(tmp_path))
    sdk = tmp_path / "test-sdk"
    sdk.mkdir(exist_ok=True)
    (sdk / "host-toolchain-sentinel.txt").write_text("not-an-executable")
    root = tmp_path / "wasm_staging" / name
    source, output = root / "source", root / "output"
    source.mkdir(parents=True)
    output.mkdir()
    (source / "host-source-sentinel.txt").write_text("not-a-program")
    return sdk, source, output


def acl_text(path):
    result = subprocess.run(["icacls", str(path)], capture_output=True, timeout=15)
    assert result.returncode == 0
    return result.stdout.decode(errors="replace")


def test_private_journal_and_normal_profile_lifecycle(tmp_path, monkeypatch):
    sdk, source, output = paths(tmp_path, monkeypatch)
    with AppContainerCompiler(readonly_roots=[sdk, source], writable_root=output,
            recover_staging=True) as compiler:
        journal = compiler._journal
        assert journal is not None and journal.path.is_file()
        assert recovery.parent_is_dead(journal.record["pid"], journal.record["created"]) is False
        # These routines enumerate the actual Windows security descriptor and
        # permit exactly current host user, SYSTEM and Administrators full ACEs.
        recovery._private_acl(journal.directory, journal.owner, directory=True)
        recovery._private_acl(journal.path, journal.owner, directory=False)
        assert journal.directory.parent == tmp_path / "wasm_staging"
        assert not journal.directory.is_relative_to(output)
        assert len(journal.record["drives"]) == 3
        sid, saved_path = compiler.sid_string, journal.path
        journal.recover()  # Current PID + creation time must be left untouched.
        assert saved_path.exists()
    assert not saved_path.exists()
    assert not compiler._created and not compiler._drives
    for directory in (sdk, source, output):
        assert sid not in acl_text(directory)


def test_dead_host_profile_and_dos_mappings_are_recovered(tmp_path, monkeypatch):
    sdk, source, output = paths(tmp_path, monkeypatch, "compile-orphaned123")
    metadata = tmp_path / "orphan-metadata.json"
    script = '''import json, sys
from pathlib import Path
from cpp_assessor.sandbox.appcontainer import AppContainerCompiler
c=AppContainerCompiler(readonly_roots=[Path(sys.argv[1]),Path(sys.argv[2])],writable_root=Path(sys.argv[3]),recover_staging=True)
c.__enter__()
Path(sys.argv[4]).write_text(json.dumps({"profile":c.name,"sid":c.sid_string,"path":str(c._journal.path),"record":c._journal.record}),encoding="utf-8")
# Intentionally leave the context without __exit__: no compiler/guest starts.
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script,
        str(sdk), str(source), str(output), str(metadata)], env=env,
        capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    orphan = json.loads(metadata.read_text(encoding="utf-8"))
    assert recovery.parent_is_dead(orphan["record"]["pid"], orphan["record"]["created"])
    assert Path(orphan["path"]).exists()
    assert orphan["sid"] in acl_text(sdk)
    new_root = tmp_path / "wasm_staging" / "compile-nextlife123"
    new_source, new_output = new_root / "source", new_root / "output"
    new_source.mkdir(parents=True)
    new_output.mkdir()
    deletions = []
    delete = recovery._u.DeleteAppContainerProfile

    def recording_delete(name):
        result = delete(name)
        deletions.append((name, result))
        return result

    monkeypatch.setattr(recovery._u, "DeleteAppContainerProfile", recording_delete)
    with AppContainerCompiler(readonly_roots=[sdk, new_source], writable_root=new_output,
            recover_staging=True):
        assert not Path(orphan["path"]).exists()
        assert deletions == [(orphan["profile"], 0)]
        for directory in (sdk, source, output):
            assert orphan["sid"] not in acl_text(directory)
    assert list((tmp_path / "wasm_staging" / ".host-journals").glob("*.json")) == []

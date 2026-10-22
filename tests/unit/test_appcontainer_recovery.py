"""Recovery must never authorize cleanup from incomplete/unrelated evidence."""
from __future__ import annotations

import copy
import ctypes
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows recovery policy")

if os.name == "nt":
    from cpp_assessor.sandbox import appcontainer_recovery as recovery
    from cpp_assessor.sandbox.appcontainer import AppContainerCompiler


PROFILE = "cppassessor-" + "a" * 32
SID = "S-1-15-2-1-2-3-4-5-6-7"
OWNER = "S-1-5-21-1-2-3-1001"


@pytest.fixture
def record_paths(tmp_path, monkeypatch):
    base, sdk = tmp_path / "wasm_staging", tmp_path / "sdk"
    source, output = base / "compile-testing123" / "source", base / "compile-testing123" / "output"
    for directory in (sdk, source, output):
        directory.mkdir(parents=True)
    monkeypatch.setattr(recovery, "derived_profile_sid", lambda profile: SID)
    record = {"version": 1, "owner": OWNER, "profile": PROFILE, "sid": SID,
        "sdk": str(sdk), "source": str(source), "output": str(output),
        "pid": 1234, "created": 123456, "drives": [{"letter": "Z:", "target": "\\??\\" + str(output)}]}
    return record, {"base": base, "sdk": sdk, "owner": OWNER}


def test_recovery_accepts_only_current_fixed_staging_layout(record_paths):
    record, context = record_paths
    assert recovery.validate_record(record, **context) is record


@pytest.mark.parametrize("field,value", [
    ("owner", "S-1-5-21-OTHER"), ("profile", "unrelated-profile"),
    ("sid", "S-1-15-2-7-6-5-4-3-2-1"), ("pid", 0), ("pid", True),
    ("created", 0), ("created", True), ("sdk", r"C:\unrelated-sdk"),
    ("source", r"C:\unrelated\source"), ("output", r"C:\unrelated\output"),
    ("source", "relative/source"),
    ("drives", [{"letter": "C:", "target": "\\??\\C:\\"}]),
    ("drives", [{"letter": 12, "target": "bad"}]),
    ("drives", [{"letter": "Z:", "target": ["bad"]}]),
])
def test_recovery_rejects_unrelated_or_malformed_record(record_paths, field, value):
    record, context = record_paths
    record[field] = value
    with pytest.raises(ValueError):
        recovery.validate_record(record, **context)


def test_recovery_rejects_different_staging_sibling_and_duplicate_drives(record_paths):
    record, context = record_paths
    original = copy.deepcopy(record)
    record["output"] = str(Path(record["source"]).parent.parent / "compile-other123" / "output")
    with pytest.raises(ValueError, match="outside"):
        recovery.validate_record(record, **context)
    record = original
    record["drives"].append(dict(record["drives"][0]))
    with pytest.raises(ValueError, match="DOS mapping"):
        recovery.validate_record(record, **context)


@pytest.mark.parametrize("targets,removed", [
    (["\\??\\C:\\specific-output"], True),
    (["\\??\\C:\\different-output"], False),
    (["\\??\\C:\\specific-output", "\\??\\C:\\older-mapping"], False),
])
def test_dos_cleanup_requires_exact_single_mapping(monkeypatch, targets, removed):
    calls = []

    def query(letter, buffer, size):
        document = "\0".join(targets) + "\0\0"
        for index, char in enumerate(document):
            buffer[index] = char
        return len(document)

    monkeypatch.setattr(recovery._k, "QueryDosDeviceW", query)
    monkeypatch.setattr(recovery._k, "DefineDosDeviceW", lambda *args: calls.append(args) or True)
    recovery._remove_exact_drive("Z:", "\\??\\C:\\specific-output")
    assert calls == ([(15, "Z:", "\\??\\C:\\specific-output")] if removed else [])


@pytest.mark.parametrize("dead", [False, True])
def test_recovery_skips_live_owner_and_cleans_only_valid_dead_owner(record_paths, monkeypatch, dead):
    record, context = record_paths
    directory = context["base"] / ".host-journals"
    directory.mkdir()
    path = directory / (PROFILE + ".json")
    path.write_text(json.dumps(record), encoding="utf-8")
    calls = []
    journal = recovery.CompilerJournal.__new__(recovery.CompilerJournal)
    journal.base, journal.sdk, journal.owner, journal.directory = context["base"], context["sdk"], OWNER, directory
    journal.revoke = lambda target, sid: calls.append(("revoke", target, sid))
    monkeypatch.setattr(recovery, "_private_acl", lambda *args, **kwargs: None)
    monkeypatch.setattr(recovery, "parent_is_dead", lambda pid, created: dead)
    monkeypatch.setattr(recovery, "_remove_exact_drive", lambda letter, target: calls.append(("drive", letter, target)))
    monkeypatch.setattr(recovery._u, "DeleteAppContainerProfile", lambda name: calls.append(("profile", name)) or 0)
    journal.recover()
    if dead:
        assert not path.exists()
        assert calls == [("drive", "Z:", record["drives"][0]["target"]),
            *( ("revoke", Path(record[key]), SID) for key in ("output", "source", "sdk") ),
            ("profile", PROFILE)]
    else:
        assert path.exists()
        assert calls == []


def test_real_live_process_identity_is_never_dead():
    created = recovery._creation_time(recovery._k.GetCurrentProcess())
    assert not recovery.parent_is_dead(os.getpid(), created)


def test_pid_reuse_and_access_denied_fail_as_specified(monkeypatch):
    closed = []
    monkeypatch.setattr(recovery._k, "OpenProcess", lambda *args: 123)
    monkeypatch.setattr(recovery._k, "CloseHandle", closed.append)
    monkeypatch.setattr(recovery, "_creation_time", lambda handle: 99)
    assert recovery.parent_is_dead(42, 88)
    assert closed == [123]
    monkeypatch.setattr(recovery._k, "OpenProcess", lambda *args: 0)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    assert not recovery.parent_is_dead(42, 88)


def test_host_log_and_plan_read_rejects_outside_or_reparse_paths(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    compiler = AppContainerCompiler.__new__(AppContainerCompiler)
    compiler.writable_root = output
    outside = tmp_path / "private.log"
    outside.write_text("PRIVATE")
    with pytest.raises(ValueError, match="outside"):
        compiler._read_output_text(outside)
    nested = output / "nested"
    nested.mkdir()
    log = nested / "compile.stderr.txt"
    log.write_text("diagnostic")
    original_lstat = Path.lstat

    def fake_reparse(path, *args, **kwargs):
        return SimpleNamespace(st_file_attributes=0x400) if path == nested else original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_reparse)
    with pytest.raises(ValueError, match="reparse"):
        compiler._read_output_text(log)
    with pytest.raises(ValueError, match="reparse"):
        compiler._mapped_overlay(log, output)


def test_host_log_actual_read_has_fixed_byte_bound(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    log = output / "compiler.stderr.txt"
    log.write_bytes(b"x" * (300 << 10))
    compiler = AppContainerCompiler.__new__(AppContainerCompiler)
    compiler.writable_root = output
    original_open = Path.open
    sizes = []

    class Reader:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def read(self, size=-1):
            assert 0 < size <= 1 << 20
            sizes.append(size)
            return self.file.read(size)

    monkeypatch.setattr(Path, "open", lambda path, *args, **kwargs: Reader(original_open(path, *args, **kwargs)) if path == log else original_open(path, *args, **kwargs))
    assert compiler._read_output_text(log)
    assert len(sizes) == 1

"""Bound host reads and account for all writable output without launching code."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows resource monitor")

if os.name == "nt":
    from cpp_assessor.sandbox import win32
    from cpp_assessor.sandbox.local import _read_text


def test_log_read_is_bounded_before_allocating(tmp_path, monkeypatch):
    log = tmp_path / "compiler.log"
    log.write_bytes(b"diagnostic" * 8192)
    original_open = Path.open
    read_sizes = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size=-1):
            assert size == 32, "Log bytes must be bounded before host allocation"
            read_sizes.append(size)
            return self.stream.read(size)

    def guarded_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return BoundedReader(stream) if path == log else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    assert _read_text(log, limit=32) == (b"diagnostic" * 4)[:32].decode()
    assert read_sizes == [32]


def test_missing_log_is_empty(tmp_path):
    assert _read_text(tmp_path / "missing.log") == ""


@pytest.mark.parametrize("failed_query", [1, 2])
def test_failed_accounting_query_cannot_look_like_empty_job(monkeypatch, failed_query):
    calls = []

    def query(*args):
        calls.append(args[1])
        return len(calls) != failed_query

    monkeypatch.setattr(win32.kernel32, "QueryInformationJobObject", query)
    job = win32.JobObject.__new__(win32.JobObject)
    job.handle = 123
    with pytest.raises(OSError, match="QueryInformationJobObject"):
        job.accounting()
    assert len(calls) == failed_query


def test_failed_termination_is_reported(monkeypatch):
    monkeypatch.setattr(win32.kernel32, "TerminateJobObject", lambda *args: False)
    job = win32.JobObject.__new__(win32.JobObject)
    job.handle = 123
    with pytest.raises(OSError, match="TerminateJobObject"):
        job.terminate()


@pytest.mark.parametrize(("limit", "exceeded"), [(7, False), (6, True)])
def test_disk_scan_prunes_reparse_directories_and_counts_link_itself(tmp_path, monkeypatch, limit, exceeded):
    regular = tmp_path / "regular"
    regular.mkdir()
    (regular / "normal").write_bytes(b"ok")
    original_lstat = Path.lstat

    def lstat(path):
        if path.name == "junction":
            return SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR, st_size=0)
        if path.name == "symbolic":
            return SimpleNamespace(st_file_attributes=0, st_mode=stat.S_IFLNK, st_size=0)
        if path.name == "symbolic-file":
            return SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFLNK, st_size=5)
        return original_lstat(path)

    def walk(root, *, topdown, followlinks):
        assert root == tmp_path and topdown and not followlinks
        directories = ["junction", "symbolic", "regular"]
        yield root, directories, ["symbolic-file"]
        assert directories == ["regular"], "Junctions must be pruned before descent"
        yield regular, [], ["normal"]

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(win32.os, "walk", walk)
    assert win32._workdir_exceeds_limit(tmp_path, limit) is exceeded


def test_disk_scan_rejects_replaced_root_before_walking(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR))
    monkeypatch.setattr(win32.os, "walk", lambda *args, **kwargs: pytest.fail("must not enumerate root target"))
    with pytest.raises(OSError, match="writable root became a reparse point"):
        win32._workdir_exceeds_limit(tmp_path, 100)


def test_output_budget_reads_open_handles_without_stating_guest_paths(tmp_path, monkeypatch):
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    original_stat = Path.stat
    original_fstat = os.fstat
    observed_handles = []

    def path_stat(path, *args, **kwargs):
        assert path not in (stdout, stderr), "Guest-controlled output paths must not be followed"
        return original_stat(path, *args, **kwargs)

    def fstat(handle):
        observed_handles.append(handle)
        return original_fstat(handle)

    job = SimpleNamespace(assign=lambda handle: None, terminate=lambda code: None,
                          accounting=lambda: SimpleNamespace(active_processes=0, stop_reason=None))
    process = SimpleNamespace(_handle=123, pid=456, returncode=0,
                              poll=lambda: 0, wait=lambda timeout=None: 0)
    monkeypatch.setattr(Path, "stat", path_stat)
    monkeypatch.setattr(win32.os, "fstat", fstat)
    monkeypatch.setattr(win32, "resume_process_threads", lambda pid: None)
    monkeypatch.setattr(win32.kernel32, "WaitForSingleObject", lambda *args: win32.WAIT_OBJECT_0)
    result = win32.spawn_in_job(
        ["not-executed"], cwd=tmp_path, env={}, job=job, stdin_path=None,
        stdout_path=stdout, stderr_path=stderr, wall_timeout_ms=1000,
        output_limit=1, process_factory=lambda *args, **kwargs: process,
    )
    assert result[3].stop_reason is None
    assert len(observed_handles) == 2
    assert observed_handles[0] != observed_handles[1]


@pytest.mark.parametrize(
    ("output_in_cwd", "monitor_parent", "expected_stop"),
    [(False, True, "DISK_LIMIT"), (False, False, None), (True, False, "DISK_LIMIT")],
)
def test_disk_monitor_accounts_for_selected_root(
    tmp_path, monkeypatch, output_in_cwd, monitor_parent, expected_stop
):
    root = tmp_path / "output"
    cwd = root / "conversion"
    cwd.mkdir(parents=True)
    output = (cwd if output_in_cwd else root) / "converted.wasm"

    class FakeProcess:
        _handle = 123
        pid = 456
        returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    class FakeJob:
        def __init__(self):
            self.terminated = []

        def assign(self, handle):
            assert handle == FakeProcess._handle

        def terminate(self, code):
            self.terminated.append(code)

        def accounting(self):
            return SimpleNamespace(active_processes=0, stop_reason=None)

    def fake_launch(*args, **kwargs):
        output.write_bytes(b"x" * 128)
        return FakeProcess()

    monkeypatch.setattr(win32, "resume_process_threads", lambda pid: None)
    monkeypatch.setattr(win32.kernel32, "WaitForSingleObject", lambda *args: win32.WAIT_OBJECT_0)
    job = FakeJob()
    result = win32.spawn_in_job(
        ["not-executed"], cwd=cwd, env={}, job=job, stdin_path=None,
        stdout_path=cwd / "stdout", stderr_path=cwd / "stderr",
        wall_timeout_ms=1000, workdir_limit=64,
        monitored_root=root if monitor_parent else None, process_factory=fake_launch,
    )
    assert result[3].stop_reason == expected_stop
    assert job.terminated == ([5] if expected_stop else [])


@pytest.mark.parametrize("children_exit", [True, False])
def test_host_waits_for_all_job_children_or_fails_closed(tmp_path, monkeypatch, children_exit):
    observations = []
    terminated = []
    active_counts = iter([1, 1, 1, 0] if children_exit else [])

    def accounting():
        active = next(active_counts, 1)
        observations.append(active)
        return SimpleNamespace(active_processes=active, stop_reason=None)

    job = SimpleNamespace(assign=lambda handle: None, terminate=terminated.append,
                          accounting=accounting)
    process = SimpleNamespace(_handle=123, pid=456, returncode=0,
                              poll=lambda: 0, wait=lambda timeout=None: 0)
    monkeypatch.setattr(win32, "resume_process_threads", lambda pid: None)
    monkeypatch.setattr(win32.kernel32, "WaitForSingleObject", lambda *args: win32.WAIT_OBJECT_0)
    monkeypatch.setattr(win32.time, "sleep", lambda delay: None)
    if not children_exit:
        ticks = iter(range(100))
        monkeypatch.setattr(win32.time, "perf_counter", lambda: next(ticks))

    def run():
        return win32.spawn_in_job(
            ["not-executed"], cwd=tmp_path, env={}, job=job, stdin_path=None,
            stdout_path=tmp_path / "stdout", stderr_path=tmp_path / "stderr",
            wall_timeout_ms=100000, process_factory=lambda *args, **kwargs: process,
        )

    if children_exit:
        assert run()[3].active_processes == 0
        assert observations == [1, 1, 1, 0]
    else:
        with pytest.raises(OSError, match="job processes did not exit"):
            run()
        assert all(active == 1 for active in observations)
    assert terminated == [3]


@pytest.mark.parametrize("reason", ["OUTPUT_LIMIT", "CANCELLED", "WALL_TIME_LIMIT"])
def test_limit_exit_waits_for_children_before_main_handle(tmp_path, monkeypatch, reason):
    observations = []
    counts = iter([2, 1, 0])
    terminated = []

    def accounting():
        active = next(counts, 0)
        observations.append(active)
        return SimpleNamespace(active_processes=active, stop_reason=None)

    def wait(timeout=None):
        assert observations[-1] == 0
        assert 0 <= timeout <= 5

    job = SimpleNamespace(assign=lambda handle: None, terminate=terminated.append,
                          accounting=accounting)
    process = SimpleNamespace(_handle=123, pid=456, returncode=0,
                              poll=lambda: 0, wait=wait)
    monkeypatch.setattr(win32, "resume_process_threads", lambda pid: None)
    monkeypatch.setattr(win32.kernel32, "WaitForSingleObject", lambda *args: pytest.fail("limit must stop before wait"))
    monkeypatch.setattr(win32.time, "sleep", lambda delay: None)

    def launch(*args, **kwargs):
        if reason == "OUTPUT_LIMIT":
            (tmp_path / "stdout").write_bytes(b"large output")
        return process

    result = win32.spawn_in_job(
        ["not-executed"], cwd=tmp_path, env={}, job=job, stdin_path=None,
        stdout_path=tmp_path / "stdout", stderr_path=tmp_path / "stderr",
        wall_timeout_ms=0 if reason == "WALL_TIME_LIMIT" else 1000,
        output_limit=1 if reason == "OUTPUT_LIMIT" else None,
        cancelled=(lambda: True) if reason == "CANCELLED" else None,
        process_factory=launch,
    )
    assert result[3].active_processes == 0
    assert result[1] == (reason == "WALL_TIME_LIMIT")
    assert result[3].stop_reason == (None if reason == "WALL_TIME_LIMIT" else reason)
    assert terminated == ([2, 3] if reason == "WALL_TIME_LIMIT" else [5, 3])


def test_main_exit_handle_must_be_confirmed_even_when_job_is_empty(tmp_path, monkeypatch):
    def wait(timeout=None):
        raise subprocess.TimeoutExpired("not-executed", timeout)

    job = SimpleNamespace(assign=lambda handle: None, terminate=lambda code: None,
                          accounting=lambda: SimpleNamespace(active_processes=0, stop_reason=None))
    process = SimpleNamespace(_handle=123, pid=456, returncode=0,
                              poll=lambda: 0, wait=wait)
    monkeypatch.setattr(win32, "resume_process_threads", lambda pid: None)
    monkeypatch.setattr(win32.kernel32, "WaitForSingleObject", lambda *args: win32.WAIT_OBJECT_0)
    with pytest.raises(OSError, match="main process exit could not be confirmed"):
        win32.spawn_in_job(
            ["not-executed"], cwd=tmp_path, env={}, job=job, stdin_path=None,
            stdout_path=tmp_path / "stdout", stderr_path=tmp_path / "stderr",
            wall_timeout_ms=1000, process_factory=lambda *args, **kwargs: process,
        )

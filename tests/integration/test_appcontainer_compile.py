"""Real Windows LPAC probes. Test source is trusted and never student code."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer")

if os.name == "nt":
    from cpp_assessor.sandbox.appcontainer import AppContainerCompiler, advapi32, kernel32
    from cpp_assessor.sandbox.win32 import JobObject, spawn_in_job
    from cpp_assessor.sandbox.base import CompileRequest
    from cpp_assessor.domain.models import BuildPlan, ResourceLimits
    from cpp_assessor.domain.enums import CompileStatus


@pytest.fixture(autouse=True)
def real_parent():
    token = wintypes.HANDLE()
    assert advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 8, ctypes.byref(token))
    try:
        result, size = wintypes.DWORD(), wintypes.DWORD()
        assert advapi32.GetTokenInformation(token, 29, ctypes.byref(result), 4, ctypes.byref(size))
        if result.value:
            pytest.skip("A normal parent token is required; nested Codex AppContainer denies compiler startup")
    finally:
        kernel32.CloseHandle(token)


def resource_limits():
    root = Path(__file__).resolve().parents[2]
    return ResourceLimits.model_validate(json.loads((root / "task_library/tasks/t1_phone_validation/1.0.0/task.json").read_text(encoding="utf-8"))["limits"])


def sdk():
    root = Path(__file__).resolve().parents[2] / "tools/wasi-sdk"
    if not (root / "bin/clang++.exe").is_file():
        pytest.skip("optional WASI toolchain unavailable")
    return root


def test_lpac_cpp_compilation_and_cleanup(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir(); output.mkdir()
    unit = source / "test.cpp"
    unit.write_text('#include <iostream>\nint main(){std::cout << "LPAC-compiled";}')
    toolchain = sdk()
    with AppContainerCompiler(readonly_roots=[toolchain, source], writable_root=output) as compiler:
        sid = compiler.sid_string
        assert sid.startswith("S-1-15-2-")
        drives = [letter for _, letter, _ in compiler._drives]
        result = compiler.compile(CompileRequest(BuildPlan.model_construct(), toolchain / "bin/clang++.exe",
            [unit], [source], ["--no-default-config", "--target=wasm32-wasip1", "--sysroot=" + str(toolchain / "share/wasi-sysroot"),
                "-fwasm-exceptions", "-isystem", str(toolchain / "share/wasi-sysroot/include/wasm32-wasip1/eh/c++/v1"),
                "-L" + str(toolchain / "share/wasi-sysroot/lib/wasm32-wasip1/eh"), "-std=c++17", "-lunwind"],
            output / "test.wasm", output, resource_limits()))
        assert result.status == CompileStatus.SUCCESS, result.stderr
        assert (output / "test.wasm").read_bytes().startswith(b"\0asm")
    assert not compiler._created
    assert not compiler._drives
    acls = subprocess.run(["icacls", str(unit)], capture_output=True).stdout.decode(errors="replace")
    assert sid not in acls
    for letter in drives:
        assert not kernel32.GetLogicalDrives() & (1 << (ord(letter[0]) - 65))


def test_real_lpac_blocks_files_and_network(tmp_path):
    native = shutil.which("g++.exe")
    if native is None:
        pytest.skip("trusted Win32 probe build requires optional native g++")
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir(); output.mkdir()
    outside = tmp_path / "private-host.txt"
    outside.write_text("PRIVATE-HOST-SENTINEL")
    (source / "readonly.txt").write_text("IMMUTABLE")
    unit = source / "probe.cpp"
    unit.write_text(r'''#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
int wmain(int argc,wchar_t **argv) {
  HANDLE h=CreateFileW(argv[1],GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,0,NULL);
  printf("private_read=%lu\n",h==INVALID_HANDLE_VALUE?GetLastError():0);if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);
  h=CreateFileW(argv[2],GENERIC_WRITE,0,NULL,OPEN_EXISTING,0,NULL);
  printf("readonly_write=%lu\n",h==INVALID_HANDLE_VALUE?GetLastError():0);if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);
  h=CreateFileW(L"allowed-output.txt",GENERIC_WRITE,0,NULL,CREATE_ALWAYS,0,NULL);
  printf("output_write=%lu\n",h==INVALID_HANDLE_VALUE?GetLastError():0);if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);
  WSADATA data;int startup=WSAStartup(MAKEWORD(2,2),&data);printf("wsa_startup=%d\n",startup);SOCKET s=socket(AF_INET,SOCK_STREAM,0);
  sockaddr_in addr={};addr.sin_family=AF_INET;addr.sin_addr.s_addr=htonl(0x7f000001);addr.sin_port=htons(_wtoi(argv[3]));
  int rc=connect(s,(sockaddr*)&addr,sizeof(addr));printf("network=%d\n",rc==0?0:WSAGetLastError());closesocket(s);WSACleanup();return 0;
}''', encoding="utf-8")
    subprocess.run([native, str(unit), "-static", "-municode", "-lws2_32", "-o", str(source / "probe.exe")], check=True, capture_output=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        env = {"SystemRoot": os.environ["SystemRoot"], "WINDIR": os.environ["SystemRoot"],
            "LOCALAPPDATA": str(output), "USERPROFILE": str(output), "TEMP": str(output), "TMP": str(output)}
        probe_argv = [str(source / "probe.exe"), str(outside), str(source / "readonly.txt"), str(listener.getsockname()[1])]
        # The identical self-authored probe must work without LPAC, proving
        # denial is not a broken socket test or missing Winsock installation.
        baseline = subprocess.run(probe_argv, cwd=output, env=env, capture_output=True, timeout=5)
        assert baseline.returncode == 0
        assert b"wsa_startup=0\r\n" in baseline.stdout
        assert b"network=0\r\n" in baseline.stdout
        connected, address = listener.accept()
        connected.close()
        with AppContainerCompiler(readonly_roots=[source], writable_root=output) as compiler:
            with JobObject(cpu_ms=5000, memory_bytes=128 << 20, active_process_limit=1, cpu_rate_percent=100) as job:
                result = spawn_in_job(probe_argv,
                    cwd=output, env=env, job=job, stdin_path=None, stdout_path=output / "probe.out", stderr_path=output / "probe.err",
                    wall_timeout_ms=5000, process_factory=compiler._launch)
            assert result[0] == 0, (output / "probe.err").read_text()
            evidence = (output / "probe.out").read_text()
            assert "private_read=5" in evidence
            assert "readonly_write=5" in evidence
            assert "output_write=0" in evidence
            # Zero-capability LPAC may deny Winsock provider initialization
            # itself before connect; retain the exact OS error as evidence.
            assert "network=0\n" not in evidence, evidence
            assert "network=10013" in evidence or ("network=10093" in evidence and "wsa_startup=0\n" not in evidence), evidence
    assert outside.read_text() == "PRIVATE-HOST-SENTINEL"
    assert (source / "readonly.txt").read_text() == "IMMUTABLE"


def test_refuses_overlapping_roots(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="disjoint"):
        AppContainerCompiler(readonly_roots=[source], writable_root=source)


def test_partial_acl_setup_is_revoked_and_fails_closed(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir(); output.mkdir()
    unit = source / "private.txt"
    unit.write_text("readonly-staged-source")

    class PartialFailure(AppContainerCompiler):
        def _acl(self, root, action, permission):
            super()._acl(root, action, permission)
            if action == "/grant":
                raise OSError("simulated failure after ACL mutation")

    compiler = PartialFailure(readonly_roots=[source], writable_root=output)
    with pytest.raises(OSError, match="simulated failure"):
        with compiler:
            pytest.fail("Failed isolation setup must never enter compilation")
    assert not compiler._created
    assert not compiler._granted
    assert not compiler._drives
    acls = subprocess.run(["icacls", str(unit)], capture_output=True).stdout.decode(errors="replace")
    assert compiler.sid_string not in acls

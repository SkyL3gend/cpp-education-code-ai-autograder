"""Windows LPAC compiler runner, with no capabilities and no host fallback.

Only dedicated staging directories and the selected toolchain receive a unique
profile SID ACE. ACL changes never target a user-profile/workspace ancestor.
LPAC is an OS security boundary, not a VM: kernel/runtime vulnerabilities and
system resources explicitly exposed to restricted application packages remain
outside this implementation's assurance. Source policy/VFS are defence in depth.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import msvcrt
import ntpath
import os
import json
import re
import shlex
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from ..domain.canonical import sha256_hex
from ..config import PROJECT_ROOT
from ..domain.enums import CompileStatus, FaultOwner
from .base import CompileOutcome
from .local import _read_text
from .win32 import JobObject, spawn_in_job

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
SIZE_T = ctypes.c_size_t
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
SECURITY_CAPABILITIES_ATTRIBUTE = 0x00020009
HANDLE_LIST_ATTRIBUTE = 0x00020002
ALL_APPLICATION_PACKAGES_POLICY_ATTRIBUTE = 0x0002000F
_DRIVE_LOCK = threading.Lock()


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]


kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.DuplicateHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD,
    wintypes.DWORD, ctypes.POINTER(SIZE_T)]
kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD,
    SIZE_T, ctypes.c_void_p, SIZE_T, ctypes.c_void_p, ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
    wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEX), ctypes.POINTER(PROCESS_INFORMATION)]
kernel32.CreateProcessW.restype = wintypes.BOOL
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.GetLogicalDrives.restype = wintypes.DWORD
kernel32.DefineDosDeviceW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR]
kernel32.DefineDosDeviceW.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
advapi32.FreeSid.argtypes = [ctypes.c_void_p]
userenv.CreateAppContainerProfile.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
userenv.CreateAppContainerProfile.restype = ctypes.c_long
userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
userenv.DeleteAppContainerProfile.restype = ctypes.c_long


def _check(ok, action):
    if not ok:
        code = ctypes.get_last_error()
        raise OSError(code, f"{action}: {ctypes.FormatError(code)}")


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _safe_directory(path: Path):
    path = path.resolve()
    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home()))).resolve()
    protected = {PROJECT_ROOT.resolve(), *PROJECT_ROOT.resolve().parents, user_profile, *user_profile.parents,
        *(user_profile / child for child in ("Desktop", "Documents", "Downloads", "AppData"))}
    if not path.is_dir() or path in protected or len(path.parts) < 4:
        raise ValueError(f"AppContainer ACL root must be a dedicated directory: {path}")
    # Do not follow junctions/symlinks while granting recursive inherited access.
    for current, dirs, files in os.walk(path, followlinks=False):
        for name in [*dirs, *files]:
            item = Path(current) / name
            if item.lstat().st_file_attributes & 0x400:
                raise ValueError(f"Reparse points are not allowed in sandbox roots: {item}")
    return path


class _Process:
    """Small Popen-compatible handle owner used by the existing Job monitor."""
    def __init__(self, info):
        self._handle = info.hProcess
        self.pid = info.dwProcessId
        self.returncode = None
        kernel32.CloseHandle(info.hThread)

    def poll(self):
        if kernel32.WaitForSingleObject(self._handle, 0) == 0:
            code = wintypes.DWORD()
            _check(kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)), "GetExitCodeProcess")
            self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        if kernel32.WaitForSingleObject(self._handle, 0xFFFFFFFF if timeout is None else int(timeout * 1000)) == 258:
            raise subprocess.TimeoutExpired("appcontainer compiler", timeout)
        return self.poll()

    def kill(self):
        _check(kernel32.TerminateProcess(self._handle, 1), "TerminateProcess")

    def __del__(self):
        if getattr(self, "_handle", None):
            kernel32.CloseHandle(self._handle)


class AppContainerCompiler:
    """Context-managed LPAC profile and per-profile ACL grants.

    All readonly_roots must be dedicated toolchain/source-staging directories;
    writable_root must be a fresh output staging tree. Caller deletes staging
    after copying bounded outputs, once this context has closed.
    """
    def __init__(self, *, readonly_roots: list[Path], writable_root: Path, recover_staging: bool = False):
        if os.name != "nt":
            raise OSError("AppContainer compilation requires Windows")
        self.readonly_roots = [_safe_directory(p) for p in readonly_roots]
        self.writable_root = _safe_directory(writable_root)
        if any(_inside(self.writable_root, p) or _inside(p, self.writable_root) for p in self.readonly_roots):
            raise ValueError("Read-only and writable AppContainer roots must be disjoint")
        self.name = "cppassessor-" + uuid.uuid4().hex
        self.sid = ctypes.c_void_p()
        self.sid_string = ""
        self._granted = []
        self._created = False
        self._drives = []
        self._mutex = None
        self._owns_mutex = False
        self._recover_staging = recover_staging
        self._journal = None

    def __enter__(self):
        try:
            # Serialize DACL propagation and per-logon DOS device allocation
            # across server/test processes, rather than racing icacls updates.
            self._mutex = kernel32.CreateMutexW(None, False, "Local\\CppAssessorCompilerIsolationV1")
            _check(self._mutex, "CreateMutexW")
            wait = kernel32.WaitForSingleObject(self._mutex, 60000)
            if wait not in (0, 128):
                raise OSError("Timed out waiting for isolated compiler setup")
            self._owns_mutex = True
            if self._recover_staging:
                if len(self.readonly_roots) != 2:
                    raise ValueError("Recovery requires exactly one SDK and one staged source root")
                from .appcontainer_recovery import CompilerJournal
                self._journal = CompilerJournal(sdk=self.readonly_roots[0], source=self.readonly_roots[1],
                    output=self.writable_root, profile=self.name,
                    revoke=lambda path, sid: self._acl(path, "/remove:g", "*" + sid))
            result = userenv.CreateAppContainerProfile(self.name, self.name, "C++ WASI compilation", None, 0, ctypes.byref(self.sid))
            if result < 0:
                raise OSError(result, f"CreateAppContainerProfile failed: {result & 0xffffffff:#x}")
            self._created = True
            sid_text = wintypes.LPWSTR()
            _check(advapi32.ConvertSidToStringSidW(self.sid, ctypes.byref(sid_text)), "ConvertSidToStringSidW")
            self.sid_string = sid_text.value
            kernel32.LocalFree(sid_text)
            if self._journal is not None and self.sid_string != self._journal.record["sid"]:
                raise ValueError("Actual compiler profile SID differs from host journal")
            for root in self.readonly_roots:
                # Record before mutation so even a partially failed/timed-out
                # icacls propagation is revoked by the cleanup path.
                self._granted.append(root)
                self._acl(root, "/grant", f"*{self.sid_string}:(OI)(CI)RX")
            self._granted.append(self.writable_root)
            self._acl(self.writable_root, "/grant", f"*{self.sid_string}:(OI)(CI)M")
            self._acl(self.writable_root, "/setintegritylevel", "(OI)(CI)L")
            # LLVM calls GetLongPathNameW on its image path, which enumerates
            # ancestors. Temporary per-logon DOS roots avoid granting access to
            # the user's home/Desktop/workspace ancestors merely for this call.
            with _DRIVE_LOCK:
                used = kernel32.GetLogicalDrives()
                for root in [*self.readonly_roots, self.writable_root]:
                    letter = next((chr(n + 65) + ":" for n in range(25, 3, -1) if not used & (1 << n)), None)
                    if letter is None:
                        raise OSError("No temporary drive letter available for isolated compilation")
                    target = "\\??\\" + str(root)
                    if self._journal is not None:
                        # Persist intent before side effects. Recovery removes
                        # it only if QueryDosDevice exactly matches this target.
                        self._journal.record_drive(letter, target)
                    _check(kernel32.DefineDosDeviceW(1 | 8, letter, target), "DefineDosDeviceW")
                    self._drives.append((root, letter, target))
                    used |= 1 << (ord(letter[0]) - 65)
            (self.writable_root / "Packages" / self.name / "AC/Temp").mkdir(parents=True, exist_ok=True)
        except BaseException:
            self.close()
            raise
        return self

    def _acl(self, root, action, permission):
        # This fixed, trusted host tool must also die with its caller. Otherwise
        # an orphaned icacls could finish granting an ACE after crash recovery
        # has revoked it. The non-inherited Job handle closes on host death.
        # These logs are outside every guest-writable/readable sandbox root.
        with tempfile.TemporaryDirectory(prefix=".host-acl-", dir=self.writable_root.parent) as temporary:
            work = Path(temporary)
            stdout, stderr = work / "stdout.txt", work / "stderr.txt"
            with JobObject(cpu_ms=10000, memory_bytes=128 << 20,
                    active_process_limit=1, cpu_rate_percent=100) as job:
                code, timed_out, _, accounting, _ = spawn_in_job([
                    str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/icacls.exe"),
                    str(root), action, permission, "/Q"],
                    cwd=work, env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")},
                    job=job, stdin_path=None, stdout_path=stdout, stderr_path=stderr,
                    wall_timeout_ms=60000, output_limit=256 << 10, workdir_limit=1 << 20,
                    cpu_limit_ms=10000)
                if code or timed_out or accounting.stop_reason:
                    raise OSError(f"AppContainer ACL {action} failed ({code}, "
                        f"{accounting.stop_reason or ('TIMEOUT' if timed_out else 'EXIT')}): " + _read_text(stderr))

    def close(self):
        errors = []
        for root, letter, target in reversed(self._drives):
            if not kernel32.DefineDosDeviceW(1 | 2 | 4 | 8, letter, target):
                errors.append(f"Unable to remove temporary DOS device {letter}")
        self._drives.clear()
        for root in reversed(self._granted):
            try:
                self._acl(root, "/remove:g", "*" + self.sid_string)
            except OSError as exc:
                errors.append(str(exc))
        self._granted.clear()
        if self._created:
            result = userenv.DeleteAppContainerProfile(self.name)
            if result < 0:
                errors.append(f"DeleteAppContainerProfile: {result & 0xffffffff:#x}")
            self._created = False
        if self.sid:
            advapi32.FreeSid(self.sid)
            self.sid = ctypes.c_void_p()
        if not errors and self._journal is not None:
            try:
                self._journal.complete()
                self._journal = None
            except OSError as exc:
                errors.append(str(exc))
        if self._mutex:
            if self._owns_mutex:
                kernel32.ReleaseMutex(self._mutex)
            kernel32.CloseHandle(self._mutex)
            self._mutex = None
            self._owns_mutex = False
        if errors:
            raise OSError("; ".join(errors))

    def __exit__(self, *exc_info):
        self.close()

    def map_path(self, path: Path) -> str:
        return self._mapped(str(path.resolve()))

    def _mapped(self, value: str) -> str:
        for root, letter, target in sorted(self._drives, key=lambda item: len(str(item[0])), reverse=True):
            value = value.replace(str(root), letter).replace(root.as_posix(), letter)
            value = re.sub(re.escape(letter) + r"(?=$|;)", lambda match: letter + "\\", value)
        return value

    def _mapped_overlay(self, path: Path, work_dir: Path) -> Path:
        self._check_output_path(path)
        self._check_output_path(work_dir)
        if path.stat().st_size > 16 << 20:
            raise ValueError("Generated VFS overlay exceeds the bounded host input limit")
        with path.open("r", encoding="utf-8") as file:
            document = json.loads(file.read(16 << 20))
        if document.get("redirecting-with") != "redirect-only":
            raise ValueError("Only closed file VFS overlays are supported")
        for entry in document.get("roots", []):
            if entry.get("type") != "file":
                raise ValueError("VFS directory remaps are prohibited")
            for key in ("name", "external-contents"):
                value = entry[key]
                if not any(_inside(Path(value), root) for root in self.readonly_roots):
                    raise ValueError("VFS entry is outside readonly sandbox roots")
                entry[key] = self._mapped(value).replace("\\", "/")
        mapped = work_dir / "appcontainer-vfs.json"
        self._check_output_path(mapped)
        mapped.write_text(json.dumps(document), encoding="utf-8")
        return mapped

    def _check_output_path(self, path: Path):
        # Check lexical containment first, without resolving guest-created links.
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.writable_root):
            raise ValueError("Host compiler I/O path lies outside writable staging")
        for current in [path, *path.parents]:
            if current == self.writable_root.parent:
                break
            try:
                status = current.lstat()
            except FileNotFoundError:
                continue
            if status.st_file_attributes & 0x400:
                raise ValueError("Host compiler I/O path contains a reparse point")

    def _read_output_text(self, path: Path):
        self._check_output_path(path)
        return _read_text(path)

    def _launch(self, argv, *, cwd, env, stdin, stdout, stderr, **unused):
        caps = SECURITY_CAPABILITIES(self.sid, None, 0, 0)
        lpac = wintypes.DWORD(1)
        attribute_size = SIZE_T()
        kernel32.InitializeProcThreadAttributeList(None, 3, 0, ctypes.byref(attribute_size))
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        _check(kernel32.InitializeProcThreadAttributeList(attribute_buffer, 3, 0, ctypes.byref(attribute_size)), "InitializeProcThreadAttributeList")
        handles = []
        null_input = open(os.devnull, "rb") if stdin == subprocess.DEVNULL else None
        try:
            for stream in [null_input or stdin, stdout, stderr]:
                copied = wintypes.HANDLE()
                process = kernel32.GetCurrentProcess()
                _check(kernel32.DuplicateHandle(process, msvcrt.get_osfhandle(stream.fileno()), process,
                    ctypes.byref(copied), 0, True, 2), "DuplicateHandle(stdio)")
                handles.append(copied.value)
            handle_array = (wintypes.HANDLE * 3)(*handles)
            for key, value in [(SECURITY_CAPABILITIES_ATTRIBUTE, caps),
                    (ALL_APPLICATION_PACKAGES_POLICY_ATTRIBUTE, lpac), (HANDLE_LIST_ATTRIBUTE, handle_array)]:
                _check(kernel32.UpdateProcThreadAttribute(attribute_buffer, 0, key, ctypes.byref(value),
                    ctypes.sizeof(value), None, None), "UpdateProcThreadAttribute")
            startup = STARTUPINFOEX()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
            startup.lpAttributeList = ctypes.cast(attribute_buffer, ctypes.c_void_p)
            environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            info = PROCESS_INFORMATION()
            _check(kernel32.CreateProcessW(argv[0], command, None, None, True,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | 0x08000004,
                environment, str(cwd), ctypes.byref(startup), ctypes.byref(info)), "CreateProcessW(LPAC)")
            process = _Process(info)
            token = wintypes.HANDLE()
            try:
                _check(advapi32.OpenProcessToken(process._handle, 8, ctypes.byref(token)), "OpenProcessToken")
                is_container = wintypes.DWORD()
                returned = wintypes.DWORD()
                _check(advapi32.GetTokenInformation(token, 29, ctypes.byref(is_container), 4, ctypes.byref(returned)), "TokenIsAppContainer")
                if is_container.value != 1:
                    raise OSError("Compiler process token is not AppContainer")
                # TOKEN_GROUPS starts with DWORD GroupCount; require zero granted capabilities.
                group_bytes = ctypes.create_string_buffer(65536)
                _check(advapi32.GetTokenInformation(token, 30, group_bytes, len(group_bytes), ctypes.byref(returned)), "TokenCapabilities")
                if wintypes.DWORD.from_buffer(group_bytes).value != 0:
                    raise OSError("Compiler token unexpectedly has capabilities")
            except BaseException:
                process.kill()
                process.wait(timeout=5)
                raise
            finally:
                if token:
                    kernel32.CloseHandle(token)
            return process
        finally:
            kernel32.DeleteProcThreadAttributeList(attribute_buffer)
            for handle in handles:
                kernel32.CloseHandle(handle)
            if null_input:
                null_input.close()

    def compile(self, request):
        started = time.perf_counter()
        try:
            if not self._created:
                raise OSError("AppContainerCompiler must be used inside a with block")
            compiler = request.compiler.resolve()
            allowed_names = {"clang.exe", "clang++.exe", "wasm-ld.exe", "wasm-opt.exe"}
            if not self.readonly_roots or compiler.parent != self.readonly_roots[0] / "bin" or compiler.name.lower() not in allowed_names:
                raise ValueError("Only fixed WASI toolchain compiler executables are allowed")
            for unit in request.source_units:
                if not any(_inside(unit, root) for root in self.readonly_roots) and not _inside(unit, self.writable_root):
                    raise ValueError("Source outside staged sandbox roots")
            for directory in request.include_dirs:
                if not any(_inside(directory, root) for root in self.readonly_roots):
                    raise ValueError("Include directory outside readonly staging")
            if not _inside(request.output, self.writable_root) or not _inside(request.work_dir, self.writable_root):
                raise ValueError("Output/work directory outside writable staging")
            self._check_output_path(request.work_dir)
            self._check_output_path(request.output)
            request.work_dir.mkdir(parents=True, exist_ok=True)
            request.output.parent.mkdir(parents=True, exist_ok=True)
            stdout_path = request.work_dir / "compile.stdout.txt"
            stderr_path = request.work_dir / "compile.stderr.txt"
            flags = list(request.flags)
            if "-ivfsoverlay" in flags:
                index = flags.index("-ivfsoverlay") + 1
                flags[index] = str(self._mapped_overlay(Path(flags[index]), request.work_dir))
            argv = [str(compiler), *flags, *("-I" + str(p) for p in request.include_dirs),
                *(str(p) for p in request.source_units), "-o", str(request.output)]
            system = os.environ.get("SystemRoot", r"C:\Windows")
            env = {"SystemRoot": system, "WINDIR": system,
                "PATH": str(compiler.parent) + os.pathsep + str(Path(system) / "System32"),
                "TEMP": str(request.work_dir), "TMP": str(request.work_dir),
                "USERPROFILE": str(request.work_dir), "LOCALAPPDATA": str(request.work_dir),
                "APPDATA": str(request.work_dir),
                "NUMBER_OF_PROCESSORS": "1", "BINARYEN_CORES": "1"}
            argv = [self._mapped(arg) for arg in argv]
            env = {key: self._mapped(value) for key, value in env.items()}
            with JobObject(cpu_ms=request.limits.compile_cpu_ms,
                    memory_bytes=request.limits.compile_memory_bytes,
                    active_process_limit=request.limits.compile_process_count,
                    cpu_rate_percent=request.limits.cpu_rate_percent) as job:
                def execute(command, step):
                    nonlocal stdout_path, stderr_path
                    stdout_path = request.work_dir / f"compile-{step}.stdout.txt"
                    stderr_path = request.work_dir / f"compile-{step}.stderr.txt"
                    # Previous compiler steps cannot trick the host's next
                    # stdio open into truncating a linked private host file.
                    self._check_output_path(stdout_path)
                    self._check_output_path(stderr_path)
                    remaining = request.limits.compile_wall_ms - int((time.perf_counter() - started) * 1000)
                    return spawn_in_job(command, cwd=request.work_dir, env=env, job=job,
                        stdin_path=None, stdout_path=stdout_path, stderr_path=stderr_path,
                        wall_timeout_ms=max(1, remaining), output_limit=request.limits.output_bytes,
                        workdir_limit=request.limits.workdir_bytes, cancelled=request.cancelled,
                        process_factory=self._launch, cpu_limit_ms=request.limits.compile_cpu_ms,
                        monitored_root=self.writable_root)
                # A restricted compiler cannot itself spawn its cc1/linker.
                # The host brokers only the trusted driver's -### plan, and
                # launches each selected tool into a new equally restricted
                # token. No compiler-controlled command is executed natively.
                if compiler.name.lower() in {"clang.exe", "clang++.exe"}:
                    exit_code, timed_out, wall_ms, accounting, _ = execute([argv[0], "-###", *argv[1:]], "plan")
                    plan_stderr = self._read_output_text(stderr_path)
                    if exit_code or timed_out or accounting.stop_reason:
                        commands = []
                    else:
                        commands = []
                        allowed_tools = {self.map_path(compiler), self.map_path(compiler.parent / "clang.exe"),
                            self.map_path(compiler.parent / "clang++.exe"), self.map_path(compiler.parent / "wasm-ld.exe"),
                            self.map_path(compiler.parent / "wasm-opt.exe")}
                        for line in plan_stderr.splitlines():
                            if line.startswith(' "'):
                                command = shlex.split(line, posix=True)
                                if command and not Path(command[0]).suffix:
                                    command[0] += ".exe"
                                if not command or ntpath.normcase(ntpath.normpath(command[0])) not in {ntpath.normcase(ntpath.normpath(p)) for p in allowed_tools}:
                                    raise ValueError("Compiler plan selected a tool outside the WASI toolchain: " + (command[0] if command else "empty command"))
                                commands.append(command)
                        if not commands or len(commands) > 32:
                            raise ValueError("No bounded WASI compilation plan was produced")
                else:
                    commands = [argv]
                stdout_parts, stderr_parts = [], []
                for index, command in enumerate(commands):
                    exit_code, timed_out, wall_ms, accounting, _ = execute(command, str(index))
                    stdout_parts.append(self._read_output_text(stdout_path))
                    stderr_parts.append(self._read_output_text(stderr_path))
                    if exit_code or timed_out or accounting.stop_reason:
                        break
                stdout = "".join(stdout_parts) if commands else self._read_output_text(stdout_path)
                stderr = "".join(stderr_parts) if commands else self._read_output_text(stderr_path)
            wall_ms = int((time.perf_counter() - started) * 1000)
            if timed_out or accounting.stop_reason:
                return CompileOutcome(CompileStatus.CANCELLED if accounting.stop_reason == "CANCELLED" else CompileStatus.RESOURCE_LIMIT,
                    exit_code, stdout, stderr, wall_ms, None, FaultOwner.UNKNOWN,
                    accounting.stop_reason or "COMPILE_WALL_TIME_LIMIT")
            # Validate lexical containment/reparse attributes before even a
            # metadata query that could follow a guest-created output link.
            self._check_output_path(request.output)
            if exit_code != 0 or not request.output.is_file():
                return CompileOutcome(CompileStatus.COMPILER_ERROR, exit_code, stdout, stderr, wall_ms,
                    None, FaultOwner.SUBMISSION, "COMPILE_ERROR")
            # The host must never follow a compiler-created output link when
            # hashing/copying artifacts after the restricted process exits.
            if request.output.stat().st_size > request.limits.workdir_bytes:
                return CompileOutcome(CompileStatus.RESOURCE_LIMIT, exit_code, stdout, stderr,
                    wall_ms, None, FaultOwner.UNKNOWN, "DISK_LIMIT")
            return CompileOutcome(CompileStatus.SUCCESS, exit_code, stdout, stderr, wall_ms,
                sha256_hex(request.output.read_bytes()), FaultOwner.NONE)
        except (OSError, ValueError) as exc:
            return CompileOutcome(CompileStatus.INFRA_ERROR, None, "", str(exc),
                int((time.perf_counter() - started) * 1000), None, FaultOwner.INFRA,
                "APPCONTAINER_COMPILE_UNAVAILABLE")

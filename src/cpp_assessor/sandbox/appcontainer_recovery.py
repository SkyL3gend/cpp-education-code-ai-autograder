"""Host-only recovery journal for interrupted WASI compiler containers.

Records are deliberately outside all guest-readable/writable trees. Recovery
accepts only this user's dead process, the current SDK, a derived profile SID,
and fixed source/output names below the current wasm_staging/compile-* root.
It never removes an unrelated DOS mapping or recursively deletes any files.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import time
import uuid

from ..config import data_dir

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_a = ctypes.WinDLL("advapi32", use_last_error=True)
_u = ctypes.WinDLL("userenv", use_last_error=True)
_PROFILE = re.compile(r"cppassessor-[0-9a-f]{32}\Z")
_COMPILE = re.compile(r"compile-[A-Za-z0-9_-]{6,64}\Z")
_SID = re.compile(r"S-1-15-2-(?:[0-9]+-){6}[0-9]+\Z")
_FULL_CONTROL = 0x1F01FF
_FILE_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.20, 0.20, 0.20)

_k.GetCurrentProcess.restype = wintypes.HANDLE
_k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k.OpenProcess.restype = wintypes.HANDLE
_k.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
_k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k.CloseHandle.argtypes = [wintypes.HANDLE]
_k.LocalFree.argtypes = [ctypes.c_void_p]
_k.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_k.QueryDosDeviceW.restype = wintypes.DWORD
_k.DefineDosDeviceW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR]
_k.DefineDosDeviceW.restype = wintypes.BOOL
_a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_a.FreeSid.argtypes = [ctypes.c_void_p]
_a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
_a.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
_a.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
    ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
_a.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_a.SetNamedSecurityInfoW.restype = wintypes.DWORD
_a.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_a.GetNamedSecurityInfoW.restype = wintypes.DWORD
_a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_u.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
_u.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
_u.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
_u.DeleteAppContainerProfile.restype = ctypes.c_long


class _ACL(ctypes.Structure):
    _fields_ = [("AclRevision", wintypes.BYTE), ("Sbz1", wintypes.BYTE),
        ("AclSize", wintypes.WORD), ("AceCount", wintypes.WORD), ("Sbz2", wintypes.WORD)]


def _require(ok, action):
    if not ok:
        raise OSError(ctypes.get_last_error(), action)


def _sid_text(sid):
    text = wintypes.LPWSTR()
    _require(_a.ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSidW")
    try:
        return text.value
    finally:
        _k.LocalFree(text)


def user_sid():
    token, needed = wintypes.HANDLE(), wintypes.DWORD()
    _require(_a.OpenProcessToken(_k.GetCurrentProcess(), 8, ctypes.byref(token)), "OpenProcessToken")
    try:
        _a.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        _require(_a.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)), "TokenUser")
        return _sid_text(ctypes.c_void_p.from_buffer(buffer).value)
    finally:
        _k.CloseHandle(token)


def derived_profile_sid(name):
    if not _PROFILE.fullmatch(name):
        raise ValueError("Invalid compiler profile name")
    sid = ctypes.c_void_p()
    result = _u.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid))
    if result < 0:
        raise OSError(result, "Unable to derive compiler profile SID")
    try:
        return _sid_text(sid)
    finally:
        _a.FreeSid(sid)


def _creation_time(handle):
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    _require(_k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
        ctypes.byref(kernel), ctypes.byref(user)), "GetProcessTimes")
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def parent_is_dead(pid, created):
    """False includes access denied/unknown: uncertainty must never authorize cleanup."""
    handle = _k.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # nonexistent PID, not access denied
    try:
        if _creation_time(handle) != created:
            return True  # PID was reused; original process has exited
        return _k.WaitForSingleObject(handle, 0) == 0
    except OSError:
        return False
    finally:
        _k.CloseHandle(handle)


def _no_reparse(path):
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    if status.st_file_attributes & 0x400:
        raise ValueError("Recovery paths may not be reparse points")


def _private_acl(path, owner, *, directory):
    """Verify inherited and explicit ACEs grant only trusted host principals."""
    _no_reparse(path)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Host recovery journal has an unexpected file type")
    sd, acl, sid = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    error = _a.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(sid), None,
        ctypes.byref(acl), None, ctypes.byref(sd))
    if error:
        raise OSError(error, "Unable to inspect host recovery journal permissions")
    try:
        trusted = {owner, "S-1-5-18", "S-1-5-32-544"}
        if _sid_text(sid) not in trusted:
            raise ValueError("Recovery journal has an untrusted owner")
        control, revision = wintypes.WORD(), wintypes.DWORD()
        _require(_a.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)), "GetSecurityDescriptorControl")
        if directory and not control.value & 0x1000:
            raise ValueError("Host journal directory must have a protected DACL")
        if not acl or ctypes.cast(acl, ctypes.POINTER(_ACL)).contents.AceCount != 3:
            raise ValueError("Unexpected host journal permissions")
        found = set()
        for index in range(3):
            ace = ctypes.c_void_p()
            _require(_a.GetAce(acl, index, ctypes.byref(ace)), "GetAce")
            header = (ctypes.c_ubyte * 4).from_address(ace.value)
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            if header[0] != 0 or header[1] & ~0x13 or mask != _FULL_CONTROL:
                raise ValueError("Unexpected host journal ACE")
            found.add(_sid_text(ace.value + 8))
        if found != trusted:
            raise ValueError("Host journal grants a non-host security principal")
    finally:
        _k.LocalFree(sd)


def _journal_directory(base, owner):
    path = base / ".host-journals"
    if not path.exists():
        path.mkdir()
        sd, acl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        _require(_a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{owner})", 1,
            ctypes.byref(sd), None), "ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            _require(_a.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)), "GetSecurityDescriptorDacl")
            error = _a.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None)
            if error:
                raise OSError(error, "Unable to protect host-only journal directory")
        finally:
            _k.LocalFree(sd)
    _private_acl(path, owner, directory=True)
    return path


def validate_record(record, *, base, sdk, owner):
    """Validate every cleanup target independently of the journal's trust."""
    required = {"version", "owner", "profile", "sid", "sdk", "source", "output", "pid", "created", "drives"}
    if not isinstance(record, dict) or set(record) != required or record["version"] != 1 or record["owner"] != owner:
        raise ValueError("Invalid compiler recovery journal schema/owner")
    if not isinstance(record["profile"], str) or not _PROFILE.fullmatch(record["profile"]):
        raise ValueError("Invalid compiler recovery profile")
    if not isinstance(record["sid"], str) or not _SID.fullmatch(record["sid"]) or record["sid"] != derived_profile_sid(record["profile"]):
        raise ValueError("Compiler recovery SID does not match its profile")
    if type(record["pid"]) is not int or not 0 < record["pid"] <= 0xFFFFFFFF or type(record["created"]) is not int or record["created"] <= 0:
        raise ValueError("Invalid parent process identity")
    if not all(isinstance(record[key], str) for key in ("sdk", "source", "output")):
        raise ValueError("Invalid compiler recovery paths")
    toolchain, source, output = (Path(record[key]) for key in ("sdk", "source", "output"))
    for path in (toolchain, source, output):
        if not path.is_absolute() or path != Path(os.path.abspath(path)):
            raise ValueError("Recovery paths must be normalized and absolute")
    root = source.parent
    if toolchain != sdk or root.parent != base or not _COMPILE.fullmatch(root.name) or source != root / "source" or output != root / "output":
        raise ValueError("Recovery paths are outside the current toolchain/staging")
    for path in (base, root, source, output, toolchain):
        _no_reparse(path)
    # Refuse any descendant reparse point before inherited ACL propagation.
    for path in (toolchain, source, output):
        if path.exists():
            for current, directories, files in os.walk(path, followlinks=False):
                for name in [*directories, *files]:
                    _no_reparse(Path(current) / name)
    drives = record["drives"]
    allowed = {"\\??\\" + str(path) for path in (toolchain, source, output)}
    if not isinstance(drives, list) or len(drives) > 3:
        raise ValueError("Invalid compiler recovery drive list")
    letters = set()
    for drive in drives:
        if (not isinstance(drive, dict) or set(drive) != {"letter", "target"}
                or not isinstance(drive["letter"], str) or not isinstance(drive["target"], str)
                or not re.fullmatch(r"[D-Z]:", drive["letter"]) or drive["target"] not in allowed
                or drive["letter"] in letters):
            raise ValueError("Invalid compiler recovery DOS mapping")
        letters.add(drive["letter"])
    return record


def _remove_exact_drive(letter, target):
    buffer = ctypes.create_unicode_buffer(32768)
    length = _k.QueryDosDeviceW(letter, buffer, len(buffer))
    if not length:
        if ctypes.get_last_error() in (2, 3):
            return
        raise OSError(ctypes.get_last_error(), "Unable to inspect compiler DOS mapping")
    targets = [item for item in buffer[:length].split("\0") if item]
    # QueryDosDevice can expose a stack; only remove the exact sole mapping.
    if targets != [target]:
        return
    _require(_k.DefineDosDeviceW(1 | 2 | 4 | 8, letter, target), "Unable to remove stale compiler DOS mapping")


def _retry_journal_file(operation):
    """Bound transient Windows sharing locks on already-private host files.

    A read handle without FILE_SHARE_DELETE can make an atomic replacement
    report ACCESS_DENIED (5), not only SHARING_VIOLATION (32). Retry the same
    operation at most eight times, with 0.9 seconds of total backoff. Persistent
    permission errors and all other failures still abort; no ACL, destination,
    atomicity or recovery validation is changed. Never use this for ACL grants.
    """
    for attempt in range(len(_FILE_RETRY_DELAYS) + 1):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == len(_FILE_RETRY_DELAYS):
                raise
            time.sleep(_FILE_RETRY_DELAYS[attempt])


class CompilerJournal:
    """Caller holds the compiler cross-process mutex throughout this lifetime."""
    def __init__(self, *, sdk, source, output, profile, revoke):
        self.base = (data_dir() / "wasm_staging").resolve()
        self.sdk, self.owner = Path(sdk).resolve(), user_sid()
        self.directory = _journal_directory(self.base, self.owner)
        self.revoke = revoke
        self.recover()
        self.record = {"version": 1, "owner": self.owner, "profile": profile,
            "sid": derived_profile_sid(profile), "sdk": str(self.sdk),
            "source": str(Path(source).resolve()), "output": str(Path(output).resolve()),
            "pid": os.getpid(), "created": _creation_time(_k.GetCurrentProcess()), "drives": []}
        validate_record(self.record, base=self.base, sdk=self.sdk, owner=self.owner)
        self.path = self.directory / (profile + ".json")
        self._write(create=True)

    def _write(self, *, create=False):
        temporary = self.path.with_name(self.path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as file:
                json.dump(self.record, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            _private_acl(temporary, self.owner, directory=False)
            if create and self.path.exists():
                raise ValueError("Compiler recovery journal collision")
            _retry_journal_file(lambda: os.replace(temporary, self.path))
        finally:
            if temporary.exists():
                _retry_journal_file(temporary.unlink)

    def record_drive(self, letter, target):
        self.record["drives"].append({"letter": letter, "target": target})
        validate_record(self.record, base=self.base, sdk=self.sdk, owner=self.owner)
        self._write()

    def complete(self):
        _retry_journal_file(lambda: self.path.unlink(missing_ok=True))

    def recover(self):
        for path in self.directory.glob("*.json"):
            _private_acl(path, self.owner, directory=False)
            if path.stat().st_size > 65536 or not _PROFILE.fullmatch(path.stem):
                raise ValueError("Invalid compiler recovery journal file")
            # A metadata size check alone does not bound allocation if a trusted
            # host process races an update. Guest tokens cannot access this
            # directory; still cap the actual read before decoding/parsing.
            with path.open("rb") as file:
                document = file.read(65537)
            if len(document) > 65536:
                raise ValueError("Compiler recovery journal exceeds its size limit")
            record = json.loads(document.decode("utf-8"))
            validate_record(record, base=self.base, sdk=self.sdk, owner=self.owner)
            if path.stem != record["profile"]:
                raise ValueError("Recovery journal filename/profile mismatch")
            if not parent_is_dead(record["pid"], record["created"]):
                continue
            for drive in reversed(record["drives"]):
                _remove_exact_drive(drive["letter"], drive["target"])
            for key in ("output", "source", "sdk"):
                target = Path(record[key])
                if target.exists():
                    self.revoke(target, record["sid"])
            result = _u.DeleteAppContainerProfile(record["profile"])
            if result < 0 and (result & 0xFFFFFFFF) not in (0x80070002, 0x80070003):
                raise OSError(result, "Unable to remove stale compiler profile")
            _retry_journal_file(path.unlink)

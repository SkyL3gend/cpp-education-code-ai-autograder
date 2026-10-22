"""Experimental Windows/WASI backend, explicitly not a production attestation.

WASI guests receive stdio only. Native clang compilation requires AppContainer
and a closed VFS, with disposable staged inputs and outputs. The pilot has not
completed production attestation. No native student execution fallback exists.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.metadata
from functools import lru_cache
import json
import os
import platform
from pathlib import Path
import sys
import time

from ..config import PROJECT_ROOT
from ..domain.enums import CompileStatus, FaultOwner, IsolationLevel, Termination
from ..domain.models import SandboxCapabilities
from .base import CompileOutcome
from .local import LocalJobBackend
from .wasm_staging import staged_compile

PILOT_TASK = "t1_phone_validation"


def sdk_root() -> Path:
    return Path(os.environ.get("ASSESSOR_WASI_SDK", str(PROJECT_ROOT / "tools" / "wasi-sdk"))).resolve()


@lru_cache(maxsize=8)
def _tool_fingerprints(files):
    return [hashlib.file_digest(Path(path).open("rb"), "sha256").hexdigest()
            for path, _size, _modified in files]


class WasmPilotBackend:
    backend_id = "wasm_pilot"
    simulated = False

    def capabilities(self):
        ready = all((sdk_root() / ("bin/" + name)).is_file() for name in ("clang++.exe", "wasm-ld.exe", "wasm-opt.exe"))
        try:
            version = importlib.metadata.version("wasmtime")
        except importlib.metadata.PackageNotFoundError:
            version, ready = "missing", False
        fingerprints = ()
        if ready:
            tools = [sdk_root() / ("bin/" + name) for name in ("clang++.exe", "wasm-ld.exe", "wasm-opt.exe")]
            fingerprints = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in tools)
        digest = {"policy": "wasi-p1-stdio-appcontainer-compiler-v3", "wasmtime": version,
                  "tools": _tool_fingerprints(fingerprints) if ready else []}
        return SandboxCapabilities(
            schema_version="1.0", backend_id=self.backend_id,
            environment_digest=hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest(),
            isolation=IsolationLevel.WASM_RUNTIME, network_block=False,
            cpu_limit=ready, memory_limit=ready, process_limit=ready,
            wall_timeout=ready, tree_kill=ready, clean_reset=True,
            filesystem_isolation=False,
            notes=["试验：WASI 仅开放标准输入输出，不开放文件目录、网络、环境变量或宿主命令。",
                   "编译必须使用零能力 AppContainer、只读输入及独立输出目录；隔离不可用即拒绝编译。此试验尚未完成正式隔离能力验收。"],
        )

    def compile(self, request):
        started = time.monotonic()
        sdk = sdk_root()
        compiler = sdk / "bin/clang++.exe"
        allowed = {"-std=c++17", "-std=c++14", "-std=c++11", "-std=c++20",
                   "-O0", "-O1", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-c",
                   "-finput-charset=UTF-8"}
        unsupported = set(request.flags) - allowed
        if unsupported:
            return CompileOutcome(CompileStatus.COMPILER_ERROR, None, "",
                "WASM 试验不支持这些编译参数（不会回退本机执行）：" + str(sorted(unsupported)),
                0, None, FaultOwner.UNKNOWN, "WASM_UNSUPPORTED_FLAGS")
        request.work_dir.mkdir(parents=True, exist_ok=True)
        # The host compiler parses untrusted source. It must be isolated even
        # though the resulting guest is WebAssembly. No native fallback.
        try:
            from .appcontainer import AppContainerCompiler
            with staged_compile(request) as (staged, source_root, output_root):
                with AppContainerCompiler(readonly_roots=[sdk, source_root], writable_root=output_root,
                                         recover_staging=True) as compiler_sandbox:
                    outcome = self._compile_staged(staged, sdk, source_root, compiler_sandbox, started)
                if outcome.status == CompileStatus.SUCCESS:
                    request.output.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copyfile(staged.output, request.output)
                return replace(outcome, duration_ms=int((time.monotonic() - started) * 1000))
        except (OSError, RuntimeError, ValueError) as exc:
            return CompileOutcome(CompileStatus.INFRA_ERROR, None, "",
                f"无法建立编译隔离，已拒绝编译且不会回退：{exc}",
                int((time.monotonic() - started) * 1000), None, FaultOwner.INFRA,
                "COMPILER_ISOLATION_UNAVAILABLE")

    def _compile_staged(self, request, sdk, source_root, compiler_sandbox, started):
        compiler = sdk / "bin/clang++.exe"
        # Enumerated files, not directory remaps: even ../ cannot expose other
        # host files. This controls ordinary clang reads, not compiler exploits.
        roots = []
        directories = [sdk / "share/wasi-sysroot/include/wasm32-wasip1", sdk / "lib/clang", *request.include_dirs]
        files = set(p.resolve() for p in request.source_units)
        for directory in directories:
            if directory.exists():
                files.update(p.resolve() for p in directory.rglob("*") if p.is_file() and not p.is_symlink())
        for path in sorted(files):
            roots.append({"type": "file", "name": path.as_posix(), "external-contents": path.as_posix()})
        overlay = request.work_dir / "wasm-vfs.json"
        overlay.write_text(json.dumps({"version": 0, "case-sensitive": False,
            "redirecting-with": "redirect-only", "roots": roots}), encoding="utf-8")
        flags = ["--target=wasm32-wasip1", "--sysroot=" + str(sdk / "share/wasi-sysroot"),
                 "-fwasm-exceptions", "-isystem", str(sdk / "share/wasi-sysroot/include/wasm32-wasip1/eh/c++/v1"),
                 "-L" + str(sdk / "share/wasi-sysroot/lib/wasm32-wasip1/eh"),
                 "-ivfsoverlay", str(overlay.resolve()), *request.flags]
        if "-c" not in request.flags:
            flags.append("-lunwind")
        result = compiler_sandbox.compile(replace(request, compiler=compiler, flags=flags, env={}))
        if result.status != CompileStatus.SUCCESS or "-c" in request.flags:
            return result
        remaining = request.limits.compile_wall_ms - int((time.monotonic() - started) * 1000)
        if remaining <= 0:
            return replace(result, status=CompileStatus.RESOURCE_LIMIT, artifact_hash=None, issue_code="COMPILE_WALL_TIME_LIMIT")
        # LLVM's prebuilt libc++ uses legacy EH. Translate the whole module to
        # standardized exnref without altering source or suppressing exceptions.
        converted = request.output.with_suffix(".converted.wasm")
        import shutil
        linked_input = source_root / "linked.wasm"
        shutil.copyfile(request.output, linked_input)
        conversion = compiler_sandbox.compile(replace(request,
            compiler=sdk / "bin/wasm-opt.exe", source_units=[linked_input], include_dirs=[],
            flags=["--enable-exception-handling", "--emit-exnref"], output=converted,
            work_dir=request.work_dir / "wasm-conversion",
            limits=request.limits.model_copy(update={"compile_wall_ms": remaining}),
            env={"BINARYEN_CORES": "1"}))
        if conversion.status == CompileStatus.SUCCESS:
            converted.replace(request.output)
        return replace(conversion, stdout=result.stdout + conversion.stdout,
            stderr=result.stderr + conversion.stderr,
            duration_ms=int((time.monotonic() - started) * 1000))

    def run(self, request):
        request.work_dir.mkdir(parents=True, exist_ok=True)
        spec = request.work_dir / "wasm-worker.json"
        spec.write_text(json.dumps({"module": str(request.executable.resolve()),
            "stdin": str(request.stdin_path.resolve()), "argv": request.argv,
            "fuel": max(100000, request.limits.case_cpu_ms * 50000),
            "memory_bytes": request.limits.case_memory_bytes,
            "output_bytes": request.limits.output_bytes}), encoding="utf-8")
        if getattr(sys, "frozen", False):
            argv = ["--wasm-worker", str(spec.resolve())]
        else:
            argv = [str(Path(__file__).with_name("wasm_worker.py")), str(spec.resolve())]
        outcome = LocalJobBackend().run(replace(request, executable=Path(sys.executable), argv=argv,
            env={"PROCESSOR_ARCHITECTURE": platform.machine(), "PYTHONUTF8": "1"}))
        if outcome.exit_code in {73, 75}:
            outcome = replace(outcome, termination=Termination.CPU_TIME_LIMIT if outcome.exit_code == 73 else Termination.OUTPUT_LIMIT,
                              fault_owner=FaultOwner.SUBMISSION,
                              resource_events=[*outcome.resource_events, "WASM_FUEL_EXHAUSTED" if outcome.exit_code == 73 else "WASM_OUTPUT_LIMIT"])
        elif outcome.exit_code == 76:
            outcome = replace(outcome, termination=Termination.INFRA_ERROR,
                fault_owner=FaultOwner.INFRA, resource_events=[*outcome.resource_events, "WASM_RUNTIME_ERROR"])
        return outcome

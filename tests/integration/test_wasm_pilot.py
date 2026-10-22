"""Real Wasmtime isolation probes; no model requests or SDK required."""
import json
import os
from pathlib import Path

import pytest

wasmtime = pytest.importorskip("wasmtime")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="pilot uses Windows Job Objects")

from cpp_assessor.domain.enums import Termination
from cpp_assessor.domain.models import ResourceLimits
from cpp_assessor.sandbox.base import RunRequest
from cpp_assessor.sandbox.wasm import WasmPilotBackend


def limits():
    root = Path(__file__).resolve().parents[2]
    return ResourceLimits.model_validate(json.loads((root / "task_library/tasks/t1_phone_validation/1.0.0/task.json").read_text(encoding="utf-8"))["limits"])


def execute(tmp_path, wat, **changes):
    module = tmp_path / "test.wasm"
    module.write_bytes(wasmtime.wat2wasm(wat))
    stdin = tmp_path / "stdin.txt"
    stdin.write_text("", encoding="utf-8")
    return WasmPilotBackend().run(RunRequest(module, [], stdin, tmp_path / "out", tmp_path / "err", tmp_path,
                                             limits().model_copy(update=changes)))


def test_minimal_module_exits(tmp_path):
    result = execute(tmp_path, '(module (func (export "_start")))')
    assert result.termination == Termination.EXITED, (result, (tmp_path / "err").read_text())


def test_infinite_loop_stops_by_fuel(tmp_path):
    result = execute(tmp_path, '(module (func (export "_start") (loop $again br $again)))')
    assert result.termination == Termination.CPU_TIME_LIMIT
    assert "WASM_FUEL_EXHAUSTED" in result.resource_events


def test_host_function_import_denied(tmp_path):
    result = execute(tmp_path, '(module (import "env" "system" (func)) (func (export "_start") call 0))')
    assert result.termination == Termination.CRASH
    assert "unknown import" in (tmp_path / "err").read_text()


def test_no_preopened_host_directory(tmp_path):
    result = execute(tmp_path, '''(module
      (import "wasi_snapshot_preview1" "fd_prestat_get" (func $pre (param i32 i32) (result i32)))
      (memory (export "memory") 1)
      (func (export "_start")
        i32.const 3 i32.const 0 call $pre i32.eqz if unreachable end))''')
    assert result.termination == Termination.EXITED


def test_memory_growth_is_limited(tmp_path):
    result = execute(tmp_path, '''(module (memory (export "memory") 1)
      (func (export "_start") i32.const 10000 memory.grow i32.const -1 i32.ne if unreachable end))''')
    assert result.termination == Termination.EXITED


def test_native_executable_is_never_run(tmp_path):
    module = tmp_path / "native.exe"
    module.write_bytes(b"MZnotawasm")
    stdin = tmp_path / "stdin"
    stdin.write_bytes(b"")
    result = WasmPilotBackend().run(RunRequest(module, [], stdin, tmp_path / "out", tmp_path / "err", tmp_path, limits()))
    assert result.termination == Termination.CRASH


def test_output_is_bounded(tmp_path):
    result = execute(tmp_path, '''(module
      (import "wasi_snapshot_preview1" "fd_write" (func $write (param i32 i32 i32 i32) (result i32)))
      (memory (export "memory") 1) (data (i32.const 32) "abcdefgh")
      (func (export "_start")
       i32.const 0 i32.const 32 i32.store
       i32.const 4 i32.const 8 i32.store
       (loop $again i32.const 1 i32.const 0 i32.const 1 i32.const 16 call $write drop br $again)))''', output_bytes=128)
    assert result.termination == Termination.OUTPUT_LIMIT
    assert (tmp_path / "out").stat().st_size <= 128


def compile_cpp(tmp_path, source):
    from cpp_assessor.sandbox.wasm import sdk_root
    from cpp_assessor.sandbox.base import CompileRequest
    from cpp_assessor.domain.models import BuildPlan
    if not (sdk_root() / "bin/clang++.exe").is_file():
        pytest.skip("optional WASI SDK not installed")
    unit = tmp_path / "program.cpp"
    unit.write_text(source, encoding="utf-8")
    return WasmPilotBackend().compile(CompileRequest(BuildPlan.model_construct(), Path("unused"), [unit], [tmp_path],
        ["-std=c++17", "-O2"], tmp_path / "program.wasm", tmp_path, limits()))


def test_cpp_compiler_and_stdio(tmp_path):
    from cpp_assessor.domain.enums import CompileStatus
    outcome = compile_cpp(tmp_path, '#include <iostream>\nint main(){std::cout << "hello-wasi";}')
    assert outcome.status == CompileStatus.SUCCESS, outcome.stderr
    assert (tmp_path / "program.wasm").read_bytes().startswith(b"\0asm")
    stdin = tmp_path / "stdin"
    stdin.write_bytes(b"")
    result = WasmPilotBackend().run(RunRequest(tmp_path / "program.wasm", [], stdin, tmp_path / "out", tmp_path / "err", tmp_path, limits()))
    assert result.termination == Termination.EXITED, (tmp_path / "err").read_text(encoding="utf-8")
    assert (tmp_path / "out").read_text() == "hello-wasi"


def test_compiler_cannot_include_outside_vfs(tmp_path):
    from cpp_assessor.domain.enums import CompileStatus
    secret = tmp_path / "outside.h"
    secret.write_text('#error PRIVATE_TEST_SENTINEL', encoding="utf-8")
    work = tmp_path / "compile"
    work.mkdir()
    outcome = compile_cpp(work, '#include "' + secret.as_posix() + '"\nint main(){}')
    assert outcome.status == CompileStatus.COMPILER_ERROR
    assert "PRIVATE_TEST_SENTINEL" not in outcome.stderr
    assert "file not found" in outcome.stderr

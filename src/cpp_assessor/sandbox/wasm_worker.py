"""Private WASI Preview 1 worker. No preopened directories, environment or sockets.

The parent runs this trusted worker in a resource-limited Windows Job Object.
Only validated WebAssembly is instantiated; native executables are never run.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(config_path: str) -> int:
    import wasmtime

    spec = json.loads(Path(config_path).read_text(encoding="utf-8"))
    config = wasmtime.Config()
    config.consume_fuel = True
    config.wasm_exceptions = True
    config.wasm_threads = False
    config.wasm_memory64 = False
    config.parallel_compilation = False
    engine = wasmtime.Engine(config)
    store = wasmtime.Store(engine)
    store.set_fuel(spec["fuel"])
    store.set_limits(memory_size=spec["memory_bytes"], instances=1, memories=1,
                     tables=4, table_elements=10000)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["submission.wasm", *spec["argv"]]
    wasi.stdin_file = spec["stdin"]
    count = 0
    exceeded = False

    def output(stream):
        def write(data):
            nonlocal count, exceeded
            available = max(0, spec["output_bytes"] - count)
            stream.write(data[:available])
            stream.flush()
            count += len(data)
            if count > spec["output_bytes"]:
                exceeded = True
                # The guest may ignore an I/O error and keep issuing hostcalls.
                # Exit the disposable worker immediately; the parent maps 75.
                os._exit(75)
            return len(data)
        return write

    wasi.stdout_custom = output(sys.stdout.buffer)
    wasi.stderr_custom = output(sys.stderr.buffer)
    store.set_wasi(wasi)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    try:
        module = wasmtime.Module.from_file(engine, spec["module"])
        instance = linker.instantiate(store, module)
        instance.exports(store)["_start"](store)
        return 75 if exceeded else 0
    except wasmtime.ExitTrap as exc:
        return 75 if exceeded else (0 if exc.code == 0 else 1)
    except wasmtime.Trap as exc:
        print(str(exc), file=sys.stderr)
        return 75 if exceeded else 73 if exc.trap_code == wasmtime.TrapCode.OUT_OF_FUEL else 1
    except wasmtime.WasmtimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def guarded_main(config_path: str) -> int:
    try:
        return main(config_path)
    except Exception as exc:
        print(f"WASM_RUNTIME_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 76


if __name__ == "__main__":
    raise SystemExit(guarded_main(sys.argv[1]))

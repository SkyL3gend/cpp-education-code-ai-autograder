"""Build a local Windows x64 pilot bundle, never containing keys or run data.

Usage: python tools/build_wasm_portable.py
Requires maintainer dependencies: pyinstaller, project[wasm], prepared WASI SDK.
"""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import ssl
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "cpp-assessor-wasm-pilot"


def main():
    sdk = ROOT / "tools/wasi-sdk"
    for tool in ("clang++.exe", "wasm-ld.exe", "wasm-opt.exe"):
        if not (sdk / "bin" / tool).is_file():
            raise SystemExit("Prepare WASI SDK and Binaryen first: " + tool)
    extra_data = []
    # Some managed Python distributions keep public trust-policy data beside
    # ssl.py. Preserve it when freezing; never disable certificate validation.
    trust_data = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if trust_data.is_file():
        extra_data = ["--add-data", str(trust_data) + os.pathsep + "."]
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--name", NAME, "--paths", str(ROOT / "src"),
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/wasm-portable"),
        "--specpath", str(ROOT / "build"),
        "--collect-all", "wasmtime", "--copy-metadata", "wasmtime",
        "--add-data", str(ROOT / "src/cpp_assessor/interfaces/web/templates") + os.pathsep + "cpp_assessor/interfaces/web/templates",
        "--add-data", str(ROOT / "src/cpp_assessor/interfaces/web/static") + os.pathsep + "cpp_assessor/interfaces/web/static",
        *extra_data, str(ROOT / "tools/wasm_portable_entry.py")], check=True, cwd=ROOT)
    out = ROOT / "dist" / NAME
    for folder in ("task_library", "skills/cpp-microchange-judge-author", "examples/wasm_phone", "examples/wasm_birthday", "examples/wasm_date"):
        shutil.copytree(ROOT / folder, out / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Only WASIp1, exceptions enabled, no LTO, no debugger/clang shared library.
    for p in sdk.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(sdk)
        name = rel.as_posix()
        keep = (name in {"bin/clang++.exe", "bin/clang++.cfg", "bin/wasm-ld.exe", "bin/wasm-opt.exe"}
                or name.startswith("lib/clang/23/include/")
                or name.startswith("lib/clang/23/lib/wasm32-unknown-wasip1/")
                or name.startswith("share/wasi-sysroot/include/wasm32-wasip1/")
                or name.startswith("share/wasi-sysroot/lib/wasm32-wasip1/")
                or "license" in name.lower() or name == "manifest.json")
        if keep and "noeh" not in rel.parts and "llvm-lto" not in rel.parts and "__cxx03" not in rel.parts:
            target = out / "tools/wasi-sdk" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
    # Official SDK requires the Microsoft C++ runtime. Include app-local copies
    # for local evaluation; redistribution licensing is documented separately.
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = Path(os.environ["SystemRoot"]) / "System32" / name
        if not source.is_file():
            raise SystemExit("Missing Microsoft runtime: " + name)
        shutil.copyfile(source, out / "tools/wasi-sdk/bin" / name)
    (out / "START.cmd").write_text('@echo off\r\ncd /d "%~dp0"\r\n"cpp-assessor-wasm-pilot.exe"\r\npause\r\n', encoding="ascii")
    (out / "README.txt").write_text(
        "C++ 手机号校验、生日提醒与日期校验 / WebAssembly 本地试验包\n\n"
        "Windows x64：解压到可写目录，双击 START.cmd。无需另装 Python、编译器、Docker 或 WSL。\n"
        "打开 http://127.0.0.1:8766 ，选择对应题目，上传 examples 目录中该题的 cpp 或 zip。\n"
        "模型调用需要联网，在页面填写你自己的 API Key；发行包不含密钥。默认 DeepSeek 兼容服务。\n"
        "已有 8766 服务时，可运行 exe web --backend wasm_pilot --allow-unattested-backend --port 8767。\n"
        "本地试验包：WASI 仅开放标准输入输出，原生编译工具强制在 Windows AppContainer 内运行。\n"
        "隔离不可用即停止，不回退本机运行学生程序；尚未完成公开不可信上传服务的安全验收。\n"
        "固定工程判断程序增加正确/缺陷夹具回归，并按题目单独展示学生程序的补充规则检查。\n"
        "结果可展示真实观测及不匹配；试验后端和 CHECKED 判断程序不具备正式评分资格。\n"
        "完整操作见 docs/使用手册.md；各题的验证范围分别见 docs/reviews/ 下记录。\n"
        "仅在当前机器验证过，未完成干净 Windows 镜像兼容性及再分发许可验收。\n",
        encoding="utf-8")
    if (ROOT / "docs/reviews/wasm-phone-pilot.md").exists():
        shutil.copyfile(ROOT / "docs/reviews/wasm-phone-pilot.md", out / "VALIDATION.md")
    for relative in ("docs/使用手册.md", "docs/reviews/wasm-phone-pilot.md", "docs/reviews/wasm-birthday-pilot.md", "docs/reviews/wasm-date-pilot.md"):
        source = ROOT / relative
        if source.is_file():
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    dependencies = {name: importlib.metadata.version(name) for name in
                    ("wasmtime", "fastapi", "uvicorn", "pydantic", "pyinstaller")}
    inventory = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in out.rglob("*") if p.is_file()]
    (out / "BUILD-MANIFEST.json").write_text(json.dumps({"dependencies": dependencies,
        "files": inventory}, indent=2), encoding="utf-8")
    archive = out.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in out.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(out.parent))
    metrics = {"archive": str(archive), "archive_bytes": archive.stat().st_size,
               "unpacked_bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
               "sdk_bytes": sum(p.stat().st_size for p in (out / "tools/wasi-sdk").rglob("*") if p.is_file()),
               "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    (ROOT / "data/wasm-pilot/package-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2), flush=True)


if __name__ == "__main__":
    main()

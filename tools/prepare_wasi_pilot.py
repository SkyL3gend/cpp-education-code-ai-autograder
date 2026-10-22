"""Download official SDK with resumable chunks, then extract only C++/WASI p1.

Maintainer tool, never invoked automatically by the portable application.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "data/wasm-pilot/downloads"
URL = "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-34/wasi-sdk-34.0-x86_64-windows.tar.gz"
SIZE = 619003408
SDK_SHA256 = "cccb5c323a9b34f0349a9b09e8804a0a7632c68c3310f4b5f437ed57d7e71d8f"
BINARYEN_URL = "https://github.com/WebAssembly/binaryen/releases/download/version_133/binaryen-version_133-x86_64-windows.tar.gz"
BINARYEN_SHA256 = "17a2cbeac6b5693c5fbafab3838d3c65fd9c1eb38b05f5baec6c657e8c84995b"


def main():
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    archive = DOWNLOADS / "wasi-sdk-34.0-x86_64-windows.tar.gz"
    start = archive.stat().st_size if archive.exists() else 0
    if start < SIZE:
        with urllib.request.urlopen(URL, timeout=30) as response:
            asset = response.url
        ranges = [(n, min(SIZE, n + 16 * 1024 * 1024)) for n in range(start, SIZE, 16 * 1024 * 1024)]

        def fetch(pair):
            begin, end = pair
            part = DOWNLOADS / f"part-{begin}"
            if part.exists() and part.stat().st_size == end - begin:
                return part
            for retry in range(4):
                try:
                    request = urllib.request.Request(asset + f"&chunk={begin}", headers={"Range": f"bytes={begin}-{end-1}"})
                    with urllib.request.urlopen(request, timeout=45) as response:
                        if response.status != 206:
                            raise RuntimeError("Server did not honor range")
                        data = response.read()
                    if len(data) != end - begin:
                        raise RuntimeError("Incomplete download")
                    part.write_bytes(data)
                    print(f"Downloaded {begin}:{end}", flush=True)
                    return part
                except Exception:
                    if retry == 3:
                        raise
                    time.sleep(1)

        with ThreadPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(fetch, ranges))
        with archive.open("ab") as output:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output)
    if archive.stat().st_size != SIZE:
        raise RuntimeError("SDK size mismatch")
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if digest != SDK_SHA256:
        raise RuntimeError("SDK SHA-256 mismatch; refusing extraction")
    destination = ROOT / "tools/wasi-sdk"
    destination.mkdir(parents=True, exist_ok=True)
    included = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            rel = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            name = rel.as_posix()
            if member.isdir() or name == ".":
                continue
            if ".." in rel.parts or rel.is_absolute() or not member.isfile():
                # Official tar has hardlinked driver names; copy those below.
                continue
            keep = (name.startswith("bin/") and (rel.name in {"clang.exe", "clang-23.exe", "clang++.exe", "wasm-ld.exe", "lld.exe"} or rel.suffix in {".dll", ".cfg"})) or name.startswith("lib/clang/") or name.startswith("share/wasi-sysroot/include/") or name.startswith("share/wasi-sysroot/lib/wasm32-wasi/") or name.startswith("share/wasi-sysroot/lib/wasm32-wasip1/") or "license" in name.lower()
            if keep:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                included.append({"file": name, "bytes": member.size})
    clang = next((p for p in [destination / "bin/clang.exe", *destination.glob("bin/clang-*.exe")] if p.is_file()), None)
    if clang and not (destination / "bin/clang++.exe").exists():
        shutil.copyfile(clang, destination / "bin/clang++.exe")
    if not (destination / "bin/wasm-ld.exe").exists() and (destination / "bin/lld.exe").exists():
        shutil.copyfile(destination / "bin/lld.exe", destination / "bin/wasm-ld.exe")
    binaryen = DOWNLOADS / "binaryen-133.tar.gz"
    if not binaryen.is_file():
        with urllib.request.urlopen(BINARYEN_URL, timeout=60) as response, binaryen.open("wb") as output:
            shutil.copyfileobj(response, output)
    if hashlib.file_digest(binaryen.open("rb"), "sha256").hexdigest() != BINARYEN_SHA256:
        raise RuntimeError("Binaryen SHA-256 mismatch; refusing extraction")
    with tarfile.open(binaryen) as tar:
        member = next(m for m in tar if m.isfile() and m.name.endswith("/bin/wasm-opt.exe"))
        with tar.extractfile(member) as source, (destination / "bin/wasm-opt.exe").open("wb") as output:
            shutil.copyfileobj(source, output)
    notices = {
        "BINARYEN-LICENSE.txt": "https://raw.githubusercontent.com/WebAssembly/binaryen/version_133/LICENSE",
        "LLVM-LICENSE.txt": "https://raw.githubusercontent.com/llvm/llvm-project/895aa2c896ada719451be2e3673c83da8ddf1141/LICENSE.TXT",
        "WASI-SDK-LICENSE.txt": "https://raw.githubusercontent.com/WebAssembly/wasi-sdk/wasi-sdk-34/LICENSE",
    }
    for name, url in notices.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            (destination / name).write_bytes(response.read())
    manifest = {"url": URL, "archive_bytes": SIZE, "sha256": digest,
                "binaryen_url": BINARYEN_URL, "binaryen_sha256": BINARYEN_SHA256,
                "extracted_bytes": sum(p.stat().st_size for p in destination.rglob("*") if p.is_file()), "files": included}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in manifest.items() if k != "files"}), flush=True)


if __name__ == "__main__":
    main()

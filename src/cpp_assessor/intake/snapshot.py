"""提交快照（只读导入，不执行任何提交内容）。

支持：单个 .cpp/.h 文件、目录、.zip 压缩包。
导入后写入 ``<data>/submissions/<submission_id>/original/``，
并生成 ``SubmissionManifest``。解压与拷贝都在校验过的路径上进行，
不会调用解压工具，也不会执行提交中的任何文件。
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..domain.canonical import canonical_json, sha256_hex, sha256_text
from ..domain.errors import InvalidInputError
from ..domain.models import FileEntry, SubmissionManifest

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class IntakeLimits:
    max_archive_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024
    max_single_file_bytes: int = 8 * 1024 * 1024
    max_files: int = 500


@dataclass(frozen=True)
class IngestResult:
    manifest: SubmissionManifest
    root: Path
    manifest_path: Path


def _detect_encoding(data: bytes) -> str:
    for candidate in ("utf-8", "gbk"):
        try:
            data.decode(candidate)
            return candidate
        except UnicodeDecodeError:
            continue
    return "binary"


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag) or stat.S_ISLNK(info.st_mode)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _relative_files(root: Path) -> list[tuple[str, Path]]:
    """遍历目录，返回 (规范相对路径, 源路径)，跳过版本控制目录。"""
    from .path_policy import validate_relative_path

    collected: list[tuple[str, Path]] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__pycache__", ".vs"})
        for name in sorted(filenames):
            absolute = Path(current) / name
            if _is_reparse_point(absolute):
                raise InvalidInputError(f"提交中包含链接或重解析点：{absolute}")
            relative = absolute.relative_to(root).as_posix()
            collected.append((validate_relative_path(relative), absolute))
    return collected


def _validate_archive(member: zipfile.ZipInfo) -> None:
    from .path_policy import validate_relative_path

    if member.is_dir():
        return
    mode = member.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise InvalidInputError(f"压缩包中包含符号链接：{member.filename}")
    validate_relative_path(member.filename)


def _extract_archive(archive: Path, destination: Path, limits: IntakeLimits) -> None:
    from .path_policy import ensure_unique_casefold, validate_relative_path

    with zipfile.ZipFile(archive) as bundle:
        members = [m for m in bundle.infolist() if not m.is_dir()]
        if len(members) > limits.max_files:
            raise InvalidInputError(
                f"压缩包内文件数 {len(members)} 超过上限 {limits.max_files}"
            )
        total = 0
        names = []
        for member in members:
            _validate_archive(member)
            names.append(validate_relative_path(member.filename))
        ensure_unique_casefold(names)

        for member in members:
            if member.file_size > limits.max_single_file_bytes:
                raise InvalidInputError(
                    f"压缩包内文件过大：{member.filename} ({member.file_size} 字节)"
                )
            total += member.file_size
            if total > limits.max_total_bytes:
                raise InvalidInputError(
                    f"压缩包解压后总大小超过上限 {limits.max_total_bytes} 字节"
                )
            target = destination / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, open(target, "wb") as sink:
                written = 0
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limits.max_single_file_bytes:
                        raise InvalidInputError(
                            f"压缩包内文件实际大小超过上限：{member.filename}"
                        )
                    sink.write(chunk)


def ingest(
    source: str | Path,
    submissions_root: str | Path,
    limits: IntakeLimits | None = None,
    target_before: str | Path | None = None,
) -> IngestResult:
    from .path_policy import ensure_unique_casefold

    limits = limits or IntakeLimits()
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise InvalidInputError(f"提交路径不存在：{source_path}")
    if _is_reparse_point(source_path):
        raise InvalidInputError(f"提交路径是链接或重解析点：{source_path}")

    submissions_root = Path(submissions_root).resolve()
    submissions_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".intake-", dir=submissions_root))
    try:
        payload = staging / "payload"
        payload.mkdir()

        if source_path.is_dir():
            pairs = _relative_files(source_path)
            if not pairs:
                raise InvalidInputError("提交目录中没有任何文件")
            for relative, absolute in pairs:
                _copy_file(absolute, payload / relative)
        elif source_path.suffix.lower() == ".zip":
            if source_path.stat().st_size > limits.max_archive_bytes:
                raise InvalidInputError(
                    f"压缩包超过上限 {limits.max_archive_bytes} 字节：{source_path}"
                )
            _extract_archive(source_path, payload, limits)
        else:
            relative = source_path.name
            from .path_policy import validate_relative_path

            validate_relative_path(relative)
            _copy_file(source_path, payload / relative)

        entries: list[FileEntry] = []
        total_bytes = 0
        for relative, absolute in _relative_files(payload):
            data = absolute.read_bytes()
            if len(data) > limits.max_single_file_bytes:
                raise InvalidInputError(f"单个文件过大：{relative}")
            total_bytes += len(data)
            if total_bytes > limits.max_total_bytes:
                raise InvalidInputError(
                    f"提交总大小超过上限 {limits.max_total_bytes} 字节"
                )
            entries.append(
                FileEntry(
                    file_id=f"f{len(entries) + 1:03d}",
                    path=relative,
                    size=len(data),
                    sha256=sha256_hex(data),
                    encoding=_detect_encoding(data),
                )
            )
        if not entries:
            raise InvalidInputError("提交中没有任何可导入的文件")
        if len(entries) > limits.max_files:
            raise InvalidInputError(f"提交文件数超过上限 {limits.max_files}")
        ensure_unique_casefold([entry.path for entry in entries])

        snapshot_hash = sha256_text(
            canonical_json([entry.model_dump(mode="json") for entry in entries])
        )
        submission_id = f"sub_{snapshot_hash[:16]}"

        target_before_id = None
        if target_before is not None:
            before = ingest(target_before, submissions_root, limits)
            target_before_id = before.manifest.submission_id

        manifest = SubmissionManifest(
            schema_version=SCHEMA_VERSION,
            submission_id=submission_id,
            files=entries,
            snapshot_hash=snapshot_hash,
            target_before_id=target_before_id,
        )

        final_root = submissions_root / submission_id
        if final_root.exists():
            shutil.rmtree(final_root)
        final_root.mkdir(parents=True)
        shutil.move(str(payload), str(final_root / "original"))
        manifest_path = final_root / "manifest.json"
        manifest_path.write_text(
            canonical_json(manifest.model_dump(mode="json")) + "\n", encoding="utf-8"
        )
        return IngestResult(manifest=manifest, root=final_root, manifest_path=manifest_path)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def required_file(manifest: SubmissionManifest, relative: str) -> FileEntry | None:
    """按规范相对路径查找文件（大小写不敏感）。"""
    folded = relative.casefold()
    for entry in manifest.files:
        if entry.path.casefold() == folded:
            return entry
    return None


def find_files(manifest: SubmissionManifest, pattern: str) -> list[FileEntry]:
    """按 glob 匹配提交内文件（大小写不敏感）。"""
    from fnmatch import fnmatch

    folded = pattern.casefold()
    return [
        entry
        for entry in manifest.files
        if fnmatch(entry.path.casefold(), folded)
        or fnmatch(entry.path.rsplit("/", 1)[-1].casefold(), folded)
    ]

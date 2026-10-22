"""Copy only declared compiler inputs into a disposable permission boundary."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile

from ..config import data_dir


@contextmanager
def staged_compile(request):
    base = (data_dir() / "wasm_staging").resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="compile-", dir=base)).resolve()
    if not root.is_relative_to(base) or root == base:
        raise RuntimeError("Invalid compiler staging directory")
    source_root, output_root = root / "source", root / "output"
    source_root.mkdir()
    output_root.mkdir()
    try:
        # Root input directories can overlap. Keep one copy per original path
        # so quoted includes and pragma-once semantics retain their structure.
        candidates = {Path(p).resolve() for p in request.include_dirs}
        candidates.update(Path(p).resolve().parent for p in request.source_units)
        origins = []
        for candidate in sorted(candidates, key=lambda p: (len(p.parts), str(p))):
            if not any(candidate.is_relative_to(parent) for parent in origins):
                origins.append(candidate)
        mapping = {}
        for index, origin in enumerate(origins):
            destination = source_root / f"input_{index}"
            if not origin.is_dir() or origin.is_symlink():
                raise ValueError("Compiler input directory missing or linked")
            destination.mkdir()
            for source in origin.rglob("*"):
                if source.is_symlink() or getattr(source.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError("Compiler inputs may not contain reparse points")
                relative = source.relative_to(origin)
                target = destination / relative
                if source.is_dir():
                    target.mkdir(exist_ok=True)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            mapping[origin] = destination

        def mapped(path):
            absolute = Path(path).resolve()
            for origin, destination in mapping.items():
                if absolute.is_relative_to(origin):
                    return destination / absolute.relative_to(origin)
            raise ValueError("Undeclared compiler input")

        staged = replace(request, source_units=[mapped(p) for p in request.source_units],
            include_dirs=[mapped(p) for p in request.include_dirs],
            output=output_root / request.output.name, work_dir=output_root, env={})
        yield staged, source_root, output_root
    finally:
        # AppContainer/job lifetimes must end before this context is left.
        # The exact target was created here and checked against the task root.
        if root.is_relative_to(base) and root != base and root.exists():
            shutil.rmtree(root)

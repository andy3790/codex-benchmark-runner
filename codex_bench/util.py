from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BenchError(Exception):
    """An actionable configuration or execution error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    replace_file(temporary, path)


def replace_file(source: Path, destination: Path) -> None:
    # Windows readers and antivirus scanners can briefly hold a non-delete-sharing
    # handle. Retry the atomic operation, never degrade to a partial in-place write.
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.02 * 2**attempt)


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def is_within(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def regular_files(root: Path):
    """Reject symlinks, Windows junctions/reparse points, and special files."""
    if not root.is_dir():
        raise BenchError(f"Not a directory: {root}")
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BenchError(f"Links/reparse points are not allowed: {path}")
        if stat.S_ISDIR(info.st_mode):
            # Check each directory before traversal: rglob could traverse a Windows
            # junction into another experiment before noticing that it is a link.
            pending.extend(reversed(sorted(path.iterdir())))
            continue
        if not stat.S_ISREG(info.st_mode):
            raise BenchError(f"Special file is not allowed: {path}")
        yield path


def tree_hash(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in regular_files(root)}


def copy_tree(source: Path, target: Path, max_bytes: int = 128 * 1024 * 1024) -> None:
    files = list(regular_files(source))
    if sum(p.stat().st_size for p in files) > max_bytes:
        raise BenchError(f"Tree exceeds {max_bytes} bytes: {source}")
    target.mkdir(parents=True, exist_ok=False)
    for path in files:
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Do not retain source executable bits or read-only attributes.
        destination.write_bytes(path.read_bytes())

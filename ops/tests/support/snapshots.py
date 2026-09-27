"""Capture bounded digests for explicitly named temporary fixture files."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath


def snapshot_files(root: Path, relative_paths: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Return size and SHA-256 for declared files only; never recurse."""

    resolved_root = root.resolve(strict=True)
    if root.is_symlink() or not resolved_root.is_dir():
        raise ValueError("snapshot root must be a real directory")
    snapshots: dict[str, tuple[int, str]] = {}
    for relative in relative_paths:
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or "\\" in relative
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"snapshot path must be safe and relative: {relative}")
        candidate = resolved_root.joinpath(*path.parts)
        cursor = resolved_root
        for part in path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"snapshot path cannot contain symlinks: {relative}")
        resolved = candidate.resolve(strict=True)
        if resolved != candidate or not resolved.is_file():
            raise ValueError(f"snapshot target must be a regular file: {relative}")
        payload = resolved.read_bytes()
        snapshots[relative] = (len(payload), hashlib.sha256(payload).hexdigest())
    return snapshots

"""Filesystem effects dispatched only after owner intent and fence checks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .core import AdmissionError
from .owner_journal import fsync_directory, read_regular
from .paths import resolve_beneath


def create_artifact(vault: Path, relative: str, content: bytes) -> bool:
    target = resolve_beneath(vault, relative)
    if not relative.startswith("01_AI_Review/Pending/") or len(Path(relative).parts) != 3:
        raise AdmissionError("PATH_DENIED", "proposal artifact must be one Pending item")
    observed = read_regular(target)
    if observed is not None:
        if observed != content:
            raise AdmissionError("ARTIFACT_CONFLICT", "existing artifact bytes differ from frozen intent")
        return False
    if not target.parent.is_dir():
        raise AdmissionError("PATH_DENIED", "Pending directory must exist in the admitted Vault")
    descriptor, name = tempfile.mkstemp(prefix=".proposal-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        resolve_beneath(vault, relative)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            if read_regular(target) != content:
                raise AdmissionError("ARTIFACT_CONFLICT", "concurrent artifact bytes differ") from None
            return False
        fsync_directory(target.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)
        fsync_directory(target.parent)

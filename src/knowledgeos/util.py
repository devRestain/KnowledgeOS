"""Small shared helpers: hashing, canonical JSON, time and atomic file writes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    """The single canonical serialization used for digests and stored records."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return sha256(canonical(value))


class Clock:
    """Wall clock; tests substitute a fixed or advancing clock."""

    def now(self) -> datetime:
        return datetime.now(SEOUL)


def iso(moment: datetime) -> str:
    return moment.astimezone(SEOUL).isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def new_uuid() -> str:
    return str(uuid.uuid4())


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write bytes to path through a same-directory temporary file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kos-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def create_exclusive(path: Path, data: bytes) -> None:
    """Create a new file with its complete bytes, failing if it already exists.

    The bytes go to a same-directory temporary file that is then hard-linked into place, so a
    concurrent reader never sees an empty or partial record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kos-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

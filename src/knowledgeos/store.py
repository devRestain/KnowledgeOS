"""Owner State: per-record JSON (atomic replace), append-only JSONL streams and blobs.

Nothing rewrites the whole State (Blueprint §8.5). State is losable operational data
and is never the only copy of user knowledge (Blueprint §2).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from operationkit.canonical import canonical, sha256

from .util import atomic_write, create_exclusive

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class StoreError(ValueError):
    pass


def _check(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name) or ".." in name:
        raise StoreError(f"invalid State record name: {name!r}")
    return name


class Store:
    """Records live at ``<kind>/<id>.json``; streams at ``<stream>.jsonl``; blobs at ``blobs/<sha256>``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------ locking

    def lock(self, key: str) -> threading.RLock:
        """A process-wide lock for read-modify-write of one record or stream."""
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    # ------------------------------------------------------------ records

    def _record_path(self, kind: str, record_id: str) -> Path:
        return self.root / _check(kind) / f"{_check(record_id)}.json"

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._record_path(kind, record_id).read_bytes())
        except FileNotFoundError:
            return None

    def put(self, kind: str, record_id: str, value: dict[str, Any]) -> None:
        atomic_write(self._record_path(kind, record_id), canonical(value) + b"\n")

    def create(self, kind: str, record_id: str, value: dict[str, Any]) -> bool:
        """Create a record exclusively. Returns False if it already exists."""
        try:
            create_exclusive(self._record_path(kind, record_id), canonical(value) + b"\n")
        except FileExistsError:
            return False
        return True

    def update(self, kind: str, record_id: str, change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """Read, mutate in place (``change`` may also return a replacement) and write back."""
        with self.lock(f"{kind}/{record_id}"):
            record = self.get(kind, record_id)
            if record is None:
                raise StoreError(f"no record {kind}/{record_id}")
            replaced = change(record)
            if isinstance(replaced, dict):
                record = replaced
            self.put(kind, record_id, record)
            return record

    def ids(self, kind: str) -> list[str]:
        directory = self.root / _check(kind)
        if not directory.is_dir():
            return []
        return sorted(path.stem for path in directory.glob("*.json"))

    def list(self, kind: str) -> list[dict[str, Any]]:
        records = []
        for record_id in self.ids(kind):
            record = self.get(kind, record_id)
            if record is not None:
                records.append(record)
        return records

    # ------------------------------------------------------------ streams

    def append(self, stream: str, entry: dict[str, Any]) -> None:
        path = self.root / f"{_check(stream)}.jsonl"
        with self.lock(f"stream:{stream}"), path.open("ab") as handle:
            handle.write(canonical(entry) + b"\n")
            handle.flush()

    def read_stream(self, stream: str) -> list[dict[str, Any]]:
        path = self.root / f"{_check(stream)}.jsonl"
        try:
            lines = path.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn final line after a crash
        return entries

    # ------------------------------------------------------------ blobs

    def put_blob(self, data: bytes) -> str:
        digest = sha256(data)
        path = self.root / "blobs" / digest
        if not path.exists():
            atomic_write(path, data)
        return digest

    def get_blob(self, digest: str) -> bytes | None:
        if not SHA_RE.match(digest or ""):
            raise StoreError(f"invalid blob digest {digest!r}")
        try:
            return (self.root / "blobs" / digest).read_bytes()
        except FileNotFoundError:
            return None

    def put_json_blob(self, value: Any) -> str:
        return self.put_blob(canonical(value))

    def get_json_blob(self, digest: str) -> Any:
        data = self.get_blob(digest)
        return None if data is None else json.loads(data)

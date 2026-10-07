"""Operation-owned decisions with byte CAS, durable fencing and writer exclusion."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .core import PINS, AdmissionError, canonical
from .paths import ResolvedPaths, resolve_beneath

CURRENT_OWNER_INTENT: ContextVar[str | None] = ContextVar("knowledgeos_owner_intent", default=None)


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise AdmissionError("CLOCK_INVALID", "owner clock must be timezone aware")
    return now.isoformat()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise AdmissionError("PATH_DENIED", "State directory must not be a symlink")
    if not path.exists():
        path.mkdir(mode=0o700)
        fsync_directory(path.parent)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise AdmissionError("STATE_MODE_INVALID", "State directory must have mode 0700")


def read_regular(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4194304:
            raise AdmissionError("STATE_INVALID", "record must be a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def replace_exact(path: Path, before: bytes | None, after: bytes) -> None:
    if read_regular(path) != before:
        raise AdmissionError("STALE_WRITER", "previous record bytes changed")
    descriptor, name = tempfile.mkstemp(prefix=".owner-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        if read_regular(path) != before:
            raise AdmissionError("STALE_WRITER", "previous record bytes changed before replacement")
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class JournalSession:
    journal: OwnerJournal
    value: dict[str, Any]
    raw: bytes | None
    generation: int
    lock_inode: int

    def fence(self) -> None:
        current = self.journal.read()
        if current["generation"] != self.generation or read_regular(self.journal.path) != self.raw:
            raise AdmissionError("STALE_WRITER", "owner generation or previous bytes changed")
        if self.journal.lock_path.stat(follow_symlinks=False).st_ino != self.lock_inode:
            raise AdmissionError("STALE_WRITER", "writer coordination inode changed")

    def commit(self, now: datetime) -> None:
        self.fence()
        self.value["revision"] += 1
        self.value["observed_at"] = stamp(now)
        self.journal.prune(self.value, now)
        payload = canonical(self.value) + b"\n"
        if len(payload) > 4194304:
            raise AdmissionError("STATE_CAPACITY", "owner State capacity must be resolved before admission")
        replace_exact(self.journal.path, self.raw, payload)
        self.raw = payload


@dataclass(frozen=True)
class OwnerJournal:
    roots: ResolvedPaths
    identity: dict[str, Any]

    @property
    def path(self) -> Path:
        return resolve_beneath(self.roots.state, "owner-control.json")

    @property
    def lock_path(self) -> Path:
        # A cooperating-writer lock travels with its fenced State. Host PID/locks
        # remain reconstructable Runtime data and cannot authorize this writer.
        return resolve_beneath(self.roots.state, ".owner-control.lock")

    def empty(self) -> dict[str, Any]:
        return {
            "state_version": 4, "owner_operation_id": "knowledgeos", "pins": dict(PINS),
            "fabric_id": self.identity["fabric_id"], "instance_id": self.identity["instance_id"],
            "generation": 1, "revision": 0, "observed_at": None,
            "intents": {}, "receipts": {}, "human_requests": {}, "decisions": {},
            "gateway": {}, "correlations": {}, "history": [], "experience": [], "outbox": [],
            "experience_events": [], "experience_cursor": 0, "interops_outbound": {},
        }

    def read(self) -> dict[str, Any]:
        raw = read_regular(self.path)
        if raw is None:
            if self.roots.state.exists():
                entries = [item for item in self.roots.state.iterdir() if item.name != ".owner-control.lock"]
                if any(not item.is_dir() or any(item.iterdir()) for item in entries):
                    raise AdmissionError("STATE_VERSION_UNSUPPORTED", "nonempty State requires its exact v3 owner journal; no automatic conversion")
            return self.empty()
        metadata = self.path.stat(follow_symlinks=False)
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
            raise AdmissionError("STATE_MODE_INVALID", "owner journal must be owned by the local process with mode 0600")
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError):
            raise AdmissionError("STATE_INVALID", "owner journal could not be parsed") from None
        template = self.empty()
        if not isinstance(value, dict) or set(value) != set(template):
            raise AdmissionError("STATE_VERSION_UNSUPPORTED", "unknown State fields or version")
        if any(value[key] != template[key] for key in ("state_version", "owner_operation_id", "pins", "fabric_id", "instance_id")):
            raise AdmissionError("STATE_PIN_MISMATCH", "State identity or pins differ; explicit cutover is required")
        if type(value["generation"]) is not int or not 1 <= value["generation"] <= 2147483647 or type(value["revision"]) is not int or value["revision"] < 0:
            raise AdmissionError("STATE_INVALID", "invalid owner generation or revision")
        for key in ("intents", "receipts", "human_requests", "decisions", "gateway", "correlations", "interops_outbound"):
            if not isinstance(value[key], dict):
                raise AdmissionError("STATE_INVALID", "invalid owner record collection")
        for key in ("history", "experience", "outbox", "experience_events"):
            if not isinstance(value[key], list):
                raise AdmissionError("STATE_INVALID", "invalid owner history collection")
        if type(value["experience_cursor"]) is not int or value["experience_cursor"] < 0:
            raise AdmissionError("STATE_INVALID", "invalid Experience source cursor")
        return value

    @contextmanager
    def writer(self) -> Iterator[JournalSession]:
        self.read()  # Reject incompatible State before creating even a lock.
        private_directory(self.roots.state)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
                raise AdmissionError("STATE_MODE_INVALID", "owner writer lock must be a locally owned regular file with mode 0600")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            value = self.read()
            session = JournalSession(self, copy.deepcopy(value), read_regular(self.path), value["generation"], os.fstat(descriptor).st_ino)
            yield session
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def prune(self, value: dict[str, Any], now: datetime) -> None:
        value["history"] = value["history"][-100:]
        retained_sequences = {item["sequence"] for item in value["history"]}
        for item in value["gateway"].values():
            if "detail_sequence" in item and item["detail_sequence"] not in retained_sequences:
                item.pop("envelope", None)
        for records in (value["gateway"], value["receipts"]):
            for key, item in list(records.items()):
                if key.startswith("exops.work."):
                    continue
                if item.get("unresolved") or key in value["intents"] and value["intents"][key]["state"] in {"pending", "unknown", "approved"}:
                    continue
                if (now - datetime.fromisoformat(item["recorded_at"])).total_seconds() >= 43200:
                    del records[key]
        for key, intent in value["intents"].items():
            if key.startswith("exops.work."):
                # A WorkRun and its dispatch uncertainty remain durable owner
                # state until an explicit reconciliation or retention decision.
                continue
            if intent["state"] == "pending" and (now - datetime.fromisoformat(intent["recorded_at"])).total_seconds() >= 43200:
                intent["state"] = "expired"
        for request in value["human_requests"].values():
            if request["lifecycle"] == "pending" and now >= datetime.fromisoformat(request["expires_at"]):
                request["lifecycle"] = "expired"
        # Pending/unknown intents, approval targets and reconciliation evidence
        # are active State, never evicted merely to fit a history limit.

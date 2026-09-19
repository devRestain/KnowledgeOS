"""Durable, provider-free recovery journals for local Vault transactions.

The journal is control-side runtime state.  It is deliberately separate from
Vault notes and from bridge transport: a journal records enough hashes and
paths to decide whether a retry may finish an already-started transaction,
but it does not store canonical Markdown or arbitrary input text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECOVERY_SCHEMA_VERSION = 1
RECOVERY_STATES = frozenset({"intent", "applying", "published", "moved", "completed", "conflict"})
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "operation",
        "sequence",
        "state",
        "event_at",
        "payload",
        "previous_record_sha256",
        "record_sha256",
    }
)


class RecoveryError(ValueError):
    """Base error for invalid or unavailable recovery state."""


class RecoveryConflict(RecoveryError):
    """Raised when a retry does not match an existing journal."""


class RecoveryCorruption(RecoveryError):
    """Raised when a journal or receipt cannot be trusted."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value deterministically for hashes and records."""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise RecoveryError("recovery payload is not canonical JSON") from error


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_job_id(value: str | None) -> str:
    """Validate a lowercase UUIDv4 or allocate one for a new transaction."""

    candidate = str(uuid.uuid4()) if value is None else value
    if not isinstance(candidate, str) or not _UUID_V4_RE.fullmatch(candidate):
        raise RecoveryError("job_id must be a lowercase UUID v4")
    return candidate


def fsync_directory(path: str | Path) -> None:
    """Durably publish a directory entry without changing its contents."""

    directory = Path(path)
    if directory.is_symlink() or not directory.is_dir():
        raise RecoveryError(f"cannot fsync a non-directory path: {directory}")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise RecoveryError(f"runtime path is not a real directory: {path}")
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise RecoveryError(f"runtime directory mode must be 0700: {path}")
        return
    path.mkdir(mode=0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise RecoveryError(f"runtime directory was not created with mode 0700: {path}")


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except BaseException:
        if path.exists() or path.is_symlink():
            path.unlink()
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _write_append(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RecoveryError("recovery journal append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def _parse_event_at(value: Any) -> None:
    if not isinstance(value, str):
        raise RecoveryCorruption("recovery journal event_at is not text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RecoveryCorruption("recovery journal event_at is not ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryCorruption("recovery journal event_at must include a timezone")


class RecoveryJournal:
    """One immutable-intent, append-only journal under ``runtime/runs``."""

    def __init__(self, root: str | Path, *, job_id: str, operation: str):
        candidate = Path(root).expanduser()
        if candidate.is_symlink() or not candidate.is_dir():
            raise RecoveryError("control root must be an existing non-symlink directory")
        if not isinstance(operation, str) or not _OPERATION_RE.fullmatch(operation):
            raise RecoveryError("recovery operation name is invalid")
        self.workspace = candidate.resolve()
        self.job_id = validate_job_id(job_id)
        self.operation = operation
        self.runtime = self.workspace / "runtime"
        self.runs = self.runtime / "runs"
        self.job_dir = self.runs / self.job_id
        self.path = self.job_dir / "journal.jsonl"

    @property
    def receipt_path(self) -> Path:
        return self.runtime / "receipts" / f"{self.job_id}-{self.operation}.json"

    @property
    def exists(self) -> bool:
        if self.job_dir.is_symlink():
            raise RecoveryCorruption("recovery job directory must not be a symlink")
        # C31 private provider envelopes share the per-job runtime directory
        # with later local transaction journals.  A job directory by itself
        # is therefore not evidence that this operation has started; the
        # operation-specific journal file is the durable identity.
        return self.path.is_file()

    def _prepare_run_directories(self) -> None:
        _ensure_private_directory(self.runtime)
        _ensure_private_directory(self.runs)

    def _record(
        self,
        *,
        sequence: int,
        state: str,
        payload: Mapping[str, Any],
        previous_record_sha256: str | None,
    ) -> bytes:
        if state not in RECOVERY_STATES:
            raise RecoveryError(f"unsupported recovery journal state: {state}")
        body: dict[str, Any] = {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "job_id": self.job_id,
            "operation": self.operation,
            "sequence": sequence,
            "state": state,
            "event_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "payload": dict(payload),
            "previous_record_sha256": previous_record_sha256,
        }
        body["record_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return canonical_json_bytes(body) + b"\n"

    def start(self, intent: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        """Create and fsync an intent, or verify an existing identical one."""

        if not isinstance(intent, Mapping) or not intent:
            raise RecoveryError("recovery intent must be a non-empty object")
        canonical_json_bytes(intent)
        self._prepare_run_directories()
        if self.exists:
            records = self.records()
            existing = records[0]["payload"]
            if canonical_json_bytes(existing) != canonical_json_bytes(intent):
                raise RecoveryConflict("same job_id has a different recovery intent")
            return records
        if self.job_dir.exists():
            if self.job_dir.is_symlink() or not self.job_dir.is_dir():
                raise RecoveryCorruption("recovery job directory is missing or unsafe")
            if stat.S_IMODE(self.job_dir.stat().st_mode) != 0o700:
                raise RecoveryError("recovery job directory mode must be 0700")
            first = self._record(sequence=1, state="intent", payload=intent, previous_record_sha256=None)
            try:
                _write_exclusive(self.path, first)
            except FileExistsError:
                records = self.records()
                if canonical_json_bytes(records[0]["payload"]) != canonical_json_bytes(intent):
                    raise RecoveryConflict("same job_id has a different recovery intent")
                return records
            fsync_directory(self.job_dir)
            fsync_directory(self.runs)
            fsync_directory(self.runtime)
            return self.records()
        try:
            self.job_dir.mkdir(mode=0o700)
        except FileExistsError:
            records = self.records()
            if canonical_json_bytes(records[0]["payload"]) != canonical_json_bytes(intent):
                raise RecoveryConflict("same job_id has a different recovery intent")
            return records
        if stat.S_IMODE(self.job_dir.stat().st_mode) != 0o700:
            raise RecoveryError("recovery job directory mode must be 0700")
        try:
            first = self._record(sequence=1, state="intent", payload=intent, previous_record_sha256=None)
            _write_exclusive(self.path, first)
            fsync_directory(self.job_dir)
            fsync_directory(self.runs)
            fsync_directory(self.runtime)
        except BaseException:
            if self.path.exists() or self.path.is_symlink():
                self.path.unlink()
            if self.job_dir.exists() and not self.job_dir.is_symlink():
                self.job_dir.rmdir()
            raise
        return self.records()

    def records(self) -> tuple[Mapping[str, Any], ...]:
        if self.job_dir.is_symlink() or not self.job_dir.is_dir():
            raise RecoveryCorruption("recovery job directory is missing or unsafe")
        if self.path.is_symlink() or not self.path.is_file():
            raise RecoveryCorruption("recovery journal is missing or unsafe")
        try:
            raw = self.path.read_bytes()
        except (OSError, UnicodeError) as error:
            raise RecoveryCorruption("recovery journal cannot be read") from error
        if not raw.endswith(b"\n"):
            raise RecoveryCorruption("recovery journal has an incomplete final record")
        records: list[Mapping[str, Any]] = []
        previous_hash: str | None = None
        for expected_sequence, line in enumerate(raw.splitlines(), start=1):
            if not line:
                raise RecoveryCorruption("recovery journal contains an empty record")
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise RecoveryCorruption("recovery journal contains invalid JSON") from error
            if not isinstance(value, Mapping) or set(value) != _RECORD_KEYS:
                raise RecoveryCorruption("recovery journal record shape is invalid")
            if value["schema_version"] != RECOVERY_SCHEMA_VERSION:
                raise RecoveryCorruption("recovery journal schema version is unsupported")
            if value["job_id"] != self.job_id or value["operation"] != self.operation:
                raise RecoveryCorruption("recovery journal identity does not match its path")
            if value["sequence"] != expected_sequence:
                raise RecoveryCorruption("recovery journal sequence is not contiguous")
            if value["state"] not in RECOVERY_STATES or not isinstance(value["payload"], Mapping):
                raise RecoveryCorruption("recovery journal state or payload is invalid")
            _parse_event_at(value["event_at"])
            if value["previous_record_sha256"] != previous_hash:
                raise RecoveryCorruption("recovery journal hash chain is broken")
            unsigned = dict(value)
            observed_hash = unsigned.pop("record_sha256")
            if not isinstance(observed_hash, str) or observed_hash != sha256_bytes(
                canonical_json_bytes(unsigned)
            ):
                raise RecoveryCorruption("recovery journal record hash is invalid")
            previous_hash = observed_hash
            records.append(dict(value))
        if not records or records[0]["state"] != "intent":
            raise RecoveryCorruption("recovery journal must begin with an intent")
        return tuple(records)

    def intent(self) -> Mapping[str, Any]:
        return self.records()[0]["payload"]

    def latest(self) -> Mapping[str, Any]:
        return self.records()[-1]

    def append(self, state: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        records = self.records()
        if records[-1]["state"] in {"completed", "conflict"}:
            raise RecoveryConflict("recovery journal is already terminal")
        record_bytes = self._record(
            sequence=len(records) + 1,
            state=state,
            payload=payload or {},
            previous_record_sha256=str(records[-1]["record_sha256"]),
        )
        _write_append(self.path, record_bytes)
        return self.records()[-1]

    def journal_sha256(self) -> str:
        return sha256_bytes(self.path.read_bytes())

    def write_receipt(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create or byte-verify the immutable receipt for a completed job."""

        if not isinstance(payload, Mapping) or not payload:
            raise RecoveryError("recovery receipt must be a non-empty object")
        _ensure_private_directory(self.runtime)
        receipts = self.receipt_path.parent
        _ensure_private_directory(receipts)
        encoded = canonical_json_bytes(payload) + b"\n"
        if self.receipt_path.exists() or self.receipt_path.is_symlink():
            if self.receipt_path.is_symlink() or not self.receipt_path.is_file():
                raise RecoveryCorruption("recovery receipt is not a regular file")
            if self.receipt_path.read_bytes() != encoded:
                raise RecoveryConflict("existing recovery receipt bytes differ")
        else:
            _write_exclusive(self.receipt_path, encoded)
        return dict(payload)

    def completion_receipt(self) -> Mapping[str, Any]:
        latest = self.latest()
        if latest["state"] != "completed":
            raise RecoveryError("completion receipt requested before completion")
        payload = latest["payload"]
        receipt = dict(payload["receipt"])
        receipt["journal_sha256"] = self.journal_sha256()
        return self.write_receipt(receipt)

    def quarantine(self, reason: str) -> str | None:
        """Move an unsafe journal out of the active run set, preserving bytes."""

        if not self.exists:
            return None
        if not isinstance(reason, str) or not reason.strip():
            raise RecoveryError("quarantine reason must be non-empty text")
        quarantine_root = self.runtime / "quarantine"
        transaction_root = quarantine_root / "transactions"
        _ensure_private_directory(self.runtime)
        _ensure_private_directory(quarantine_root)
        _ensure_private_directory(transaction_root)
        destination = transaction_root / f"{self.job_id}-{self.operation}-{uuid.uuid4().hex[:12]}"
        os.rename(self.job_dir, destination)
        marker = destination / "quarantine.json"
        marker_payload = {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "job_id": self.job_id,
            "operation": self.operation,
            "reason": reason,
            "quarantined_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _write_exclusive(marker, canonical_json_bytes(marker_payload) + b"\n")
        fsync_directory(transaction_root)
        fsync_directory(quarantine_root)
        fsync_directory(self.runtime)
        return destination.relative_to(self.workspace).as_posix()

"""Sealed, create-only temporary Vault smoke lifecycle.

The public entrypoints are intentionally separate from default tests and
diagnostics. A smoke run requires an explicit authorization flag, uses one
registered adapter, writes one generated note below ``99_System/Smoke``, and
can recover a stale note only when its identity, journal, marker, and sealed
digest all agree.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import current_thread, main_thread
from types import TracebackType
from typing import Any, Self

from .obsidian_status import _validate_vault_identity
from .paths import ResolvedPaths, resolve_api_paths
from .recovery import (
    RecoveryError,
    _ensure_private_directory,
    _write_append,
    _write_exclusive,
    canonical_json_bytes,
    fsync_directory,
    sha256_bytes,
    validate_job_id,
)
from .yaml_safe import load_yaml_file

SMOKE_SCHEMA_VERSION = 1
SMOKE_LEASE_SECONDS = 600
SMOKE_MAX_TIMEOUT_SECONDS = 120
SMOKE_MAX_NOTE_BYTES = 65536
SMOKE_MAX_JOURNAL_BYTES = 262144
_SMOKE_DIRECTORY = "99_System/Smoke"
_SMOKE_NAME = re.compile(
    r"^99_System/Smoke/KnowledgeOS-Smoke-filesystem-\d{8}T\d{6}Z-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.md$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_STATES = frozenset(
    {
        "reserved",
        "namespace_ready",
        "created",
        "observed",
        "quiesced",
        "cleanup_started",
        "cleaned",
        "conflict",
    }
)
_NEXT_STATES = {
    "reserved": {"namespace_ready", "cleanup_started", "conflict"},
    "namespace_ready": {"created", "cleanup_started", "conflict"},
    "created": {"observed", "cleanup_started", "conflict"},
    "observed": {"quiesced", "cleanup_started", "conflict"},
    "quiesced": {"cleanup_started", "conflict"},
    "cleanup_started": {"cleaned", "conflict"},
    "cleaned": set(),
    "conflict": set(),
}


def smoke_evidence_observations(*, static: str = "not_run", runtime: str = "not_run") -> dict[str, dict[str, str]]:
    """Keep static, runtime, deployment, and device evidence independent."""

    return {
        "static": {"state": static, "evidence_class": "static"},
        "runtime": {"state": runtime, "evidence_class": "runtime"},
        "deployment": {"state": "not_run", "evidence_class": "deployment"},
        "device": {"state": "not_run", "evidence_class": "device"},
    }


class SmokeError(ValueError):
    """Stable smoke preflight or lifecycle failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _SmokeInterruption(Exception):
    def __init__(self, signum: int):
        super().__init__(f"smoke interrupted by signal {signum}")
        self.signum = signum


class _CatchableSmokeSignals:
    def __enter__(self) -> Self:
        self.previous: dict[int, Any] = {}
        if current_thread() is main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self.previous[signum] = signal.signal(signum, self._raise)
        return self

    def _raise(self, signum: int, _frame: Any) -> None:
        raise _SmokeInterruption(signum)

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _require_private_directory(path: Path, *, missing_code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SmokeError(missing_code, "private smoke runtime directory is missing") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise SmokeError(missing_code, "private smoke runtime directory must be a non-symlink mode-0700 directory")


def _read_private_file(path: Path, *, limit: int, code: str, description: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SmokeError(code, description) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SmokeError(code, description)
        if metadata.st_size > limit:
            raise SmokeError(code, description)
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        if len(result) > limit:
            raise SmokeError(code, description)
        return result
    except OSError as error:
        raise SmokeError(code, description) from error
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class _ValidatedRoots:
    control: Path
    vault: Path
    state: Path
    identity: dict[str, Any]
    sentinel: dict[str, Any]
    identity_sha256: str


@dataclass(frozen=True)
class _Run:
    roots: _ValidatedRoots
    run_id: str
    kind: str
    adapter: str
    relative_path: str
    marker: str
    body: bytes
    expires_at: datetime
    namespace_preexisting: bool
    prior_git_status: str
    journal: _SmokeJournal
    active_path: Path
    intent_record_sha256: str


class _SmokeJournal:
    def __init__(
        self,
        control: str | Path | ResolvedPaths,
        run_id: str,
        *,
        runtime_root: Path | None = None,
    ):
        self.run_id = validate_job_id(run_id)
        self.runtime = runtime_root or resolve_api_paths(control).state
        self.run_dir = self.runtime / "smoke" / "runs" / self.run_id
        self.path = self.run_dir / "journal.jsonl"

    def create(self, intent: dict[str, Any]) -> str:
        _ensure_smoke_directories(self.runtime, (self.runtime / "smoke", self.runtime / "smoke/runs"))
        if self.run_dir.exists() or self.run_dir.is_symlink():
            raise SmokeError("SMOKE_RUN_COLLISION", "the generated run journal path already exists")
        try:
            self.run_dir.mkdir(mode=0o700)
        except OSError as error:
            raise SmokeError("SMOKE_JOURNAL_PATH_INVALID", "private smoke journal directory could not be created") from error
        if stat.S_IMODE(self.run_dir.stat().st_mode) != 0o700:
            raise SmokeError("SMOKE_RUNTIME_MODE_INVALID", "smoke run directory must use mode 0700")
        record = self._record(
            sequence=1,
            state="reserved",
            payload={"intent": intent},
            previous_record_sha256=None,
        )
        try:
            _write_exclusive(self.path, canonical_json_bytes(record) + b"\n")
            fsync_directory(self.run_dir)
            fsync_directory(self.run_dir.parent)
        except BaseException:
            if self.path.exists() or self.path.is_symlink():
                self.path.unlink()
            if self.run_dir.exists() and not self.run_dir.is_symlink():
                self.run_dir.rmdir()
            raise
        return str(record["record_sha256"])

    def _record(
        self,
        *,
        sequence: int,
        state: str,
        payload: dict[str, Any],
        previous_record_sha256: str | None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": SMOKE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": sequence,
            "state": state,
            "event_at": _now().isoformat(timespec="seconds"),
            "payload": payload,
            "previous_record_sha256": previous_record_sha256,
        }
        record["record_sha256"] = sha256_bytes(canonical_json_bytes(record))
        return record

    def records(self) -> tuple[dict[str, Any], ...]:
        for directory in (self.runtime, self.runtime / "smoke", self.runtime / "smoke/runs", self.run_dir):
            _require_private_directory(directory, missing_code="SMOKE_JOURNAL_PATH_INVALID")
        raw = _read_private_file(
            self.path,
            limit=SMOKE_MAX_JOURNAL_BYTES,
            code="SMOKE_JOURNAL_PATH_INVALID",
            description="private smoke journal must be a bounded mode-0600 regular file",
        )
        if not raw.endswith(b"\n"):
            raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal has an incomplete final record")
        records: list[dict[str, Any]] = []
        previous_hash: str | None = None
        expected_keys = {
            "schema_version",
            "run_id",
            "sequence",
            "state",
            "event_at",
            "payload",
            "previous_record_sha256",
            "record_sha256",
        }
        for sequence, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_strict_json_pairs)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal contains invalid JSON") from error
            except ValueError as error:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal contains duplicate JSON keys") from error
            if not isinstance(record, dict) or set(record) != expected_keys:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal record shape is invalid")
            if (
                isinstance(record["schema_version"], bool)
                or not isinstance(record["schema_version"], int)
                or record["schema_version"] != SMOKE_SCHEMA_VERSION
                or record["run_id"] != self.run_id
                or isinstance(record["sequence"], bool)
                or not isinstance(record["sequence"], int)
                or record["sequence"] != sequence
                or not isinstance(record["state"], str)
                or record["state"] not in _JOURNAL_STATES
                or not isinstance(record["payload"], dict)
                or record["previous_record_sha256"] != previous_hash
            ):
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal identity or sequence is invalid")
            try:
                event_at = datetime.fromisoformat(record["event_at"])
            except (TypeError, ValueError) as error:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal timestamp is invalid") from error
            if event_at.tzinfo is None or event_at.utcoffset() is None:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal timestamp must include a timezone")
            unsigned = dict(record)
            observed_hash = unsigned.pop("record_sha256")
            try:
                expected_hash = sha256_bytes(canonical_json_bytes(unsigned))
            except (RecoveryError, TypeError, UnicodeError, ValueError) as error:
                raise SmokeError(
                    "SMOKE_JOURNAL_CORRUPT",
                    "private smoke journal contains a non-canonical payload",
                ) from error
            if not isinstance(observed_hash, str) or observed_hash != expected_hash:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal hash chain is invalid")
            if records and record["state"] not in _NEXT_STATES[records[-1]["state"]]:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal transition is invalid")
            previous_hash = observed_hash
            records.append(record)
        if not records or records[0]["state"] != "reserved" or set(records[0]["payload"]) != {"intent"}:
            raise SmokeError("SMOKE_JOURNAL_CORRUPT", "private smoke journal must begin with one reservation intent")
        return tuple(records)

    def append(self, state: str, payload: dict[str, Any]) -> dict[str, Any]:
        if state not in _JOURNAL_STATES:
            raise SmokeError("SMOKE_JOURNAL_STATE_INVALID", "smoke journal state is not registered")
        records = self.records()
        previous = records[-1]
        if state not in _NEXT_STATES[previous["state"]]:
            raise SmokeError("SMOKE_JOURNAL_STATE_INVALID", "smoke journal transition is not registered")
        record = self._record(
            sequence=len(records) + 1,
            state=state,
            payload=payload,
            previous_record_sha256=previous["record_sha256"],
        )
        _write_append(self.path, canonical_json_bytes(record) + b"\n")
        return record

    def intent(self) -> dict[str, Any]:
        return self.records()[0]["payload"]["intent"]

    def journal_sha256(self) -> str:
        return sha256_bytes(
            _read_private_file(
                self.path,
                limit=SMOKE_MAX_JOURNAL_BYTES,
                code="SMOKE_JOURNAL_PATH_INVALID",
                description="private smoke journal must be a bounded mode-0600 regular file",
            )
        )

    def remove_unpublished(self, expected_intent_hash: str) -> None:
        """Remove only a just-created reservation that never reached Vault state."""

        records = self.records()
        if len(records) != 1 or records[0]["record_sha256"] != expected_intent_hash:
            raise SmokeError("SMOKE_JOURNAL_BINDING_MISMATCH", "unpublished reservation changed before removal")
        self.path.unlink()
        fsync_directory(self.run_dir)
        self.run_dir.rmdir()
        fsync_directory(self.run_dir.parent)


def _ensure_smoke_directories(runtime: Path, directories: tuple[Path, ...]) -> None:
    try:
        _ensure_private_directory(runtime)
        for directory in directories:
            _ensure_private_directory(directory)
    except RecoveryError as error:
        raise SmokeError("SMOKE_RUNTIME_PATH_INVALID", "private smoke runtime directories are unsafe") from error


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SmokeError("SMOKE_GIT_PREFLIGHT_FAILED", "local Git preflight did not complete") from error
    if result.returncode != 0:
        raise SmokeError("SMOKE_GIT_PREFLIGHT_FAILED", "local Git preflight did not pass")
    return result.stdout.strip()


def _identity_digest(sentinel: dict[str, Any]) -> str:
    selected = {
        key: sentinel.get(key)
        for key in (
            "schema_version",
            "contract_id",
            "vault_uuid",
            "canonical_vault_name",
            "remote_identity_sha256",
            "expected_branch",
        )
    }
    return sha256_bytes(canonical_json_bytes(selected))


def _validated_roots(root: str | Path) -> _ValidatedRoots:
    try:
        resolved = resolve_api_paths(root)
        control, vault = resolved.control, resolved.vault
    except (OSError, TypeError, ValueError) as error:
        raise SmokeError("SMOKE_ROOT_INVALID", "control and Vault roots must be existing real directories") from error
    sentinel_path = vault / ".knowledgeos-root.json"
    if sentinel_path.is_symlink() or not sentinel_path.is_file():
        raise SmokeError("SMOKE_VAULT_IDENTITY_INVALID", "a valid Vault root identity is required")
    try:
        sentinel_value = json.loads(sentinel_path.read_text(encoding="utf-8"))
        blueprint = load_yaml_file(control / "blueprint/blueprint.yaml")
        identity = _validate_vault_identity(control, vault)
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as error:
        raise SmokeError("SMOKE_VAULT_IDENTITY_INVALID", "a valid Vault root identity is required") from error
    if identity.get("state") != "validated" or not isinstance(sentinel_value, dict):
        raise SmokeError("SMOKE_VAULT_IDENTITY_INVALID", "a valid Vault root identity is required")
    from .bridge_contract import validate_root_sentinel

    if not validate_root_sentinel(sentinel_value, blueprint).passed:
        raise SmokeError("SMOKE_VAULT_IDENTITY_INVALID", "a valid Vault root identity is required")
    vault_uuid = sentinel_value.get("vault_uuid")
    expected_branch = sentinel_value.get("expected_branch")
    if not isinstance(vault_uuid, str) or not isinstance(expected_branch, str):
        raise SmokeError("SMOKE_VAULT_IDENTITY_INVALID", "Vault identity is missing its UUID or branch binding")
    if _git(control, "rev-parse", "--show-toplevel") != str(control):
        raise SmokeError("SMOKE_GIT_ROOT_INVALID", "control must be its own Git root")
    if _git(vault, "rev-parse", "--show-toplevel") != str(vault):
        raise SmokeError("SMOKE_GIT_ROOT_INVALID", "KnowledgeHub must be an independent Git root")
    if _git(vault, "branch", "--show-current") != expected_branch:
        raise SmokeError("SMOKE_BRANCH_MISMATCH", "Vault branch does not match its validated identity")
    system_root = vault / "99_System"
    if system_root.is_symlink() or not system_root.is_dir():
        raise SmokeError("SMOKE_SYSTEM_ROOT_INVALID", "99_System must be an existing real directory")
    runtime = resolved.state
    return _ValidatedRoots(
        control=control,
        vault=vault,
        state=runtime,
        identity=identity,
        sentinel=sentinel_value,
        identity_sha256=_identity_digest(sentinel_value),
    )


def _active_path(roots: _ValidatedRoots) -> Path:
    return roots.state / "smoke" / "active" / f"{roots.sentinel['vault_uuid']}.json"


def _validate_adapter(kind: str, adapter: str) -> bytes:
    if kind != "filesystem" or adapter != "filesystem-roundtrip":
        raise SmokeError("SMOKE_ADAPTER_NOT_REGISTERED", "only the registered filesystem-roundtrip adapter is available")
    return b"# KnowledgeOS temporary filesystem smoke\n\nDeterministic disposable probe.\n"


def _fixed_relative_path(run_id: str, created_at: datetime, kind: str = "filesystem") -> str:
    stamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{_SMOKE_DIRECTORY}/KnowledgeOS-Smoke-{kind}-{stamp}-{run_id}.md"


def _expected_marker(run_id: str, kind: str, expires_at: datetime) -> str:
    expiry = expires_at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return f"<!-- knowledgeos-smoke:v1 run_id={run_id} kind={kind} expires_at={expiry} -->"


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str):
        raise SmokeError("SMOKE_PATH_INVALID", "smoke path must be generated text")
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or "\\" in relative_path
        or len(path.parts) != 3
        or path.parts[:2] != ("99_System", "Smoke")
        or not _SMOKE_NAME.fullmatch(relative_path)
    ):
        raise SmokeError("SMOKE_PATH_INVALID", "smoke path is not one generated file below 99_System/Smoke")
    return tuple(path.parts)


def _open_smoke_parent(vault: Path, relative_path: str, *, create_namespace: bool = False) -> tuple[int, str, bool]:
    parts = _relative_parts(relative_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        system_fd = os.open(vault / "99_System", flags)
    except OSError as error:
        raise SmokeError("SMOKE_SYSTEM_ROOT_INVALID", "99_System is missing, symlinked, or unreadable") from error
    smoke_created = False
    try:
        try:
            smoke_fd = os.open("Smoke", flags, dir_fd=system_fd)
        except FileNotFoundError:
            if not create_namespace:
                raise SmokeError("SMOKE_PATH_INVALID", "the smoke namespace is absent")
            try:
                os.mkdir("Smoke", mode=0o700, dir_fd=system_fd)
                smoke_created = True
                os.fsync(system_fd)
            except FileExistsError:
                pass
            try:
                smoke_fd = os.open("Smoke", flags, dir_fd=system_fd)
            except OSError as error:
                raise SmokeError("SMOKE_NAMESPACE_INVALID", "the smoke namespace is symlinked or unsafe") from error
        except OSError as error:
            raise SmokeError("SMOKE_NAMESPACE_INVALID", "the smoke namespace is symlinked or unsafe") from error
        if smoke_created and stat.S_IMODE(os.fstat(smoke_fd).st_mode) != 0o700:
            os.close(smoke_fd)
            raise SmokeError("SMOKE_NAMESPACE_MODE_INVALID", "new smoke namespace must use mode 0700")
        os.close(system_fd)
        return smoke_fd, parts[-1], smoke_created
    except BaseException:
        os.close(system_fd)
        raise


def _read_probe(vault: Path, relative_path: str) -> bytes:
    parent_fd, name, _ = _open_smoke_parent(vault, relative_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > SMOKE_MAX_NOTE_BYTES:
                raise SmokeError("SMOKE_PROBE_UNSAFE", "smoke probe must be a bounded regular file")
            chunks: list[bytes] = []
            remaining = SMOKE_MAX_NOTE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 8192))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > SMOKE_MAX_NOTE_BYTES:
                raise SmokeError("SMOKE_PROBE_UNSAFE", "smoke probe exceeds its size bound")
            return data
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SmokeError("SMOKE_PROBE_UNSAFE", "smoke probe is missing, symlinked, or unreadable") from error
    finally:
        os.close(parent_fd)


def _probe_exists(vault: Path, relative_path: str) -> bool:
    try:
        parent_fd, name, _ = _open_smoke_parent(vault, relative_path)
    except SmokeError as error:
        if (
            error.code == "SMOKE_PATH_INVALID"
            and not (vault / _SMOKE_DIRECTORY).exists()
            and not (vault / _SMOKE_DIRECTORY).is_symlink()
        ):
            return False
        raise
    try:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(metadata.st_mode):
            raise SmokeError("SMOKE_PROBE_UNSAFE", "smoke probe path is not a regular file")
        return True
    finally:
        os.close(parent_fd)


def _current_probe_digest(vault: Path, relative_path: str) -> str | None:
    try:
        return sha256_bytes(_read_probe(vault, relative_path)) if _probe_exists(vault, relative_path) else None
    except SmokeError:
        return None


def _prepare_namespace(vault: Path, relative_path: str) -> bool:
    parent_fd, _name, namespace_created = _open_smoke_parent(
        vault, relative_path, create_namespace=True
    )
    os.close(parent_fd)
    return namespace_created


def _write_probe(vault: Path, relative_path: str, payload: bytes) -> None:
    parent_fd, name, _ = _open_smoke_parent(vault, relative_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.unlink(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
            raise
        os.fsync(parent_fd)
    except FileExistsError as error:
        raise SmokeError("SMOKE_PROBE_COLLISION", "the generated smoke path already exists") from error
    finally:
        os.close(parent_fd)


def _unlink_probe(
    vault: Path,
    relative_path: str,
    *,
    expected_digest: str,
    expected_marker: str,
) -> None:
    parent_fd, name, _ = _open_smoke_parent(vault, relative_path)
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > SMOKE_MAX_NOTE_BYTES:
            raise SmokeError("SMOKE_PROBE_UNSAFE", "smoke probe path is not a regular file")
        chunks: list[bytes] = []
        remaining = SMOKE_MAX_NOTE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if (
            len(payload) > SMOKE_MAX_NOTE_BYTES
            or sha256_bytes(payload) != expected_digest
            or not _marker_matches(payload, expected_marker)
        ):
            raise SmokeError(
                "SMOKE_PROBE_CHANGED_BEFORE_DELETE",
                "smoke probe changed after its cleanup proof was checked",
            )
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise SmokeError(
                "SMOKE_PROBE_CHANGED_BEFORE_DELETE",
                "smoke probe path no longer names the file whose digest was checked",
            )
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except SmokeError:
        raise
    except OSError as error:
        raise SmokeError("SMOKE_CLEANUP_FAILED", "the exact smoke probe could not be removed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _remove_created_namespace(vault: Path) -> None:
    system_path = vault / "99_System"
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        system_fd = os.open(system_path, flags)
    except OSError:
        return
    try:
        smoke_fd = os.open("Smoke", flags, dir_fd=system_fd)
        try:
            if os.listdir(smoke_fd):
                return
        finally:
            os.close(smoke_fd)
        try:
            os.rmdir("Smoke", dir_fd=system_fd)
            os.fsync(system_fd)
        except OSError:
            return
    except OSError:
        return
    finally:
        os.close(system_fd)


def _path_git_status(roots: _ValidatedRoots, relative_path: str) -> str:
    return _git(roots.vault, "status", "--porcelain=v1", "--untracked-files=all", "--", relative_path)


def _active_lock(path: Path) -> dict[str, Any] | None:
    for directory in (path.parent.parent.parent, path.parent.parent, path.parent):
        if directory.exists() or directory.is_symlink():
            _require_private_directory(directory, missing_code="SMOKE_ACTIVE_LOCK_INVALID")
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = json.loads(
            _read_private_file(
                path,
                limit=8192,
                code="SMOKE_ACTIVE_LOCK_INVALID",
                description="active smoke lock must be a bounded mode-0600 regular file",
            ).decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SmokeError("SMOKE_ACTIVE_LOCK_INVALID", "active smoke lock is not valid JSON") from error
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "vault_uuid",
        "run_id",
        "journal_relative_path",
        "intent_record_sha256",
        "reserved_at",
    }:
        raise SmokeError("SMOKE_ACTIVE_LOCK_INVALID", "active smoke lock shape is invalid")
    if (
        isinstance(value.get("schema_version"), bool)
        or not isinstance(value.get("schema_version"), int)
        or value.get("schema_version") != SMOKE_SCHEMA_VERSION
        or not isinstance(value.get("vault_uuid"), str)
        or not _SHA256.fullmatch(str(value.get("intent_record_sha256", "")))
    ):
        raise SmokeError("SMOKE_ACTIVE_LOCK_INVALID", "active smoke lock binding is invalid")
    try:
        validate_job_id(value.get("run_id"))
        reserved_at = datetime.fromisoformat(str(value.get("reserved_at")))
    except (TypeError, ValueError) as error:
        raise SmokeError("SMOKE_ACTIVE_LOCK_INVALID", "active smoke lock identity or timestamp is invalid") from error
    if reserved_at.tzinfo is None or reserved_at.utcoffset() is None:
        raise SmokeError("SMOKE_ACTIVE_LOCK_INVALID", "active smoke lock timestamp must include a timezone")
    if value.get("journal_relative_path") != f"state/smoke/runs/{value.get('run_id')}/journal.jsonl":
        raise SmokeError("SMOKE_PRIVATE_JOURNAL_BINDING_MISMATCH", "active lock does not name the exact private journal path")
    return value


def _create_active_lock(roots: _ValidatedRoots, run_id: str, intent_hash: str) -> Path:
    active = _active_path(roots)
    _ensure_smoke_directories(roots.state, (roots.state / "smoke", roots.state / "smoke/active"))
    value = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "vault_uuid": roots.sentinel["vault_uuid"],
        "run_id": run_id,
        "journal_relative_path": f"state/smoke/runs/{run_id}/journal.jsonl",
        "intent_record_sha256": intent_hash,
        "reserved_at": _now().isoformat(timespec="seconds"),
    }
    try:
        _write_exclusive(active, canonical_json_bytes(value) + b"\n")
    except FileExistsError as error:
        raise SmokeError("SMOKE_ACTIVE_RUN_EXISTS", "one smoke run is already active for this Vault") from error
    return active


def _intent_and_run(roots: _ValidatedRoots, run_id: str, journal: _SmokeJournal) -> _Run:
    records = journal.records()
    intent = journal.intent()
    expected_intent_keys = {
        "schema_version",
        "run_id",
        "kind",
        "adapter",
        "vault_uuid",
        "vault_identity_sha256",
        "relative_path",
        "created_at",
        "expires_at",
        "timeout_seconds",
        "namespace_preexisting",
        "creation_owner",
        "allowed_sha256",
        "expected_marker",
        "authorization_scope",
        "prior_git_status_sha256",
        "prior_git_status",
    }
    if set(intent) != expected_intent_keys:
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke reservation intent shape is invalid")
    if (
        isinstance(intent.get("schema_version"), bool)
        or not isinstance(intent.get("schema_version"), int)
        or intent.get("schema_version") != SMOKE_SCHEMA_VERSION
        or intent.get("run_id") != run_id
        or intent.get("vault_uuid") != roots.sentinel.get("vault_uuid")
        or intent.get("vault_identity_sha256") != roots.identity_sha256
    ):
        raise SmokeError("SMOKE_IDENTITY_MISMATCH", "current Vault identity does not match the reserved run")
    relative_path = intent.get("relative_path")
    _relative_parts(relative_path)
    try:
        created_at = datetime.fromisoformat(str(intent["created_at"]))
        expires_at = datetime.fromisoformat(str(intent["expires_at"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke reservation timestamp is invalid") from error
    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or not 0 < (expires_at - created_at).total_seconds() <= SMOKE_LEASE_SECONDS
    ):
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke lease timestamps are outside the accepted bound")
    kind = intent.get("kind")
    adapter = intent.get("adapter")
    if not isinstance(kind, str) or not isinstance(adapter, str):
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke adapter identity is invalid")
    body = _validate_adapter(kind, adapter)
    expected_path = _fixed_relative_path(run_id, created_at, kind)
    expected_marker = _expected_marker(run_id, kind, expires_at)
    if relative_path != expected_path or intent.get("expected_marker") != expected_marker:
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke path or ownership marker binding is invalid")
    timeout_seconds = intent.get("timeout_seconds")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= SMOKE_MAX_TIMEOUT_SECONDS:
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke timeout binding is invalid")
    if (
        not isinstance(intent.get("namespace_preexisting"), bool)
        or intent.get("creation_owner") != "driver"
        or intent.get("allowed_sha256") != []
        or intent.get("authorization_scope")
        != [
            "create_one_generated_note",
            "read_one_generated_note",
            "delete_same_generated_note",
            "write_private_smoke_journal",
        ]
        or not isinstance(intent.get("prior_git_status"), str)
        or intent.get("prior_git_status_sha256")
        != sha256_bytes(intent["prior_git_status"].encode("utf-8"))
    ):
        raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke authorization or preflight binding is invalid")
    active_path = _active_path(roots)
    lock = _active_lock(active_path)
    if (
        lock is None
        or lock.get("vault_uuid") != roots.sentinel.get("vault_uuid")
        or lock.get("run_id") != run_id
        or lock.get("journal_relative_path") != f"state/smoke/runs/{run_id}/journal.jsonl"
        or lock.get("intent_record_sha256") != records[0]["record_sha256"]
    ):
        raise SmokeError("SMOKE_ACTIVE_LOCK_MISMATCH", "private active lock does not bind this exact smoke journal")
    return _Run(
        roots=roots,
        run_id=run_id,
        kind=kind,
        adapter=adapter,
        relative_path=str(relative_path),
        marker=str(intent["expected_marker"]),
        body=body,
        expires_at=expires_at,
        namespace_preexisting=bool(intent.get("namespace_preexisting")),
        prior_git_status=str(intent.get("prior_git_status", "")),
        journal=journal,
        active_path=active_path,
        intent_record_sha256=str(records[0]["record_sha256"]),
    )


def _sealed_digests(records: tuple[dict[str, Any], ...]) -> set[str]:
    digests: set[str] = set()
    for record in records:
        value = record["payload"].get("sha256")
        if record["state"] in {"created", "observed", "quiesced"} and isinstance(value, str) and _SHA256.fullmatch(value):
            digests.add(value)
    return digests


def _marker_matches(payload: bytes, marker: str) -> bool:
    try:
        first_line = payload.splitlines()[0].decode("utf-8")
    except (IndexError, UnicodeError):
        return False
    return first_line == marker


def _confirmation_request(run: _Run, reason_code: str, current_digest: str | None) -> dict[str, Any]:
    try:
        sealed_digests = sorted(_sealed_digests(run.journal.records()))
    except SmokeError:
        sealed_digests = []
    return {
        "required": True,
        "action": "confirm_exact_smoke_note_disposition",
        "vault_identity": {
            "canonical_vault_name": run.roots.sentinel.get("canonical_vault_name"),
            "vault_uuid": run.roots.sentinel.get("vault_uuid"),
            "identity_sha256": run.roots.identity_sha256,
        },
        "relative_path": run.relative_path,
        "current_sha256": current_digest,
        "sealed_sha256": sealed_digests,
        "private_journal_path": f"state/smoke/runs/{run.run_id}/journal.jsonl",
        "reason_code": reason_code,
        "automatic_deletion": False,
    }


def _append_conflict(run: _Run, reason_code: str) -> None:
    latest = run.journal.records()[-1]["state"]
    if latest not in {"conflict", "cleaned"}:
        run.journal.append("conflict", {"reason_code": reason_code})


def _remove_active_lock(run: _Run) -> None:
    lock = _active_lock(run.active_path)
    if (
        lock is None
        or lock.get("schema_version") != SMOKE_SCHEMA_VERSION
        or lock.get("vault_uuid") != run.roots.sentinel.get("vault_uuid")
        or lock.get("run_id") != run.run_id
        or lock.get("journal_relative_path") != f"state/smoke/runs/{run.run_id}/journal.jsonl"
        or lock.get("intent_record_sha256") != run.intent_record_sha256
    ):
        raise SmokeError("SMOKE_ACTIVE_LOCK_MISMATCH", "active lock changed during smoke cleanup")
    run.active_path.unlink()
    fsync_directory(run.active_path.parent)


def _write_receipt(run: _Run, *, status: str, assertion_passed: bool, cleanup_passed: bool) -> dict[str, Any]:
    receipts = run.roots.state / "smoke" / "receipts"
    _ensure_smoke_directories(run.roots.state, (run.roots.state / "smoke", receipts))
    receipt = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "run_id": run.run_id,
        "vault_uuid": run.roots.sentinel["vault_uuid"],
        "relative_path": run.relative_path,
        "adapter": run.adapter,
        "status": status,
        "assertion_passed": assertion_passed,
        "cleanup_passed": cleanup_passed,
        "journal_sha256": run.journal.journal_sha256(),
        "completed_at": _now().isoformat(timespec="seconds"),
    }
    path = receipts / f"{run.run_id}.json"
    if path.exists() or path.is_symlink():
        raise SmokeError("SMOKE_RECEIPT_COLLISION", "smoke receipt path already exists")
    _write_exclusive(path, canonical_json_bytes(receipt) + b"\n")
    return receipt


def _existing_receipt(run: _Run) -> dict[str, Any] | None:
    path = run.roots.state / "smoke" / "receipts" / f"{run.run_id}.json"
    if not path.exists() and not path.is_symlink():
        return None
    for directory in (run.roots.state, run.roots.state / "smoke", path.parent):
        _require_private_directory(directory, missing_code="SMOKE_RECEIPT_PATH_INVALID")
    try:
        value = json.loads(
            _read_private_file(
                path,
                limit=8192,
                code="SMOKE_RECEIPT_INVALID",
                description="smoke receipt must be a bounded mode-0600 regular file",
            ).decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SmokeError("SMOKE_RECEIPT_INVALID", "smoke receipt is not valid JSON") from error
    expected_keys = {
        "schema_version",
        "run_id",
        "vault_uuid",
        "relative_path",
        "adapter",
        "status",
        "assertion_passed",
        "cleanup_passed",
        "journal_sha256",
        "completed_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or isinstance(value.get("schema_version"), bool)
        or not isinstance(value.get("schema_version"), int)
        or value.get("schema_version") != SMOKE_SCHEMA_VERSION
        or value.get("run_id") != run.run_id
        or value.get("vault_uuid") != run.roots.sentinel.get("vault_uuid")
        or value.get("relative_path") != run.relative_path
        or value.get("adapter") != run.adapter
        or not isinstance(value.get("status"), str)
        or value.get("status") not in {"PASS", "FAIL", "RECOVERED"}
        or not isinstance(value.get("assertion_passed"), bool)
        or value.get("cleanup_passed") is not True
        or value.get("journal_sha256") != run.journal.journal_sha256()
        or not isinstance(value.get("completed_at"), str)
    ):
        raise SmokeError("SMOKE_RECEIPT_INVALID", "smoke receipt does not bind the cleaned run")
    try:
        completed_at = datetime.fromisoformat(value["completed_at"])
    except ValueError as error:
        raise SmokeError("SMOKE_RECEIPT_INVALID", "smoke receipt timestamp is invalid") from error
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise SmokeError("SMOKE_RECEIPT_INVALID", "smoke receipt timestamp must include a timezone")
    return value


def _cleanup(
    run: _Run, *, assertion_passed: bool
) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
    current_digest: str | None = None
    try:
        records = run.journal.records()
        if records[-1]["state"] in {"conflict", "cleaned"}:
            raise SmokeError("SMOKE_PREVIOUS_TERMINAL_STATE", "smoke journal is already terminal")
        if _probe_exists(run.roots.vault, run.relative_path):
            payload = _read_probe(run.roots.vault, run.relative_path)
            digest = sha256_bytes(payload)
            current_digest = digest
            if not _marker_matches(payload, run.marker):
                raise SmokeError("SMOKE_OWNERSHIP_MARKER_MISMATCH", "smoke probe ownership marker changed")
            if digest not in _sealed_digests(records):
                raise SmokeError("SMOKE_DIGEST_NOT_SEALED", "smoke probe digest is not sealed by this run journal")
            run.journal.append("cleanup_started", {"sha256": digest})
            _unlink_probe(
                run.roots.vault,
                run.relative_path,
                expected_digest=digest,
                expected_marker=run.marker,
            )
        else:
            run.journal.append("cleanup_started", {"probe_absent": True})

        deadline = time.monotonic() + 1.0
        stable_absence = 0
        while time.monotonic() < deadline and stable_absence < 2:
            if _probe_exists(run.roots.vault, run.relative_path):
                stable_absence = 0
            else:
                stable_absence += 1
            if stable_absence < 2:
                time.sleep(0.05)
        if stable_absence < 2:
            raise SmokeError("SMOKE_PROBE_RECREATED", "smoke probe did not remain absent after cleanup")
        if _path_git_status(run.roots, run.relative_path) != run.prior_git_status:
            raise SmokeError("SMOKE_GIT_PATH_CHANGED", "path-scoped Vault Git state did not return to its preflight value")
        run.journal.append("cleaned", {"probe_absent": True, "git_path_restored": True})
        status = "PASS" if assertion_passed else "FAIL"
        receipt = _write_receipt(
            run,
            status=status,
            assertion_passed=assertion_passed,
            cleanup_passed=True,
        )
        _remove_active_lock(run)
        if not run.namespace_preexisting:
            _remove_created_namespace(run.roots.vault)
        return True, receipt, None
    except SmokeError as error:
        current_digest = _current_probe_digest(run.roots.vault, run.relative_path)
        try:
            _append_conflict(run, error.code)
        except SmokeError:
            # Preserve the original ownership conflict if journaling also fails.
            pass
        return False, None, _confirmation_request(run, error.code, current_digest)
    except Exception:  # noqa: BLE001
        # Unknown cleanup failures must preserve the probe and require confirmation.
        try:
            _append_conflict(run, "SMOKE_CLEANUP_ERROR")
        except Exception:  # noqa: BLE001, S110
            # Keep the primary cleanup failure; the journal remains recoverable.
            pass
        current_digest = _current_probe_digest(run.roots.vault, run.relative_path)
        return False, None, _confirmation_request(run, "SMOKE_CLEANUP_ERROR", current_digest)


def _reserve_run(
    root: str | Path,
    *,
    kind: str,
    adapter: str,
    timeout_seconds: int,
    authorized: bool,
    run_id: str | None = None,
) -> _Run:
    if not authorized:
        raise SmokeError("SMOKE_AUTHORIZATION_REQUIRED", "live-smoke authorization flag is required")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= SMOKE_MAX_TIMEOUT_SECONDS
    ):
        raise SmokeError("SMOKE_TIMEOUT_INVALID", "smoke timeout must be between 1 and 120 seconds")
    body = _validate_adapter(kind, adapter)
    roots = _validated_roots(root)
    if _active_lock(_active_path(roots)) is not None:
        raise SmokeError("SMOKE_ACTIVE_RUN_EXISTS", "one smoke run is already active for this Vault")
    now = _now()
    identifier = validate_job_id(run_id)
    relative_path = _fixed_relative_path(identifier, now, kind)
    _relative_parts(relative_path)
    smoke_dir = roots.vault / _SMOKE_DIRECTORY
    if smoke_dir.is_symlink() or (smoke_dir.exists() and not smoke_dir.is_dir()):
        raise SmokeError("SMOKE_NAMESPACE_INVALID", "99_System/Smoke must be absent or a real directory")
    parent_fd, filename, _ = _open_smoke_parent(roots.vault, relative_path, create_namespace=False) if smoke_dir.exists() else (-1, "", False)
    if parent_fd >= 0:
        try:
            try:
                os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SmokeError("SMOKE_PROBE_COLLISION", "the generated smoke path already exists")
        finally:
            os.close(parent_fd)
    expires_at = now + timedelta(seconds=SMOKE_LEASE_SECONDS)
    marker = _expected_marker(identifier, kind, expires_at)
    full_payload = (marker + "\n").encode("utf-8") + body
    if len(full_payload) > SMOKE_MAX_NOTE_BYTES:
        raise SmokeError("SMOKE_PROBE_TOO_LARGE", "registered smoke probe exceeds the size bound")
    prior_status = _git(roots.vault, "status", "--porcelain=v1", "--untracked-files=all", "--", relative_path)
    if prior_status:
        raise SmokeError("SMOKE_GIT_PATH_DIRTY", "the generated smoke path already has Vault Git state")
    journal = _SmokeJournal(roots.control, identifier, runtime_root=roots.state)
    intent = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "run_id": identifier,
        "kind": kind,
        "adapter": adapter,
        "vault_uuid": roots.sentinel["vault_uuid"],
        "vault_identity_sha256": roots.identity_sha256,
        "relative_path": relative_path,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timeout_seconds": timeout_seconds,
        "namespace_preexisting": smoke_dir.exists(),
        "creation_owner": "driver",
        "allowed_sha256": [],
        "expected_marker": marker,
        "authorization_scope": [
            "create_one_generated_note",
            "read_one_generated_note",
            "delete_same_generated_note",
            "write_private_smoke_journal",
        ],
        "prior_git_status_sha256": sha256_bytes(prior_status.encode("utf-8")),
        "prior_git_status": prior_status,
    }
    intent_hash = journal.create(intent)
    try:
        active_path = _create_active_lock(roots, identifier, intent_hash)
    except BaseException:
        try:
            journal.remove_unpublished(intent_hash)
        except Exception:  # noqa: BLE001, S110
            # Preserve the original lock-creation failure; an orphan journal is recoverable.
            pass
        raise
    return _Run(
        roots=roots,
        run_id=identifier,
        kind=kind,
        adapter=adapter,
        relative_path=relative_path,
        marker=marker,
        body=full_payload,
        expires_at=expires_at,
        namespace_preexisting=smoke_dir.exists(),
        prior_git_status=prior_status,
        journal=journal,
        active_path=active_path,
        intent_record_sha256=intent_hash,
    )


def _exercise(run: _Run, *, deadline: float) -> bool:
    if time.monotonic() >= deadline:
        raise SmokeError("SMOKE_TIMEOUT", "smoke adapter exceeded its bounded deadline")
    namespace_created = _prepare_namespace(run.roots.vault, run.relative_path)
    run.journal.append("namespace_ready", {"created_by_run": namespace_created})
    _write_probe(run.roots.vault, run.relative_path, run.body)
    digest = sha256_bytes(run.body)
    run.journal.append("created", {"sha256": digest, "size_bytes": len(run.body)})
    if time.monotonic() >= deadline:
        raise SmokeError("SMOKE_TIMEOUT", "smoke adapter exceeded its bounded deadline")
    observed = _read_probe(run.roots.vault, run.relative_path)
    passed = observed == run.body and _marker_matches(observed, run.marker)
    observed_digest = sha256_bytes(observed)
    run.journal.append("observed", {"sha256": observed_digest, "assertion_passed": passed})
    run.journal.append("quiesced", {"sha256": observed_digest})
    return passed


def _manual_confirmation(run_id: str, reason_code: str, *, vault_uuid: str | None = None) -> dict[str, Any]:
    journal_path = f"state/smoke/runs/{run_id}/journal.jsonl"
    return {
        "status": "CONFLICT",
        "operation": "smoke recover",
        "run_id": run_id,
        "relative_path": None,
        "sealed_vault_uuid": vault_uuid,
        "current_sha256": None,
        "reason_code": reason_code,
        "confirmation_request": {
            "required": True,
            "action": "confirm_exact_smoke_note_disposition",
            "run_id": run_id,
            "vault_uuid": vault_uuid,
            "relative_path": None,
            "current_sha256": None,
            "private_journal_path": journal_path,
            "automatic_deletion": False,
        },
        "automatic_deletion": False,
        "journal_state": "unknown",
        "observations": smoke_evidence_observations(static="fail"),
    }


def _unbound_active_lock_confirmation(
    roots: _ValidatedRoots, reason_code: str
) -> dict[str, Any]:
    lock_path = f"state/smoke/active/{roots.sentinel['vault_uuid']}.json"
    return {
        "status": "CONFLICT",
        "operation": "smoke run",
        "run_id": None,
        "relative_path": None,
        "vault_uuid": roots.sentinel["vault_uuid"],
        "reason_code": reason_code,
        "confirmation_request": {
            "required": True,
            "action": "inspect_and_confirm_exact_smoke_note_disposition",
            "run_id": None,
            "vault_uuid": roots.sentinel["vault_uuid"],
            "relative_path": None,
            "current_sha256": None,
            "private_lock_path": lock_path,
            "private_journal_path": None,
            "reason_code": reason_code,
            "automatic_deletion": False,
        },
        "automatic_deletion": False,
        "observations": smoke_evidence_observations(static="fail"),
    }


def _confirmation_for_identity_mismatch(root: str | Path, run_id: str, reason_code: str) -> dict[str, Any]:
    control_path = Path(root).expanduser()
    try:
        if control_path.is_symlink() or not control_path.is_dir():
            return _manual_confirmation(run_id, reason_code)
        resolved = resolve_api_paths(control_path)
        control = resolved.control
        journal = _SmokeJournal(control, run_id, runtime_root=resolved.state)
        records = journal.records()
        intent = journal.intent()
        vault_uuid = intent.get("vault_uuid") if isinstance(intent.get("vault_uuid"), str) else None
        created_at = datetime.fromisoformat(str(intent.get("created_at", "")))
        expires_at = datetime.fromisoformat(str(intent.get("expires_at", "")))
        relative = intent.get("relative_path")
        kind = intent.get("kind")
        if (
            intent.get("run_id") != run_id
            or not isinstance(vault_uuid, str)
            or created_at.tzinfo is None
            or expires_at.tzinfo is None
            or not isinstance(kind, str)
            or relative != _fixed_relative_path(run_id, created_at, kind)
            or intent.get("expected_marker") != _expected_marker(run_id, kind, expires_at)
        ):
            return _manual_confirmation(run_id, reason_code, vault_uuid=vault_uuid)
        _relative_parts(relative)
        digest: str | None = None
        current_vault_uuid: str | None = None
        vault = resolved.vault
        if vault.is_symlink() or not vault.is_dir() or (vault / "99_System").is_symlink():
            return _manual_confirmation(run_id, reason_code, vault_uuid=vault_uuid)
        sentinel_path = vault / ".knowledgeos-root.json"
        if not sentinel_path.is_symlink() and sentinel_path.is_file():
            try:
                sentinel_value = json.loads(sentinel_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_pairs)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                sentinel_value = None
            if isinstance(sentinel_value, dict) and isinstance(sentinel_value.get("vault_uuid"), str):
                current_vault_uuid = sentinel_value["vault_uuid"]
        if _probe_exists(vault, relative):
            digest = sha256_bytes(_read_probe(vault, relative))
        confirmation = {
            "required": True,
            "action": "confirm_exact_smoke_note_disposition",
            "run_id": run_id,
            "sealed_vault_uuid": vault_uuid,
            "current_vault_uuid": current_vault_uuid,
            "relative_path": relative,
            "current_sha256": digest,
            "sealed_sha256": sorted(_sealed_digests(records)),
            "private_journal_path": f"state/smoke/runs/{run_id}/journal.jsonl",
            "reason_code": reason_code,
            "automatic_deletion": False,
        }
        return {
            "status": "CONFLICT",
            "operation": "smoke recover",
            "run_id": run_id,
            "relative_path": relative,
            "sealed_vault_uuid": vault_uuid,
            "current_vault_uuid": current_vault_uuid,
            "current_sha256": digest,
            "reason_code": reason_code,
            "confirmation_request": confirmation,
            "journal_state": records[-1]["state"],
            "observations": smoke_evidence_observations(static="fail"),
        }
    except (SmokeError, OSError, ValueError, TypeError, KeyError):
        return _manual_confirmation(run_id, reason_code)


def _cleanup_stale(run: _Run) -> dict[str, Any]:
    current_digest: str | None = None
    try:
        records = run.journal.records()
        terminal_state = records[-1]["state"]
        if terminal_state == "conflict":
            reason = records[-1]["payload"].get("reason_code")
            current_digest = _current_probe_digest(run.roots.vault, run.relative_path)
            return {
                "status": "CONFLICT",
                "operation": "smoke recover",
                "run_id": run.run_id,
                "relative_path": run.relative_path,
                "reason_code": "SMOKE_PREVIOUS_CONFLICT",
                "confirmation_request": _confirmation_request(
                    run,
                    str(reason) if isinstance(reason, str) else "SMOKE_PREVIOUS_CONFLICT",
                    current_digest,
                ),
                "automatic_deletion": False,
                "observations": smoke_evidence_observations(static="pass", runtime="conflict"),
            }
        if _probe_exists(run.roots.vault, run.relative_path):
            payload = _read_probe(run.roots.vault, run.relative_path)
            digest = sha256_bytes(payload)
            current_digest = digest
            if not _marker_matches(payload, run.marker):
                raise SmokeError("SMOKE_OWNERSHIP_MARKER_MISMATCH", "the current probe marker does not match the run")
            if digest not in _sealed_digests(records):
                raise SmokeError("SMOKE_DIGEST_NOT_SEALED", "the current probe digest is not sealed by the run journal")
        if terminal_state != "cleaned":
            if terminal_state not in {"reserved", "namespace_ready", "created", "observed", "quiesced", "cleanup_started"}:
                raise SmokeError("SMOKE_JOURNAL_CORRUPT", "smoke journal is not recoverable")
            if current_digest is not None:
                if terminal_state != "cleanup_started":
                    run.journal.append("cleanup_started", {"sha256": current_digest, "recovered": True})
                _unlink_probe(
                    run.roots.vault,
                    run.relative_path,
                    expected_digest=current_digest,
                    expected_marker=run.marker,
                )
            elif terminal_state != "cleanup_started":
                run.journal.append("cleanup_started", {"probe_absent": True, "recovered": True})
        deadline = time.monotonic() + 1.0
        stable_absence = 0
        while time.monotonic() < deadline and stable_absence < 2:
            if _probe_exists(run.roots.vault, run.relative_path):
                stable_absence = 0
            else:
                stable_absence += 1
            if stable_absence < 2:
                time.sleep(0.05)
        if stable_absence < 2:
            raise SmokeError("SMOKE_PROBE_RECREATED", "the recovered probe path is not stably absent")
        if _path_git_status(run.roots, run.relative_path) != run.prior_git_status:
            raise SmokeError("SMOKE_GIT_PATH_CHANGED", "path-scoped Vault Git state differs from its preflight value")
        if terminal_state != "cleaned":
            run.journal.append("cleaned", {"probe_absent": True, "recovered": True})
        receipt = _existing_receipt(run)
        if receipt is None:
            receipt = _write_receipt(run, status="RECOVERED", assertion_passed=False, cleanup_passed=True)
        _remove_active_lock(run)
        if not run.namespace_preexisting:
            _remove_created_namespace(run.roots.vault)
        return {
            "status": "PASS",
            "operation": "smoke recover",
            "run_id": run.run_id,
            "relative_path": run.relative_path,
            "removed": _probe_exists(run.roots.vault, run.relative_path) is False,
            "receipt": receipt,
            "observations": smoke_evidence_observations(static="pass", runtime="pass"),
        }
    except SmokeError as error:
        current_digest = _current_probe_digest(run.roots.vault, run.relative_path)
        try:
            _append_conflict(run, error.code)
        except SmokeError:
            pass
        return {
            "status": "CONFLICT",
            "operation": "smoke recover",
            "run_id": run.run_id,
            "relative_path": run.relative_path,
            "reason_code": error.code,
            "confirmation_request": _confirmation_request(run, error.code, current_digest),
            "automatic_deletion": False,
            "observations": smoke_evidence_observations(static="pass", runtime="conflict"),
        }
    except Exception:  # noqa: BLE001
        # Convert an unexpected recovery failure into a fail-closed confirmation.
        current_digest = _current_probe_digest(run.roots.vault, run.relative_path)
        try:
            _append_conflict(run, "SMOKE_RECOVERY_ERROR")
        except Exception:  # noqa: BLE001, S110
            # Keep the recovery conflict visible even if secondary journaling fails.
            pass
        return {
            "status": "CONFLICT",
            "operation": "smoke recover",
            "run_id": run.run_id,
            "relative_path": run.relative_path,
            "reason_code": "SMOKE_RECOVERY_ERROR",
            "confirmation_request": _confirmation_request(run, "SMOKE_RECOVERY_ERROR", current_digest),
            "automatic_deletion": False,
            "observations": smoke_evidence_observations(static="pass", runtime="conflict"),
        }


def plan_smoke(root: str | Path, *, kind: str, adapter: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Validate a prospective adapter and report its bounded effects without writes."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= SMOKE_MAX_TIMEOUT_SECONDS
    ):
        raise SmokeError("SMOKE_TIMEOUT_INVALID", "smoke timeout must be between 1 and 120 seconds")
    _validate_adapter(kind, adapter)
    roots = _validated_roots(root)
    active = _active_lock(_active_path(roots))
    if active is not None:
        raise SmokeError("SMOKE_ACTIVE_RUN_EXISTS", "one smoke run is already active for this Vault")
    prospective_id = str(uuid.uuid4())
    prospective_path = _fixed_relative_path(prospective_id, _now(), kind)
    smoke_dir = roots.vault / _SMOKE_DIRECTORY
    if smoke_dir.is_symlink() or (smoke_dir.exists() and not smoke_dir.is_dir()):
        raise SmokeError("SMOKE_NAMESPACE_INVALID", "99_System/Smoke must be absent or a real directory")
    return {
        "operation": "smoke plan",
        "status": "PASS",
        "kind": kind,
        "adapter": adapter,
        "evidence_class": "static",
        "observations": smoke_evidence_observations(static="pass"),
        "capability": "create_read_and_remove_one_generated_markdown_probe",
        "relative_path": prospective_path,
        "namespace_preexisting": smoke_dir.exists(),
        "timeout_seconds": timeout_seconds,
        "effects": {
            "vault_probe_count": 1,
            "vault_probe_root": _SMOKE_DIRECTORY,
            "private_runtime_journal": True,
            "git_network_or_sync": False,
            "plugin_configuration_changes": False,
        },
        "authorization_required_for_run": True,
    }


def run_smoke(
    root: str | Path,
    *,
    kind: str,
    adapter: str,
    authorized: bool,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run one explicitly authorized fixed filesystem smoke and clean its probe."""

    if not authorized:
        raise SmokeError("SMOKE_AUTHORIZATION_REQUIRED", "live-smoke authorization flag is required")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= SMOKE_MAX_TIMEOUT_SECONDS
    ):
        raise SmokeError("SMOKE_TIMEOUT_INVALID", "smoke timeout must be between 1 and 120 seconds")
    _validate_adapter(kind, adapter)
    roots = _validated_roots(root)
    active_path = _active_path(roots)
    if (active_path.exists() or active_path.is_symlink()):
        try:
            active = _active_lock(active_path)
        except SmokeError as error:
            return _unbound_active_lock_confirmation(roots, error.code)
        if active is None:
            return _unbound_active_lock_confirmation(roots, "SMOKE_ACTIVE_LOCK_INVALID")
        run_id = validate_job_id(active.get("run_id"))
        if active.get("vault_uuid") != roots.sentinel.get("vault_uuid"):
            return _confirmation_for_identity_mismatch(root, run_id, "SMOKE_ACTIVE_LOCK_IDENTITY_MISMATCH")
        stale_journal = _SmokeJournal(roots.control, run_id, runtime_root=roots.state)
        try:
            stale_run = _intent_and_run(roots, run_id, stale_journal)
        except SmokeError as error:
            return _confirmation_for_identity_mismatch(root, run_id, error.code)
        if stale_run.expires_at > _now():
            return {
                "status": "ACTIVE",
                "operation": "smoke run",
                "run_id": run_id,
                "relative_path": stale_run.relative_path,
                "reason_code": "SMOKE_ACTIVE_RUN_NOT_EXPIRED",
                "automatic_deletion": False,
                "observations": smoke_evidence_observations(static="pass"),
            }
        recovered = _cleanup_stale(stale_run)
        if recovered["status"] != "PASS":
            recovered["operation"] = "smoke run"
            return recovered
    run = _reserve_run(
        root,
        kind=kind,
        adapter=adapter,
        timeout_seconds=timeout_seconds,
        authorized=True,
    )
    deadline = time.monotonic() + timeout_seconds
    assertion_passed = False
    primary_failure: str | None = None
    try:
        with _CatchableSmokeSignals():
            assertion_passed = _exercise(run, deadline=deadline)
        if not assertion_passed:
            primary_failure = "SMOKE_ASSERTION_FAILED"
    except SmokeError as error:
        assertion_passed = False
        primary_failure = error.code
    except (_SmokeInterruption, KeyboardInterrupt):
        assertion_passed = False
        primary_failure = "SMOKE_INTERRUPTED"
    except Exception:  # noqa: BLE001
        # Registered adapter failures are reported only after the normal cleanup path.
        assertion_passed = False
        primary_failure = "SMOKE_ACTION_ERROR"
    cleanup_passed, receipt, confirmation = _cleanup(run, assertion_passed=assertion_passed)
    if assertion_passed and cleanup_passed:
        status = "PASS"
    elif cleanup_passed:
        if primary_failure == "SMOKE_TIMEOUT":
            status = "TIMEOUT_CLEAN"
        elif primary_failure == "SMOKE_INTERRUPTED":
            status = "INTERRUPTED_CLEAN"
        else:
            status = "FAIL_CLEAN"
    else:
        status = "CLEANUP_CONFLICT"
    report = {
        "operation": "smoke run",
        "status": status,
        "kind": kind,
        "adapter": adapter,
        "evidence_class": "runtime",
        "run_id": run.run_id,
        "vault_uuid": run.roots.sentinel["vault_uuid"],
        "relative_path": run.relative_path,
        "assertion_passed": assertion_passed,
        "cleanup_passed": cleanup_passed,
        "receipt": receipt,
        "observations": smoke_evidence_observations(
            static="pass",
            runtime=(
                "pass"
                if status == "PASS"
                else "conflict"
                if status == "CLEANUP_CONFLICT"
                else "fail"
            ),
        ),
    }
    if primary_failure is not None:
        report["primary_failure"] = primary_failure
    if confirmation is not None:
        report["confirmation_request"] = confirmation
    return report


def recover_smoke(root: str | Path, *, run_id: str, authorized: bool) -> dict[str, Any]:
    """Recover only a journal-bound stale note whose exact proof still matches."""

    if not authorized:
        raise SmokeError("SMOKE_AUTHORIZATION_REQUIRED", "smoke recovery authorization flag is required")
    try:
        identifier = validate_job_id(run_id)
    except (TypeError, ValueError) as error:
        raise SmokeError("SMOKE_RUN_ID_INVALID", "smoke recovery requires a lowercase UUIDv4 run ID") from error
    try:
        resolved = resolve_api_paths(root)
    except (OSError, TypeError, ValueError) as error:
        raise SmokeError("SMOKE_ROOT_INVALID", "control root must resolve to an existing real directory") from error
    control = resolved.control
    journal = _SmokeJournal(control, identifier, runtime_root=resolved.state)
    try:
        records = journal.records()
        intent = journal.intent()
    except SmokeError as error:
        return _manual_confirmation(identifier, error.code)
    vault_uuid = intent.get("vault_uuid")
    try:
        validate_job_id(vault_uuid)
    except (TypeError, ValueError):
        return _manual_confirmation(identifier, "SMOKE_JOURNAL_CORRUPT")
    if intent.get("run_id") != identifier:
        return _manual_confirmation(identifier, "SMOKE_JOURNAL_CORRUPT", vault_uuid=vault_uuid)
    active_path = resolved.state / "smoke" / "active" / f"{vault_uuid}.json"
    try:
        active = _active_lock(active_path)
    except SmokeError as error:
        return _confirmation_for_identity_mismatch(control, identifier, error.code)
    if (
        active is None
        or active.get("vault_uuid") != vault_uuid
        or active.get("run_id") != identifier
        or active.get("journal_relative_path") != f"state/smoke/runs/{identifier}/journal.jsonl"
        or active.get("intent_record_sha256") != records[0]["record_sha256"]
    ):
        return _confirmation_for_identity_mismatch(control, identifier, "SMOKE_PRIVATE_JOURNAL_BINDING_MISMATCH")
    try:
        roots = _validated_roots(control)
    except SmokeError:
        return _confirmation_for_identity_mismatch(control, identifier, "SMOKE_VAULT_IDENTITY_INVALID")
    if roots.sentinel.get("vault_uuid") != intent.get("vault_uuid"):
        return _confirmation_for_identity_mismatch(control, identifier, "SMOKE_VAULT_IDENTITY_MISMATCH")
    try:
        run = _intent_and_run(roots, identifier, journal)
    except SmokeError as error:
        return _confirmation_for_identity_mismatch(control, identifier, error.code)
    if run.expires_at > _now():
        return {
            "status": "ACTIVE",
            "operation": "smoke recover",
            "run_id": identifier,
            "relative_path": run.relative_path,
            "reason_code": "SMOKE_ACTIVE_RUN_NOT_EXPIRED",
            "automatic_deletion": False,
            "observations": smoke_evidence_observations(static="pass"),
        }
    return _cleanup_stale(run)

"""Provider-free background worker and inactive LaunchAgent artifacts.

The C24 boundary is deliberately narrower than an unattended automation
deployment.  The worker performs one local wake pass: it discovers committed
bridge requests, lets the C17 ingester create idempotent runtime queue
manifests, and reconciles durable local transaction journals.  It never calls
a provider, writes canonical Vault content, or performs network Git I/O.

The LaunchAgent plist is a deterministic install-time artifact only.  C24
does not install or activate it; that is the separately gated E03 overlay.
"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yaml import YAMLError

from .bridge_publish import ingest_bridge_request
from .paths import ResolvedPaths, RootResolutionError, resolve_api_paths
from .reconcile import reconcile_transactions
from .recovery import canonical_json_bytes, sha256_bytes
from .yaml_safe import load_yaml_file

EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

CAPABILITY = "C24"
LAUNCHD_LABEL = "com.knowledgeos.vaultops"
LAUNCHD_ARTIFACT_PATH = "ops/launchd/com.knowledgeos.vaultops.plist"
BACKGROUND_CONFIG_PATH = "ops/config/background.yaml"
WORKER_REPORT_SCHEMA_PATH = "ops/schemas/worker-report.schema.json"
DEFAULT_BASELINE_PATH = "ops/tests/fixtures/c24_background/evaluation.yaml"
WORKER_STDOUT_LOG_PATH = "logs/worker.stdout.log"
WORKER_STDERR_LOG_PATH = "logs/worker.stderr.log"
WORKER_STDOUT_LOG_TEMPLATE = "__KNOWLEDGEOS_RUNTIME_ROOT__/logs/worker.stdout.log"
WORKER_STDERR_LOG_TEMPLATE = "__KNOWLEDGEOS_RUNTIME_ROOT__/logs/worker.stderr.log"
CONTROL_ROOT_TEMPLATE = "__KNOWLEDGEOS_CONTROL_ROOT__"
VAULT_ROOT_TEMPLATE = "__KNOWLEDGEOS_VAULT_ROOT__"
RUNTIME_ROOT_TEMPLATE = "__KNOWLEDGEOS_RUNTIME_ROOT__"
WORKER_LOG_MAX_AGE_SECONDS = 60 * 60
WORKER_LOG_MAX_BYTES = 16 * 1024
WORKER_LOG_RETAIN_BYTES = 8 * 1024
_WORKER_LOG_HEADER = b"# KnowledgeOS worker log v1\n"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_REQUEST_RE = re.compile(
    r"^\.vault-bridge/requests/\d{4}/\d{2}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$"
)
_WAKE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class BackgroundError(ValueError):
    """Raised when a background input cannot be inspected safely."""


class BackgroundConflict(BackgroundError):
    """Raised when a background pass finds a state that must not be adopted."""


class WorkerBusy(BackgroundConflict):
    """Raised when another worker owns the non-blocking execution lock."""


class _BoundedTextWriter:
    """Limit one launchd-bound text stream without changing terminal output."""

    def __init__(self, stream: Any, remaining_bytes: int) -> None:
        self._stream = stream
        self._remaining_bytes = max(0, remaining_bytes)

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            value = str(value)
        if self._remaining_bytes > 0:
            encoded = value.encode("utf-8")
            if len(encoded) <= self._remaining_bytes:
                self._stream.write(value)
                self._remaining_bytes -= len(encoded)
            else:
                bounded = encoded[: self._remaining_bytes].decode("utf-8", errors="ignore")
                if bounded:
                    self._stream.write(bounded)
                self._remaining_bytes = 0
            self._stream.flush()
        return len(value)

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")


def _same_regular_file(file_descriptor: int, path: Path) -> bool:
    try:
        descriptor_stat = os.fstat(file_descriptor)
        path_stat = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return False
    return stat.S_ISREG(descriptor_stat.st_mode) and (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
    ) == (path_stat.st_dev, path_stat.st_ino)


def _bounded_log_tail(
    path: Path,
    retain_bytes: int,
    *,
    max_age_seconds: int = WORKER_LOG_MAX_AGE_SECONDS,
) -> bytes:
    """Return a bounded complete-record tail, discarding legacy log formats."""

    retain_budget = max(retain_bytes - len(_WORKER_LOG_HEADER), 0)
    try:
        if path.is_symlink() or not path.is_file():
            return _WORKER_LOG_HEADER
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return _WORKER_LOG_HEADER
        with path.open("rb") as handle:
            if handle.read(len(_WORKER_LOG_HEADER)) != _WORKER_LOG_HEADER:
                return _WORKER_LOG_HEADER
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= retain_bytes:
                handle.seek(0)
                data = handle.read(retain_bytes)
            else:
                handle.seek(size - retain_budget)
                data = handle.read(retain_budget)
    except OSError:
        return _WORKER_LOG_HEADER

    if not data.startswith(_WORKER_LOG_HEADER):
        newline = data.find(b"\n")
        data = data[newline + 1 :] if newline >= 0 else b""
    if data and not data.endswith(b"\n"):
        newline = data.rfind(b"\n")
        data = data[: newline + 1] if newline >= 0 else b""
    data = data.removeprefix(_WORKER_LOG_HEADER)
    return _WORKER_LOG_HEADER + data[:retain_budget]


def _prepare_bounded_log_fd(file_descriptor: int, path: Path) -> int | None:
    """Compact one launchd log in place and return its retained byte count."""

    if not _same_regular_file(file_descriptor, path):
        return None
    retained = _bounded_log_tail(path, WORKER_LOG_RETAIN_BYTES)
    try:
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        os.ftruncate(file_descriptor, 0)
        offset = 0
        while offset < len(retained):
            offset += os.write(file_descriptor, retained[offset:])
        os.fchmod(file_descriptor, 0o600)
        os.fsync(file_descriptor)
        os.lseek(file_descriptor, 0, os.SEEK_END)
    except OSError:
        return None
    return len(retained)


def configure_worker_log_streams(root: str | Path | ResolvedPaths) -> bool:
    """Bound only the two files opened by launchd, leaving terminal output unchanged."""

    try:
        roots = resolve_api_paths(root)
    except (RootResolutionError, OSError, TypeError, ValueError):
        return False
    runtime = roots.runtime
    if not runtime.is_absolute():
        return False

    streams = (
        (1, runtime / WORKER_STDOUT_LOG_PATH, "stdout"),
        (2, runtime / WORKER_STDERR_LOG_PATH, "stderr"),
    )
    bounded_stdout = False
    for file_descriptor, path, stream_name in streams:
        try:
            stream = getattr(sys, stream_name)
            stream.flush()
        except (AttributeError, OSError):
            continue
        retained = _prepare_bounded_log_fd(file_descriptor, path)
        if retained is None:
            continue
        bounded = _BoundedTextWriter(stream, WORKER_LOG_MAX_BYTES - retained)
        if stream_name == "stdout":
            sys.stdout = bounded
            bounded_stdout = True
        else:
            sys.stderr = bounded
    return bounded_stdout


def worker_log_record(
    report: Mapping[str, Any],
    exit_code: int,
    *,
    logged_at: str | None = None,
) -> dict[str, Any]:
    """Return a privacy-minimized, bounded summary for the launchd log."""

    recovery = report.get("recovery")
    recovery_summary = recovery.get("summary", {}) if isinstance(recovery, Mapping) else {}
    requests = report.get("requests")
    request_count = report.get("request_count")
    if not isinstance(request_count, int):
        request_count = len(requests) if isinstance(requests, Sequence) else 0
    errors = report.get("errors")
    return {
        "schema_version": 1,
        "record_type": "worker_summary",
        "logged_at": logged_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "operation": str(report.get("operation", "ai worker")),
        "status": str(report.get("status", "FAIL")),
        "exit_code": int(exit_code),
        "wake": report.get("wake", {}),
        "request_count": request_count,
        "recovery_status": str(
            report.get(
                "recovery_status",
                recovery.get("status", "NOT_RUN") if isinstance(recovery, Mapping) else "NOT_RUN",
            )
        ),
        "recovery_summary": dict(recovery_summary) if isinstance(recovery_summary, Mapping) else {},
        "runtime_mutation_performed": bool(report.get("runtime_mutation_performed", False)),
        "provider_called": bool(report.get("provider_called", False)),
        "vault_mutated": bool(report.get("vault_mutated", False)),
        "git_network_called": bool(report.get("git_network_called", False)),
        "error_count": len(errors) if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)) else 0,
    }


def scheduled_worker_record(report: Mapping[str, Any], exit_code: int) -> dict[str, Any]:
    """Return the fixed, privacy-minimized result emitted by the scheduler adapter."""

    status = report.get("status")
    if status not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL"}:
        status = "FAIL"
    recovery_status = report.get("recovery_status", "NOT_RUN")
    if recovery_status not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL", "NOT_RUN"}:
        recovery_status = "FAIL"
    recovery = report.get("recovery")
    recovery_summary = recovery.get("summary", {}) if isinstance(recovery, Mapping) else {}
    summary: dict[str, int] = {}
    if isinstance(recovery_summary, Mapping):
        for key in ("jobs", "complete", "repairable", "conflict"):
            value = recovery_summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000:
                summary[key] = value
    request_count = report.get("request_count", 0)
    if not isinstance(request_count, int) or isinstance(request_count, bool):
        request_count = 0
    return {
        "schema_version": 1,
        "record_type": "scheduled_worker_result",
        "operation": "ai worker",
        "status": status,
        "outcome": "needs_attention" if status == "REPAIR_REQUIRED" else (
            "completed" if status == "PASS" else "failed"
        ),
        "exit_code": int(exit_code),
        "delivery": "local",
        "agent_reasoning": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "request_count": min(max(request_count, 0), 100),
        "recovery_status": recovery_status,
        "recovery_summary": summary,
    }


_WORKER_ATTEMPT_RELATIVE = Path("worker/attempt.json")
_WORKER_LOCK_RELATIVE = Path("worker/worker.lock")
_WORKER_ATTEMPT_KEYS = frozenset(
    {"schema_version", "attempt_id", "owner", "status", "started_at", "finished_at", "result_status"}
)


def _worker_directory(state: Path) -> Path:
    directory = state / "worker"
    if directory.is_symlink():
        raise BackgroundError("worker state directory must not be a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
        raise BackgroundError("worker state directory must be owned by the caller with mode 0700")
    return directory


def _read_worker_attempt(directory: Path) -> dict[str, Any] | None:
    path = directory / _WORKER_ATTEMPT_RELATIVE.name
    if not path.exists() and not path.is_symlink():
        return None
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BackgroundError("worker attempt marker must be a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid() or info.st_size > 4096:
            raise BackgroundError("worker attempt marker permissions or size are invalid")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except BackgroundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackgroundError("worker attempt marker is unreadable") from error
    if not isinstance(value, dict) or set(value) != _WORKER_ATTEMPT_KEYS:
        raise BackgroundError("worker attempt marker schema is invalid")
    attempt_id = value.get("attempt_id")
    owner = value.get("owner")
    status = value.get("status")
    started_at = value.get("started_at")
    finished_at = value.get("finished_at")
    result_status = value.get("result_status")
    if (
        raw != canonical_json_bytes(value) + b"\n"
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not isinstance(attempt_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
        or not isinstance(owner, str)
        or owner not in {"manual", "scheduler"}
        or not isinstance(status, str)
        or status not in {"running", "completed", "needs_attention"}
        or not isinstance(started_at, str)
        or not started_at
        or finished_at is not None and (not isinstance(finished_at, str) or not finished_at)
        or result_status is not None and not isinstance(result_status, str)
        or result_status not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL", None}
        or status == "running" and (finished_at is not None or result_status is not None)
        or status == "completed" and (finished_at is None or result_status != "PASS")
        or status == "needs_attention"
        and (finished_at is None or result_status not in {"REPAIR_REQUIRED", "CONFLICT", "FAIL"})
    ):
        raise BackgroundError("worker attempt marker schema is invalid")
    return value


def _write_worker_attempt(directory: Path, record: Mapping[str, Any]) -> None:
    path = directory / _WORKER_ATTEMPT_RELATIVE.name
    if path.is_symlink():
        raise BackgroundError("worker attempt marker must not be a symlink")
    temporary = directory / f".attempt-{uuid.uuid4().hex}.tmp"
    payload = canonical_json_bytes(dict(record)) + b"\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if stat.S_IMODE(temporary.stat().st_mode) != 0o600:
            raise BackgroundError("worker attempt temporary marker mode is invalid")
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _worker_attempt_record(
    *,
    attempt_id: str,
    owner: str,
    status: str,
    started_at: str,
    result_status: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "owner": owner,
        "status": status,
        "started_at": started_at,
        "finished_at": None if status == "running" else datetime.now(UTC).isoformat(timespec="seconds"),
        "result_status": result_status,
    }


def _recovery_block_report(recovery: Mapping[str, Any], exit_code: int) -> tuple[dict[str, Any], int]:
    status = recovery.get("status")
    if status not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL"}:
        status = "FAIL"
    return {
        "status": status,
        "operation": "ai worker",
        "capability": CAPABILITY,
        "mode": "once",
        "wake": {"kind": "invocation", "id": "recovery-check", "synthetic": False},
        "requests": [],
        "request_count": 0,
        "recovery": dict(recovery),
        "recovery_status": status,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
        "runtime_mutation_performed": False,
        "dry_run": False,
        "activation": {"launchd_active": False, "installation": "deferred_to_E03"},
        "errors": [_issue("WORKER_RECOVERY_REQUIRED", "/state/worker/attempt", "previous worker attempt requires operator attention")],
    }, exit_code


def _issue(code: str, locator: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "locator": locator, "message": message}
    if details:
        result["details"] = details
    return result


def _failure(
    operation: str,
    code: str,
    message: str,
    *,
    exit_code: int = EXIT_INPUT_INVALID,
    **details: Any,
) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = {
        "status": "FAIL" if exit_code != EXIT_CONFLICT else "CONFLICT",
        "operation": operation,
        "capability": CAPABILITY,
        "mode": "once",
        "wake": {"kind": "invocation", "id": "rejected", "synthetic": False},
        "requests": [],
        "recovery": {"status": "NOT_RUN", "summary": {}, "jobs": []},
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
        "runtime_mutation_performed": False,
        "activation": {"launchd_active": False, "installation": "deferred_to_E03"},
        "errors": [_issue(code, "/", message)],
    }
    report.update(details)
    return report, exit_code


def _workspace(root: str | Path | ResolvedPaths) -> ResolvedPaths:
    try:
        roots = resolve_api_paths(root)
    except (RootResolutionError, OSError, TypeError, ValueError) as error:
        raise BackgroundError(str(error)) from error
    if roots.vault.is_symlink() or not roots.vault.is_dir():
        raise BackgroundError("selected Vault root must be an existing non-symlink directory")
    runtime = roots.state
    if runtime.is_symlink() or not runtime.is_dir():
        raise BackgroundError("selected runtime root must be an existing non-symlink directory")
    if stat.S_IMODE(runtime.stat().st_mode) != 0o700:
        raise BackgroundError("runtime root mode must be 0700")
    return roots


def _private_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BackgroundError(f"{label} must be an existing non-symlink directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise BackgroundError(f"{label} mode must be 0700")


def _validate_runtime(state: Path) -> None:
    """Validate the durable queue and recovery directories in Operation State."""
    for name in ("queue", "quarantine", "runs"):
        _private_directory(state / name, label=f"state/{name}")


def _validate_wake_id(wake_id: str) -> str:
    if not isinstance(wake_id, str) or not _WAKE_ID_RE.fullmatch(wake_id):
        raise BackgroundError("wake_id must be a bounded alphanumeric identifier")
    return wake_id


def _safe_request_path(vault: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        path = candidate
    else:
        relative = candidate.as_posix()
        if not _REQUEST_RE.fullmatch(relative):
            raise BackgroundError("request path must match the committed bridge request namespace")
        path = vault / Path(*relative.split("/"))
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise BackgroundError(f"request path cannot be resolved: {error}") from error
    try:
        resolved.relative_to(vault)
    except ValueError as error:
        raise BackgroundError("request path escapes KnowledgeHub") from error
    relative = resolved.relative_to(vault).as_posix()
    if not _REQUEST_RE.fullmatch(relative):
        raise BackgroundError("request path must match the committed bridge request namespace")
    if path.is_symlink() or not path.is_file():
        raise BackgroundError("request path must be an existing regular file")
    return path


def _request_files(vault: Path) -> list[Path]:
    request_root = vault / ".vault-bridge" / "requests"
    _private_or_tracked_directory(request_root, label="KnowledgeHub/.vault-bridge/requests")
    files: list[Path] = []
    invalid: list[str] = []
    for path in sorted(request_root.rglob("*"), key=lambda item: item.relative_to(vault).as_posix()):
        if path.is_symlink():
            raise BackgroundError(
                f"bridge request namespace contains a symlink: {path.relative_to(vault).as_posix()}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(vault).as_posix()
        if _REQUEST_RE.fullmatch(relative):
            files.append(path)
        else:
            invalid.append(relative)
    if invalid:
        raise BackgroundError(
            "bridge request namespace contains an unsafe or unrecognized file: "
            + min(invalid)
        )
    return files


def _private_or_tracked_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BackgroundError(f"{label} must be an existing non-symlink directory")


def _request_item(path: Path, vault: Path, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"path": path.relative_to(vault).as_posix()}
    if result is None:
        item.update({"status": "NOT_RUN", "state": "discovered"})
        return item
    item["status"] = result.get("status", "FAIL")
    item["state"] = result.get("state", "unknown")
    if "queue_write" in result:
        item["queue_write"] = result["queue_write"]
    if "job_id" in result:
        item["job_id"] = result["job_id"]
    if result.get("errors"):
        item["errors"] = list(result["errors"][:5])
    return item


def _recovery_summary(recovery: Mapping[str, Any]) -> tuple[str, int]:
    status = str(recovery.get("status", "FAIL"))
    if status == "CONFLICT":
        return "CONFLICT", EXIT_CONFLICT
    if status == "REPAIR_REQUIRED":
        return "REPAIR_REQUIRED", EXIT_OK
    if status == "PASS":
        return "PASS", EXIT_OK
    return "FAIL", EXIT_INPUT_INVALID


def _worker_once_pass(
    root: str | Path | ResolvedPaths,
    *,
    wake_id: str | None = None,
    request_path: str | Path | None = None,
    dry_run: bool = False,
    max_requests: int = 100,
    synthetic: bool | None = None,
) -> tuple[dict[str, Any], int]:
    """Run one bounded local wake pass.

    The default pass scans all committed bridge requests.  ``request_path``
    is an optional validated narrowing for diagnostics and tests.  In either
    form, the C17 ingester performs the Git branch, source-blob, replay, and
    privacy checks before creating a runtime queue manifest.
    """

    selected_wake_id = _validate_wake_id(wake_id or "manual")
    is_synthetic = bool(synthetic) if synthetic is not None else wake_id is not None
    operation = "ai worker"
    try:
        roots = _workspace(root)
        vault = roots.vault
        _validate_runtime(roots.state)
        if request_path is None:
            paths = _request_files(vault)
        else:
            paths = [_safe_request_path(vault, request_path)]
        if len(paths) > max_requests:
            raise BackgroundError(f"worker request limit exceeded: {len(paths)} > {max_requests}")
    except (BackgroundError, OSError, TypeError, ValueError) as error:
        return _failure(operation, "BACKGROUND_INPUT_INVALID", str(error))

    requests: list[dict[str, Any]] = []
    request_codes: list[int] = []
    runtime_mutation = False
    if dry_run:
        requests = [_request_item(path, vault) for path in paths]
    else:
        for path in paths:
            result, code = ingest_bridge_request(roots.control, request_path=path)
            request_codes.append(code)
            requests.append(_request_item(path, vault, result))
            runtime_mutation = runtime_mutation or result.get("queue_write") == "CREATED"

    recovery, recovery_code = reconcile_transactions(roots)
    recovery_status, recovery_exit = _recovery_summary(recovery)
    request_conflict = any(code == EXIT_CONFLICT for code in request_codes)
    request_invalid = any(code not in {EXIT_OK, EXIT_CONFLICT} for code in request_codes)
    if request_conflict or recovery_exit == EXIT_CONFLICT:
        status = "CONFLICT"
        exit_code = EXIT_CONFLICT
    elif request_invalid or recovery_exit == EXIT_INPUT_INVALID:
        status = "FAIL"
        exit_code = EXIT_INPUT_INVALID
    elif recovery_status == "REPAIR_REQUIRED":
        status = "REPAIR_REQUIRED"
        exit_code = EXIT_OK
    else:
        status = "PASS"
        exit_code = EXIT_OK

    report: dict[str, Any] = {
        "status": status,
        "operation": operation,
        "capability": CAPABILITY,
        "mode": "once",
        "wake": {
            "kind": "synthetic" if is_synthetic else "invocation",
            "id": selected_wake_id,
            "synthetic": is_synthetic,
        },
        "requests": requests,
        "request_count": len(requests),
        "recovery": recovery,
        "recovery_status": recovery_status,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
        "runtime_mutation_performed": runtime_mutation,
        "dry_run": dry_run,
        "activation": {
            "launchd_active": False,
            "installation": "deferred_to_E03",
            "worker_invocation": "explicit_local_pass",
        },
    }
    if recovery_code != EXIT_OK and recovery.get("errors"):
        report["errors"] = list(recovery["errors"][:10])
    return report, exit_code


def _acquire_worker_lock(directory: Path) -> int:
    path = directory / _WORKER_LOCK_RELATIVE.name
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
        ):
            raise BackgroundError("worker lock permissions or file type are invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerBusy("another worker owns the execution lock") from error
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def worker_once(
    root: str | Path | ResolvedPaths,
    *,
    wake_id: str | None = None,
    request_path: str | Path | None = None,
    dry_run: bool = False,
    max_requests: int = 100,
    synthetic: bool | None = None,
    scheduler_attempt_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Run one non-blocking, recoverable worker pass shared by manual and scheduled calls."""

    try:
        if scheduler_attempt_id is not None and not re.fullmatch(r"[0-9a-f]{32}", scheduler_attempt_id):
            raise BackgroundError("scheduled attempt identity is invalid")
        roots = _workspace(root)
        _validate_runtime(roots.state)
        directory = _worker_directory(roots.state)
        descriptor = _acquire_worker_lock(directory)
    except WorkerBusy as error:
        report, _ = _failure("ai worker", "WORKER_ALREADY_RUNNING", str(error))
        report["status"] = "CONFLICT"
        return report, EXIT_CONFLICT
    except (BackgroundError, OSError, TypeError, ValueError) as error:
        return _failure("ai worker", "BACKGROUND_INPUT_INVALID", str(error))

    try:
        try:
            previous = _read_worker_attempt(directory)
        except BackgroundError as error:
            return _failure("ai worker", "WORKER_ATTEMPT_INVALID", str(error))

        scheduled_current = (
            scheduler_attempt_id is not None
            and previous is not None
            and previous["owner"] == "scheduler"
            and previous["status"] == "running"
            and previous["attempt_id"] == scheduler_attempt_id
        )
        if scheduler_attempt_id is not None and not scheduled_current:
            report, _ = _failure(
                "ai worker",
                "SCHEDULED_ATTEMPT_MISMATCH",
                "scheduled attempt does not own the current State marker",
            )
            report["status"] = "CONFLICT"
            return report, EXIT_CONFLICT
        if (
            scheduler_attempt_id is None
            and previous is not None
            and previous["owner"] == "scheduler"
            and previous["status"] == "running"
        ):
            report, _ = _failure(
                "ai worker",
                "SCHEDULED_ATTEMPT_PENDING",
                "a scheduled attempt is unresolved and blocks manual replay",
            )
            report["status"] = "CONFLICT"
            return report, EXIT_CONFLICT

        if (
            not scheduled_current
            and previous is not None
            and previous["status"] in {"running", "needs_attention"}
        ):
            recovery, recovery_code = reconcile_transactions(roots)
            if recovery.get("status") != "PASS":
                status = recovery.get("status")
                final_status = status if status in {"REPAIR_REQUIRED", "CONFLICT", "FAIL"} else "FAIL"
                blocked, code = _recovery_block_report(recovery, recovery_code)
                blocked["status"] = final_status
                _write_worker_attempt(
                    directory,
                    _worker_attempt_record(
                        attempt_id=previous["attempt_id"],
                        owner=previous["owner"],
                        status="needs_attention",
                        started_at=previous["started_at"],
                        result_status=final_status,
                    ),
                )
                return blocked, code

        if scheduled_current:
            attempt_id = scheduler_attempt_id
            owner = "scheduler"
            started_at = previous["started_at"]
        else:
            attempt_id = uuid.uuid4().hex
            owner = "manual"
            started_at = datetime.now(UTC).isoformat(timespec="seconds")
            _write_worker_attempt(
                directory,
                _worker_attempt_record(
                    attempt_id=attempt_id,
                    owner=owner,
                    status="running",
                    started_at=started_at,
                    result_status=None,
                ),
            )
        report, exit_code = _worker_once_pass(
            roots,
            wake_id=wake_id,
            request_path=request_path,
            dry_run=dry_run,
            max_requests=max_requests,
            synthetic=synthetic,
        )
        result_status = report.get("status")
        marker_status = "completed" if result_status == "PASS" else "needs_attention"
        _write_worker_attempt(
            directory,
            _worker_attempt_record(
                attempt_id=attempt_id,
                owner=owner,
                status=marker_status,
                started_at=started_at,
                result_status=result_status if result_status in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL"} else "FAIL",
            ),
        )
        return report, exit_code
    except (BackgroundError, OSError, TypeError, ValueError) as error:
        return _failure("ai worker", "WORKER_ATTEMPT_WRITE_FAILED", str(error))
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def run_worker_once(
    root: str | Path | ResolvedPaths,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Compatibility entry point for one bounded worker pass."""

    return worker_once(root, **kwargs)


def run_worker(
    root: str | Path | ResolvedPaths,
    *,
    once: bool = True,
    max_cycles: int | None = None,
    wake_id: str | None = None,
    dry_run: bool = False,
    scheduler_attempt_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Run one pass or a bounded file-watch loop.

    C24 verification uses ``once=True``.  The watch mode is intentionally
    opt-in and has no default cycle limit so a separately authorized local
    process can remain supervised by its host.  Every wake still executes the
    same one-shot, provider-free pass.
    """

    if once:
        return worker_once(
            root,
            wake_id=wake_id,
            dry_run=dry_run,
            scheduler_attempt_id=scheduler_attempt_id,
        )
    if scheduler_attempt_id is not None:
        return _failure("ai worker", "BACKGROUND_WATCH_UNAVAILABLE", "scheduled mode requires one pass")
    if max_cycles is not None and (not isinstance(max_cycles, int) or max_cycles < 1):
        return _failure("ai worker", "BACKGROUND_INPUT_INVALID", "max_cycles must be a positive integer")

    try:
        roots = _workspace(root)
        _validate_runtime(roots.state)
        watch_roots = (
            roots.vault / ".vault-bridge" / "requests",
            roots.state / "runs",
        )
        for path in watch_roots:
            _private_or_tracked_directory(path, label=str(path))
        from watchfiles import watch
    except (BackgroundError, ImportError, OSError, TypeError, ValueError) as error:
        return _failure("ai worker", "BACKGROUND_WATCH_UNAVAILABLE", str(error))

    reports: list[dict[str, Any]] = []
    first, first_code = worker_once(
        roots,
        wake_id=wake_id or "watch-initial",
        dry_run=dry_run,
        synthetic=True,
    )
    reports.append(first)
    if first_code != EXIT_OK and first.get("status") == "FAIL":
        return first, first_code
    cycles = 1
    if max_cycles == cycles:
        first["mode"] = "watch"
        first["wake_count"] = len(reports)
        first["wake_reports"] = reports
        return first, first_code
    for _changes in watch(*watch_roots):
        current, current_code = worker_once(
            roots,
            wake_id=f"watch-{cycles}",
            dry_run=dry_run,
            synthetic=True,
        )
        reports.append(current)
        cycles += 1
        if current_code != EXIT_OK and current.get("status") in {"FAIL", "CONFLICT"}:
            return current, current_code
        if max_cycles is not None and cycles >= max_cycles:
            break
    final = reports[-1]
    final["mode"] = "watch"
    final["wake_count"] = len(reports)
    final["wake_reports"] = reports
    return final, 0 if all(report["status"] in {"PASS", "REPAIR_REQUIRED"} for report in reports) else EXIT_CONFLICT


def evaluate_synthetic_wake(
    root: str | Path,
    *,
    wake_id: str = "synthetic-wake",
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Exercise wake, replay, and recovery observation as one read-only test."""

    first, first_code = worker_once(
        root,
        wake_id=wake_id,
        dry_run=dry_run,
        synthetic=True,
    )
    second, second_code = worker_once(
        root,
        wake_id=f"{wake_id}-replay",
        dry_run=dry_run,
        synthetic=True,
    )
    first_paths = [item.get("path") for item in first.get("requests", [])]
    second_by_path = {item.get("path"): item for item in second.get("requests", [])}
    replay_noop = all(
        second_by_path.get(path, {}).get("status") == "NO_OP" for path in first_paths
    )
    recovery_stable = first.get("recovery", {}).get("summary") == second.get("recovery", {}).get("summary")
    statuses_pass = first_code in {EXIT_OK} and second_code in {EXIT_OK}
    status = "PASS" if statuses_pass and replay_noop and recovery_stable else (
        "CONFLICT" if EXIT_CONFLICT in {first_code, second_code} else "FAIL"
    )
    code = EXIT_OK if status == "PASS" else (
        EXIT_CONFLICT if status == "CONFLICT" else EXIT_INPUT_INVALID
    )
    report = {
        "status": status,
        "operation": "ai worker evaluate",
        "capability": CAPABILITY,
        "synthetic_wake": True,
        "wake_id": wake_id,
        "first": first,
        "second": second,
        "checks": {
            "replay_no_op": replay_noop,
            "recovery_stable": recovery_stable,
            "provider_called": False,
            "vault_mutated": False,
            "git_network_called": False,
        },
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
    }
    return report, code


def evaluate_sleep_wake(
    root: str | Path,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Alias matching the Blueprint acceptance scenario name."""

    return evaluate_synthetic_wake(root, **kwargs)


def _source_records(source_info: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if source_info is None:
        return []
    return [
        {
            "path": str(item.get("path", "")),
            "selectors": [str(selector) for selector in item.get("selectors", ())],
            "sha256": str(item.get("sha256", "")),
        }
        for item in source_info
    ]


def background_config_document(
    blueprint: Mapping[str, Any],
    *,
    source_info: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the deterministic C24 background manifest from Blueprint values."""

    automation = blueprint.get("automation_lanes", {})
    bridge = blueprint.get("bridge", {})
    async_lane = automation.get("l1_mobile_async", {}) if isinstance(automation, Mapping) else {}
    detection = bridge.get("detection", {}) if isinstance(bridge, Mapping) else {}
    commands = blueprint.get("commands", ())
    command_list = [str(command) for command in commands if isinstance(command, str)]
    return {
        "schema_version": 1,
        "contract_id": "knowledgeos-background-v1",
        "capability": CAPABILITY,
        "authoritative_inputs": _source_records(source_info),
        "roots": {
            "control_root": CONTROL_ROOT_TEMPLATE,
            "vault_root": VAULT_ROOT_TEMPLATE,
            "runtime_root": RUNTIME_ROOT_TEMPLATE,
        },
        "activation": {
            "enabled_by_default": False,
            "launchd_active": False,
            "installation": "deferred_to_E03",
            "explicit_authorization_required": True,
        },
        "worker": {
            "command": ["vaultctl", "ai", "worker", "--once"],
            "declared_command": "vaultctl ai worker" in command_list,
            "request_namespace": "__KNOWLEDGEOS_VAULT_ROOT__/.vault-bridge/requests/YYYY/MM/JOB_ID.json",
            "detection_baseline": detection.get("baseline"),
            "scheduled_reconcile_scans_local_committed_head": detection.get(
                "scheduled_reconcile_scans_local_committed_head"
            ),
            "performs_git_network_io": detection.get("performs_git_network_io"),
            "runtime_write_scope": list(async_lane.get("runtime_output_scope", ()))
            if isinstance(async_lane, Mapping)
            else [],
            "provider_called": False,
            "vault_mutated": False,
            "git_network_called": False,
            "recovery_action": "reconcile_local_transaction_journals_without_apply",
        },
        "logging": {
            "format": "bounded_jsonl_summary",
            "stdout_path": WORKER_STDOUT_LOG_TEMPLATE,
            "stderr_path": WORKER_STDERR_LOG_TEMPLATE,
            "max_age_seconds": WORKER_LOG_MAX_AGE_SECONDS,
            "max_bytes": WORKER_LOG_MAX_BYTES,
            "retain_bytes": WORKER_LOG_RETAIN_BYTES,
            "legacy_format_action": "discard_on_first_bounded_wake",
        },
        "launchd": {
            "label": LAUNCHD_LABEL,
            "run_at_load": False,
            "start_interval_seconds": 300,
            "throttle_interval_seconds": 60,
            "rollback": "bootout_the_exact_label_and_remove_the_installed_plist",
        },
    }


def launchd_plist_document(
    blueprint: Mapping[str, Any],
    *,
    source_info: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a portable install-time LaunchAgent document.

    The placeholders are intentional.  E03 must bind a concrete control root
    and executable after explicit authorization; C24 must not discover or
    write a host path on the user's behalf.
    """

    manifest = background_config_document(blueprint, source_info=source_info)
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            "/usr/bin/env",
            "vaultctl",
            "ai",
            "worker",
            "--once",
            "--root",
            "__KNOWLEDGEOS_CONTROL_ROOT__",
        ],
        "WorkingDirectory": "__KNOWLEDGEOS_CONTROL_ROOT__",
        "EnvironmentVariables": {
            "KNOWLEDGEOS_CONTROL_ROOT": CONTROL_ROOT_TEMPLATE,
            "KNOWLEDGEOS_VAULT_ROOT": VAULT_ROOT_TEMPLATE,
            "KNOWLEDGEOS_RUNTIME_ROOT": RUNTIME_ROOT_TEMPLATE,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "RunAtLoad": False,
        "StartInterval": int(manifest["launchd"]["start_interval_seconds"]),
        "ThrottleInterval": int(manifest["launchd"]["throttle_interval_seconds"]),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "AbandonProcessGroup": True,
        "Umask": 0o077,
        "StandardOutPath": WORKER_STDOUT_LOG_TEMPLATE,
        "StandardErrorPath": WORKER_STDERR_LOG_TEMPLATE,
        "KnowledgeOS": {
            "capability": CAPABILITY,
            "enabled_by_default": False,
            "installation": "deferred_to_E03",
            "provider_called": False,
            "vault_mutated": False,
            "git_network_called": False,
            "authoritative_inputs": _source_records(source_info),
        },
    }


def launchd_plist_bytes(
    blueprint: Mapping[str, Any],
    *,
    source_info: Sequence[Mapping[str, Any]] | None = None,
) -> bytes:
    """Serialize one deterministic UTF-8 XML plist."""

    return plistlib.dumps(
        launchd_plist_document(blueprint, source_info=source_info),
        fmt=plistlib.FMT_XML,
        sort_keys=False,
    )


def worker_report_schema() -> dict[str, Any]:
    """Return the C24 worker-report schema used by tests and artifact checks."""

    request_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "status", "state"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "status": {"type": "string", "minLength": 1},
            "state": {"type": "string", "minLength": 1},
            "queue_write": {"type": "string", "enum": ["CREATED", "EXISTING"]},
            "job_id": {"type": "string", "pattern": _UUID_V4_RE.pattern},
            "errors": {"type": "array", "maxItems": 5, "items": {"type": "object"}},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/worker-report.schema.json",
        "title": "KnowledgeOS C24 worker report",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "operation",
            "capability",
            "mode",
            "wake",
            "requests",
            "recovery",
            "provider_called",
            "vault_mutated",
            "git_network_called",
            "mutation_performed",
            "runtime_mutation_performed",
            "activation",
        ],
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL"]},
            "operation": {"const": "ai worker"},
            "capability": {"const": CAPABILITY},
            "mode": {"enum": ["once", "watch"]},
            "wake": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "id", "synthetic"],
                "properties": {
                    "kind": {"enum": ["invocation", "synthetic"]},
                    "id": {"type": "string", "minLength": 1},
                    "synthetic": {"type": "boolean"},
                },
            },
            "requests": {"type": "array", "items": request_item},
            "request_count": {"type": "integer", "minimum": 0},
            "wake_count": {"type": "integer", "minimum": 1},
            "wake_reports": {"type": "array", "items": {"type": "object"}},
            "recovery": {"type": "object"},
            "recovery_status": {"type": "string", "minLength": 1},
            "provider_called": {"const": False},
            "vault_mutated": {"const": False},
            "git_network_called": {"const": False},
            "mutation_performed": {"const": False},
            "runtime_mutation_performed": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "activation": {
                "type": "object",
                "additionalProperties": False,
                "required": ["launchd_active", "installation"],
                "properties": {
                    "launchd_active": {"const": False},
                    "installation": {"const": "deferred_to_E03"},
                    "worker_invocation": {"const": "explicit_local_pass"},
                },
            },
            "errors": {"type": "array", "items": {"type": "object"}},
        },
    }


def _canonical_case(value: Any) -> bytes:
    if not isinstance(value, Mapping):
        raise BackgroundError("evaluation case must be an object")
    return canonical_json_bytes(value)


def evaluate_frozen_baseline(
    root: str | Path,
    *,
    baseline_path: str | Path = DEFAULT_BASELINE_PATH,
) -> tuple[dict[str, Any], int]:
    """Evaluate checked-in synthetic wake cases without provider or Vault writes."""

    workspace = Path(root).resolve()
    path = Path(baseline_path)
    if not path.is_absolute():
        path = workspace / path
    try:
        document = load_yaml_file(path)
        if not isinstance(document, Mapping):
            raise BackgroundError("C24 evaluation root must be a mapping")
        cases = document.get("cases")
        if not isinstance(cases, list) or not cases:
            raise BackgroundError("C24 evaluation must contain one or more cases")
        case_reports: list[dict[str, Any]] = []
        all_passed = True
        for case in cases:
            case_bytes = _canonical_case(case)
            case_data = dict(case)
            wake_id = case_data.get("wake_id", "synthetic-wake")
            result, code = evaluate_synthetic_wake(workspace, wake_id=wake_id)
            expected = case_data.get("expected", {})
            if not isinstance(expected, Mapping):
                raise BackgroundError("C24 evaluation expected value must be a mapping")
            checks = dict(result.get("checks", {}))
            for key, expected_value in expected.items():
                checks[f"expected:{key}"] = result.get(key) == expected_value
            passed = (
                code == EXIT_OK
                and all(checks.get(key) is True for key in ("replay_no_op", "recovery_stable"))
                and all(
                    checks.get(key) is False
                    for key in ("provider_called", "vault_mutated", "git_network_called")
                )
                and all(
                    value is True
                    for key, value in checks.items()
                    if key.startswith("expected:")
                )
            )
            case_reports.append(
                {
                    "case_sha256": sha256_bytes(case_bytes),
                    "wake_id": wake_id,
                    "status": "PASS" if passed else "FAIL",
                    "checks": checks,
                    "result": result,
                }
            )
            all_passed = all_passed and passed
    except (BackgroundError, OSError, UnicodeError, TypeError, ValueError, YAMLError) as error:
        return _failure("ai worker evaluate", "BACKGROUND_EVALUATION_INVALID", str(error))

    report = {
        "status": "PASS" if all_passed else "FAIL",
        "operation": "ai worker evaluate",
        "capability": CAPABILITY,
        "cases": case_reports,
        "metrics": {"cases": len(case_reports), "all_cases_passed": all_passed},
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
    }
    return report, EXIT_OK if all_passed else EXIT_CONFLICT


def render_background_artifacts(root: str | Path) -> tuple[dict[str, Any], int]:
    """Inspect already-rendered C24 artifacts without changing the workspace."""

    workspace = Path(root).resolve()
    plist_path = workspace / LAUNCHD_ARTIFACT_PATH
    config_path = workspace / BACKGROUND_CONFIG_PATH
    schema_path = workspace / WORKER_REPORT_SCHEMA_PATH
    try:
        if any(path.is_symlink() or not path.is_file() for path in (plist_path, config_path, schema_path)):
            raise BackgroundError("one or more C24 artifacts are missing or unsafe")
        plist = plistlib.loads(plist_path.read_bytes())
        config = load_yaml_file(config_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if plist.get("Label") != LAUNCHD_LABEL:
            raise BackgroundError("C24 LaunchAgent label is invalid")
        if plist.get("RunAtLoad") is not False or plist.get("KnowledgeOS", {}).get("enabled_by_default") is not False:
            raise BackgroundError("C24 LaunchAgent must remain inactive by default")
        if config.get("activation", {}).get("launchd_active") is not False:
            raise BackgroundError("C24 background manifest must remain inactive")
        logging = config.get("logging", {})
        if logging != {
            "format": "bounded_jsonl_summary",
            "stdout_path": WORKER_STDOUT_LOG_TEMPLATE,
            "stderr_path": WORKER_STDERR_LOG_TEMPLATE,
            "max_age_seconds": WORKER_LOG_MAX_AGE_SECONDS,
            "max_bytes": WORKER_LOG_MAX_BYTES,
            "retain_bytes": WORKER_LOG_RETAIN_BYTES,
            "legacy_format_action": "discard_on_first_bounded_wake",
        }:
            raise BackgroundError("C24 worker logging policy is invalid")
        if config.get("roots") != {
            "control_root": CONTROL_ROOT_TEMPLATE,
            "vault_root": VAULT_ROOT_TEMPLATE,
            "runtime_root": RUNTIME_ROOT_TEMPLATE,
        }:
            raise BackgroundError("C24 background root bindings are invalid")
        worker = config.get("worker", {})
        if not isinstance(worker, Mapping) or worker.get("request_namespace") != (
            "__KNOWLEDGEOS_VAULT_ROOT__/.vault-bridge/requests/YYYY/MM/JOB_ID.json"
        ):
            raise BackgroundError("C24 background request root binding is invalid")
        environment = plist.get("EnvironmentVariables", {})
        if not isinstance(environment, Mapping) or any(
            environment.get(key) != value
            for key, value in {
                "KNOWLEDGEOS_CONTROL_ROOT": CONTROL_ROOT_TEMPLATE,
                "KNOWLEDGEOS_VAULT_ROOT": VAULT_ROOT_TEMPLATE,
                "KNOWLEDGEOS_RUNTIME_ROOT": RUNTIME_ROOT_TEMPLATE,
            }.items()
        ):
            raise BackgroundError("C24 LaunchAgent root environment bindings are invalid")
        if (
            plist.get("StandardOutPath") != WORKER_STDOUT_LOG_TEMPLATE
            or plist.get("StandardErrorPath") != WORKER_STDERR_LOG_TEMPLATE
        ):
            raise BackgroundError("C24 LaunchAgent log paths must use the selected runtime root")
        if schema.get("$id") != "https://local.invalid/knowledgeos/worker-report.schema.json":
            raise BackgroundError("C24 worker schema identity is invalid")
    except (BackgroundError, OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError, YAMLError) as error:
        return _failure("background artifacts", "BACKGROUND_ARTIFACT_INVALID", str(error))
    return {
        "status": "PASS",
        "operation": "background artifacts",
        "capability": CAPABILITY,
        "artifacts": [LAUNCHD_ARTIFACT_PATH, BACKGROUND_CONFIG_PATH, WORKER_REPORT_SCHEMA_PATH],
        "launchd_active": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
    }, EXIT_OK


def launchd_install(
    root: str | Path,
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Preview the E03 install boundary and refuse activation in C24."""

    artifacts, code = render_background_artifacts(root)
    if code != EXIT_OK:
        return artifacts, code
    if not dry_run:
        return _failure(
            "launchd install",
            "LAUNCHD_INSTALL_DEFERRED",
            "LaunchAgent installation is deferred to E03 and requires explicit authorization",
            exit_code=EXIT_CONFLICT,
            launchd_active=False,
            installed=False,
            artifact=LAUNCHD_ARTIFACT_PATH,
        )
    return {
        "status": "PASS",
        "operation": "launchd install preview",
        "capability": CAPABILITY,
        "artifact": LAUNCHD_ARTIFACT_PATH,
        "installed": False,
        "launchd_active": False,
        "installation": "deferred_to_E03",
        "would_install_label": LAUNCHD_LABEL,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mutation_performed": False,
    }, EXIT_OK


# Explicit aliases keep the small public surface discoverable for callers
# that use the noun from the Blueprint rather than the CLI verb.
worker = worker_once
synthetic_wake = evaluate_synthetic_wake
render_launchd_artifact = launchd_plist_bytes


__all__ = [
    "BACKGROUND_CONFIG_PATH",
    "CAPABILITY",
    "DEFAULT_BASELINE_PATH",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "LAUNCHD_ARTIFACT_PATH",
    "LAUNCHD_LABEL",
    "WORKER_LOG_MAX_AGE_SECONDS",
    "WORKER_LOG_MAX_BYTES",
    "WORKER_LOG_RETAIN_BYTES",
    "WORKER_REPORT_SCHEMA_PATH",
    "WORKER_STDERR_LOG_PATH",
    "WORKER_STDOUT_LOG_PATH",
    "background_config_document",
    "configure_worker_log_streams",
    "evaluate_frozen_baseline",
    "evaluate_sleep_wake",
    "evaluate_synthetic_wake",
    "launchd_install",
    "launchd_plist_bytes",
    "launchd_plist_document",
    "render_background_artifacts",
    "render_launchd_artifact",
    "run_worker",
    "run_worker_once",
    "synthetic_wake",
    "worker",
    "worker_log_record",
    "worker_once",
    "worker_report_schema",
]

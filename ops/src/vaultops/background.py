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

import json
import os
import plistlib
import re
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yaml import YAMLError

from .bridge_publish import ingest_bridge_request
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
WORKER_STDOUT_LOG_PATH = "runtime/logs/worker.stdout.log"
WORKER_STDERR_LOG_PATH = "runtime/logs/worker.stderr.log"
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


def configure_worker_log_streams(root: str | Path) -> bool:
    """Bound only the two files opened by launchd, leaving terminal output unchanged."""

    workspace = Path(root).expanduser()
    if not workspace.is_absolute():
        return False
    try:
        workspace = workspace.resolve()
    except OSError:
        return False

    streams = (
        (1, workspace / WORKER_STDOUT_LOG_PATH, "stdout"),
        (2, workspace / WORKER_STDERR_LOG_PATH, "stderr"),
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


def _workspace(root: str | Path) -> tuple[Path, Path]:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise BackgroundError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    runtime = workspace / "runtime"
    if vault.is_symlink() or not vault.is_dir():
        raise BackgroundError("KnowledgeHub must be an existing non-symlink directory")
    if runtime.is_symlink() or not runtime.is_dir():
        raise BackgroundError("runtime must be an existing non-symlink directory")
    if stat.S_IMODE(runtime.stat().st_mode) != 0o700:
        raise BackgroundError("runtime root mode must be 0700")
    return workspace, vault.resolve()


def _private_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BackgroundError(f"{label} must be an existing non-symlink directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise BackgroundError(f"{label} mode must be 0700")


def _validate_runtime(workspace: Path) -> None:
    runtime = workspace / "runtime"
    for name in ("queue", "quarantine", "runs", "logs", "cache"):
        _private_directory(runtime / name, label=f"runtime/{name}")


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


def worker_once(
    root: str | Path,
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
        workspace, vault = _workspace(root)
        _validate_runtime(workspace)
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
            result, code = ingest_bridge_request(workspace, request_path=path)
            request_codes.append(code)
            requests.append(_request_item(path, vault, result))
            runtime_mutation = runtime_mutation or result.get("queue_write") == "CREATED"

    recovery, recovery_code = reconcile_transactions(workspace)
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


def run_worker_once(
    root: str | Path,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Compatibility entry point for one bounded worker pass."""

    return worker_once(root, **kwargs)


def run_worker(
    root: str | Path,
    *,
    once: bool = True,
    max_cycles: int | None = None,
    wake_id: str | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run one pass or a bounded file-watch loop.

    C24 verification uses ``once=True``.  The watch mode is intentionally
    opt-in and has no default cycle limit so a separately authorized local
    process can remain supervised by its host.  Every wake still executes the
    same one-shot, provider-free pass.
    """

    if once:
        return worker_once(root, wake_id=wake_id, dry_run=dry_run)
    if max_cycles is not None and (not isinstance(max_cycles, int) or max_cycles < 1):
        return _failure("ai worker", "BACKGROUND_INPUT_INVALID", "max_cycles must be a positive integer")

    try:
        workspace, vault = _workspace(root)
        _validate_runtime(workspace)
        watch_roots = (
            vault / ".vault-bridge" / "requests",
            workspace / "runtime" / "runs",
        )
        for path in watch_roots:
            _private_or_tracked_directory(path, label=str(path.relative_to(workspace)))
        from watchfiles import watch
    except (BackgroundError, ImportError, OSError, TypeError, ValueError) as error:
        return _failure("ai worker", "BACKGROUND_WATCH_UNAVAILABLE", str(error))

    reports: list[dict[str, Any]] = []
    first, first_code = worker_once(
        workspace,
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
            workspace,
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
        "activation": {
            "enabled_by_default": False,
            "launchd_active": False,
            "installation": "deferred_to_E03",
            "explicit_authorization_required": True,
        },
        "worker": {
            "command": ["vaultctl", "ai", "worker", "--once"],
            "declared_command": "vaultctl ai worker" in command_list,
            "request_namespace": ".vault-bridge/requests/YYYY/MM/JOB_ID.json",
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
            "stdout_path": WORKER_STDOUT_LOG_PATH,
            "stderr_path": WORKER_STDERR_LOG_PATH,
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
            "KNOWLEDGEOS_CONTROL_ROOT": "__KNOWLEDGEOS_CONTROL_ROOT__",
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "RunAtLoad": False,
        "StartInterval": int(manifest["launchd"]["start_interval_seconds"]),
        "ThrottleInterval": int(manifest["launchd"]["throttle_interval_seconds"]),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "AbandonProcessGroup": True,
        "Umask": 0o077,
        "StandardOutPath": "__KNOWLEDGEOS_CONTROL_ROOT__/runtime/logs/worker.stdout.log",
        "StandardErrorPath": "__KNOWLEDGEOS_CONTROL_ROOT__/runtime/logs/worker.stderr.log",
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
            "stdout_path": WORKER_STDOUT_LOG_PATH,
            "stderr_path": WORKER_STDERR_LOG_PATH,
            "max_age_seconds": WORKER_LOG_MAX_AGE_SECONDS,
            "max_bytes": WORKER_LOG_MAX_BYTES,
            "retain_bytes": WORKER_LOG_RETAIN_BYTES,
            "legacy_format_action": "discard_on_first_bounded_wake",
        }:
            raise BackgroundError("C24 worker logging policy is invalid")
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

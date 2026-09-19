"""C36 one-shot provider queue consumption.

The queue consumer is deliberately a small orchestration layer around the
existing C24 bridge queue, C32 synthetic broker, C31 private envelopes, and
C17 exact response publisher.  It owns lifecycle state only; it does not
become a provider, a scheduler, a Vault writer, or a Git network client.

An eligible queue item is claimed by an atomic rename into ``runtime/running``
and receives a short-lived private lease.  The immutable bridge manifest is
never edited.  Provider artifacts remain in the private C31 run directory;
only a revalidated bridge response and (when needed) a review proposal are
handed to C17.  A stale lease is requeued, while malformed or digest-drifted
state is quarantined.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .bridge_contract import canonical_json_bytes as bridge_canonical_json_bytes
from .bridge_contract import (
    validate_bridge_response,
)
from .bridge_publish import ingest_bridge_request, publish_bridge_response
from .note_engine import NoteContractError, NoteEngine
from .provider_broker import PIPELINES, run_synthetic_job, validate_synthetic_output
from .provider_contract import (
    ProviderContractError,
    provider_schema,
    read_context_envelope,
)
from .provider_contract import canonical_json_bytes as provider_canonical_json_bytes
from .recovery import (
    canonical_json_bytes,
    fsync_directory,
    sha256_bytes,
    validate_job_id,
)
from .template_engine import render_note_template
from .yaml_safe import load_yaml_file

CAPABILITY = "C36"
OPERATION = "ai queue"
EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

DEFAULT_MAX_JOBS = 1
DEFAULT_MAX_CONCURRENCY = 1
MAX_CONCURRENCY = 4
DEFAULT_LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 3600
MAX_QUEUE_JOBS = 100

QUEUE_STATES = frozenset({"ingested", "queued"})
BRIDGE_TO_RUNTIME = {
    "needs_review": ("proposal_ready", "review"),
    "answer_ready": ("answer_ready", "review"),
    "no_change": ("no_change", "done"),
    "insufficient_input": ("insufficient_input", "done"),
    "refused": ("refused", "done"),
    "failed": ("failed", "failed"),
    "conflict": ("conflict", "conflict"),
    "expired": ("expired", "expired"),
}
PROPOSAL_PIPELINES = frozenset({"triage", "draft_note", "link_suggestions", "normalize"})
ANSWER_PIPELINES = frozenset({"summarize", "answer"})
TERMINAL_RUNTIME_DIRECTORIES = tuple(sorted({value[1] for value in BRIDGE_TO_RUNTIME.values()}))

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE = re.compile(r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.{1,2}(?:/|$))[^\r\n\x00]+$")
_REQUEST_PATH = re.compile(
    r"^\.vault-bridge/requests/\d{4}/\d{2}/"
    r"(?P<job>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
_TERMINAL_STATE_BY_STATUS = {
    "needs_review": "proposal_ready",
    "answer_ready": "answer_ready",
    "no_change": "no_change",
    "insufficient_input": "insufficient_input",
    "refused": "refused",
    "failed": "failed",
    "conflict": "conflict",
    "expired": "expired",
}


class QueueError(ValueError):
    """A malformed or unsafe queue lifecycle state."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class QueueConflict(QueueError):
    """An immutable queue or response identity differs from the expected one."""


class InjectedQueueCrash(RuntimeError):
    """Test-only interruption seam used to exercise stale-lease recovery."""


@dataclass(frozen=True)
class QueueClaim:
    job_id: str
    pipeline: str
    request_commit: str
    request_sha256: str
    manifest_sha256: str
    manifest_path: Path
    lease_path: Path
    state_path: Path
    manifest: dict[str, Any]
    owner: str
    attempt: int


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise QueueError("C36_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return candidate.resolve()


def _private_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise QueueError("C36_RUNTIME_PATH_INVALID", f"{label} must be an existing directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise QueueError("C36_RUNTIME_MODE_INVALID", f"{label} must be mode 0700")


def _runtime(workspace: Path) -> Path:
    runtime = workspace / "runtime"
    _private_directory(runtime, "runtime")
    for name in (
        "queue",
        "quarantine",
        "running",
        "review",
        "done",
        "failed",
        "conflict",
        "expired",
        "runs",
    ):
        _private_directory(runtime / name, f"runtime/{name}")
    return runtime


def _regular_private(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise QueueError("C36_RUNTIME_FILE_INVALID", f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise QueueError("C36_RUNTIME_MODE_INVALID", f"{label} must be mode 0600")
    try:
        return path.read_bytes()
    except OSError as error:
        raise QueueError("C36_RUNTIME_READ_FAILED", f"{label} could not be read") from error


def _parse_object(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {token}")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise QueueError("C36_JSON_INVALID", f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise QueueError("C36_JSON_INVALID", f"{label} root must be an object")
    return value


def _read_private_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_private(path, label)
    value = _parse_object(raw, label)
    if provider_canonical_json_bytes(value) != raw:
        raise QueueError("C36_SERIALIZATION_INVALID", f"{label} is not canonical JSON")
    return value, raw


def _read_marker(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_private(path, label)
    value = _parse_object(raw, label)
    expected = canonical_json_bytes(value) + b"\n"
    if raw != expected:
        raise QueueError("C36_SERIALIZATION_INVALID", f"{label} is not canonical marker JSON")
    return value, raw


def _read_queue_manifest(path: Path, *, expected_job_id: str | None = None) -> tuple[dict[str, Any], bytes]:
    raw = _regular_private(path, "queue manifest")
    value = _parse_object(raw, "queue manifest")
    # C24 historically emits the bridge canonical bytes plus one additional
    # newline.  Accept only that exact canonical form, never arbitrary JSON
    # whitespace, so the existing queue remains immutable and digest-bound.
    if raw.rstrip(b"\n") != bridge_canonical_json_bytes(value).rstrip(b"\n"):
        raise QueueError("C36_SERIALIZATION_INVALID", "queue manifest is not canonical JSON")
    job_id = validate_job_id(str(value.get("job_id")))
    if expected_job_id is not None and job_id != expected_job_id:
        raise QueueConflict("C36_JOB_ID_DRIFT", "queue manifest job_id differs from its filename")
    if path.name != f"{job_id}.json":
        raise QueueConflict("C36_QUEUE_PATH_INVALID", "queue manifest filename is not bound to job_id")
    if value.get("schema_version") != 1 or value.get("state") not in QUEUE_STATES:
        raise QueueError("C36_QUEUE_STATE_INVALID", "queue manifest schema or state is unsupported")
    required_hashes = (
        "request_sha256",
        "source_blob_sha256",
        "normalized_internal_manifest_sha256",
        "bridge_schema_sha256",
    )
    for field in required_hashes:
        if not isinstance(value.get(field), str) or not _SHA256.fullmatch(value[field]):
            raise QueueError("C36_QUEUE_DIGEST_INVALID", f"queue manifest field is not a SHA-256: {field}")
    request_path = value.get("request_path")
    if not isinstance(request_path, str) or _REQUEST_PATH.fullmatch(request_path) is None:
        raise QueueError("C36_REQUEST_PATH_INVALID", "queue request_path is not canonical")
    if _REQUEST_PATH.fullmatch(request_path).group("job") != job_id:
        raise QueueConflict("C36_REQUEST_JOB_DRIFT", "queue request_path job_id differs from manifest")
    for field in ("source_path", "request_blob_id", "introducing_commit", "committed_tree_id", "source_blob_id"):
        if not isinstance(value.get(field), str) or not value[field] or not _SAFE_RELATIVE.fullmatch(value[field]):
            # Git object IDs are safe opaque identifiers even though they are
            # not paths; the same bounded text check is sufficient here.
            raise QueueError("C36_QUEUE_FIELD_INVALID", f"queue manifest field is invalid: {field}")
    return value, raw


def _read_bridge_private_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    """Read the private C36 bridge event using C17's newline canonical form."""

    raw = _regular_private(path, label)
    value = _parse_object(raw, label)
    if raw.rstrip(b"\n") != bridge_canonical_json_bytes(value).rstrip(b"\n"):
        raise QueueError("C36_SERIALIZATION_INVALID", f"{label} is not canonical bridge JSON")
    return value, raw


def _write_create_only(path: Path, payload: bytes, label: str) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise QueueError("C36_RUNTIME_FILE_INVALID", f"{label} is not a regular file")
    if path.exists():
        existing = _regular_private(path, label)
        if existing == payload:
            return "EXISTING"
        raise QueueConflict("C36_IMMUTABLE_CONFLICT", f"existing {label} bytes differ")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except BaseException:
        if path.exists() or path.is_symlink():
            path.unlink()
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
    return "CREATED"


def _write_marker(path: Path, value: dict[str, Any], label: str) -> str:
    return _write_create_only(path, canonical_json_bytes(value) + b"\n", label)


def _remove_private(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise QueueError("C36_RUNTIME_FILE_INVALID", f"cannot remove unsafe runtime path: {path.name}")
    path.unlink()
    fsync_directory(path.parent)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QueueError("C36_TIME_INVALID", "queue timestamps require a timezone")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise QueueError("C36_TIME_INVALID", f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise QueueError("C36_TIME_INVALID", f"{label} must be an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QueueError("C36_TIME_INVALID", f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _hash_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _private_job(workspace: Path, job_id: str) -> Path:
    validate_job_id(job_id)
    runs = workspace / "runtime" / "runs"
    job = runs / job_id
    _private_directory(job, f"runtime/runs/{job_id}")
    return job


def _validate_provider_schema(value: dict[str, Any], kind: str) -> None:
    errors = sorted(
        Draft202012Validator(provider_schema(kind), format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path) or "/"
        raise QueueConflict("C36_PROVIDER_SCHEMA_INVALID", f"{kind} envelope invalid at {locator}: {error.message}")


def _validate_provider_digest(value: dict[str, Any], field: str, label: str) -> str:
    observed = value.get(field)
    if not isinstance(observed, str) or not _SHA256.fullmatch(observed):
        raise QueueConflict("C36_PROVIDER_DIGEST_INVALID", f"{label} digest is invalid")
    copy = dict(value)
    copy.pop(field, None)
    expected = _hash_json(copy)
    if observed != expected:
        raise QueueConflict("C36_PROVIDER_DIGEST_DRIFT", f"{label} digest does not match its bytes")
    return observed


def _read_provider_artifacts(workspace: Path, claim: QueueClaim) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes]:
    job = _private_job(workspace, claim.job_id)
    request, _request_raw = _read_private_json(job / "request.json", "provider request")
    response, response_raw = _read_private_json(job / "response.json", "provider response")
    receipt, receipt_raw = _read_private_json(job / "receipts" / "provider-receipt.json", "provider receipt")
    _validate_provider_schema(request, "request")
    _validate_provider_schema(response, "response")
    _validate_provider_schema(receipt, "receipt")
    _validate_provider_digest(request, "request_sha256", "provider request")
    _validate_provider_digest(response, "response_sha256", "provider response")
    _validate_provider_digest(receipt, "receipt_sha256", "provider receipt")
    if request.get("job_id") != claim.job_id:
        raise QueueConflict("C36_PROVIDER_REQUEST_BINDING", "provider request is not bound to the queue job")
    if (
        response.get("job_id") != claim.job_id
        or response.get("request_sha256") != request.get("request_sha256")
        or response.get("context_sha256") != request.get("context_sha256")
        or response.get("provider") != request.get("provider")
        or response.get("output_schema") != request.get("output_schema")
    ):
        raise QueueConflict("C36_PROVIDER_RESPONSE_BINDING", "provider response is not bound to the request")
    if (
        receipt.get("job_id") != claim.job_id
        or receipt.get("request_sha256") != request.get("request_sha256")
        or receipt.get("context_sha256") != request.get("context_sha256")
        or receipt.get("response_sha256") != response.get("response_sha256")
        or receipt.get("provider") != request.get("provider")
    ):
        raise QueueConflict("C36_PROVIDER_RECEIPT_BINDING", "provider receipt is not bound to the response")
    expected_output = None if response.get("output") is None else _hash_json(response["output"])
    if response.get("output_sha256") != expected_output:
        raise QueueConflict("C36_PROVIDER_OUTPUT_DIGEST", "provider response output digest differs")
    context = read_context_envelope(workspace, job_id=claim.job_id)
    if response.get("status") == "completed":
        validate_synthetic_output(workspace, context, response.get("output"), pipeline=claim.pipeline)
    return request, context, response, response_raw, receipt_raw


def _terminal_paths(runtime: Path, job_id: str, status: str) -> tuple[Path, Path]:
    try:
        _runtime_state, runtime_directory = BRIDGE_TO_RUNTIME[status]
    except KeyError as error:
        raise QueueError("C36_STATUS_INVALID", f"unsupported terminal bridge status: {status}") from error
    return runtime / runtime_directory / f"{job_id}.json", runtime / runtime_directory / f"{job_id}.manifest.json"


def _existing_terminal(runtime: Path, job_id: str) -> dict[str, Any] | None:
    for directory in TERMINAL_RUNTIME_DIRECTORIES:
        path = runtime / directory / f"{job_id}.json"
        if path.exists() or path.is_symlink():
            value, _raw = _read_marker(path, f"terminal state {job_id}")
            if value.get("job_id") != job_id:
                raise QueueConflict("C36_TERMINAL_JOB_DRIFT", "terminal marker job_id differs from its path")
            return value
    return None


def _quarantine_paths(
    runtime: Path,
    job_id: str,
    paths: list[Path],
    reason: str,
) -> str | None:
    quarantine = runtime / "quarantine" / "provider"
    if quarantine.exists() or quarantine.is_symlink():
        _private_directory(quarantine, "runtime/quarantine/provider")
    else:
        quarantine.mkdir(mode=0o700)
        fsync_directory(quarantine.parent)
    destination = quarantine / f"{job_id}-{uuid.uuid4().hex[:12]}"
    destination.mkdir(mode=0o700)
    for source in paths:
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink() or not source.is_file():
            raise QueueError("C36_QUARANTINE_INVALID", f"unsafe queue path cannot be quarantined: {source.name}")
        os.rename(source, destination / source.name)
    marker = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "job_id": job_id,
        "reason": reason[:1000],
        "quarantined_at": _iso(datetime.now(UTC)),
    }
    _write_marker(destination / "quarantine.json", marker, "provider quarantine marker")
    fsync_directory(destination)
    fsync_directory(quarantine)
    fsync_directory(runtime)
    return destination.relative_to(runtime.parent).as_posix()


def _cleanup_running(runtime: Path, job_id: str) -> None:
    for path in (
        runtime / "running" / f"{job_id}.state.json",
        runtime / "running" / f"{job_id}.lease.json",
    ):
        _remove_private(path)


def _recover_terminal_marker(runtime: Path, marker: dict[str, Any]) -> bool:
    """Finish a marker-first terminal move after a process interruption."""

    job_id = validate_job_id(str(marker.get("job_id")))
    manifest_hash = marker.get("manifest_sha256")
    if not isinstance(manifest_hash, str) or not _SHA256.fullmatch(manifest_hash):
        raise QueueConflict("C36_TERMINAL_DIGEST_INVALID", "terminal marker manifest digest is invalid")
    final_value = marker.get("manifest_path")
    if not isinstance(final_value, str) or not _SAFE_RELATIVE.fullmatch(final_value):
        raise QueueConflict("C36_TERMINAL_PATH_INVALID", "terminal marker manifest path is invalid")
    final_path = runtime.parent / final_value
    running_path = runtime / "running" / f"{job_id}.manifest.json"
    if final_path.exists() or final_path.is_symlink():
        final_raw = _regular_private(final_path, "terminal manifest")
        if _hash_bytes(final_raw) != manifest_hash:
            raise QueueConflict("C36_TERMINAL_MANIFEST_DRIFT", "terminal manifest digest differs")
        if running_path.exists() or running_path.is_symlink():
            running_raw = _regular_private(running_path, "running manifest")
            if running_raw != final_raw:
                raise QueueConflict("C36_RUNNING_MANIFEST_DRIFT", "running and terminal manifest bytes differ")
            _remove_private(running_path)
    elif running_path.exists() or running_path.is_symlink():
        running_raw = _regular_private(running_path, "running manifest")
        if _hash_bytes(running_raw) != manifest_hash:
            raise QueueConflict("C36_RUNNING_MANIFEST_DRIFT", "running manifest digest differs")
        os.rename(running_path, final_path)
        fsync_directory(final_path.parent)
    else:
        raise QueueConflict("C36_TERMINAL_MANIFEST_MISSING", "terminal manifest is missing during recovery")
    _cleanup_running(runtime, job_id)
    return True


def _recover_running(runtime: Path, now: datetime) -> dict[str, Any]:
    recovered = 0
    active = 0
    quarantined: list[str] = []
    for state_path in sorted((runtime / "running").glob("*.state.json")):
        job_id = state_path.name.removesuffix(".state.json")
        try:
            job_id = validate_job_id(job_id)
            marker, _raw = _read_marker(state_path, f"running state {job_id}")
            if marker.get("job_id") != job_id:
                raise QueueConflict("C36_RUNNING_JOB_DRIFT", "running marker job_id differs from its path")
            lease_path = runtime / "running" / f"{job_id}.lease.json"
            manifest_path = runtime / "running" / f"{job_id}.manifest.json"
            lease, _lease_raw = _read_marker(lease_path, f"running lease {job_id}")
            manifest_raw = _regular_private(manifest_path, "running manifest")
            if _hash_bytes(manifest_raw) != marker.get("manifest_sha256") or _hash_bytes(manifest_raw) != lease.get("manifest_sha256"):
                raise QueueConflict("C36_RUNNING_DIGEST_DRIFT", "running manifest digest differs from lease or state")
            expires = _parse_time(lease.get("expires_at"), "lease expires_at")
            if expires > now:
                active += 1
                continue
            queue_path = runtime / "queue" / f"{job_id}.json"
            if queue_path.exists() or queue_path.is_symlink():
                queue_raw = _regular_private(queue_path, "recovery queue manifest")
                if queue_raw != manifest_raw:
                    quarantined_path = _quarantine_paths(runtime, job_id, [queue_path, manifest_path, lease_path, state_path], "expired lease collided with different queue bytes")
                    if quarantined_path:
                        quarantined.append(quarantined_path)
                    continue
                _remove_private(manifest_path)
            else:
                os.rename(manifest_path, queue_path)
                fsync_directory(queue_path.parent)
            _remove_private(lease_path)
            _remove_private(state_path)
            recovered += 1
        except (QueueError, OSError, ValueError) as error:
            paths = [state_path]
            for suffix in ("lease.json", "manifest.json"):
                candidate = runtime / "running" / f"{job_id}.{suffix}"
                if candidate.exists() or candidate.is_symlink():
                    paths.append(candidate)
            quarantined_path = _quarantine_paths(runtime, job_id, paths, str(error))
            if quarantined_path:
                quarantined.append(quarantined_path)
    # A crash may occur after the atomic manifest rename and before its state
    # marker.  There is no safe identity to resume, so quarantine it instead
    # of guessing an owner or silently replaying it.
    for manifest_path in sorted((runtime / "running").glob("*.manifest.json")):
        job_id = manifest_path.name.removesuffix(".manifest.json")
        state_path = runtime / "running" / f"{job_id}.state.json"
        if state_path.exists() or state_path.is_symlink():
            continue
        lease_path = runtime / "running" / f"{job_id}.lease.json"
        try:
            validate_job_id(job_id)
            quarantined_path = _quarantine_paths(runtime, job_id, [manifest_path, lease_path], "claimed manifest has no running state marker")
        except (QueueError, ValueError) as error:
            quarantined_path = _quarantine_paths(runtime, "unknown", [manifest_path, lease_path], str(error))
        if quarantined_path:
            quarantined.append(quarantined_path)
    return {"recovered": recovered, "active": active, "quarantined": quarantined}


def _provider_job_preflight(workspace: Path, job_id: str) -> tuple[str | None, str | None]:
    """Return (pipeline, defer_reason); only local lanes are eligible."""

    job = workspace / "runtime" / "runs" / job_id
    request_path = job / "request.json"
    context_path = job / "context.json"
    if not request_path.is_file() or not context_path.is_file():
        return None, "C36_PROVIDER_JOB_MISSING"
    try:
        request, _raw = _read_private_json(request_path, "provider request")
    except QueueError:
        # Claim malformed envelopes so the consumer can persist a terminal
        # conflict marker rather than repeatedly selecting the same bytes.
        return None, None
    action = request.get("action")
    route = request.get("provider", {}).get("route") if isinstance(request.get("provider"), dict) else None
    if not isinstance(action, str) or action not in PIPELINES:
        return None, None
    if not isinstance(route, str) or not route.startswith("local:"):
        return None, "C36_PROVIDER_ROUTE_INACTIVE"
    return action, None


def _claim(
    workspace: Path,
    runtime: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    manifest_raw: bytes,
    *,
    pipeline: str,
    request_commit: str,
    owner: str,
    now: datetime,
    lease_seconds: int,
) -> QueueClaim | None:
    job_id = validate_job_id(str(manifest["job_id"]))
    running_manifest = runtime / "running" / f"{job_id}.manifest.json"
    if running_manifest.exists() or running_manifest.is_symlink():
        return None
    os.rename(manifest_path, running_manifest)
    fsync_directory(manifest_path.parent)
    fsync_directory(running_manifest.parent)
    manifest_hash = _hash_bytes(manifest_raw)
    previous = _existing_terminal(runtime, job_id)
    attempt = int(previous.get("attempt", 0)) + 1 if previous else 1
    lease_path = runtime / "running" / f"{job_id}.lease.json"
    state_path = runtime / "running" / f"{job_id}.state.json"
    lease = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "job_id": job_id,
        "owner": owner,
        "attempt": attempt,
        "claimed_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=lease_seconds)),
        "manifest_sha256": manifest_hash,
        "request_sha256": manifest["request_sha256"],
    }
    state = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "job_id": job_id,
        "state": "running",
        "runtime_state": "running",
        "pipeline": pipeline,
        "attempt": attempt,
        "owner": owner,
        "claimed_at": _iso(now),
        "manifest_sha256": manifest_hash,
        "request_sha256": manifest["request_sha256"],
        "request_commit": request_commit,
        "lease_path": lease_path.relative_to(runtime.parent).as_posix(),
    }
    try:
        _write_marker(lease_path, lease, "provider lease")
        _write_marker(state_path, state, "provider running state")
    except BaseException:
        if running_manifest.exists() and not running_manifest.is_symlink():
            _quarantine_paths(runtime, job_id, [running_manifest, lease_path, state_path], "claim marker creation failed")
        raise
    return QueueClaim(
        job_id=job_id,
        pipeline=pipeline,
        request_commit=request_commit,
        request_sha256=str(manifest["request_sha256"]),
        manifest_sha256=manifest_hash,
        manifest_path=running_manifest,
        lease_path=lease_path,
        state_path=state_path,
        manifest=manifest,
        owner=owner,
        attempt=attempt,
    )


def _next_sequence(vault: Path, job_id: str) -> int:
    maximum = 0
    root = vault / ".vault-bridge" / "responses"
    if root.exists() or root.is_symlink():
        if root.is_symlink() or not root.is_dir():
            raise QueueError("C36_VAULT_PATH_INVALID", "bridge response root is unsafe")
        for path in root.glob(f"*/ */{job_id}/*.json".replace(" ", "")):
            if path.is_symlink() or not path.is_file():
                raise QueueConflict("C36_RESPONSE_PATH_INVALID", "existing bridge response is unsafe")
            match = re.match(r"^(\d{4})-[a-z_]+\.json$", path.name)
            if match:
                maximum = max(maximum, int(match.group(1)))
    if maximum >= 9999:
        raise QueueConflict("C36_SEQUENCE_EXHAUSTED", "bridge response sequence is exhausted")
    return maximum + 1


def _source_details(context: dict[str, Any]) -> tuple[str, str, str]:
    sources = context.get("source_hashes")
    if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
        raise QueueConflict("C36_SOURCE_BINDING_INVALID", "provider context has no source binding")
    source = sources[0]
    path = source.get("path")
    source_hash = source.get("content_hash")
    locator = source.get("locator") or "frozen evidence"
    if not isinstance(path, str) or not _SAFE_RELATIVE.fullmatch(path):
        raise QueueConflict("C36_SOURCE_BINDING_INVALID", "provider source path is unsafe")
    if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
        raise QueueConflict("C36_SOURCE_BINDING_INVALID", "provider source hash is invalid")
    return path, source_hash, str(locator)


def _proposal_manifest(
    workspace: Path,
    context: dict[str, Any],
    pipeline: str,
    output: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    source_path, source_hash, _locator = _source_details(context)
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise QueueError("C36_VAULT_PATH_INVALID", "KnowledgeHub must be an existing directory")
    source_file = vault / source_path
    source_raw = b""
    if source_file.exists() or source_file.is_symlink():
        if source_file.is_symlink() or not source_file.is_file():
            raise QueueConflict("C36_SOURCE_PATH_INVALID", "provider source is not a regular file")
        source_raw = source_file.read_bytes()
        if _hash_bytes(source_raw) != source_hash:
            raise QueueConflict("C36_SOURCE_DRIFT", "provider source bytes differ from the frozen source hash")

    if pipeline == "triage":
        proposal = output.get("proposal")
        if not isinstance(proposal, dict):
            raise QueueConflict("C36_TRIAGE_OUTPUT_INVALID", "triage output has no proposal object")
        proposal_id = proposal.get("proposal_id")
        title = "C36 triage " + str(proposal_id)[:12]
        target_path = source_path
        target_markdown = source_raw.decode("utf-8") if source_raw else "# Frozen source\n"
        expected_target = source_hash
        action = "update_note"
    else:
        proposal_id = output.get("proposal_id")
        target = output.get("target")
        if not isinstance(target, dict):
            raise QueueConflict("C36_PROPOSAL_TARGET_INVALID", "proposal output has no target")
        target_path = target.get("path")
        target_type = target.get("type")
        if not isinstance(target_path, str) or not _SAFE_RELATIVE.fullmatch(target_path):
            raise QueueConflict("C36_PROPOSAL_TARGET_INVALID", "proposal target path is unsafe")
        if not isinstance(target_type, str):
            raise QueueConflict("C36_PROPOSAL_TARGET_INVALID", "proposal target type is missing")
        excerpt = "frozen evidence"
        candidates = context.get("frozen_candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            excerpt = str(candidates[0].get("excerpt", excerpt)).replace("\x00", " ").strip()[:1200]
        if pipeline == "draft_note":
            candidate = output.get("candidate")
            candidate_title = candidate.get("title") if isinstance(candidate, dict) else "C36 draft"
            target_markdown = (
                f"---\nschema_version: 1\ntype: {target_type}\ntitle: {candidate_title}\n---\n\n{excerpt}\n"
            )
            expected_target = None
            action = "create_note"
        else:
            target_markdown = source_raw.decode("utf-8") if source_raw else excerpt + "\n"
            expected_target = source_hash
            action = "update_note"
        title = "C36 " + pipeline + " " + str(proposal_id)[:12]

    if not isinstance(proposal_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", proposal_id
    ):
        raise QueueConflict("C36_PROPOSAL_ID_INVALID", "provider proposal_id is not UUIDv4")
    manifest: dict[str, Any] = {
        "action": action,
        "target_path": target_path,
        "target_markdown": target_markdown,
    }
    if expected_target is not None:
        manifest["expected_target_sha256"] = expected_target
    body = (
        f"# AI 제안 — {title}\n\n"
        "> 이 노트는 C36 provider queue가 검증한 제안이며 정본이 아니다.\n\n"
        "## 제안 요약\n\n"
        f"Bounded `{pipeline}` output for `{source_path}` at `{_source_details(context)[2]}`. "
        "Canonical mutation remains behind C19 approval.\n\n"
        "## Validated provider output\n\n"
        "```json\n"
        f"{json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        "```\n\n"
        "## C19 manifest\n\n"
        "<!-- vaultops:proposal-manifest\n"
        f"{json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('-->', r'\u002d\u002d>')}\n"
        "-->\n"
    )
    proposal_path = f"01_AI_Review/Pending/C36 {pipeline} {proposal_id[:12]}.md"
    created_at = str(context.get("created_at") or datetime.now(UTC).isoformat(timespec="seconds"))
    rendered = render_note_template(
        "T01_AI_Proposal.md",
        {
            "title": title,
            "id": proposal_id,
            "proposal_id": proposal_id,
            "created": created_at,
            "modified": created_at,
            "sensitivity": "personal",
            "source_hashes": [f"{source_path}|sha256:{source_hash}"],
            "body": body,
        },
    )
    artifact = rendered.markdown
    try:
        NoteEngine.from_root(workspace).typed_note(proposal_path, artifact)
    except (NoteContractError, OSError, UnicodeError, ValueError) as error:
        raise QueueConflict("C36_PROPOSAL_NOTE_INVALID", str(error)) from error
    return proposal_path, manifest, artifact


def _build_bridge_response(
    workspace: Path,
    runtime: Path,
    claim: QueueClaim,
    request: dict[str, Any],
    context: dict[str, Any],
    response: dict[str, Any],
    response_raw: bytes,
    receipt_raw: bytes,
    *,
    event_at: datetime,
) -> tuple[dict[str, Any], bytes | None, str | None]:
    provider_status = str(response.get("status"))
    output_schema_sha = response.get("output_schema", {}).get("sha256") if isinstance(response.get("output_schema"), dict) else None
    if not isinstance(output_schema_sha, str) or not _SHA256.fullmatch(output_schema_sha):
        raise QueueConflict("C36_OUTPUT_SCHEMA_INVALID", "provider response output schema digest is invalid")
    common: dict[str, Any] = {
        "schema_version": 1,
        "job_id": claim.job_id,
        "sequence": _next_sequence(workspace / "KnowledgeHub", claim.job_id),
        "event_at": _iso(event_at),
        "request_sha256": claim.request_sha256,
        "request_commit": claim.request_commit,
        "job_receipt_sha256": _hash_bytes(receipt_raw),
        "output_schema_sha256": output_schema_sha,
        "warnings": [str(item)[:1000] for item in response.get("warnings", []) if isinstance(item, str)][:20],
    }
    proposal_file: bytes | None = None
    proposal_path: str | None = None
    output = response.get("output")
    if provider_status == "completed" and isinstance(output, dict):
        if claim.pipeline in PROPOSAL_PIPELINES:
            proposal_path, _manifest, proposal_file = _proposal_manifest(
                workspace, context, claim.pipeline, output
            )
            if claim.pipeline == "triage":
                proposal_id = output.get("proposal", {}).get("proposal_id") if isinstance(output.get("proposal"), dict) else None
            else:
                proposal_id = output.get("proposal_id")
            if not isinstance(proposal_id, str):
                raise QueueConflict("C36_PROPOSAL_ID_INVALID", "validated proposal output has no proposal_id")
            common.update(
                {
                    "status": "needs_review",
                    "completed_at": str(response["completed_at"]),
                    "proposal_id": proposal_id,
                    "proposal_path": proposal_path,
                    "proposal_sha256": _hash_bytes(proposal_file),
                }
            )
        elif claim.pipeline in ANSWER_PIPELINES:
            if claim.pipeline == "summarize":
                answer_id = output.get("summary_id")
                answer_plaintext = output.get("summary_plaintext")
                answer_sha = output.get("summary_sha256")
            else:
                answer_id = output.get("answer_id")
                answer_plaintext = output.get("answer_plaintext")
                answer_sha = output.get("answer_sha256")
            citations: list[dict[str, str]] = []
            for citation in output.get("citations", []) if isinstance(output.get("citations"), list) else []:
                if isinstance(citation, dict) and all(isinstance(citation.get(key), str) for key in ("path", "locator", "sha256")):
                    citations.append(
                        {"path": citation["path"], "locator": citation["locator"], "sha256": citation["sha256"]}
                    )
            if not isinstance(answer_id, str) or not isinstance(answer_plaintext, str) or not isinstance(answer_sha, str) or not citations:
                raise QueueConflict("C36_ANSWER_OUTPUT_INVALID", "validated answer output is incomplete")
            common.update(
                {
                    "status": "answer_ready",
                    "completed_at": str(response["completed_at"]),
                    "answer_id": answer_id,
                    "answer_plaintext": answer_plaintext,
                    "citations": citations,
                    "answer_sha256": answer_sha,
                }
            )
        else:
            raise QueueConflict("C36_PIPELINE_INVALID", "provider pipeline has no bridge response mapping")
    else:
        if provider_status == "refused":
            bridge_status = "refused"
        elif provider_status == "conflict":
            bridge_status = "conflict"
        else:
            bridge_status = "failed"
        common.update(
            {
                "status": bridge_status,
                "completed_at": str(response["completed_at"]),
            }
        )
    blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
    validation = validate_bridge_response(common, blueprint)
    if not validation.passed:
        raise QueueConflict(
            "C36_BRIDGE_RESPONSE_INVALID",
            json.dumps(validation.as_dict(), ensure_ascii=False, sort_keys=True),
        )
    return common, proposal_file, proposal_path


def _persist_private_bridge_artifact(path: Path, payload: bytes, label: str) -> str:
    return _write_create_only(path, payload, label)


def _terminalize(
    runtime: Path,
    claim: QueueClaim,
    *,
    status: str,
    now: datetime,
    provider_status: str | None,
    broker_report: dict[str, Any],
    publication: dict[str, Any] | None,
    error: dict[str, str] | None = None,
    bridge_response_sha256: str | None = None,
    receipt_sha256: str | None = None,
) -> dict[str, Any]:
    runtime_state, _runtime_directory = BRIDGE_TO_RUNTIME.get(status, ("conflict", "conflict"))
    state_path, final_manifest = _terminal_paths(runtime, claim.job_id, status)
    marker = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "job_id": claim.job_id,
        "state": _TERMINAL_STATE_BY_STATUS.get(status, "conflict"),
        "runtime_state": runtime_state,
        "status": status,
        "pipeline": claim.pipeline,
        "attempt": claim.attempt,
        "owner": claim.owner,
        "manifest_sha256": claim.manifest_sha256,
        "request_sha256": claim.request_sha256,
        "request_commit": claim.request_commit,
        "manifest_path": final_manifest.relative_to(runtime.parent).as_posix(),
        "completed_at": _iso(now),
        "provider_status": provider_status,
        "provider_response_sha256": broker_report.get("response_sha256"),
        "provider_receipt_sha256": receipt_sha256 or broker_report.get("receipt_sha256"),
        "bridge_response_sha256": bridge_response_sha256,
        "publication_status": publication.get("status") if publication else "not_run",
        "provider_called": bool(broker_report.get("provider_called", False)),
        "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
        "live_provider_called": bool(broker_report.get("live_provider_called", False)),
        "vault_mutation_performed": bool(publication and publication.get("status") == "PASS"),
        "canonical_apply_allowed": False,
    }
    if error is not None:
        marker["error"] = error
    _write_marker(state_path, marker, "provider terminal state")
    if final_manifest.exists() or final_manifest.is_symlink():
        final_raw = _regular_private(final_manifest, "terminal manifest")
        if _hash_bytes(final_raw) != claim.manifest_sha256:
            raise QueueConflict("C36_TERMINAL_MANIFEST_DRIFT", "existing terminal manifest differs")
    elif claim.manifest_path.exists() or claim.manifest_path.is_symlink():
        raw = _regular_private(claim.manifest_path, "running manifest")
        if _hash_bytes(raw) != claim.manifest_sha256:
            raise QueueConflict("C36_RUNNING_MANIFEST_DRIFT", "running manifest differs during terminal move")
        os.rename(claim.manifest_path, final_manifest)
        fsync_directory(final_manifest.parent)
    else:
        raise QueueConflict("C36_MANIFEST_MISSING", "claimed manifest is missing during terminal move")
    # C17 re-ingests the committed request during publication.  When the
    # claimed manifest was already moved out of the queue, that idempotent
    # ingest may recreate the same queue bytes; remove only that exact copy.
    queue_path = runtime / "queue" / f"{claim.job_id}.json"
    if queue_path.exists() or queue_path.is_symlink():
        queue_raw = _regular_private(queue_path, "post-publish queue manifest")
        if _hash_bytes(queue_raw) != claim.manifest_sha256:
            raise QueueConflict("C36_QUEUE_REAPPEARANCE_DRIFT", "recreated queue bytes differ from the claimed manifest")
        _remove_private(queue_path)
    _cleanup_running(runtime, claim.job_id)
    return marker


def _claim_report(claim: QueueClaim) -> dict[str, Any]:
    return {
        "job_id": claim.job_id,
        "pipeline": claim.pipeline,
        "attempt": claim.attempt,
        "manifest_sha256": claim.manifest_sha256,
        "request_sha256": claim.request_sha256,
        "owner": claim.owner,
        "state_path": claim.state_path.relative_to(claim.state_path.parents[2]).as_posix(),
    }


def _process_claim(
    workspace: Path,
    runtime: Path,
    claim: QueueClaim,
    *,
    adapter: Any | None,
    event_at: datetime,
    publish_lock: Lock,
    fault_after: str | None,
) -> tuple[dict[str, Any], int]:
    broker_report: dict[str, Any] = {
        "status": "FAIL",
        "provider_called": False,
        "synthetic_provider_called": False,
        "live_provider_called": False,
    }
    publication: dict[str, Any] | None = None
    try:
        if fault_after == "claim":
            raise InjectedQueueCrash("injected crash after atomic claim")
        broker_report, _broker_code = run_synthetic_job(
            workspace,
            job_id=claim.job_id,
            pipeline=claim.pipeline,
            adapter=adapter,
        )
        if fault_after == "provider":
            raise InjectedQueueCrash("injected crash after provider artifacts")
        request, context, provider_response, response_raw, receipt_raw = _read_provider_artifacts(workspace, claim)
        bridge_response_path = workspace / "runtime" / "runs" / claim.job_id / "c36-response.json"
        proposal_path_private = workspace / "runtime" / "runs" / claim.job_id / "c36-proposal.md"
        if bridge_response_path.exists() or bridge_response_path.is_symlink():
            bridge_response, bridge_response_raw = _read_bridge_private_json(
                bridge_response_path, "C36 bridge response"
            )
            proposal_bytes = None
            proposal_path = bridge_response.get("proposal_path")
            if proposal_path:
                proposal_bytes = _regular_private(proposal_path_private, "C36 proposal artifact")
        else:
            bridge_response, proposal_bytes, proposal_path = _build_bridge_response(
                workspace,
                runtime,
                claim,
                request,
                context,
                provider_response,
                response_raw,
                receipt_raw,
                event_at=event_at,
            )
            bridge_response_raw = bridge_canonical_json_bytes(bridge_response)
            _persist_private_bridge_artifact(bridge_response_path, bridge_response_raw, "C36 bridge response")
            if proposal_bytes is not None:
                _persist_private_bridge_artifact(proposal_path_private, proposal_bytes, "C36 proposal artifact")
        blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
        validation = validate_bridge_response(bridge_response, blueprint)
        if not validation.passed:
            raise QueueConflict("C36_BRIDGE_RESPONSE_INVALID", "stored C36 bridge response no longer validates")
        with publish_lock:
            publication, publish_code = publish_bridge_response(
                workspace,
                response_file=bridge_response_path,
                proposal_file=proposal_path_private if proposal_bytes is not None or proposal_path else None,
            )
        if fault_after == "publish":
            raise InjectedQueueCrash("injected crash after exact bridge publication")
        publication_ok = publish_code == EXIT_OK and publication.get("status") in {"PASS", "NO_OP"}
        terminal_status = str(bridge_response["status"]) if publication_ok else "conflict"
        publication_error = None if publication_ok else {
            "code": "C36_BRIDGE_PUBLICATION_FAILED",
            "message": "C17 exact bridge publication did not complete",
        }
        marker = _terminalize(
            runtime,
            claim,
            status=terminal_status,
            now=event_at,
            provider_status=str(provider_response.get("status")),
            broker_report=broker_report,
            publication=publication,
            error=publication_error,
            bridge_response_sha256=_hash_bytes(bridge_response_raw),
            receipt_sha256=_hash_bytes(receipt_raw),
        )
        if fault_after == "terminal":
            raise InjectedQueueCrash("injected crash after terminal marker")
        report = {
            "status": "PASS" if publication_ok else "CONFLICT",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "state": marker["state"],
            "runtime_state": marker["runtime_state"],
            "job_id": claim.job_id,
            "pipeline": claim.pipeline,
            "provider_status": provider_response.get("status"),
            "bridge_response_status": bridge_response["status"],
            "provider_called": bool(broker_report.get("provider_called", False)),
            "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
            "live_provider_called": bool(broker_report.get("live_provider_called", False)),
            "mutation_performed": bool(publication.get("status") == "PASS"),
            "vault_mutation_performed": bool(publication.get("status") == "PASS"),
            "canonical_apply_allowed": False,
            "replayed": broker_report.get("status") == "NO_OP" or publication.get("status") == "NO_OP",
            "publication": publication,
            "terminal_path": state_path if (state_path := _terminal_paths(runtime, claim.job_id, terminal_status)[0]).exists() else None,
            "errors": broker_report.get("errors", []),
        }
        return report, EXIT_OK if publication_ok else EXIT_CONFLICT
    except InjectedQueueCrash as error:
        return {
            "status": "FAIL",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "job_id": claim.job_id,
            "pipeline": claim.pipeline,
            "crashed": True,
            "provider_called": bool(broker_report.get("provider_called", False)),
            "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
            "live_provider_called": bool(broker_report.get("live_provider_called", False)),
            "mutation_performed": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
            "errors": [{"code": "C36_INJECTED_CRASH", "message": str(error)}],
        }, EXIT_CONFLICT
    except QueueConflict as error:
        try:
            marker = _terminalize(
                runtime,
                claim,
                status="conflict",
                now=event_at,
                provider_status=broker_report.get("status"),
                broker_report=broker_report,
                publication=publication,
                error={"code": error.code, "message": str(error)},
            )
            return {
                "status": "CONFLICT",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "state": marker["state"],
                "runtime_state": marker["runtime_state"],
                "job_id": claim.job_id,
                "pipeline": claim.pipeline,
                "provider_called": bool(broker_report.get("provider_called", False)),
                "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
                "live_provider_called": bool(broker_report.get("live_provider_called", False)),
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "errors": [{"code": error.code, "message": str(error)}],
            }, EXIT_CONFLICT
        except (QueueError, OSError, ValueError) as terminal_error:
            return {
                "status": "CONFLICT",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "job_id": claim.job_id,
                "pipeline": claim.pipeline,
                "provider_called": False,
                "live_provider_called": False,
                "synthetic_provider_called": False,
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "errors": [
                    {"code": error.code, "message": str(error)},
                    {"code": "C36_TERMINALIZATION_FAILED", "message": str(terminal_error)},
                ],
            }, EXIT_CONFLICT
    except (QueueError, ProviderContractError) as error:
        try:
            marker = _terminalize(
                runtime,
                claim,
                status="conflict",
                now=event_at,
                provider_status=broker_report.get("status"),
                broker_report=broker_report,
                publication=publication,
                error={"code": getattr(error, "code", "C36_QUEUE_FAILED"), "message": str(error)},
            )
            return {
                "status": "CONFLICT",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "state": marker["state"],
                "runtime_state": marker["runtime_state"],
                "job_id": claim.job_id,
                "pipeline": claim.pipeline,
                "provider_called": bool(broker_report.get("provider_called", False)),
                "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
                "live_provider_called": bool(broker_report.get("live_provider_called", False)),
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "errors": [{"code": getattr(error, "code", "C36_QUEUE_FAILED"), "message": str(error)}],
            }, EXIT_CONFLICT
        except (QueueError, OSError, ValueError) as terminal_error:
            return {
                "status": "CONFLICT",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "job_id": claim.job_id,
                "pipeline": claim.pipeline,
                "provider_called": False,
                "synthetic_provider_called": False,
                "live_provider_called": False,
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "errors": [
                    {"code": getattr(error, "code", "C36_QUEUE_FAILED"), "message": str(error)},
                    {"code": "C36_TERMINALIZATION_FAILED", "message": str(terminal_error)},
                ],
            }, EXIT_CONFLICT
    except (OSError, TypeError, ValueError) as error:
        return {
            "status": "FAIL",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "job_id": claim.job_id,
            "pipeline": claim.pipeline,
            "provider_called": bool(broker_report.get("provider_called", False)),
            "synthetic_provider_called": bool(broker_report.get("synthetic_provider_called", False)),
            "live_provider_called": bool(broker_report.get("live_provider_called", False)),
            "mutation_performed": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
            "errors": [{"code": getattr(error, "code", "C36_QUEUE_FAILED"), "message": str(error)}],
        }, EXIT_INPUT_INVALID


def queue_report_schema() -> dict[str, Any]:
    """Return the stable report shape for one C36 queue wake."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c36-queue-report.schema.json",
        "title": "KnowledgeOS C36 provider queue report",
        "type": "object",
        "required": [
            "status",
            "operation",
            "capability",
            "provider_called",
            "synthetic_provider_called",
            "live_provider_called",
            "vault_mutation_performed",
            "canonical_apply_allowed",
            "claimed",
            "processed",
            "recovered",
            "deferred",
            "results",
        ],
        "properties": {
            "status": {"enum": ["PASS", "FAIL", "CONFLICT", "DEFERRED"]},
            "operation": {"const": OPERATION},
            "capability": {"const": CAPABILITY},
            "provider_called": {"type": "boolean"},
            "synthetic_provider_called": {"type": "boolean"},
            "live_provider_called": {"const": False},
            "vault_mutation_performed": {"type": "boolean"},
            "canonical_apply_allowed": {"const": False},
            "claimed": {"type": "integer", "minimum": 0},
            "processed": {"type": "integer", "minimum": 0},
            "recovered": {"type": "integer", "minimum": 0},
            "deferred": {"type": "integer", "minimum": 0},
            "results": {"type": "array"},
        },
        "additionalProperties": True,
    }


def consume_provider_queue(
    root: str | Path,
    *,
    max_jobs: int = DEFAULT_MAX_JOBS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    job_id: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    owner_id: str | None = None,
    adapter: Any | None = None,
    fault_after: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Consume at most one bounded batch of eligible local provider jobs."""

    workspace: Path | None = None
    try:
        if not isinstance(max_jobs, int) or isinstance(max_jobs, bool) or not 1 <= max_jobs <= MAX_QUEUE_JOBS:
            raise QueueError("C36_MAX_JOBS_INVALID", f"max_jobs must be between 1 and {MAX_QUEUE_JOBS}")
        if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise QueueError("C36_CONCURRENCY_INVALID", f"max_concurrency must be between 1 and {MAX_CONCURRENCY}")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise QueueError("C36_LEASE_INVALID", f"lease_seconds must be between 1 and {MAX_LEASE_SECONDS}")
        if fault_after not in {None, "claim", "provider", "publish", "terminal"}:
            raise QueueError("C36_FAULT_POINT_INVALID", "fault_after is not a supported test seam")
        workspace = _workspace(root)
        runtime = _runtime(workspace)
        if adapter is not None and getattr(adapter, "adapter_kind", "synthetic") == "live":
            raise QueueError("C36_LIVE_PROVIDER_DISABLED", "C36 queue consumption does not activate a live provider adapter")
        selected_job = validate_job_id(job_id) if job_id is not None else None
        selected_now = (now or datetime.now(UTC)).astimezone(UTC)
        if selected_now.tzinfo is None or selected_now.utcoffset() is None:
            raise QueueError("C36_TIME_INVALID", "now must include a timezone")
        owner = owner_id or f"c36-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", owner):
            raise QueueError("C36_OWNER_INVALID", "owner_id contains an unsafe character")
        recovery = {"recovered": 0, "active": 0, "quarantined": []}
        if not dry_run:
            recovery = _recover_running(runtime, selected_now)
        queue_paths = sorted((runtime / "queue").glob("*.json"))
        if selected_job is not None:
            queue_paths = [path for path in queue_paths if path.name == f"{selected_job}.json"]
        results: list[dict[str, Any]] = []
        claims: list[QueueClaim] = []
        deferred = 0
        conflicts = 0
        scanned = 0
        for path in queue_paths:
            if len(claims) >= max_jobs:
                break
            scanned += 1
            try:
                manifest, manifest_raw = _read_queue_manifest(path, expected_job_id=selected_job)
                queue_job_id = str(manifest["job_id"])
                pipeline, defer_reason = _provider_job_preflight(workspace, queue_job_id)
                if defer_reason:
                    deferred += 1
                    results.append(
                        {
                            "status": "DEFERRED",
                            "job_id": queue_job_id,
                            "code": defer_reason,
                            "message": "provider job remains queued because the local C31 envelope or route is unavailable",
                        }
                    )
                    continue
                if pipeline is None:
                    pipeline = str(manifest.get("pipeline_kind", ""))
                if pipeline not in PIPELINES:
                    raise QueueConflict("C36_PIPELINE_INVALID", "queue job does not bind a supported provider pipeline")
                ingest_report, ingest_code = ingest_bridge_request(workspace, job_id=queue_job_id)
                if ingest_code != EXIT_OK:
                    raise QueueConflict("C36_INGEST_FAILED", json.dumps(ingest_report, ensure_ascii=False, sort_keys=True))
                request_commit = str(ingest_report.get("request_commit", ""))
                if not re.fullmatch(r"[0-9a-f]{40}", request_commit):
                    raise QueueConflict("C36_REQUEST_COMMIT_INVALID", "bridge ingest did not return a commit identity")
                claim = _claim(
                    workspace,
                    runtime,
                    path,
                    manifest,
                    manifest_raw,
                    pipeline=pipeline,
                    request_commit=request_commit,
                    owner=owner,
                    now=selected_now,
                    lease_seconds=lease_seconds,
                )
                if claim is not None:
                    claims.append(claim)
            except (QueueError, OSError, TypeError, ValueError) as error:
                conflicts += 1
                job_text = path.stem
                try:
                    safe_job = validate_job_id(job_text)
                except ValueError:
                    safe_job = "unknown"
                quarantined = _quarantine_paths(runtime, safe_job, [path], str(error)) if path.exists() or path.is_symlink() else None
                results.append(
                    {
                        "status": "CONFLICT",
                        "job_id": safe_job,
                        "errors": [{"code": getattr(error, "code", "C36_QUEUE_INVALID"), "message": str(error)}],
                        "quarantined": quarantined,
                    }
                )
        if dry_run:
            status = "CONFLICT" if conflicts else ("DEFERRED" if deferred and not queue_paths else "PASS")
            report = {
                "status": status,
                "operation": OPERATION,
                "capability": CAPABILITY,
                "provider_called": False,
                "synthetic_provider_called": False,
                "live_provider_called": False,
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "claimed": 0,
                "processed": 0,
                "recovered": 0,
                "active": 0,
                "deferred": deferred,
                "scanned": scanned,
                "results": results,
            }
            return report, EXIT_CONFLICT if conflicts else EXIT_OK
        publish_lock = Lock()
        processed = 0
        vault_mutated = False
        synthetic_called = False
        provider_called = False
        if claims:
            with ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="c36-queue") as pool:
                futures = {
                    pool.submit(
                        _process_claim,
                        workspace,
                        runtime,
                        claim,
                        adapter=adapter,
                        event_at=selected_now,
                        publish_lock=publish_lock,
                        fault_after=fault_after,
                    ): claim
                    for claim in claims
                }
                for future in as_completed(futures):
                    claim = futures[future]
                    result, result_code = future.result()
                    processed += 1
                    results.append(result)
                    vault_mutated = vault_mutated or bool(result.get("vault_mutation_performed", False))
                    synthetic_called = synthetic_called or bool(result.get("synthetic_provider_called", False))
                    provider_called = provider_called or bool(result.get("provider_called", False))
                    if result_code != EXIT_OK:
                        conflicts += 1 if result.get("status") == "CONFLICT" else 0
        if conflicts:
            status = "CONFLICT"
        elif any(result.get("status") == "FAIL" for result in results):
            status = "FAIL"
        elif deferred and not processed:
            status = "DEFERRED"
        else:
            status = "PASS"
        report = {
            "status": status,
            "operation": OPERATION,
            "capability": CAPABILITY,
            "provider_called": provider_called,
            "synthetic_provider_called": synthetic_called,
            "live_provider_called": False,
            "mutation_performed": vault_mutated,
            "vault_mutation_performed": vault_mutated,
            "canonical_apply_allowed": False,
            "claimed": len(claims),
            "processed": processed,
            "recovered": int(recovery.get("recovered", 0)),
            "active": int(recovery.get("active", 0)),
            "deferred": deferred,
            "scanned": scanned,
            "quarantined": list(recovery.get("quarantined", [])),
            "results": results,
        }
        crashed = any(result.get("crashed") is True for result in results)
        return report, EXIT_CONFLICT if conflicts or crashed else (EXIT_INPUT_INVALID if status == "FAIL" else EXIT_OK)
    except (QueueError, OSError, TypeError, ValueError) as error:
        report = {
            "status": "FAIL",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "provider_called": False,
            "synthetic_provider_called": False,
            "live_provider_called": False,
            "mutation_performed": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
            "claimed": 0,
            "processed": 0,
            "recovered": 0,
            "deferred": 0,
            "results": [],
            "errors": [{"code": getattr(error, "code", "C36_QUEUE_INVALID"), "message": str(error)}],
        }
        return report, EXIT_INPUT_INVALID


run_queue_once = consume_provider_queue

__all__ = [
    "CAPABILITY",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_MAX_JOBS",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "MAX_CONCURRENCY",
    "QueueConflict",
    "QueueError",
    "consume_provider_queue",
    "queue_report_schema",
    "run_queue_once",
]

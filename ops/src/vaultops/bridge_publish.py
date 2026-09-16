"""Provider-free local bridge ingestion and exact Vault Git publishing.

This module is the bounded C17 implementation.  It observes requests only
after they are committed to the independent ``KnowledgeHub`` repository and
publishes one immutable response event (plus a proposal artifact when the
response needs review) in one exact-path local commit.  It never fetches,
pulls, pushes, invokes a provider, or performs a device round trip.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .bridge_contract import (
    canonical_json_bytes,
    validate_bridge_request,
    validate_bridge_response,
    validate_root_sentinel,
)
from .recovery import (
    RecoveryConflict,
    RecoveryCorruption,
    RecoveryError,
    RecoveryJournal,
    fsync_directory,
    sha256_bytes,
    validate_job_id,
)
from .yaml_safe import load_yaml_file

EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_VALIDATION_FAILED = 13
EXIT_CONFLICT = 30

REQUEST_ROOT = ".vault-bridge/requests"
RESPONSE_ROOT = ".vault-bridge/responses"
PROPOSAL_ROOT = "01_AI_Review/Pending"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_PROPOSAL_BYTES = 10 * 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REQUEST_PATH = re.compile(
    r"^\.vault-bridge/requests/(?P<year>\d{4})/(?P<month>\d{2})/(?P<job>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
_RESPONSE_PATH = re.compile(
    r"^\.vault-bridge/responses/(?P<year>\d{4})/(?P<month>\d{2})/(?P<job>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/(?P<sequence>\d{4})-(?P<status>[a-z_]+)\.json$"
)
_STATUS_FILENAME = re.compile(r"^[a-z_]+$")


class BridgeInputError(ValueError):
    """Raised when a bridge path, document, or Git boundary is unsafe."""


class BridgeConflict(BridgeInputError):
    """Raised when immutable bridge state cannot be adopted safely."""


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
        "status": "FAIL",
        "operation": operation,
        "errors": [_issue(code, "/", message)],
        "created": [],
    }
    report.update(details)
    return report, exit_code


def _workspace_and_vault(root: str | Path) -> tuple[Path, Path]:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise BridgeInputError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise BridgeInputError("KnowledgeHub must be an existing non-symlink directory")
    return workspace, vault.resolve()


def _runtime_ready(workspace: Path) -> None:
    runtime = workspace / "runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise BridgeInputError("runtime must be an existing non-symlink directory")
    if stat.S_IMODE(runtime.stat().st_mode) != 0o700:
        raise BridgeInputError("runtime root mode must be 0700")


def _git(repo: Path, *args: str) -> tuple[int, bytes, bytes]:
    """Run a fixed local Git query or mutation with no network operation."""

    if any(argument in {"fetch", "pull", "push", "clone", "ls-remote"} for argument in args):
        raise BridgeInputError("bridge Git transaction forbids network Git commands")
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BridgeInputError(f"Git command could not complete: {error}") from error
    return completed.returncode, completed.stdout, completed.stderr


def _git_text(repo: Path, *args: str) -> str:
    code, stdout, stderr = _git(repo, *args)
    if code != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise BridgeInputError(message or f"Git command failed: {' '.join(args)}")
    return stdout.decode("utf-8", errors="strict").rstrip("\r\n")


def _git_optional(repo: Path, *args: str) -> tuple[bool, str]:
    code, stdout, _stderr = _git(repo, *args)
    return code == 0, stdout.decode("utf-8", errors="strict").rstrip("\r\n")


def _read_strict_json(path: Path, *, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise BridgeInputError(f"{label} must be an existing regular non-symlink file")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BridgeInputError(f"{label} could not be read") from error
    if len(raw) > maximum:
        raise BridgeInputError(f"{label} exceeds the {maximum}-byte limit")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError(f"duplicate JSON key: {key}")
            document[key] = value
        return document

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {token}")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise BridgeInputError(f"{label} is not strict UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise BridgeInputError(f"{label} root must be a JSON object")
    return value, raw


def _sha256_file(path: Path, *, label: str, maximum: int | None = None) -> tuple[str, bytes]:
    if path.is_symlink() or not path.is_file():
        raise BridgeInputError(f"{label} must be an existing regular non-symlink file")
    raw = path.read_bytes()
    if maximum is not None and len(raw) > maximum:
        raise BridgeInputError(f"{label} exceeds the {maximum}-byte limit")
    return sha256_bytes(raw), raw


def _safe_vault_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise BridgeInputError(f"{label} must be a non-empty Vault-relative POSIX path")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise BridgeInputError(f"{label} must be a normalized Vault-relative path")
    return value


def _vault_path(vault: Path, relative: str, *, label: str) -> Path:
    normalized = _safe_vault_relative(relative, label=label)
    path = vault / normalized
    current = vault
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise BridgeConflict(f"{label} crosses a symlink")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(vault.resolve())
    except ValueError as error:
        raise BridgeInputError(f"{label} escapes KnowledgeHub") from error
    return path


def _validate_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BridgeInputError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _load_blueprint(workspace: Path) -> Mapping[str, Any]:
    value = load_yaml_file(workspace / "blueprint/blueprint.yaml")
    if not isinstance(value, Mapping):
        raise BridgeInputError("Blueprint root must be an object")
    return value


def _sentinel_expected_branch(workspace: Path, vault: Path, blueprint: Mapping[str, Any]) -> str:
    path = vault / ".knowledgeos-root.json"
    sentinel, _raw = _read_strict_json(path, maximum=16 * 1024, label="Vault root sentinel")
    report = validate_root_sentinel(sentinel, blueprint)
    if not report.passed:
        raise BridgeConflict("Vault root sentinel failed validation")
    branch = sentinel.get("expected_branch")
    if not isinstance(branch, str) or not branch:
        raise BridgeConflict("Vault root sentinel has no expected branch")
    current = _git_text(vault, "symbolic-ref", "--quiet", "--short", "HEAD")
    if current != branch:
        raise BridgeConflict(f"Vault branch {current!r} does not match sentinel branch {branch!r}")
    return branch


def _require_independent_git_root(workspace: Path, vault: Path) -> None:
    control_root = Path(_git_text(workspace, "rev-parse", "--show-toplevel")).resolve()
    vault_root = Path(_git_text(vault, "rev-parse", "--show-toplevel")).resolve()
    if control_root != workspace or vault_root != vault:
        raise BridgeInputError("control and Vault must remain independent Git roots")
    _git_text(vault, "rev-parse", "HEAD")


def _status_paths(vault: Path) -> tuple[list[str], list[str], list[str]]:
    code, stdout, stderr = _git(vault, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise BridgeInputError(detail or "Git status could not be read")
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in stdout.decode("utf-8", errors="strict").split("\0"):
        if len(line) < 3:
            continue
        state, path = line[:2], line[3:]
        if state == "??":
            untracked.append(path)
            continue
        if state[0] != " ":
            staged.append(path)
        if state[1] != " ":
            unstaged.append(path)
    return sorted(staged), sorted(unstaged), sorted(untracked)


def _ensure_clean(vault: Path) -> None:
    staged, unstaged, untracked = _status_paths(vault)
    if staged or unstaged or untracked:
        raise BridgeConflict(
            "Vault worktree must be clean before local bridge publish: "
            f"staged={staged}, unstaged={unstaged}, untracked={untracked}"
        )


def _resolve_request_path(vault: Path, request_path: str | Path | None, job_id: str | None) -> Path:
    root = vault / REQUEST_ROOT
    if root.is_symlink() or not root.is_dir():
        raise BridgeInputError(".vault-bridge/requests must be an existing real directory")
    if request_path is not None:
        raw = Path(request_path)
        if raw.is_absolute():
            candidate = raw
        else:
            text = raw.as_posix()
            if text.startswith("KnowledgeHub/"):
                text = text.removeprefix("KnowledgeHub/")
            candidate = vault / text
        try:
            relative = candidate.absolute().relative_to(vault.absolute()).as_posix()
        except ValueError as error:
            raise BridgeInputError("request path must be inside KnowledgeHub") from error
        match = _REQUEST_PATH.fullmatch(relative)
        if not match:
            raise BridgeInputError("request path must match .vault-bridge/requests/YYYY/MM/JOB_ID.json")
        if job_id is not None and match.group("job") != job_id:
            raise BridgeInputError("request path job_id does not match --job-id")
        return _vault_path(vault, relative, label="request path")
    if job_id is not None:
        validate_job_id(job_id)
        matches = sorted(root.glob(f"*/*/{job_id}.json"))
        if len(matches) != 1:
            raise BridgeInputError(f"expected exactly one request for job_id {job_id}")
        return matches[0]
    candidates = [path for path in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.json")]
    if len(candidates) != 1:
        raise BridgeInputError("bridge ingest needs --request or exactly one request in the request root")
    return candidates[0]


def _request_history(vault: Path, relative: str) -> tuple[str, list[dict[str, str]]]:
    raw = _git_text(vault, "log", "--format=%H", "--name-status", "--", relative)
    commits: list[str] = []
    events: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        if _COMMIT.fullmatch(line):
            commits.append(line)
            continue
        parts = line.split("\t")
        if not parts or parts[0] not in {"A", "M", "D", "R", "C", "T"}:
            continue
        events.append({"status": parts[0], "path": parts[-1]})
    if not commits:
        raise BridgeConflict("bridge request is not present in committed Vault history")
    if any(event["status"] != "A" or event["path"] != relative for event in events):
        raise BridgeConflict("bridge request history is not add-only")
    if len(events) != 1:
        raise BridgeConflict("bridge request must have exactly one committed add event")
    introducing = _git_text(vault, "log", "-1", "--format=%H", "--diff-filter=A", "--", relative)
    return introducing, events


def _tree_blob(vault: Path, revision: str, relative: str) -> tuple[str, bytes]:
    blob = _git_text(vault, "rev-parse", f"{revision}:{relative}")
    if not _COMMIT.fullmatch(blob) and len(blob) != 40:
        raise BridgeConflict("Git tree object for bridge path is invalid")
    code, stdout, _stderr = _git(vault, "cat-file", "-p", f"{revision}:{relative}")
    if code != 0:
        raise BridgeConflict("Git tree bytes for bridge path could not be read")
    return blob, stdout


def _parse_request_path(path: Path, vault: Path) -> tuple[str, str]:
    relative = path.relative_to(vault).as_posix()
    match = _REQUEST_PATH.fullmatch(relative)
    if not match:
        raise BridgeInputError("request path does not match the canonical bridge request namespace")
    return relative, match.group("job")


def _write_runtime_create_only(path: Path, payload: bytes, *, label: str, runtime: Path) -> bool:
    if path.is_symlink():
        raise BridgeConflict(f"{label} is a symlink")
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise BridgeConflict(f"existing {label} bytes differ from the immutable request")
        return False
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise BridgeInputError(f"{label} parent must be an existing real directory")
    try:
        path.relative_to(runtime)
    except ValueError as error:
        raise BridgeInputError(f"{label} escapes runtime") from error
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if path.exists():
            path.unlink()
        raise
    fsync_directory(path.parent)
    return True


def _queue_manifest(workspace: Path, ingest: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    schema_path = workspace / "ops/schemas/bridge-request.schema.json"
    schema_digest, _schema_bytes = _sha256_file(
        schema_path, label="trusted bridge request schema", maximum=MAX_RESPONSE_BYTES
    )
    return {
        "schema_version": 1,
        "job_id": ingest["job_id"],
        "state": "ingested",
        "request_path": ingest["request_path"],
        "request_sha256": ingest["request_sha256"],
        "request_blob_id": ingest["request_blob_id"],
        "introducing_commit": ingest["introducing_commit"],
        "committed_tree_id": ingest["committed_tree_id"],
        "source_path": ingest["source_path"],
        "source_blob_id": ingest["source_blob_id"],
        "source_blob_sha256": ingest["source_blob_sha256"],
        "normalized_internal_manifest_sha256": sha256_bytes(canonical_json_bytes(request)),
        "bridge_schema_sha256": schema_digest,
    }


def _persist_queue_manifest(workspace: Path, manifest: Mapping[str, Any]) -> str:
    runtime = workspace / "runtime"
    queue = runtime / "queue"
    if queue.is_symlink() or not queue.is_dir() or stat.S_IMODE(queue.stat().st_mode) != 0o700:
        raise BridgeInputError("runtime/queue must be an existing private directory")
    job_id = validate_job_id(str(manifest["job_id"]))
    path = queue / f"{job_id}.json"
    payload = canonical_json_bytes(manifest) + b"\n"
    try:
        created = _write_runtime_create_only(path, payload, label="bridge queue manifest", runtime=runtime)
    except BridgeConflict as error:
        quarantine_root = runtime / "quarantine" / "bridge"
        if quarantine_root.is_symlink():
            raise
        if not quarantine_root.exists():
            quarantine_root.mkdir(mode=0o700)
            fsync_directory(quarantine_root.parent)
        if not quarantine_root.is_dir() or stat.S_IMODE(quarantine_root.stat().st_mode) != 0o700:
            raise BridgeInputError("runtime/quarantine/bridge must be a private directory")
        destination = quarantine_root / f"{job_id}-{uuid.uuid4().hex[:12]}.json"
        os.rename(path, destination)
        fsync_directory(quarantine_root)
        raise BridgeConflict(f"same job_id has a different request digest; quarantined {destination}") from error
    return "CREATED" if created else "EXISTING"


def _ingest_one(
    workspace: Path,
    vault: Path,
    path: Path,
    *,
    allowed_worktree_paths: set[str] | None = None,
) -> dict[str, Any]:
    blueprint = _load_blueprint(workspace)
    relative, path_job_id = _parse_request_path(path, vault)
    request, raw = _read_strict_json(path, maximum=MAX_REQUEST_BYTES, label="bridge request")
    report = validate_bridge_request(request, blueprint)
    if not report.passed:
        raise BridgeConflict(
            "bridge request schema validation failed: "
            + json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True)
        )
    if request.get("job_id") != path_job_id:
        raise BridgeConflict("request job_id does not match its canonical path")
    job_id = validate_job_id(str(request["job_id"]))
    _require_independent_git_root(workspace, vault)
    expected_branch = _sentinel_expected_branch(workspace, vault, blueprint)
    head = _git_text(vault, "rev-parse", "HEAD")
    staged, unstaged, untracked = _status_paths(vault)
    allowed = allowed_worktree_paths or set()
    drift = [item for item in staged + unstaged + untracked if item not in allowed]
    if drift:
        raise BridgeConflict(
            "Vault worktree drift prevents bridge ingest: "
            f"staged={staged}, unstaged={unstaged}, untracked={untracked}, "
            f"allowed={sorted(allowed)}"
        )
    request_blob_id, committed_request = _tree_blob(vault, "HEAD", relative)
    if committed_request != raw:
        raise BridgeConflict("committed request bytes differ from the current request path")
    introducing_commit, history = _request_history(vault, relative)
    if introducing_commit != head:
        # A request is an append-only transport object, but it may be in an
        # earlier commit than the current clean HEAD.  Keep both identities in
        # the receipt rather than incorrectly requiring a one-file commit.
        request_commit = introducing_commit
    else:
        request_commit = head
    source = request.get("source")
    if not isinstance(source, Mapping):
        raise BridgeConflict("request source must be an object")
    source_relative = _safe_vault_relative(source.get("path"), label="request source path")
    source_path = _vault_path(vault, source_relative, label="request source path")
    source_blob_sha256 = _validate_sha256(source.get("blob_sha256"), label="request source blob_sha256")
    source_blob_id, request_commit_source = _tree_blob(vault, introducing_commit, source_relative)
    _current_source_blob_id, committed_source = _tree_blob(vault, "HEAD", source_relative)
    current_source = _sha256_file(source_path, label="request source")[1]
    if sha256_bytes(request_commit_source) != source_blob_sha256:
        raise BridgeConflict("request source blob_sha256 does not match the request commit tree")
    if request_commit_source != committed_source:
        raise BridgeConflict("request source changed after the request was committed")
    if committed_source != current_source:
        raise BridgeConflict("request source has current worktree drift from the committed tree")
    result = {
        "status": "PASS",
        "operation": "bridge ingest",
        "state": "ingested",
        "job_id": job_id,
        "request_path": relative,
        "request_sha256": sha256_bytes(raw),
        "request_blob_id": request_blob_id,
        "introducing_commit": introducing_commit,
        "request_commit": request_commit,
        "committed_tree_id": _git_text(vault, "rev-parse", f"{introducing_commit}^{{tree}}"),
        "expected_branch": expected_branch,
        "source_path": source_relative,
        "source_blob_id": source_blob_id,
        "source_blob_sha256": source_blob_sha256,
        "request_history": history,
        "created": [],
    }
    manifest = _queue_manifest(workspace, result, request)
    queue_write = _persist_queue_manifest(workspace, manifest)
    result["queue_manifest"] = manifest
    result["queue_write"] = queue_write
    if queue_write == "CREATED":
        result["created"] = [f"runtime/queue/{job_id}.json"]
    else:
        result["status"] = "NO_OP"
    return result


def ingest_bridge_request(
    root: str | Path,
    *,
    request_path: str | Path | None = None,
    job_id: str | None = None,
    allowed_worktree_paths: set[str] | None = None,
) -> tuple[dict[str, Any], int]:
    """Validate one committed request and create its local queue manifest."""

    try:
        workspace, vault = _workspace_and_vault(root)
        path = _resolve_request_path(vault, request_path, job_id)
        result = _ingest_one(
            workspace, vault, path, allowed_worktree_paths=allowed_worktree_paths
        )
        return result, EXIT_OK
    except BridgeConflict as error:
        return _failure("bridge ingest", "BRIDGE_CONFLICT", str(error), exit_code=EXIT_CONFLICT)
    except (OSError, TypeError, UnicodeError, ValueError, BridgeInputError, RecoveryError) as error:
        return _failure("bridge ingest", "BRIDGE_INPUT_INVALID", str(error), exit_code=EXIT_INPUT_INVALID)


def _read_proposal(
    vault: Path,
    response: Mapping[str, Any],
    proposal_file: str | Path | None,
) -> tuple[str, bytes] | None:
    if response.get("status") != "needs_review":
        return None
    relative = _safe_vault_relative(response.get("proposal_path"), label="proposal path")
    if not relative.startswith(PROPOSAL_ROOT + "/"):
        raise BridgeInputError("proposal path must be under 01_AI_Review/Pending")
    expected = _validate_sha256(response.get("proposal_sha256"), label="proposal_sha256")
    target = _vault_path(vault, relative, label="proposal path")
    if proposal_file is not None:
        source = Path(proposal_file)
        source_digest, raw = _sha256_file(source, label="proposal input", maximum=MAX_PROPOSAL_BYTES)
        if source_digest != expected:
            raise BridgeConflict("proposal input bytes do not match proposal_sha256")
    elif target.exists() or target.is_symlink():
        source_digest, raw = _sha256_file(target, label="existing proposal", maximum=MAX_PROPOSAL_BYTES)
        if source_digest != expected:
            raise BridgeConflict("existing proposal bytes do not match proposal_sha256")
    else:
        raise BridgeInputError("needs_review publish requires --proposal-file for a new proposal")
    return relative, raw


def _response_target(response: Mapping[str, Any], request: Mapping[str, Any]) -> tuple[str, int]:
    job_id = validate_job_id(str(response.get("job_id")))
    event_at = response.get("event_at")
    if not isinstance(event_at, str):
        raise BridgeInputError("response event_at must be text")
    try:
        selected = datetime.fromisoformat(event_at)
    except ValueError as error:
        raise BridgeInputError("response event_at must be ISO datetime") from error
    if selected.tzinfo is None or selected.utcoffset() is None:
        raise BridgeInputError("response event_at must include a timezone")
    sequence = response.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1 or sequence > 9999:
        raise BridgeInputError("response sequence must be an integer from 1 through 9999")
    status = response.get("status")
    if not isinstance(status, str) or not _STATUS_FILENAME.fullmatch(status):
        raise BridgeInputError("response status cannot be used as a filename component")
    path = f"{RESPONSE_ROOT}/{selected.year:04d}/{selected.month:02d}/{job_id}/{sequence:04d}-{status}.json"
    if request.get("job_id") != job_id:
        raise BridgeConflict("response job_id does not match the committed request")
    return path, sequence


def _ensure_real_parent(path: Path, root: Path) -> None:
    try:
        relative = path.parent.relative_to(root)
    except ValueError as error:
        raise BridgeInputError("bridge output parent escapes KnowledgeHub") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BridgeConflict("bridge output parent crosses a symlink")
        if current.exists():
            if not current.is_dir():
                raise BridgeInputError("bridge output parent contains a non-directory path")
            continue
        current.mkdir(mode=0o755)
        fsync_directory(current.parent)


def _write_create_only(path: Path, payload: bytes, *, label: str, root: Path) -> bool:
    if path.is_symlink():
        raise BridgeConflict(f"{label} is a symlink")
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise BridgeConflict(f"existing {label} bytes differ from the immutable transaction intent")
        return False
    _ensure_real_parent(path, root)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if path.exists():
            path.unlink()
        raise
    fsync_directory(path.parent)
    return True


def _git_tree_bytes(vault: Path, relative: str) -> bytes | None:
    exists, _ = _git_optional(vault, "cat-file", "-e", f"HEAD:{relative}")
    if not exists:
        return None
    _blob_id, raw = _tree_blob(vault, "HEAD", relative)
    return raw


def _commit_paths(vault: Path, revision: str) -> list[str]:
    raw = _git_text(vault, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", revision)
    return sorted(path for path in raw.splitlines() if path)


def _expected_paths_match_head(vault: Path, files: Mapping[str, bytes]) -> bool:
    for relative, expected in files.items():
        observed = _git_tree_bytes(vault, relative)
        if observed != expected:
            return False
    return _commit_paths(vault, "HEAD") == sorted(files)


def _staged_paths(vault: Path) -> list[str]:
    raw = _git_text(vault, "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB")
    return sorted(path for path in raw.splitlines() if path)


def _stage_and_commit(vault: Path, files: Mapping[str, bytes], job_id: str, status: str) -> str:
    exact = sorted(files)
    staged, unstaged, untracked = _status_paths(vault)
    allowed = set(exact)
    if any(path not in allowed for path in staged + unstaged + untracked):
        raise BridgeConflict(
            "Vault worktree contains a path outside the exact bridge commit set: "
            f"staged={staged}, unstaged={unstaged}, untracked={untracked}, exact={exact}"
        )
    _git_text(vault, "add", "--", *exact)
    if _staged_paths(vault) != exact:
        raise BridgeConflict("Git staged path set does not equal the exact bridge commit set")
    message = f"bridge: publish {job_id} {status}"
    code, _stdout, stderr = _git(
        vault,
        "-c",
        "user.name=KnowledgeOS Bridge",
        "-c",
        "user.email=knowledgeos-bridge@localhost",
        "commit",
        "--no-verify",
        "-m",
        message,
    )
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise BridgeInputError(detail or "exact bridge Git commit failed")
    head = _git_text(vault, "rev-parse", "HEAD")
    if _commit_paths(vault, head) != exact:
        raise BridgeConflict("bridge commit contains paths outside the exact commit set")
    return head


def _receipt_payload(
    workspace: Path,
    intent: Mapping[str, Any],
    *,
    vault_head_after: str,
    blob_hashes: Mapping[str, str],
) -> dict[str, Any]:
    control_head_ok, control_head = _git_optional(workspace, "rev-parse", "HEAD")
    return {
        "schema_version": 1,
        "job_id": intent["job_id"],
        "operation": "bridge_publish",
        "request_path": intent["request_path"],
        "request_sha256": intent["request_sha256"],
        "request_commit": intent["request_commit"],
        "response_path": intent["response_path"],
        "response_sha256": intent["response_sha256"],
        "proposal_path": intent.get("proposal_path"),
        "proposal_sha256": intent.get("proposal_sha256"),
        "control_git_head": control_head if control_head_ok else "UNBORN",
        "vault_git_head_before": intent["vault_git_head_before"],
        "vault_git_head_after": vault_head_after,
        "vault_relative_paths": list(intent["exact_paths"]),
        "staged_blob_hashes": dict(blob_hashes),
    }


def _quarantine(
    journal: RecoveryJournal,
    *,
    message: str,
    code: str = "BRIDGE_CONFLICT",
) -> tuple[dict[str, Any], int]:
    try:
        destination = journal.quarantine(message)
    except (OSError, RecoveryError) as error:
        return _failure(
            "bridge publish",
            "BRIDGE_QUARANTINE_FAILED",
            f"{message}; quarantine failed: {error}",
            exit_code=EXIT_CONFLICT,
        )
    return _failure(
        "bridge publish",
        code,
        message,
        exit_code=EXIT_CONFLICT,
        quarantined=destination,
    )


def _resume_publish(
    workspace: Path,
    vault: Path,
    journal: RecoveryJournal,
    files: Mapping[str, bytes],
    blob_hashes: Mapping[str, str],
    status: str,
    *,
    replayed: bool,
) -> tuple[dict[str, Any], int]:
    intent = journal.intent()
    latest = journal.latest()["state"]
    if latest == "completed":
        receipt = journal.completion_receipt()
        return {
            "status": "NO_OP",
            "operation": "bridge publish",
            "state": "published_local",
            "mode": "apply",
            "job_id": journal.job_id,
            "response_path": intent["response_path"],
            "replayed": True,
            "commit": receipt.get("vault_git_head_after"),
            "completion_receipt": receipt,
            "created": [],
        }, EXIT_OK
    if latest == "conflict":
        return _failure(
            "bridge publish",
            "BRIDGE_TRANSACTION_CONFLICT",
            "bridge recovery journal is already marked conflict",
            exit_code=EXIT_CONFLICT,
            job_id=journal.job_id,
        )
    try:
        expected_status = str(intent["status"])
        if expected_status != status:
            raise BridgeConflict("bridge retry status differs from the fsynced publish intent")
        for relative, payload in files.items():
            _write_create_only(
                _vault_path(vault, relative, label="bridge output path"),
                payload,
                label=relative,
                root=vault,
            )
        if latest == "intent":
            journal.append("applying", {"paths": list(intent["exact_paths"]), "status": status})
        if journal.latest()["state"] == "applying":
            journal.append("published", {"paths": list(intent["exact_paths"]), "blob_sha256": dict(blob_hashes)})
        head_before = str(intent["vault_git_head_before"])
        current_head = _git_text(vault, "rev-parse", "HEAD")
        if current_head != head_before and _expected_paths_match_head(vault, files):
            head_after = current_head
        else:
            head_after = _stage_and_commit(vault, files, journal.job_id, status)
        receipt_payload = _receipt_payload(
            workspace, intent, vault_head_after=head_after, blob_hashes=blob_hashes
        )
        journal.append("completed", {"receipt": receipt_payload})
        receipt = journal.completion_receipt()
        return {
            "status": "PASS",
            "operation": "bridge publish",
            "state": "published_local",
            "mode": "apply",
            "job_id": journal.job_id,
            "response_path": intent["response_path"],
            "proposal_path": intent.get("proposal_path"),
            "commit": head_after,
            "replayed": replayed,
            "completion_receipt": receipt,
            "created": list(intent["exact_paths"]),
            "committed_paths": list(intent["exact_paths"]),
        }, EXIT_OK
    except BridgeConflict as error:
        return _quarantine(journal, message=str(error))
    except (OSError, TypeError, UnicodeError, ValueError, BridgeInputError, RecoveryError) as error:
        return _failure(
            "bridge publish",
            "BRIDGE_PUBLISH_FAILED",
            str(error),
            exit_code=EXIT_INPUT_INVALID,
            job_id=journal.job_id,
        )


def publish_bridge_response(
    root: str | Path,
    *,
    response_file: str | Path,
    proposal_file: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Publish one validated response and optional proposal in one local commit."""

    workspace: Path | None = None
    journal: RecoveryJournal | None = None
    try:
        workspace, vault = _workspace_and_vault(root)
        _runtime_ready(workspace)
        blueprint = _load_blueprint(workspace)
        response, _raw_response = _read_strict_json(
            Path(response_file), maximum=MAX_RESPONSE_BYTES, label="bridge response"
        )
        validation = validate_bridge_response(response, blueprint)
        if not validation.passed:
            raise BridgeConflict(
                "bridge response schema validation failed: "
                + json.dumps(validation.as_dict(), ensure_ascii=False, sort_keys=True)
            )
        job_id = validate_job_id(str(response["job_id"]))
        journal = RecoveryJournal(workspace, job_id=job_id, operation="bridge_publish")
        allowed_worktree_paths: set[str] | None = None
        if journal.exists:
            try:
                existing_intent = journal.intent()
            except (RecoveryCorruption, RecoveryError) as error:
                return _quarantine(journal, message=str(error), code="BRIDGE_JOURNAL_INVALID")
            existing_paths = existing_intent.get("exact_paths")
            if not isinstance(existing_paths, list) or not all(
                isinstance(path, str) for path in existing_paths
            ):
                return _quarantine(
                    journal,
                    message="bridge recovery exact_paths are invalid",
                    code="BRIDGE_JOURNAL_INVALID",
                )
            allowed_worktree_paths = set(existing_paths)
        ingest, ingest_code = ingest_bridge_request(
            workspace,
            job_id=job_id,
            allowed_worktree_paths=allowed_worktree_paths,
        )
        if ingest_code != EXIT_OK:
            return {
                "status": "FAIL" if ingest_code != EXIT_CONFLICT else "CONFLICT",
                "operation": "bridge publish",
                "job_id": job_id,
                "errors": ingest.get("errors", []),
                "created": [],
            }, ingest_code
        request_path = str(ingest["request_path"])
        request_sha256 = str(ingest["request_sha256"])
        request_commit = str(ingest["request_commit"])
        if response.get("request_sha256") != request_sha256:
            raise BridgeConflict("response request_sha256 does not match the committed request")
        if response.get("request_commit") != request_commit:
            raise BridgeConflict("response request_commit does not match the committed request")
        request_doc, _request_raw = _read_strict_json(
            vault / request_path, maximum=MAX_REQUEST_BYTES, label="committed bridge request"
        )
        response_relative, _sequence = _response_target(response, request_doc)
        event_bytes = canonical_json_bytes(response) + b"\n"
        response_sha256 = sha256_bytes(event_bytes)
        proposal = _read_proposal(vault, response, proposal_file)
        files: dict[str, bytes] = {response_relative: event_bytes}
        if proposal is not None:
            proposal_relative, proposal_bytes = proposal
            files[proposal_relative] = proposal_bytes
        blob_hashes = {relative: sha256_bytes(payload) for relative, payload in files.items()}
        exact_paths = sorted(files)
        _require_independent_git_root(workspace, vault)
        _sentinel_expected_branch(workspace, vault, blueprint)
        journal_existed = journal.exists
        current_head = _git_text(vault, "rev-parse", "HEAD")
        head_before = current_head
        if journal_existed:
            try:
                existing_intent = journal.intent()
            except (RecoveryCorruption, RecoveryError) as error:
                return _quarantine(journal, message=str(error), code="BRIDGE_JOURNAL_INVALID")
            existing_head = existing_intent.get("vault_git_head_before")
            if isinstance(existing_head, str) and existing_head:
                head_before = existing_head
        intent = {
            "schema_version": 1,
            "job_id": job_id,
            "status": response["status"],
            "request_path": request_path,
            "request_sha256": request_sha256,
            "request_commit": request_commit,
            "response_path": response_relative,
            "response_sha256": response_sha256,
            "proposal_path": proposal[0] if proposal is not None else None,
            "proposal_sha256": sha256_bytes(proposal[1]) if proposal is not None else None,
            "exact_paths": exact_paths,
            "blob_hashes": blob_hashes,
            "vault_git_head_before": head_before,
        }
        try:
            existing_records = journal.start(intent)
        except RecoveryConflict as error:
            return _quarantine(journal, message=str(error), code="BRIDGE_INTENT_MISMATCH")
        if not journal_existed:
            _ensure_clean(vault)
        elif existing_records[-1]["state"] != "completed":
            # A retry may carry the exact files or staged set left by a crash;
            # _resume_publish verifies those paths before it touches Git.
            pass
        return _resume_publish(
            workspace,
            vault,
            journal,
            files,
            blob_hashes,
            str(response["status"]),
            replayed=journal_existed,
        )
    except BridgeConflict as error:
        if journal is not None:
            return _quarantine(journal, message=str(error))
        return _failure("bridge publish", "BRIDGE_CONFLICT", str(error), exit_code=EXIT_CONFLICT)
    except (OSError, TypeError, UnicodeError, ValueError, BridgeInputError, RecoveryError) as error:
        return _failure("bridge publish", "BRIDGE_INPUT_INVALID", str(error), exit_code=EXIT_INPUT_INVALID)


def bridge_status(
    root: str | Path, *, job_id: str | None = None
) -> tuple[dict[str, Any], int]:
    """List local request/response transport files without changing state."""

    try:
        workspace, vault = _workspace_and_vault(root)
        request_root = vault / REQUEST_ROOT
        response_root = vault / RESPONSE_ROOT
        requests = []
        responses = []
        for path in sorted(request_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.json")):
            relative = path.relative_to(vault).as_posix()
            match = _REQUEST_PATH.fullmatch(relative)
            if match and (job_id is None or match.group("job") == job_id):
                requests.append(relative)
        for path in sorted(response_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*/*.json")):
            relative = path.relative_to(vault).as_posix()
            match = _RESPONSE_PATH.fullmatch(relative)
            if match and (job_id is None or match.group("job") == job_id):
                responses.append(relative)
        return {
            "status": "PASS",
            "operation": "bridge status",
            "mode": "read-only",
            "requests": requests,
            "responses": responses,
            "control_root": workspace.as_posix(),
            "vault_root": vault.as_posix(),
            "created": [],
        }, EXIT_OK
    except (OSError, TypeError, UnicodeError, ValueError, BridgeInputError) as error:
        return _failure("bridge status", "BRIDGE_STATUS_INVALID", str(error))


# Command-shaped aliases keep the Python API aligned with Blueprint names.
def reconcile_bridge_publish(
    workspace: Path, vault: Path, journal: RecoveryJournal
) -> tuple[str, dict[str, Any]]:
    """Inspect an incomplete bridge journal without changing Vault state."""

    intent = journal.intent()
    paths = intent.get("exact_paths")
    blob_hashes = intent.get("blob_hashes")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise BridgeInputError("bridge recovery exact_paths are invalid")
    if not isinstance(blob_hashes, Mapping):
        raise BridgeInputError("bridge recovery blob_hashes are invalid")
    observation: dict[str, Any] = {
        "response_path": intent.get("response_path"),
        "exact_paths": list(paths),
        "expected_blob_hashes": dict(blob_hashes),
        "vault_git_head_before": intent.get("vault_git_head_before"),
    }
    missing: list[str] = []
    mismatched: list[str] = []
    files: dict[str, bytes] = {}
    for relative in paths:
        target = _vault_path(vault, relative, label="bridge recovery path")
        if target.is_symlink() or not target.is_file():
            missing.append(relative)
            continue
        payload = target.read_bytes()
        files[relative] = payload
        expected = blob_hashes.get(relative)
        if not isinstance(expected, str) or sha256_bytes(payload) != expected:
            mismatched.append(relative)
    observation["missing_paths"] = missing
    observation["mismatched_paths"] = mismatched
    if mismatched:
        return "conflict", {
            **observation,
            "reason": "bridge output bytes differ from the recovery intent",
            "issues": [_issue("BRIDGE_OUTPUT_HASH_MISMATCH", "/blob_hashes", "bridge output bytes differ")],
            "actions": ["quarantine_journal"],
        }
    if missing:
        return "conflict", {
            **observation,
            "reason": "bridge output is missing and cannot be reconstructed from a digest-only journal",
            "issues": [_issue("BRIDGE_OUTPUT_MISSING", "/exact_paths", "bridge output is missing")],
            "actions": ["quarantine_journal"],
        }
    if _expected_paths_match_head(vault, files):
        observation["observed_head"] = _git_text(vault, "rev-parse", "HEAD")
        return "repairable", {
            **observation,
            "reason": "exact bridge files are committed and only journal completion remains",
            "actions": ["complete_journal_and_receipt"],
        }
    staged, unstaged, untracked = _status_paths(vault)
    if any(path not in set(paths) for path in staged + unstaged + untracked):
        return "conflict", {
            **observation,
            "reason": "Vault contains a path outside the bridge recovery set",
            "issues": [_issue("BRIDGE_EXACT_PATH_SET_INVALID", "/exact_paths", "unrelated Vault drift exists")],
            "actions": ["quarantine_journal"],
        }
    return "repairable", {
        **observation,
        "reason": "exact bridge files are present but are not committed",
        "actions": ["commit_bridge_paths"],
    }


def apply_bridge_recovery(
    workspace: Path, vault: Path, journal: RecoveryJournal
) -> tuple[dict[str, Any], int]:
    """Complete a bridge journal when all output bytes are still present."""

    intent = journal.intent()
    paths = intent.get("exact_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise BridgeInputError("bridge recovery exact_paths are invalid")
    files: dict[str, bytes] = {}
    blob_hashes = intent.get("blob_hashes")
    if not isinstance(blob_hashes, Mapping):
        raise BridgeInputError("bridge recovery blob_hashes are invalid")
    for relative in paths:
        target = _vault_path(vault, relative, label="bridge recovery path")
        if target.is_symlink() or not target.is_file():
            raise BridgeConflict("bridge recovery output is missing or unsafe")
        payload = target.read_bytes()
        if sha256_bytes(payload) != blob_hashes.get(relative):
            raise BridgeConflict("bridge recovery output digest does not match the journal")
        files[relative] = payload
    return _resume_publish(
        workspace,
        vault,
        journal,
        files,
        {relative: sha256_bytes(payload) for relative, payload in files.items()},
        str(intent["status"]),
        replayed=True,
    )


bridge_ingest = ingest_bridge_request
bridge_publish = publish_bridge_response

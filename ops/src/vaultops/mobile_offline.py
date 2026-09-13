"""Offline mobile Shortcut contracts and durable recovery outbox.

S09 owns the device-local part of the mobile contract only.  This module does
not access Shortcuts, Working Copy, a device filesystem, a Git remote, or the
production Vault.  It provides a small deterministic model that can be used by
an eventual device adapter and by synthetic recovery fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from .bridge_contract import canonical_json_bytes
from .yaml_safe import load_yaml_file

OUTBOX_SCHEMA_VERSION = 1
MAX_MARKDOWN_BYTES = 65_536
MAX_SELECTED_TEXT_BYTES = 20_480
STALE_WARNING_DAYS = 30
CLEANUP_MINIMUM_AGE_DAYS = 7
ALLOWED_ASSET_MIME = frozenset({"image/jpeg", "image/png", "application/pdf"})
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
OUTBOX_STATES = (
    "captured",
    "vault_written",
    "committed",
    "pushed",
    "remote_observed",
    "local_vault_transferred",
)
OUTBOX_ALLOWED_TRANSITIONS = {
    None: ("captured",),
    "captured": ("vault_written", "local_vault_transferred"),
    "vault_written": ("committed", "local_vault_transferred"),
    "committed": ("pushed", "local_vault_transferred"),
    "pushed": ("remote_observed",),
    "remote_observed": (),
    "local_vault_transferred": (),
}
OUTBOX_EVENT_REASONS = frozenset(
    {
        "offline",
        "auth_failure",
        "push_rejected",
        "shortcut_cancelled",
        "reboot_recovery",
        "unexpected_staged_path",
        "dirty_repository",
        "detached_head",
        "diverged_repository",
        "conflict",
    }
)
INPUT_KINDS = frozenset({"thought", "url", "quote", "voice", "file", "source"})
STORAGE_POLICIES = frozenset({"github_allowed", "local_only"})
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TARGET_RE = re.compile(
    r"^00_Inbox/Captures/[0-9]{4}/[0-9]{2}/[A-Za-z0-9._-]+\.md$"
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "credential_assignment",
        re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}"),
    ),
)


class MobileContractError(ValueError):
    """Raised when a mobile contract input is unsafe or malformed."""


class OutboxConflictError(MobileContractError):
    """Raised when one outbox ID is reused with different payload bytes."""


@dataclass(frozen=True)
class GateResult:
    """A bounded, non-sensitive result for a mobile safety gate."""

    passed: bool
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "FAIL",
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class OutboxRecord:
    """A verified payload and its append-only event history."""

    job_id: str
    payload: Mapping[str, Any]
    payload_sha256: str
    events: tuple[Mapping[str, Any], ...]

    @property
    def acknowledged(self) -> bool:
        return any(event.get("state") in {"remote_observed", "local_vault_transferred"} for event in self.events)

    @property
    def created_at(self) -> datetime:
        value = self.payload["created_at"]
        return _parse_datetime(value)


def sha256_bytes(content: bytes) -> str:
    """Return a lowercase SHA-256 digest without exposing the input."""

    return hashlib.sha256(content).hexdigest()


def scan_secret_patterns(content: str) -> tuple[str, ...]:
    """Return stable pattern names, never matched secret material."""

    return tuple(name for name, pattern in _SECRET_PATTERNS if pattern.search(content))


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise MobileContractError("created_at/event_at must be an RFC3339 datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MobileContractError("created_at/event_at must be an RFC3339 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MobileContractError("created_at/event_at must include a timezone offset")
    return parsed


def _validate_uuid(value: Any) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise MobileContractError("job_id must be a lowercase UUID v4")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise MobileContractError("job_id must be a lowercase UUID v4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise MobileContractError("job_id must be a lowercase UUID v4")
    return value


def _validate_target_path(value: Any, job_id: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not _TARGET_RE.fullmatch(value):
        raise MobileContractError("target_path must be an ASCII Inbox capture/import Markdown path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
        raise MobileContractError("target_path must be a normalized relative POSIX path")
    if job_id[:8] not in path.name:
        raise MobileContractError("target_path must bind its deterministic filename to the job ID")
    return value


def validate_text_input(
    content: str | bytes,
    *,
    selected_text: bool = False,
) -> tuple[str, int, str]:
    """Validate UTF-8, control characters, and mobile byte limits."""

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MobileContractError("content must be valid UTF-8") from error
    elif isinstance(content, str):
        text = content
    else:
        raise MobileContractError("content must be text or UTF-8 bytes")
    if "\x00" in text or any(ord(char) < 32 and char not in "\t\n" for char in text) or "\x7f" in text:
        raise MobileContractError("content contains a forbidden ASCII control character")
    encoded = text.encode("utf-8")
    limit = MAX_SELECTED_TEXT_BYTES if selected_text else MAX_MARKDOWN_BYTES
    if len(encoded) > limit:
        label = "selected text" if selected_text else "Markdown content"
        raise MobileContractError(f"{label} exceeds the {limit}-byte limit")
    return text, len(encoded), sha256_bytes(encoded)


def validate_storage_policy(
    storage_policy: str,
    *,
    sensitivity: str | None = None,
    content: str | None = None,
) -> GateResult:
    """Enforce the explicit Git-allowed vs local-only storage choice."""

    if storage_policy not in STORAGE_POLICIES:
        return GateResult(False, "MOBILE_STORAGE_POLICY_REQUIRED", "an explicit storage policy is required")
    if storage_policy == "github_allowed" and sensitivity == "confidential":
        return GateResult(False, "MOBILE_CONFIDENTIAL_GITHUB_FORBIDDEN", "confidential content cannot enter the notes repository")
    matches = scan_secret_patterns(content or "")
    if storage_policy == "github_allowed" and matches:
        return GateResult(
            False,
            "MOBILE_SECRET_PATTERN_FOUND",
            "bounded secret scan blocked GitHub storage",
            {"patterns": matches},
        )
    return GateResult(
        True,
        "PASS",
        "storage policy accepted",
        {"storage_policy": storage_policy, "secret_patterns": matches},
    )


def validate_asset(
    mime_type: str,
    size_bytes: int,
    *,
    filename: str = "asset",
    converted_mime_type: str | None = None,
    converted_size_bytes: int | None = None,
    placeholder: bool = False,
) -> GateResult:
    """Apply the S09 mobile MIME and size policy without downloading data."""

    if not isinstance(size_bytes, int) or size_bytes < 0:
        return GateResult(False, "MOBILE_ASSET_SIZE_INVALID", "asset size must be a non-negative integer")
    if mime_type == "image/heic":
        if converted_mime_type in ALLOWED_ASSET_MIME and isinstance(converted_size_bytes, int):
            return validate_asset(converted_mime_type, converted_size_bytes, filename=filename)
        if placeholder:
            return GateResult(True, "MOBILE_ASSET_PLACEHOLDER", "HEIC conversion was not verified; metadata placeholder required")
        return GateResult(False, "MOBILE_HEIC_REQUIRES_REVALIDATION", "HEIC must be converted to JPEG and revalidated or become a placeholder")
    if mime_type not in ALLOWED_ASSET_MIME:
        if mime_type.startswith(("audio/", "video/")) or mime_type in {"application/x-executable", "application/octet-stream"}:
            code = "MOBILE_ASSET_FORBIDDEN_MIME"
        else:
            code = "MOBILE_ASSET_UNSUPPORTED_MIME"
        return GateResult(False, code, f"asset MIME type is not allowed: {mime_type}")
    limit = MAX_PDF_BYTES if mime_type == "application/pdf" else MAX_IMAGE_BYTES
    if size_bytes > limit:
        return GateResult(
            True,
            "MOBILE_ASSET_PLACEHOLDER",
            "oversize asset must remain a metadata placeholder for Mac import",
            {"filename": filename, "mime_type": mime_type, "size_bytes": size_bytes, "limit_bytes": limit},
        )
    return GateResult(True, "PASS", "asset is within the mobile allowlist and size budget")


def build_outbox_payload(
    *,
    job_id: str,
    target_path: str,
    created_at: str,
    input_kind: str,
    storage_policy: str,
    content: str | bytes,
    selected_text: bool = False,
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """Build the strict, canonical payload stored before any Vault write."""

    normalized_job_id = _validate_uuid(job_id)
    normalized_target = _validate_target_path(target_path, normalized_job_id)
    _parse_datetime(created_at)
    if input_kind not in INPUT_KINDS:
        raise MobileContractError(f"unsupported input_kind: {input_kind}")
    text, content_bytes, content_sha256 = validate_text_input(content, selected_text=selected_text)
    storage_result = validate_storage_policy(
        storage_policy,
        sensitivity=sensitivity,
        content=text,
    )
    if not storage_result.passed:
        raise MobileContractError(storage_result.message)
    return {
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "job_id": normalized_job_id,
        "target_path": normalized_target,
        "created_at": created_at,
        "input_kind": input_kind,
        "sensitivity_choice": storage_policy,
        "content": text,
        "content_bytes": content_bytes,
        "content_sha256": content_sha256,
    }


def validate_outbox_payload(payload: Any) -> dict[str, Any]:
    """Validate and copy a payload, rejecting unknown fields and tampering."""

    required = {
        "schema_version",
        "job_id",
        "target_path",
        "created_at",
        "input_kind",
        "sensitivity_choice",
        "content",
        "content_bytes",
        "content_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise MobileContractError("outbox payload fields do not match the strict contract")
    job_id = _validate_uuid(payload["job_id"])
    target_path = _validate_target_path(payload["target_path"], job_id)
    if payload["schema_version"] != OUTBOX_SCHEMA_VERSION:
        raise MobileContractError("unsupported outbox schema_version")
    _parse_datetime(payload["created_at"])
    if payload["input_kind"] not in INPUT_KINDS:
        raise MobileContractError("unsupported outbox input_kind")
    if payload["sensitivity_choice"] not in STORAGE_POLICIES:
        raise MobileContractError("unsupported outbox sensitivity_choice")
    text, content_bytes, content_sha256 = validate_text_input(payload["content"])
    storage_result = validate_storage_policy(
        payload["sensitivity_choice"],
        content=text,
    )
    if not storage_result.passed:
        raise MobileContractError(storage_result.message)
    if payload["content_bytes"] != content_bytes or payload["content_sha256"] != content_sha256:
        raise MobileContractError("outbox content byte count or hash does not match")
    return {
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "job_id": job_id,
        "target_path": target_path,
        "created_at": payload["created_at"],
        "input_kind": payload["input_kind"],
        "sensitivity_choice": payload["sensitivity_choice"],
        "content": text,
        "content_bytes": content_bytes,
        "content_sha256": content_sha256,
    }


def _payload_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def _event_filename(sequence: int, state: str) -> str:
    return f"{sequence:04d}-{state}.json"


def _safe_event_state(state: str) -> str:
    if state not in OUTBOX_STATES:
        raise MobileContractError(f"unsupported outbox state: {state}")
    return state


def _write_exclusive(path: Path, content: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if path.exists() or path.is_symlink():
            path.unlink()
        raise


class OutboxStore:
    """A filesystem-backed, create-only outbox rooted by the caller."""

    def __init__(self, root: str | Path):
        candidate = Path(root)
        if candidate.exists() and candidate.is_symlink():
            raise MobileContractError("outbox root must not be a symlink")
        candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not candidate.is_dir():
            raise MobileContractError("outbox root must be a directory")
        candidate.chmod(0o700)
        self.root = candidate.resolve()

    def _job_dir(self, job_id: str) -> Path:
        normalized = _validate_uuid(job_id)
        path = self.root / normalized
        if path.is_symlink():
            raise MobileContractError("outbox job directory must not be a symlink")
        try:
            path.resolve().relative_to(self.root)
        except ValueError as error:
            raise MobileContractError("outbox job path escaped its root") from error
        return path

    def _payload_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "input.json"

    def persist(self, payload: Mapping[str, Any], *, event_at: str | None = None) -> OutboxRecord:
        """Create one payload and its first event; duplicates fail closed."""

        verified = validate_outbox_payload(payload)
        job_dir = self._job_dir(verified["job_id"])
        try:
            job_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            raise OutboxConflictError("outbox job ID already exists; use recover for retry") from error
        payload_path = job_dir / "input.json"
        try:
            _write_exclusive(payload_path, canonical_json_bytes(verified), 0o600)
            self.append_event(verified["job_id"], "captured", event_at=event_at)
        except BaseException:
            if job_dir.exists() or job_dir.is_symlink():
                shutil.rmtree(job_dir)
            raise
        return self.read(verified["job_id"])

    def append_event(
        self,
        job_id: str,
        state: str,
        *,
        event_at: str | None = None,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        """Append an immutable state event without modifying the payload."""

        state = _safe_event_state(state)
        if reason is not None and reason not in OUTBOX_EVENT_REASONS:
            raise MobileContractError(f"unsupported outbox event reason: {reason}")
        record = self.read(job_id)
        previous_state = record.events[-1]["state"] if record.events else None
        if previous_state != state and state not in OUTBOX_ALLOWED_TRANSITIONS.get(previous_state, ()):
            raise MobileContractError(f"outbox state transition is not allowed: {previous_state} -> {state}")
        events_dir = self._job_dir(job_id) / "events"
        if events_dir.exists() and events_dir.is_symlink():
            raise MobileContractError("outbox events directory must not be a symlink")
        events_dir.mkdir(mode=0o700, exist_ok=True)
        sequence = len(record.events) + 1
        timestamp = event_at or datetime.now(UTC).isoformat(timespec="seconds")
        _parse_datetime(timestamp)
        event: dict[str, Any] = {
            "schema_version": OUTBOX_SCHEMA_VERSION,
            "job_id": record.job_id,
            "state": state,
            "event_at": timestamp,
            "payload_sha256": record.payload_sha256,
        }
        if reason is not None:
            event["reason"] = reason
        path = events_dir / _event_filename(sequence, state)
        _write_exclusive(path, canonical_json_bytes(event), 0o600)
        return event

    def read(self, job_id: str) -> OutboxRecord:
        """Read and verify a payload plus all events for one job."""

        payload_path = self._payload_path(job_id)
        if not payload_path.is_file() or payload_path.is_symlink():
            raise MobileContractError("outbox payload is missing or not a regular file")
        try:
            payload = validate_outbox_payload(json.loads(payload_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise MobileContractError("outbox payload is not valid JSON") from error
        if payload["job_id"] != job_id:
            raise MobileContractError("outbox payload job ID does not match its directory")
        digest = _payload_digest(payload)
        events_dir = self._job_dir(job_id) / "events"
        events: list[Mapping[str, Any]] = []
        if events_dir.exists():
            if events_dir.is_symlink() or not events_dir.is_dir():
                raise MobileContractError("outbox events path is not a regular directory")
            for path in sorted(events_dir.glob("[0-9][0-9][0-9][0-9]-*.json")):
                if path.is_symlink() or not path.is_file():
                    raise MobileContractError("outbox event must be a regular file")
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise MobileContractError("outbox event is not valid JSON") from error
                if not isinstance(event, Mapping) or event.get("job_id") != job_id or event.get("payload_sha256") != digest:
                    raise MobileContractError("outbox event is not bound to the verified payload")
                _safe_event_state(str(event.get("state")))
                _parse_datetime(event.get("event_at"))
                events.append(dict(event))
        return OutboxRecord(job_id, payload, digest, tuple(events))

    def recover(self, payload: Mapping[str, Any], *, event_at: str | None = None) -> tuple[str, OutboxRecord]:
        """Recover a retry: same ID+digest is a no-op; other bytes conflict."""

        verified = validate_outbox_payload(payload)
        payload_path = self._payload_path(verified["job_id"])
        if not payload_path.exists():
            return "CREATED", self.persist(verified, event_at=event_at)
        existing = self.read(verified["job_id"])
        expected_digest = _payload_digest(verified)
        if existing.payload_sha256 == expected_digest:
            return "NO_OP", existing
        raise OutboxConflictError("same outbox ID has a different payload digest")

    def pending(self) -> tuple[OutboxRecord, ...]:
        """Return verified records without a remote/local-vault acknowledgment."""

        records: list[OutboxRecord] = []
        for job_dir in sorted(self.root.iterdir()):
            if not job_dir.is_dir() or job_dir.is_symlink() or not _UUID_RE.fullmatch(job_dir.name):
                continue
            record = self.read(job_dir.name)
            if not record.acknowledged:
                records.append(record)
        return tuple(records)

    def cleanup(
        self,
        job_id: str,
        *,
        now: datetime,
        interactive_confirmation: bool,
    ) -> GateResult:
        """Delete only after acknowledgment, seven days, and confirmation."""

        record = self.read(job_id)
        age = now - record.created_at
        if not record.acknowledged:
            return GateResult(False, "OUTBOX_ACK_REQUIRED", "remote_observed or local_vault_transferred is required")
        if age < timedelta(days=CLEANUP_MINIMUM_AGE_DAYS):
            return GateResult(False, "OUTBOX_MINIMUM_AGE_REQUIRED", "outbox must be acknowledged for at least seven days")
        if not interactive_confirmation:
            return GateResult(False, "OUTBOX_INTERACTIVE_CONFIRMATION_REQUIRED", "cleanup requires interactive confirmation")
        job_dir = self._job_dir(job_id)
        if job_dir.is_symlink() or not job_dir.is_dir():
            return GateResult(False, "OUTBOX_PATH_INVALID", "outbox job path is not a regular directory")
        shutil.rmtree(job_dir)
        return GateResult(True, "OUTBOX_CLEANED", "outbox item was removed after all cleanup gates passed")


def evaluate_git_transaction(
    *,
    repository_ok: bool,
    remote_ok: bool,
    branch_ok: bool,
    root_sentinel_ok: bool,
    repository_state: str,
    preexisting_staged_paths: Sequence[str],
    staged_paths: Sequence[str] | None,
    expected_new_paths: Sequence[str],
    action_exposes_staged_set: bool,
) -> GateResult:
    """Decide whether an exact-file automatic commit is safe.

    ``repository_state`` is deliberately supplied by an adapter.  This
    function never runs Git and always denies Pull for capture.
    """

    reasons: list[str] = []
    if not repository_ok:
        reasons.append("repository")
    if not remote_ok:
        reasons.append("remote")
    if not branch_ok:
        reasons.append("branch")
    if not root_sentinel_ok:
        reasons.append("root_sentinel")
    if repository_state in {"dirty", "detached", "diverged", "conflict"}:
        reasons.append(repository_state)
    elif repository_state not in {"clean", "ahead_only", "behind_only"}:
        reasons.append("unknown_repository_state")
    if preexisting_staged_paths:
        reasons.append("preexisting_staged_paths")
    if staged_paths is None or not action_exposes_staged_set:
        reasons.append("exact_staged_set_unavailable")
    elif set(staged_paths) != set(expected_new_paths):
        reasons.append("unexpected_staged_paths")
    if reasons:
        return GateResult(
            False,
            "MOBILE_AUTO_COMMIT_DISABLED",
            "automatic Pull/Commit/Push is disabled; open status UI and keep the outbox",
            {"reasons": tuple(reasons), "pull_allowed": False, "commit_allowed": False},
        )
    return GateResult(
        True,
        "MOBILE_EXACT_FILE_COMMIT_ALLOWED",
        "exact-file commit may be offered interactively",
        {"reasons": (), "pull_allowed": False, "commit_allowed": True, "push_requires_confirmation": True},
    )


def validate_defer_source(
    *,
    tracked: bool,
    expected_head: bool,
    index_clean: bool,
    worktree_clean: bool,
    committed_blob: bytes | None,
    expected_blob_sha256: str | None,
) -> GateResult:
    """Allow Defer only when a committed, readable blob can be hash-bound."""

    if not tracked:
        return GateResult(False, "DEFER_SOURCE_UNTRACKED", "source is not tracked at the expected HEAD")
    if not expected_head or not index_clean or not worktree_clean:
        return GateResult(False, "DEFER_SOURCE_DIRTY", "source/index/worktree is not byte-identical to the expected HEAD")
    if committed_blob is None:
        return GateResult(False, "DEFER_COMMITTED_BLOB_UNREADABLE", "committed blob bytes are unavailable")
    digest = sha256_bytes(committed_blob)
    if expected_blob_sha256 is None or not _SHA256_RE.fullmatch(expected_blob_sha256) or digest != expected_blob_sha256:
        return GateResult(False, "DEFER_BLOB_HASH_MISMATCH", "committed blob hash does not match the expected digest")
    return GateResult(True, "DEFER_COMMITTED_BLOB_VERIFIED", "Defer may build a request from the committed blob digest", {"blob_sha256": digest})


def load_shortcut_contract(root: str | Path) -> Mapping[str, Any]:
    """Load the exportable S09 catalog and verify its IDs against the Blueprint."""

    workspace = Path(root).resolve()
    blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
    catalog = load_yaml_file(workspace / "ops/config/shortcuts.yaml")
    if not isinstance(blueprint, Mapping) or not isinstance(catalog, Mapping):
        raise MobileContractError("Blueprint and Shortcut catalog must be mappings")
    expected = blueprint.get("shortcut_contracts")
    observed = catalog.get("shortcuts")
    if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)) or not isinstance(observed, Sequence) or isinstance(observed, (str, bytes)):
        raise MobileContractError("Shortcut contract lists are required")
    expected_by_id = {item.get("id"): item for item in expected if isinstance(item, Mapping)}
    observed_by_id = {item.get("id"): item for item in observed if isinstance(item, Mapping)}
    if set(expected_by_id) != set(observed_by_id):
        raise MobileContractError("Shortcut catalog IDs drift from the Blueprint")
    for identifier, contract in expected_by_id.items():
        observed_contract = observed_by_id[identifier]
        for contract_field in ("display_name", "input", "output", "overwrites_existing"):
            if observed_contract.get(contract_field) != contract.get(contract_field):
                raise MobileContractError(f"Shortcut catalog drift at {identifier}.{contract_field}")
    return catalog


def stale_warning(record: OutboxRecord, *, now: datetime) -> GateResult:
    """Report a warning once a pending outbox item is older than 30 days."""

    if now - record.created_at >= timedelta(days=STALE_WARNING_DAYS):
        return GateResult(True, "OUTBOX_STALE_WARNING", "outbox item is older than 30 days", {"job_id": record.job_id})
    return GateResult(False, "OUTBOX_NOT_STALE", "outbox item is within the normal retention warning window")

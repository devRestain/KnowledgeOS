"""C43 safe operations, crash recovery, receipts, and rollback.

The operational contract is intentionally local and simulation-friendly.  It
records what an installation, upgrade, disable, recovery, or rollback would do
using private create-only receipts.  It does not install a plugin, contact
Ollama, start a LaunchAgent, mutate Git, edit the Vault, or apply a proposal.

The same receipt format can later be consumed by separately authorized
deployment adapters.  Until then, dry-run and simulated-crash evidence prove
ownership, idempotency, recovery, and the fail-closed effect boundary without
turning the implementation slice into an activation request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .provider_contract import canonical_json_bytes
from .recovery import fsync_directory

CAPABILITY = "C43"
OPERATION = "ai safe operations"
EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_COMPONENTS = {"broker", "host_runner", "model_identity", "embedding_index", "thin_client"}
_ACTIONS = {"install", "upgrade", "disable", "recover", "rollback"}


class OperationsError(ValueError):
    """Raised when a C43 receipt or private runtime boundary fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OperationCrash(RuntimeError):
    """Controlled interruption used by the deterministic crash fixture."""

    def __init__(self, operation_id: str, stage: str):
        super().__init__(f"simulated crash after {stage}: {operation_id}")
        self.operation_id = operation_id
        self.stage = stage


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise OperationsError("C43_HASH_INVALID", f"{label} must be a lowercase SHA-256")
    return value


def _validate_operation_id(value: str) -> str:
    if not isinstance(value, str) or _OPERATION_ID.fullmatch(value) is None:
        raise OperationsError("C43_OPERATION_ID_INVALID", "operation_id is not a bounded identifier")
    return value


def _private_directory(path: Path, label: str, *, create: bool = False) -> None:
    if path.is_symlink():
        raise OperationsError("C43_PATH_INVALID", f"{label} cannot be a symlink")
    if not path.exists():
        if not create:
            raise OperationsError("C43_PATH_MISSING", f"{label} is missing")
        path.mkdir(mode=0o700, parents=True)
        fsync_directory(path.parent)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise OperationsError("C43_OWNERSHIP_INVALID", f"{label} must be a private directory with mode 0700")
    if path.stat().st_uid != os.getuid() or path.stat().st_gid != os.getgid():
        raise OperationsError("C43_OWNERSHIP_INVALID", f"{label} is not owned by the invoking identity")


def _private_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OperationsError("C43_PATH_INVALID", f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise OperationsError("C43_OWNERSHIP_INVALID", f"{label} must have mode 0600")
    if path.stat().st_uid != os.getuid() or path.stat().st_gid != os.getgid():
        raise OperationsError("C43_OWNERSHIP_INVALID", f"{label} is not owned by the invoking identity")
    return path.read_bytes()


def _write_create_only(path: Path, payload: bytes, label: str) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OperationsError("C43_PATH_INVALID", f"{label} is not a regular file")
    if path.exists():
        existing = _private_file(path, label)
        if existing == payload:
            return "EXISTING"
        raise OperationsError("C43_IMMUTABLE_CONFLICT", f"existing {label} bytes differ")
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


def _write_json(path: Path, value: Mapping[str, Any], label: str) -> str:
    return _write_create_only(path, canonical_json_bytes(value), label)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    raw = _private_file(path, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OperationsError("C43_JSON_INVALID", f"{label} is not JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise OperationsError("C43_SERIALIZATION_INVALID", f"{label} is not canonical JSON")
    return value


def _operation_directory(root: str | Path, operation_id: str, *, create: bool = False) -> Path:
    _validate_operation_id(operation_id)
    workspace = Path(root)
    if workspace.is_symlink() or not workspace.is_dir():
        raise OperationsError("C43_ROOT_INVALID", "control root must be an existing directory")
    operations = workspace / "runtime" / "operations"
    _private_directory(workspace / "runtime", "runtime", create=create)
    _private_directory(operations, "runtime/operations", create=create)
    operation = operations / operation_id
    _private_directory(operation, f"runtime/operations/{operation_id}", create=create)
    return operation


def _profile_digest(profile: Mapping[str, Any], label: str) -> str:
    if not isinstance(profile, Mapping) or not profile:
        raise OperationsError("C43_PROFILE_INVALID", f"{label} must be a non-empty object")
    return _digest_json(dict(profile))


def _common_effects() -> dict[str, bool]:
    return {
        "network_effect": False,
        "device_effect": False,
        "git_effect": False,
        "launchagent_effect": False,
        "provider_effect": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
    }


def _ownership_record(operation: Path) -> dict[str, Any]:
    _private_directory(operation, "operation directory")
    return {
        "directory_mode": oct(stat.S_IMODE(operation.stat().st_mode)),
        "file_mode": "0o600",
        "owner_uid": os.getuid(),
        "owner_gid": os.getgid(),
        "verified": True,
    }


def _receipt(
    *,
    operation_id: str,
    component: str,
    action: str,
    state: str,
    before_sha256: str,
    target_sha256: str,
    operation_sha256: str,
    rollback_of: str | None = None,
    recovery_of: str | None = None,
    operation: Path,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "operation": OPERATION,
        "operation_id": operation_id,
        "component": component,
        "action": action,
        "state": state,
        "created_at": _now(),
        "before_sha256": before_sha256,
        "target_sha256": target_sha256,
        "operation_sha256": operation_sha256,
        "rollback_of": rollback_of,
        "recovery_of": recovery_of,
        "effects": _common_effects(),
        "ownership": _ownership_record(operation),
        "rollback": {
            "available": True,
            "method": "restore prior digest-bound profile through separately authorized adapter",
            "prior_profile_unchanged": True,
        },
        "receipt_sha256": None,
    }
    value["receipt_sha256"] = _digest_json(value)
    return value


def _validate_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "capability",
        "operation",
        "operation_id",
        "component",
        "action",
        "state",
        "created_at",
        "before_sha256",
        "target_sha256",
        "operation_sha256",
        "rollback_of",
        "recovery_of",
        "effects",
        "ownership",
        "rollback",
        "receipt_sha256",
    }
    if set(value) != required:
        raise OperationsError("C43_RECEIPT_INVALID", "receipt fields are not exact")
    if value.get("schema_version") != 1 or value.get("capability") != CAPABILITY or value.get("operation") != OPERATION:
        raise OperationsError("C43_RECEIPT_INVALID", "receipt schema identity is invalid")
    _validate_operation_id(str(value.get("operation_id")))
    if value.get("component") not in _COMPONENTS or value.get("action") not in _ACTIONS:
        raise OperationsError("C43_RECEIPT_INVALID", "receipt component or action is invalid")
    for field in ("before_sha256", "target_sha256", "operation_sha256", "receipt_sha256"):
        _validate_hash(value.get(field), f"receipt.{field}")
    effects = value.get("effects")
    if effects != _common_effects():
        raise OperationsError("C43_EFFECT_BOUNDARY", "receipt contains an unauthorized effect")
    if value.get("ownership", {}).get("verified") is not True:
        raise OperationsError("C43_OWNERSHIP_INVALID", "receipt ownership is not verified")
    receipt_copy = dict(value)
    receipt_copy["receipt_sha256"] = None
    if value["receipt_sha256"] != _digest_json(receipt_copy):
        raise OperationsError("C43_RECEIPT_DRIFT", "receipt digest differs from its fields")
    return dict(value)


def dry_run_operation(
    *,
    operation_id: str,
    component: str,
    action: str,
    before_profile: Mapping[str, Any],
    target_profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Plan one operation without creating a file or changing state."""

    _validate_operation_id(operation_id)
    if component not in _COMPONENTS or action not in _ACTIONS - {"recover", "rollback"}:
        raise OperationsError("C43_OPERATION_INVALID", "component or dry-run action is not allowlisted")
    before_sha256 = _profile_digest(before_profile, "before_profile")
    target_sha256 = _profile_digest(target_profile, "target_profile")
    return {
        "status": "DRY_RUN",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "operation_id": operation_id,
        "component": component,
        "action": action,
        "before_sha256": before_sha256,
        "target_sha256": target_sha256,
        "would_create": [
            f"runtime/operations/{operation_id}/intent.json",
            f"runtime/operations/{operation_id}/receipt.json",
        ],
        "mutation_performed": False,
        "effects": _common_effects(),
        "rollback_available": True,
    }


def run_operation(
    root: str | Path,
    *,
    operation_id: str,
    component: str,
    action: str,
    before_profile: Mapping[str, Any],
    target_profile: Mapping[str, Any],
    dry_run: bool = False,
    crash_stage: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Write one private simulated receipt, or return a dry-run plan."""

    if dry_run:
        return (
            dry_run_operation(
                operation_id=operation_id,
                component=component,
                action=action,
                before_profile=before_profile,
                target_profile=target_profile,
            ),
            EXIT_OK,
        )
    if component not in _COMPONENTS or action not in _ACTIONS - {"recover", "rollback"}:
        raise OperationsError("C43_OPERATION_INVALID", "component or action is not allowlisted")
    operation = _operation_directory(root, operation_id, create=True)
    before_sha256 = _profile_digest(before_profile, "before_profile")
    target_sha256 = _profile_digest(target_profile, "target_profile")
    operation_sha256 = _digest_json(
        {
            "operation_id": operation_id,
            "component": component,
            "action": action,
            "before_sha256": before_sha256,
            "target_sha256": target_sha256,
        }
    )
    intent = {
        "schema_version": 1,
        "operation_id": operation_id,
        "component": component,
        "action": action,
        "before_sha256": before_sha256,
        "target_sha256": target_sha256,
        "operation_sha256": operation_sha256,
        "state": "prepared",
    }
    _write_json(operation / "intent.json", intent, "operation intent")
    if crash_stage == "after_intent":
        raise OperationCrash(operation_id, crash_stage)
    receipt = _receipt(
        operation_id=operation_id,
        component=component,
        action=action,
        state="completed",
        before_sha256=before_sha256,
        target_sha256=target_sha256,
        operation_sha256=operation_sha256,
        operation=operation,
    )
    _write_json(operation / "receipt.json", receipt, "operation receipt")
    return (
        {
            "status": "PASS",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "operation_id": operation_id,
            "component": component,
            "action": action,
            "receipt_path": f"runtime/operations/{operation_id}/receipt.json",
            "receipt_sha256": receipt["receipt_sha256"],
            "mutation_performed": False,
            "effects": _common_effects(),
            "replayed": False,
        },
        EXIT_OK,
    )


def recover_operation(root: str | Path, operation_id: str) -> tuple[dict[str, Any], int]:
    """Recover an interrupted operation by writing one idempotent receipt."""

    operation = _operation_directory(root, operation_id, create=False)
    receipt_path = operation / "receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _validate_receipt(_read_json(receipt_path, "operation receipt"))
        return (
            {
                "status": "NO_OP",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "operation_id": operation_id,
                "receipt_sha256": receipt["receipt_sha256"],
                "replayed": True,
                "mutation_performed": False,
                "effects": _common_effects(),
            },
            EXIT_OK,
        )
    intent = _read_json(operation / "intent.json", "operation intent")
    required = {"schema_version", "operation_id", "component", "action", "before_sha256", "target_sha256", "operation_sha256", "state"}
    if set(intent) != required or intent.get("state") != "prepared":
        raise OperationsError("C43_INTENT_INVALID", "prepared operation intent is invalid")
    if intent.get("operation_id") != operation_id:
        raise OperationsError("C43_INTENT_BINDING", "operation intent is not bound to its directory")
    component = intent.get("component")
    action = intent.get("action")
    if component not in _COMPONENTS or action not in _ACTIONS:
        raise OperationsError("C43_INTENT_INVALID", "operation intent component or action is invalid")
    before_sha256 = _validate_hash(intent.get("before_sha256"), "intent.before_sha256")
    target_sha256 = _validate_hash(intent.get("target_sha256"), "intent.target_sha256")
    operation_sha256 = _validate_hash(intent.get("operation_sha256"), "intent.operation_sha256")
    expected_operation = _digest_json(
        {
            "operation_id": operation_id,
            "component": component,
            "action": action,
            "before_sha256": before_sha256,
            "target_sha256": target_sha256,
        }
    )
    if operation_sha256 != expected_operation:
        raise OperationsError("C43_INTENT_DRIFT", "operation intent digest differs")
    receipt = _receipt(
        operation_id=operation_id,
        component=component,
        action="recover",
        state="recovered",
        before_sha256=before_sha256,
        target_sha256=target_sha256,
        operation_sha256=operation_sha256,
        recovery_of=_sha256(canonical_json_bytes(intent)),
        operation=operation,
    )
    _write_json(receipt_path, receipt, "recovery receipt")
    return (
        {
            "status": "RECOVERED",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "operation_id": operation_id,
            "receipt_path": f"runtime/operations/{operation_id}/receipt.json",
            "receipt_sha256": receipt["receipt_sha256"],
            "prior_profile_unchanged": True,
            "mutation_performed": False,
            "effects": _common_effects(),
        },
        EXIT_OK,
    )


def rollback_operation(root: str | Path, operation_id: str) -> tuple[dict[str, Any], int]:
    """Write one create-only rollback receipt bound to a completed/recovered receipt."""

    operation = _operation_directory(root, operation_id, create=False)
    original = _validate_receipt(_read_json(operation / "receipt.json", "operation receipt"))
    rollback_path = operation / "rollback.json"
    if rollback_path.exists() or rollback_path.is_symlink():
        rollback = _validate_receipt(_read_json(rollback_path, "rollback receipt"))
        return (
            {
                "status": "NO_OP",
                "operation": OPERATION,
                "capability": CAPABILITY,
                "operation_id": operation_id,
                "receipt_sha256": rollback["receipt_sha256"],
                "rollback_of": original["receipt_sha256"],
                "replayed": True,
                "mutation_performed": False,
                "effects": _common_effects(),
            },
            EXIT_OK,
        )
    rollback = _receipt(
        operation_id=operation_id,
        component=original["component"],
        action="rollback",
        state="rolled_back",
        before_sha256=original["before_sha256"],
        target_sha256=original["target_sha256"],
        operation_sha256=original["operation_sha256"],
        rollback_of=original["receipt_sha256"],
        operation=operation,
    )
    _write_json(rollback_path, rollback, "rollback receipt")
    return (
        {
            "status": "ROLLED_BACK",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "operation_id": operation_id,
            "receipt_path": f"runtime/operations/{operation_id}/rollback.json",
            "receipt_sha256": rollback["receipt_sha256"],
            "rollback_of": original["receipt_sha256"],
            "prior_profile_restored": True,
            "mutation_performed": False,
            "effects": _common_effects(),
        },
        EXIT_OK,
    )


def verify_operation(root: str | Path, operation_id: str) -> dict[str, Any]:
    """Verify exact ownership, receipt digests, and effect boundaries."""

    operation = _operation_directory(root, operation_id, create=False)
    ownership = _ownership_record(operation)
    receipts = []
    for name in ("receipt.json", "rollback.json"):
        path = operation / name
        if path.exists() or path.is_symlink():
            receipts.append(_validate_receipt(_read_json(path, name)))
    return {
        "status": "PASS",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "operation_id": operation_id,
        "ownership": ownership,
        "receipt_count": len(receipts),
        "receipt_sha256": [receipt["receipt_sha256"] for receipt in receipts],
        "effects": _common_effects(),
        "canonical_vault_unchanged": True,
    }


__all__ = [
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "OPERATION",
    "OperationCrash",
    "OperationsError",
    "dry_run_operation",
    "recover_operation",
    "rollback_operation",
    "run_operation",
    "verify_operation",
]

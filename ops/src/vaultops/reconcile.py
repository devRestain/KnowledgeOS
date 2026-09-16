"""Provider-free reconciliation and repair for local transaction journals.

Reconciliation is intentionally read-only.  It turns the durable C15 journal
and the observed Vault paths into a deterministic plan.  Applying that plan is
a separate, explicitly requested operation and is permitted only while the
same journal and hash observations still hold.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .bridge_publish import apply_bridge_recovery, reconcile_bridge_publish
from .recovery import (
    RecoveryCorruption,
    RecoveryError,
    RecoveryJournal,
    canonical_json_bytes,
    fsync_directory,
    sha256_bytes,
    validate_job_id,
)
from .transactions import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    _apply_archive_recovery,
    _apply_capture_recovery,
    _archive_directory_hashes,
    _hash_regular_file,
    _render_capture_recovery_bytes,
    _safe_vault_path,
)

RECONCILE_SCHEMA_VERSION = 1
PLAN_KIND = "knowledgeos.transaction-repair-plan"
LOCAL_OPERATIONS = frozenset({"bridge_publish", "capture_finalize", "project_archive"})
_SHA256_LENGTH = 64


class ReconcileInputError(ValueError):
    """Raised when the reconciliation root or plan input is unsafe."""


def _issue(code: str, locator: str, message: str, **details: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "locator": locator, "message": message}
    if details:
        issue["details"] = details
    return issue


def _workspace_and_vault(root: str | Path) -> tuple[Path, Path]:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ReconcileInputError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    runtime = workspace / "runtime"
    vault = workspace / "KnowledgeHub"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ReconcileInputError("runtime root must be an existing non-symlink directory")
    if stat.S_IMODE(runtime.stat().st_mode) != 0o700:
        raise ReconcileInputError("runtime root mode must be 0700")
    if vault.is_symlink() or not vault.is_dir():
        raise ReconcileInputError("Vault root must be an existing non-symlink directory")
    return workspace, vault.resolve()


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        raise ReconcileInputError(f"{label} must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ReconcileInputError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReconcileInputError(f"{label} must be a non-empty Vault-relative POSIX path")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ReconcileInputError(f"{label} must be a normalized Vault-relative path")
    return value


def _runtime_file(runtime: Path, relative: str) -> Path:
    candidate = runtime / relative
    current = runtime
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise ReconcileInputError("runtime path crosses a symlink")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(runtime.resolve())
    except ValueError as error:
        raise ReconcileInputError("runtime path escapes the runtime root") from error
    return candidate


def _read_canonical_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecoveryCorruption(f"{label} must be a regular non-symlink file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryCorruption(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, Mapping) or raw != canonical_json_bytes(value) + b"\n":
        raise RecoveryCorruption(f"{label} is not canonical JSON")
    return dict(value)


def _existing_path(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _capture_observation(
    intent: Mapping[str, Any], workspace: Path, vault: Path
) -> tuple[str, dict[str, Any]]:
    source_relative = _safe_relative(intent.get("source"), label="capture source")
    destination_relative = _safe_relative(intent.get("destination"), label="capture destination")
    expected_source = _sha256(intent.get("source_sha256"), label="capture source_sha256")
    expected_destination = _sha256(
        intent.get("destination_sha256"), label="capture destination_sha256"
    )
    source = _safe_vault_path(vault, source_relative)
    destination = _safe_vault_path(vault, destination_relative)
    source_exists = _existing_path(source)
    destination_exists = _existing_path(destination)
    observation: dict[str, Any] = {
        "source": source_relative,
        "destination": destination_relative,
        "expected_source_sha256": expected_source,
        "expected_destination_sha256": expected_destination,
        "source_exists": source_exists,
        "destination_exists": destination_exists,
    }
    if source_exists:
        if source.is_symlink() or not source.is_file():
            return "conflict", {
                **observation,
                "reason": "source path is not a regular file",
                "issues": [_issue("RECOVERY_PATH_MISMATCH", "/source", "source path is unsafe")],
            }
        observed, _ = _hash_regular_file(source, label="capture source")
        observation["observed_source_sha256"] = observed
        if observed != expected_source:
            return "conflict", {
                **observation,
                "reason": "source digest differs from the journal",
                "issues": [
                    _issue(
                        "RECOVERY_SOURCE_HASH_MISMATCH",
                        "/source_sha256",
                        "source digest differs from the journal intent",
                    )
                ],
            }
    if destination_exists:
        if destination.is_symlink() or not destination.is_file():
            return "conflict", {
                **observation,
                "reason": "destination path is not a regular file",
                "issues": [_issue("RECOVERY_PATH_MISMATCH", "/destination", "destination path is unsafe")],
            }
        observed, _ = _hash_regular_file(destination, label="capture destination")
        observation["observed_destination_sha256"] = observed
        if observed != expected_destination:
            return "conflict", {
                **observation,
                "reason": "destination digest differs from the journal",
                "issues": [
                    _issue(
                        "RECOVERY_DESTINATION_HASH_MISMATCH",
                        "/destination_sha256",
                        "destination digest differs from the journal intent",
                    )
                ],
            }
    if not source_exists and not destination_exists:
        return "conflict", {
            **observation,
            "reason": "neither guarded path exists",
            "issues": [_issue("RECOVERY_AMBIGUOUS_STATE", "/", "neither guarded path exists")],
        }
    if source_exists and destination_exists:
        return "repairable", {
            **observation,
            "reason": "destination is published and the guarded source remains",
            "actions": ["remove_source_and_complete"],
        }
    if source_exists:
        try:
            rendered = _render_capture_recovery_bytes(workspace, vault, intent)
        except (OSError, TypeError, ValueError, RecoveryError) as error:
            return "conflict", {
                **observation,
                "reason": str(error),
                "issues": [_issue("RECOVERY_DESTINATION_INVALID", "/destination_sha256", str(error))],
            }
        rendered_digest = sha256_bytes(rendered.encode("utf-8"))
        observation["recomputed_destination_sha256"] = rendered_digest
        if rendered_digest != expected_destination:
            return "conflict", {
                **observation,
                "reason": "recomputed destination digest differs from the journal",
                "issues": [
                    _issue(
                        "RECOVERY_DESTINATION_HASH_MISMATCH",
                        "/destination_sha256",
                        "recomputed destination digest differs from the journal intent",
                    )
                ],
            }
        return "repairable", {
            **observation,
            "reason": "source remains and destination has not been published",
            "actions": ["publish_destination_remove_source_and_complete"],
        }
    return "repairable", {
        **observation,
        "reason": "destination is present and source is already absent",
        "actions": ["complete_journal_and_receipt"],
    }


def _archive_observation(intent: Mapping[str, Any], vault: Path) -> tuple[str, dict[str, Any]]:
    source_relative = _safe_relative(intent.get("source"), label="archive source")
    destination_relative = _safe_relative(intent.get("destination"), label="archive destination")
    raw_hashes = intent.get("source_hashes")
    if not isinstance(raw_hashes, Mapping) or not raw_hashes:
        raise ReconcileInputError("archive source_hashes must be a non-empty object")
    expected_hashes = {
        _safe_relative(path, label="archive member"): _sha256(digest, label="archive member hash")
        for path, digest in raw_hashes.items()
    }
    source_dir = _safe_vault_path(vault, source_relative)
    destination_dir = _safe_vault_path(vault, destination_relative)
    source_exists = _existing_path(source_dir)
    destination_exists = _existing_path(destination_dir)
    observation: dict[str, Any] = {
        "source": source_relative,
        "destination": destination_relative,
        "expected_source_hashes": expected_hashes,
        "source_exists": source_exists,
        "destination_exists": destination_exists,
    }
    if source_exists:
        if source_dir.is_symlink() or not source_dir.is_dir():
            return "conflict", {
                **observation,
                "reason": "source path is not a real directory",
                "issues": [_issue("RECOVERY_PATH_MISMATCH", "/source", "source directory is unsafe")],
            }
        observed = _archive_directory_hashes(source_dir, vault)
        observation["observed_source_hashes"] = observed
        if observed != expected_hashes:
            return "conflict", {
                **observation,
                "reason": "source member digests differ from the journal",
                "issues": [
                    _issue(
                        "RECOVERY_SOURCE_HASH_MISMATCH",
                        "/source_hashes",
                        "source member digests differ from the journal intent",
                    )
                ],
            }
    expected_destination = {
        f"{destination_relative}/{Path(relative).relative_to(source_relative).as_posix()}": digest
        for relative, digest in expected_hashes.items()
    }
    if destination_exists:
        if destination_dir.is_symlink() or not destination_dir.is_dir():
            return "conflict", {
                **observation,
                "reason": "destination path is not a real directory",
                "issues": [_issue("RECOVERY_PATH_MISMATCH", "/destination", "destination directory is unsafe")],
            }
        observed = _archive_directory_hashes(destination_dir, vault)
        observation["observed_destination_hashes"] = observed
        if observed != expected_destination:
            return "conflict", {
                **observation,
                "reason": "destination member digests differ from the journal",
                "issues": [
                    _issue(
                        "RECOVERY_DESTINATION_HASH_MISMATCH",
                        "/destination",
                        "destination member digests differ from the journal intent",
                    )
                ],
            }
    if source_exists and destination_exists:
        return "conflict", {
            **observation,
            "reason": "source and destination both exist",
            "issues": [_issue("RECOVERY_AMBIGUOUS_STATE", "/", "source and destination both exist")],
        }
    if not source_exists and not destination_exists:
        return "conflict", {
            **observation,
            "reason": "neither source nor destination exists",
            "issues": [_issue("RECOVERY_AMBIGUOUS_STATE", "/", "neither source nor destination exists")],
        }
    if source_exists:
        return "repairable", {
            **observation,
            "reason": "project source remains and archive destination is absent",
            "actions": ["move_project_and_complete"],
        }
    return "repairable", {
        **observation,
        "reason": "archive destination is present and source is already absent",
        "actions": ["complete_journal_and_receipt"],
    }


def _receipt_matches_journal(journal: RecoveryJournal) -> tuple[bool, str | None]:
    latest = journal.latest()
    if latest["state"] != "completed":
        return False, "journal is not completed"
    payload = latest.get("payload")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("receipt"), Mapping):
        return False, "completed journal has no receipt payload"
    expected = dict(payload["receipt"])
    expected["journal_sha256"] = journal.journal_sha256()
    path = journal.receipt_path
    if not _existing_path(path):
        return False, "completion receipt is missing"
    try:
        observed = _read_canonical_json(path, label="completion receipt")
    except RecoveryCorruption as error:
        return False, str(error)
    if dict(observed) != expected:
        return False, "completion receipt does not match the completed journal"
    return True, None


def _extract_journal_identity(path: Path) -> tuple[str, str] | None:
    try:
        first_line = path.read_bytes().splitlines()[0]
        value = json.loads(first_line.decode("utf-8"))
    except (IndexError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    job_id = value.get("job_id")
    operation = value.get("operation")
    if not isinstance(job_id, str) or not isinstance(operation, str):
        return None
    return job_id, operation


def _job_directories(runtime: Path, requested_job_id: str | None) -> list[Path]:
    runs = runtime / "runs"
    if runs.is_symlink() or not runs.is_dir():
        raise ReconcileInputError("runtime/runs must be an existing non-symlink directory")
    if stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ReconcileInputError("runtime/runs mode must be 0700")
    if requested_job_id is not None:
        validate_job_id(requested_job_id)
        return [runs / requested_job_id]
    return sorted(runs.iterdir(), key=lambda path: path.name)


def _inspect_job(workspace: Path, vault: Path, job_dir: Path) -> dict[str, Any]:
    journal_path = job_dir / "journal.jsonl"
    if job_dir.is_symlink() or not job_dir.is_dir():
        return {
            "job_id": job_dir.name,
            "operation": None,
            "assessment": "conflict",
            "journal_path": journal_path.relative_to(workspace).as_posix(),
            "journal_sha256": None,
            "reason": "recovery job path is not a real directory",
            "issues": [_issue("RECOVERY_PATH_MISMATCH", "/runs", "recovery job path is unsafe")],
            "actions": ["quarantine_journal"],
        }
    identity = _extract_journal_identity(journal_path) if journal_path.is_file() else None
    if identity is None:
        return {
            "job_id": job_dir.name,
            "operation": None,
            "assessment": "conflict",
            "journal_path": journal_path.relative_to(workspace).as_posix(),
            "journal_sha256": None,
            "reason": "journal identity cannot be read safely",
            "issues": [_issue("RECOVERY_JOURNAL_INVALID", "/journal", "journal identity is invalid")],
            "actions": ["quarantine_journal"],
        }
    job_id, operation = identity
    if operation not in LOCAL_OPERATIONS:
        return {
            "job_id": job_id,
            "operation": operation,
            "assessment": "conflict",
            "journal_path": journal_path.relative_to(workspace).as_posix(),
            "journal_sha256": None,
            "reason": "journal operation is outside the local transaction slice",
            "issues": [_issue("RECOVERY_OPERATION_UNSUPPORTED", "/operation", "operation is not supported")],
            "actions": [],
        }
    try:
        journal = RecoveryJournal(workspace, job_id=job_id, operation=operation)
        records = journal.records()
        latest_state = str(records[-1]["state"])
        result: dict[str, Any] = {
            "job_id": job_id,
            "operation": operation,
            "assessment": "complete" if latest_state == "completed" else "repairable",
            "latest_state": latest_state,
            "journal_path": journal.path.relative_to(workspace).as_posix(),
            "journal_sha256": journal.journal_sha256(),
            "actions": [],
        }
        if latest_state == "completed":
            valid, reason = _receipt_matches_journal(journal)
            result["receipt_valid"] = valid
            if not valid:
                result["assessment"] = "repairable"
                result["reason"] = reason
                result["actions"] = ["write_completion_receipt"] if reason == "completion receipt is missing" else []
                if not result["actions"]:
                    result["assessment"] = "conflict"
                    result["issues"] = [
                        _issue("RECEIPT_INVALID", "/receipt", reason or "receipt is invalid")
                    ]
            return result
        if latest_state == "conflict":
            result["assessment"] = "conflict"
            result["reason"] = "journal is already marked conflict"
            result["issues"] = [_issue("RECOVERY_TRANSACTION_CONFLICT", "/journal", result["reason"])]
            return result
        intent = journal.intent()
        if operation == "bridge_publish":
            assessment, observation = reconcile_bridge_publish(workspace, vault, journal)
        elif operation == "capture_finalize":
            assessment, observation = _capture_observation(intent, workspace, vault)
        else:
            assessment, observation = _archive_observation(intent, vault)
        result.update(observation)
        result["assessment"] = assessment
        return result
    except (RecoveryCorruption, RecoveryError, ReconcileInputError, OSError, TypeError, ValueError) as error:
        return {
            "job_id": job_id,
            "operation": operation,
            "assessment": "conflict",
            "journal_path": journal_path.relative_to(workspace).as_posix(),
            "journal_sha256": None,
            "reason": str(error),
            "issues": [_issue("RECOVERY_JOURNAL_INVALID", "/journal", str(error))],
            "actions": ["quarantine_journal"],
        }


def _plan_payload(root: str | Path, requested_job_id: str | None = None) -> dict[str, Any]:
    workspace, vault = _workspace_and_vault(root)
    jobs = [_inspect_job(workspace, vault, path) for path in _job_directories(workspace / "runtime", requested_job_id)]
    jobs.sort(key=lambda job: (str(job.get("job_id")), str(job.get("operation"))))
    summary = {
        "jobs": len(jobs),
        "complete": sum(job["assessment"] == "complete" for job in jobs),
        "repairable": sum(job["assessment"] == "repairable" for job in jobs),
        "conflict": sum(job["assessment"] == "conflict" for job in jobs),
    }
    payload = {
        "schema_version": RECONCILE_SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "scope_job_id": requested_job_id,
        "jobs": jobs,
        "summary": summary,
    }
    digest = sha256_bytes(canonical_json_bytes(payload))
    return {**payload, "plan_sha256": digest}


def _plan_without_digest(plan: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    payload.pop("plan_sha256", None)
    return payload


def _validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping) or plan.get("schema_version") != RECONCILE_SCHEMA_VERSION:
        raise ReconcileInputError("repair plan schema version is unsupported")
    if plan.get("kind") != PLAN_KIND or not isinstance(plan.get("jobs"), list):
        raise ReconcileInputError("repair plan shape is invalid")
    observed_digest = plan.get("plan_sha256")
    if _sha256(observed_digest, label="plan_sha256") != sha256_bytes(
        canonical_json_bytes(_plan_without_digest(plan))
    ):
        raise ReconcileInputError("repair plan digest is invalid")
    return dict(plan)


def _write_create_only(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise ReconcileInputError(f"repair plan output already exists: {path}")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ReconcileInputError("repair plan output directory must be an existing real directory")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)
    fsync_directory(path.parent)


def _write_plan(workspace: Path, plan: Mapping[str, Any], output: str | Path) -> str:
    raw_output = Path(output)
    runtime = workspace / "runtime"
    if raw_output.is_absolute():
        relative = raw_output.relative_to(runtime).as_posix()
    else:
        relative = raw_output.as_posix()
        if relative == "runtime":
            raise ReconcileInputError("repair plan output must name a file")
        if relative.startswith("runtime/"):
            relative = relative.removeprefix("runtime/")
    path = _runtime_file(runtime, relative)
    relative_path = path.relative_to(runtime)
    if len(relative_path.parts) != 1 or path.suffix != ".json":
        raise ReconcileInputError("repair plan output must be directly under runtime")
    _write_create_only(path, canonical_json_bytes(plan) + b"\n")
    return path.relative_to(workspace).as_posix()


def reconcile_transactions(
    root: str | Path, *, job_id: str | None = None
) -> tuple[dict[str, Any], int]:
    """Inspect transaction journals and return a deterministic read-only report."""

    try:
        plan = _plan_payload(root, job_id)
    except (OSError, TypeError, ValueError, ReconcileInputError, RecoveryError) as error:
        return {
            "status": "FAIL",
            "operation": "reconcile",
            "errors": [_issue("RECONCILE_INPUT_INVALID", "/", str(error))],
            "jobs": [],
        }, EXIT_INPUT_INVALID
    status = "CONFLICT" if plan["summary"]["conflict"] else (
        "REPAIR_REQUIRED" if plan["summary"]["repairable"] else "PASS"
    )
    return {
        "status": status,
        "operation": "reconcile",
        "plan_sha256": plan["plan_sha256"],
        "summary": plan["summary"],
        "jobs": plan["jobs"],
    }, EXIT_CONFLICT if status == "CONFLICT" else EXIT_OK


def repair_plan(
    root: str | Path, *, job_id: str | None = None, output: str | Path | None = None
) -> tuple[dict[str, Any], int]:
    """Build a deterministic repair plan, optionally persisting it create-only."""

    try:
        workspace, _ = _workspace_and_vault(root)
        plan = _plan_payload(root, job_id)
        if output is not None:
            plan = {**plan, "plan_path": _write_plan(workspace, plan, output)}
    except (OSError, TypeError, ValueError, ReconcileInputError, RecoveryError) as error:
        return {
            "status": "FAIL",
            "operation": "repair plan",
            "errors": [_issue("REPAIR_PLAN_INPUT_INVALID", "/", str(error))],
        }, EXIT_INPUT_INVALID
    return {"status": "PASS", "operation": "repair plan", **plan}, EXIT_OK


def _load_plan(path: str | Path) -> dict[str, Any]:
    return _validate_plan(_read_canonical_json(Path(path), label="repair plan"))


def apply_repair_plan(
    root: str | Path, *, plan_path: str | Path
) -> tuple[dict[str, Any], int]:
    """Apply a still-current plan after a complete digest-bound preflight."""

    try:
        workspace, vault = _workspace_and_vault(root)
        candidate_plan_path = Path(plan_path)
        if not candidate_plan_path.is_absolute():
            candidate_plan_path = workspace / candidate_plan_path
        plan = _load_plan(candidate_plan_path)
        requested_job_id = plan.get("scope_job_id")
        if requested_job_id is not None:
            validate_job_id(requested_job_id)
        current = _plan_payload(root, requested_job_id)
        if canonical_json_bytes(_plan_without_digest(plan)) != canonical_json_bytes(
            _plan_without_digest(current)
        ):
            return {
                "status": "CONFLICT",
                "operation": "repair apply",
                "plan_sha256": plan["plan_sha256"],
                "errors": [
                    _issue(
                        "REPAIR_PLAN_STALE",
                        "/plan_sha256",
                        "repair plan no longer matches the current journal or Vault observations",
                    )
                ],
                "created": [],
            }, EXIT_CONFLICT
        if current["summary"]["conflict"]:
            return {
                "status": "CONFLICT",
                "operation": "repair apply",
                "plan_sha256": plan["plan_sha256"],
                "summary": current["summary"],
                "errors": [_issue("REPAIR_PLAN_CONFLICT", "/jobs", "plan contains an unsafe or ambiguous job")],
                "created": [],
            }, EXIT_CONFLICT
        results: list[dict[str, Any]] = []
        for job in current["jobs"]:
            if job["assessment"] != "repairable":
                continue
            journal = RecoveryJournal(workspace, job_id=job["job_id"], operation=job["operation"])
            action = job.get("actions", [None])[0]
            if action == "write_completion_receipt":
                receipt = journal.completion_receipt()
                results.append({"job_id": job["job_id"], "operation": job["operation"], "status": "PASS", "action": action, "completion_receipt": receipt})
            elif job["operation"] == "bridge_publish":
                result, code = apply_bridge_recovery(workspace, vault, journal)
                if code != EXIT_OK:
                    return {"status": "CONFLICT", "operation": "repair apply", "results": [*results, result], "created": []}, code
                results.append({**result, "action": action})
            elif job["operation"] == "capture_finalize":
                result, code = _apply_capture_recovery(journal, workspace, vault)
                if code != EXIT_OK:
                    return {"status": "CONFLICT", "operation": "repair apply", "results": [*results, result], "created": []}, code
                results.append({**result, "action": action})
            elif job["operation"] == "project_archive":
                result, code = _apply_archive_recovery(journal, vault)
                if code != EXIT_OK:
                    return {"status": "CONFLICT", "operation": "repair apply", "results": [*results, result], "created": []}, code
                results.append({**result, "action": action})
        return {
            "status": "PASS",
            "operation": "repair apply",
            "plan_sha256": plan["plan_sha256"],
            "applied": len(results),
            "results": results,
            "created": [result["destination"] for result in results if result.get("destination")],
        }, EXIT_OK
    except (OSError, TypeError, ValueError, ReconcileInputError, RecoveryError) as error:
        return {
            "status": "FAIL",
            "operation": "repair apply",
            "errors": [_issue("REPAIR_APPLY_INPUT_INVALID", "/", str(error))],
            "created": [],
        }, EXIT_INPUT_INVALID


def verify_receipts(
    root: str | Path, *, job_id: str | None = None
) -> tuple[dict[str, Any], int]:
    """Verify local transaction receipts without requiring current Vault bytes."""

    try:
        workspace, _ = _workspace_and_vault(root)
        runtime = workspace / "runtime"
        receipts = runtime / "receipts"
        if receipts.is_symlink() or not receipts.is_dir():
            raise ReconcileInputError("runtime/receipts must be an existing non-symlink directory")
        if stat.S_IMODE(receipts.stat().st_mode) != 0o700:
            raise ReconcileInputError("runtime/receipts mode must be 0700")
        if job_id is not None:
            validate_job_id(job_id)
            candidates = sorted(receipts.glob(f"{job_id}-*.json"), key=lambda path: path.name)
        else:
            candidates = sorted(receipts.glob("*.json"), key=lambda path: path.name)
        checks: list[dict[str, Any]] = []
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                checks.append({"path": path.relative_to(workspace).as_posix(), "status": "CONFLICT", "error": "receipt path is unsafe"})
                continue
            stem = path.name[:-5]
            if len(stem) <= 37 or stem[36] != "-":
                checks.append({"path": path.relative_to(workspace).as_posix(), "status": "CONFLICT", "error": "receipt filename is invalid"})
                continue
            candidate_job_id, operation = stem[:36], stem[37:]
            try:
                validate_job_id(candidate_job_id)
                if operation not in LOCAL_OPERATIONS:
                    raise ReconcileInputError("receipt operation is outside the local transaction slice")
                journal = RecoveryJournal(workspace, job_id=candidate_job_id, operation=operation)
                _read_canonical_json(path, label="completion receipt")
                valid, reason = _receipt_matches_journal(journal)
                expected_path = journal.receipt_path
                if expected_path != path or not valid:
                    raise RecoveryCorruption(reason or "receipt does not match its journal")
                checks.append({"path": path.relative_to(workspace).as_posix(), "job_id": candidate_job_id, "operation": operation, "status": "PASS", "journal_sha256": journal.journal_sha256()})
            except (OSError, TypeError, ValueError, RecoveryError, ReconcileInputError) as error:
                checks.append({"path": path.relative_to(workspace).as_posix(), "status": "CONFLICT", "error": str(error)})
        if job_id is not None and not candidates:
            checks.append({"job_id": job_id, "status": "CONFLICT", "error": "completion receipt is missing"})
        failed = [check for check in checks if check["status"] != "PASS"]
        return {
            "status": "CONFLICT" if failed else "PASS",
            "operation": "receipts verify",
            "verified": len(checks) - len(failed),
            "failed": len(failed),
            "checks": checks,
        }, EXIT_CONFLICT if failed else EXIT_OK
    except (OSError, TypeError, ValueError, ReconcileInputError, RecoveryError) as error:
        return {
            "status": "FAIL",
            "operation": "receipts verify",
            "errors": [_issue("RECEIPT_VERIFY_INPUT_INVALID", "/", str(error))],
            "checks": [],
        }, EXIT_INPUT_INVALID


# Short aliases keep the public API aligned with the CLI command names.
reconcile = reconcile_transactions
receipts_verify = verify_receipts
build_repair_plan = repair_plan
apply_repair = apply_repair_plan
verify_completion_receipts = verify_receipts

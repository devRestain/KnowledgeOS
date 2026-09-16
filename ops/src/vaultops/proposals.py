"""Provider-free C19 review, decision, and apply closure.

This module is the interactive-only boundary after C18 deterministic triage.
It treats a proposal note as an immutable input for each decision, records
digest-bound approval state under the private runtime root, and applies only a
still-current, explicitly described Markdown mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .blueprint import validate_blueprint
from .note_engine import (
    NoteContractError,
    NoteEngine,
    UnsafePathError,
    render_frontmatter,
    resolve_vault_relative_path,
    write_note_file,
)
from .recovery import (
    RecoveryConflict,
    RecoveryCorruption,
    RecoveryError,
    RecoveryJournal,
    canonical_json_bytes,
    fsync_directory,
)

EXIT_OK = 0
EXIT_CONFLICT = 30
EXIT_INPUT_INVALID = 10
SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SOURCE_HASH = re.compile(r"^(.+)\|sha256:([0-9a-f]{64})$")
_MANIFEST = re.compile(
    r"<!--\s*vaultops:proposal-manifest\s*(?P<payload>\{.*?\})\s*-->",
    re.DOTALL,
)
_MAX_REASON = 2000
_MAX_MANIFEST = 256 * 1024

PENDING_ROOT = "01_AI_Review/Pending"
RESOLVED_ROOT = "01_AI_Review/Resolved"
REJECTED_ROOT = "01_AI_Review/Rejected"
APPROVED_ROOT = "runtime/approved"
RECEIPTS_ROOT = "runtime/receipts"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _problem(
    operation: str,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    exit_code: int = EXIT_CONFLICT,
) -> tuple[dict[str, Any], int]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = dict(details)
    return {
        "status": "FAIL",
        "operation": operation,
        "provider_called": False,
        "mutation_performed": False,
        "errors": [error],
    }, exit_code


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(canonical_json_bytes(value))


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("control root must be an existing non-symlink directory")
    return candidate.resolve()


def _vault(workspace: Path) -> Path:
    root = workspace / "KnowledgeHub"
    if root.is_symlink() or not root.is_dir():
        raise ValueError("KnowledgeHub must be an existing non-symlink directory")
    return root


def _regular(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file")


def _read_bytes(path: Path, label: str) -> bytes:
    _regular(path, label)
    return path.read_bytes()


def _private_dir(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"runtime path is not a directory: {path}")
        if path.stat().st_mode & 0o777 != 0o700:
            raise ValueError(f"runtime directory mode must be 0700: {path}")
        return
    path.mkdir(mode=0o700, parents=True)
    if path.stat().st_mode & 0o777 != 0o700:
        raise ValueError(f"runtime directory mode must be 0700: {path}")


def _runtime_dirs(workspace: Path) -> tuple[Path, Path]:
    runtime = workspace / "runtime"
    _private_dir(runtime)
    approved = runtime / "approved"
    receipts = runtime / "receipts"
    _private_dir(approved)
    _private_dir(receipts)
    return approved, receipts


def _safe_vault_path(vault: Path, relative: str) -> tuple[str, Path]:
    if not isinstance(relative, str) or not relative.endswith(".md"):
        raise UnsafePathError("proposal path must be a Vault-relative Markdown path")
    path = resolve_vault_relative_path(vault, relative)
    normalized = path.relative_to(vault).as_posix()
    return normalized, path


def _git_head(repository: Path) -> str | None:
    if not (repository / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repository}", "-C", str(repository), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _tree_digest(vault: Path) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(vault.rglob("*"), key=lambda item: item.relative_to(vault).as_posix()):
        if path.is_symlink():
            raise UnsafePathError("Vault tree contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(vault).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        entries.append({"path": relative, "sha256": _hash_bytes(path.read_bytes())})
    return _hash_json(entries)


def _directory_digest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        return _hash_json([])
    entries: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        entries.append(
            {"path": path.relative_to(root).as_posix(), "sha256": _hash_bytes(path.read_bytes())}
        )
    return _hash_json(entries)


def _git_baselines(workspace: Path, vault: Path) -> dict[str, str | None]:
    return {
        "control_head": _git_head(workspace),
        "vault_head": _git_head(vault),
    }


def _load_proposal(workspace: Path, relative: str) -> tuple[dict[str, Any], int] | Any:
    vault = _vault(workspace)
    normalized, path = _safe_vault_path(vault, relative)
    try:
        if not normalized.startswith(PENDING_ROOT + "/"):
            return _problem("proposal", "PROPOSAL_NAMESPACE_INVALID", "proposal must be under Pending")
        raw = _read_bytes(path, "proposal")
        text = raw.decode("utf-8")
        typed = NoteEngine.from_root(workspace).typed_note(normalized, text)
    except (OSError, UnicodeError, UnsafePathError, NoteContractError, ValueError) as error:
        return _problem("proposal", "PROPOSAL_INVALID", str(error))
    if typed.note_type != "proposal":
        return _problem("proposal", "PROPOSAL_TYPE_INVALID", "path does not contain a proposal note")
    if typed.properties.get("status") != "pending":
        return _problem(
            "proposal",
            "PROPOSAL_ALREADY_CONSUMED",
            "only pending proposals may be consumed",
            details={"status": typed.properties.get("status")},
        )
    proposal_id = typed.properties.get("proposal_id")
    if not isinstance(proposal_id, str) or not _UUID4.fullmatch(proposal_id):
        return _problem("proposal", "PROPOSAL_ID_INVALID", "proposal_id must be lowercase UUIDv4")
    if typed.properties.get("id") != f"proposal-{proposal_id}":
        return _problem("proposal", "PROPOSAL_ID_BINDING_INVALID", "proposal id and note id differ")
    return ProposalDocument(normalized, path, raw, text, typed, proposal_id)


class ProposalDocument:
    """Validated pending proposal and its exact source bytes."""

    def __init__(self, path: str, absolute_path: Path, raw: bytes, text: str, typed: Any, proposal_id: str):
        self.path = path
        self.absolute_path = absolute_path
        self.raw = raw
        self.text = text
        self.typed = typed
        self.proposal_id = proposal_id

    @property
    def sha256(self) -> str:
        return _hash_bytes(self.raw)


class MutationPlan:
    """Validated action manifest and target precondition."""

    def __init__(
        self,
        *,
        action: str,
        target_path: str,
        target: Path,
        target_markdown: str,
        target_before_sha256: str | None,
        manifest_sha256: str,
    ):
        self.action = action
        self.target_path = target_path
        self.target = target
        self.target_markdown = target_markdown
        self.target_before_sha256 = target_before_sha256
        self.manifest_sha256 = manifest_sha256

    @property
    def target_after_sha256(self) -> str:
        return _hash_bytes(self.target_markdown.encode("utf-8"))


def _manifest(document: ProposalDocument) -> tuple[dict[str, Any], int] | dict[str, Any]:
    matches = list(_MANIFEST.finditer(document.typed.body))
    if len(matches) != 1:
        return _problem(
            "proposal",
            "PROPOSAL_MANIFEST_REQUIRED",
            "exactly one vaultops proposal manifest is required",
        )
    payload = matches[0].group("payload").strip()
    if len(payload.encode("utf-8")) > _MAX_MANIFEST:
        return _problem("proposal", "PROPOSAL_MANIFEST_TOO_LARGE", "proposal manifest is too large")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        return _problem("proposal", "PROPOSAL_MANIFEST_INVALID", str(error))
    if not isinstance(value, dict):
        return _problem("proposal", "PROPOSAL_MANIFEST_INVALID", "manifest must be an object")
    return value


def _parse_sources(workspace: Path, document: ProposalDocument) -> tuple[list[dict[str, str]], int] | list[dict[str, str]]:
    values = document.typed.properties.get("source_hashes")
    if not isinstance(values, list) or not values:
        return _problem("proposal", "PROPOSAL_SOURCES_INVALID", "source_hashes must be a non-empty list")
    vault = _vault(workspace)
    result: list[dict[str, str]] = []
    engine = NoteEngine.from_root(workspace)
    for item in values:
        if not isinstance(item, str):
            return _problem("proposal", "PROPOSAL_SOURCE_BINDING_INVALID", "source hash binding must be text")
        match = _SOURCE_HASH.fullmatch(item)
        if match is None:
            return _problem("proposal", "PROPOSAL_SOURCE_BINDING_INVALID", "source hash binding is malformed")
        source_relative, expected = match.groups()
        try:
            normalized, source = _safe_vault_path(vault, source_relative)
            raw = _read_bytes(source, "proposal source")
            observed = _hash_bytes(raw)
            if observed != expected:
                return _problem(
                    "proposal",
                    "PROPOSAL_SOURCE_DRIFT",
                    "source digest does not match proposal binding",
                    details={"path": normalized},
                )
            typed = engine.typed_note(normalized, raw.decode("utf-8"))
        except (OSError, UnicodeError, UnsafePathError, NoteContractError, ValueError) as error:
            return _problem("proposal", "PROPOSAL_SOURCE_INVALID", str(error))
        if typed.properties.get("sensitivity") == "confidential" or typed.properties.get("ai_policy") == "deny":
            return _problem(
                "proposal",
                "PROPOSAL_PRIVACY_DENIED",
                "a source privacy policy denies proposal processing",
                details={"path": normalized},
            )
        result.append({"path": normalized, "sha256": expected})
    return result


def _plan(workspace: Path, document: ProposalDocument) -> tuple[MutationPlan, int] | MutationPlan:
    manifest = _manifest(document)
    if isinstance(manifest, tuple):
        return manifest
    action = manifest.get("action")
    if action not in {"create_note", "update_note"}:
        return _problem("proposal", "PROPOSAL_ACTION_INVALID", "only create_note and update_note are supported")
    target_path_value = manifest.get("target_path")
    if not isinstance(target_path_value, str):
        return _problem("proposal", "PROPOSAL_TARGET_INVALID", "target_path is required")
    try:
        target_path, target = _safe_vault_path(_vault(workspace), target_path_value)
    except (UnsafePathError, ValueError) as error:
        return _problem("proposal", "PROPOSAL_TARGET_INVALID", str(error))
    if target_path.startswith(("01_AI_Review/", ".vault-bridge/", ".")):
        return _problem("proposal", "PROPOSAL_TARGET_FORBIDDEN", "target is outside the canonical note mutation boundary")
    if not target.parent.is_dir() or target.parent.is_symlink():
        return _problem("proposal", "PROPOSAL_TARGET_PARENT_INVALID", "target parent must already be a real directory")
    markdown = manifest.get("target_markdown")
    if not isinstance(markdown, str):
        properties = manifest.get("target_properties")
        body = manifest.get("target_body")
        if not isinstance(properties, dict) or not isinstance(body, str):
            return _problem("proposal", "PROPOSAL_TARGET_CONTENT_INVALID", "target_markdown or target_properties and target_body are required")
        markdown = render_frontmatter(properties, body)
    try:
        NoteEngine.from_root(workspace).typed_note(target_path, markdown)
    except (NoteContractError, ValueError, UnicodeError) as error:
        return _problem("proposal", "PROPOSAL_TARGET_NOTE_INVALID", str(error))
    current_exists = target.exists() or target.is_symlink()
    current_hash: str | None = None
    if current_exists:
        if target.is_symlink() or not target.is_file():
            return _problem("proposal", "PROPOSAL_TARGET_UNSAFE", "target is not a regular file")
        current_hash = _hash_bytes(target.read_bytes())
    expected_target = manifest.get("expected_target_sha256", manifest.get("target_sha256"))
    if expected_target is not None and (not isinstance(expected_target, str) or not _SHA256.fullmatch(expected_target)):
        return _problem("proposal", "PROPOSAL_TARGET_HASH_INVALID", "expected target hash must be lowercase SHA-256")
    if action == "create_note":
        if current_exists:
            return _problem("proposal", "PROPOSAL_TARGET_EXISTS", "create target already exists")
        if expected_target is not None:
            return _problem("proposal", "PROPOSAL_TARGET_HASH_INVALID", "create target cannot have an expected hash")
    else:
        if current_hash is None:
            return _problem("proposal", "PROPOSAL_TARGET_MISSING", "update target does not exist")
        if expected_target != current_hash:
            return _problem("proposal", "PROPOSAL_TARGET_DRIFT", "update target digest does not match proposal binding")
    return MutationPlan(
        action=action,
        target_path=target_path,
        target=target,
        target_markdown=markdown,
        target_before_sha256=current_hash,
        manifest_sha256=_hash_json(manifest),
    )


def _binding(workspace: Path, document: ProposalDocument, plan: MutationPlan, sources: list[dict[str, str]]) -> dict[str, Any]:
    vault = _vault(workspace)
    policies = workspace / "ops/policies"
    actions = workspace / "ops/actions"
    prompts = workspace / "ops/prompts"
    schema_path = workspace / "blueprint/blueprint.schema.json"
    return {
        "proposal_sha256": document.sha256,
        "diff_sha256": plan.target_after_sha256,
        "source_baselines": sources,
        "target_baselines": [{
            "path": plan.target_path,
            "sha256": plan.target_before_sha256,
        }],
        "bridge_request_sha256_or_null": None,
        "committed_vault_tree_id": _tree_digest(vault),
        "action_config_sha256": _directory_digest(actions),
        "prompt_sha256": _directory_digest(prompts),
        "policy_bundle_sha256": _directory_digest(policies),
        "schema_sha256": _hash_bytes(_read_bytes(schema_path, "blueprint schema")),
        "provider_route_and_resolved_model": "provider_free:interactive_only",
        "candidate_set_sha256_or_null": None,
        "retrieval_config_sha256_or_null": _hash_bytes(
            _read_bytes(policies / "retrieval.yaml", "retrieval policy")
        )
        if (policies / "retrieval.yaml").is_file()
        else None,
        "input_index_generation_or_null": None,
        "triage_selection_set_sha256_or_null": plan.manifest_sha256,
        "control_and_vault_git_baselines": _git_baselines(workspace, vault),
        "expiry": None,
    }


def _validate_approval(approval: Mapping[str, Any]) -> str | None:
    errors = sorted(Draft202012Validator(approval_schema()).iter_errors(approval), key=lambda item: item.json_path)
    return errors[0].message if errors else None


def _create_only_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink() or path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != canonical_json_bytes(payload) + b"\n":
            raise RecoveryConflict(f"create-only artifact conflicts: {path}")
        return
    _private_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(canonical_json_bytes(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)
    fsync_directory(path.parent)


def _approval_path(workspace: Path, proposal_id: str) -> Path:
    approved, _ = _runtime_dirs(workspace)
    return approved / f"{proposal_id}.json"


def _receipt_path(workspace: Path, proposal_id: str, operation: str) -> Path:
    _, receipts = _runtime_dirs(workspace)
    return receipts / f"{proposal_id}-proposal-{operation}.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _summary(workspace: Path, document: ProposalDocument, plan: MutationPlan, sources: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "proposal_id": document.proposal_id,
        "proposal_path": document.path,
        "proposal_sha256": document.sha256,
        "action": plan.action,
        "source_baselines": sources,
        "target_path": plan.target_path,
        "target_before_sha256": plan.target_before_sha256,
        "target_after_sha256": plan.target_after_sha256,
    }


def review_proposals(
    root: str | Path,
    *,
    proposal_path: str | None = None,
) -> tuple[dict[str, Any], int]:
    """List or inspect pending proposals without writing any state."""

    operation = "ai review"
    try:
        workspace = _workspace(root)
        validation = validate_blueprint(workspace)
        if not validation.passed:
            return _problem(operation, "BLUEPRINT_INVALID", "blueprint validation failed")
        vault = _vault(workspace)
        if proposal_path is None:
            pending = vault / PENDING_ROOT
            candidates = (
                sorted(
                    path.relative_to(vault).as_posix()
                    for path in pending.rglob("*.md")
                    if path.is_file() and not path.is_symlink()
                )
                if pending.is_dir()
                else []
            )
        else:
            candidates = [proposal_path]
        items: list[dict[str, Any]] = []
        for relative in candidates:
            loaded = _load_proposal(workspace, relative)
            if isinstance(loaded, tuple):
                return loaded
            sources = _parse_sources(workspace, loaded)
            if isinstance(sources, tuple):
                return sources
            plan = _plan(workspace, loaded)
            if isinstance(plan, tuple):
                return plan
            item = _summary(workspace, loaded, plan, sources)
            item["status"] = "pending"
            items.append(item)
        return {
            "status": "PASS",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": False,
            "count": len(items),
            "proposals": items,
        }, EXIT_OK
    except (OSError, UnicodeError, UnsafePathError, ValueError, KeyError) as error:
        return _problem(operation, "REVIEW_INVALID", str(error))


def approve_proposal(
    root: str | Path,
    *,
    proposal_path: str,
    expected_sha256: str,
) -> tuple[dict[str, Any], int]:
    """Create one digest-bound approval artifact without mutating the Vault."""

    operation = "ai approve"
    try:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            return _problem(operation, "APPROVAL_HASH_INVALID", "expected_sha256 must be lowercase SHA-256")
        workspace = _workspace(root)
        loaded = _load_proposal(workspace, proposal_path)
        if isinstance(loaded, tuple):
            return loaded
        document = loaded
        if document.sha256 != expected_sha256:
            return _problem(operation, "PROPOSAL_DRIFT", "proposal digest does not match expected_sha256")
        sources = _parse_sources(workspace, document)
        if isinstance(sources, tuple):
            return sources
        plan = _plan(workspace, document)
        if isinstance(plan, tuple):
            return plan
        binding = _binding(workspace, document, plan, sources)
        approval: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "operation": "proposal_approval",
            "status": "approved",
            "proposal_id": document.proposal_id,
            "proposal_path": document.path,
            "action": plan.action,
            "target_path": plan.target_path,
            "approval_binds": binding,
            "approved_at": _now(),
            "provider_called": False,
            "mutation_performed": False,
        }
        error = _validate_approval(approval)
        if error:
            return _problem(operation, "APPROVAL_SCHEMA_INVALID", error)
        path = _approval_path(workspace, document.proposal_id)
        existing = _load_json(path)
        if existing is not None:
            if existing != approval:
                return _problem(operation, "APPROVAL_CONFLICT", "existing approval artifact differs")
            return {
                "status": "NO_OP",
                "operation": operation,
                "provider_called": False,
                "mutation_performed": False,
                "replayed": True,
                "approval": existing,
            }, EXIT_OK
        _create_only_json(path, approval)
        return {
            "status": "PASS",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": False,
            "replayed": False,
            "approval": approval,
        }, EXIT_OK
    except RecoveryConflict as error:
        return _problem(operation, "APPROVAL_CONFLICT", str(error))
    except (OSError, UnicodeError, UnsafePathError, ValueError, KeyError) as error:
        return _problem(operation, "APPROVAL_INVALID", str(error))


def _decision_payload(
    workspace: Path,
    document: ProposalDocument,
    *,
    decision: str,
    reason: str,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "proposal_decision",
        "decision": decision,
        "proposal_id": document.proposal_id,
        "proposal_path": document.path,
        "proposal_sha256": document.sha256,
        "reason_sha256": _hash_bytes(reason.encode("utf-8")),
        "reason_length": len(reason),
        "approval_binds": dict(binding),
        "decided_at": _now(),
        "provider_called": False,
        "mutation_performed": True,
    }


def reject_proposal(
    root: str | Path,
    *,
    proposal_path: str,
    expected_sha256: str,
    reason: str,
) -> tuple[dict[str, Any], int]:
    """Reject and close one pending proposal into the Rejected namespace."""

    operation = "ai reject"
    try:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > _MAX_REASON:
            return _problem(operation, "REJECTION_REASON_INVALID", "reason must be bounded non-empty text")
        workspace = _workspace(root)
        loaded = _load_proposal(workspace, proposal_path)
        if isinstance(loaded, tuple):
            return loaded
        document = loaded
        if document.sha256 != expected_sha256:
            return _problem(operation, "PROPOSAL_DRIFT", "proposal digest does not match expected_sha256")
        sources = _parse_sources(workspace, document)
        if isinstance(sources, tuple):
            return sources
        plan = _plan(workspace, document)
        if isinstance(plan, tuple):
            return plan
        binding = _binding(workspace, document, plan, sources)
        payload = _decision_payload(workspace, document, decision="rejected", reason=reason, binding=binding)
        schema_error = _validate_decision(payload)
        if schema_error:
            return _problem(operation, "DECISION_SCHEMA_INVALID", schema_error)
        receipt_path = _receipt_path(workspace, document.proposal_id, "rejection")
        existing_receipt = _load_json(receipt_path)
        if existing_receipt is not None:
            if existing_receipt != payload:
                return _problem(operation, "DECISION_CONFLICT", "existing rejection receipt differs")
            return {
                "status": "NO_OP",
                "operation": operation,
                "provider_called": False,
                "mutation_performed": False,
                "replayed": True,
                "receipt": existing_receipt,
            }, EXIT_OK
        destination = _vault(workspace) / REJECTED_ROOT / Path(document.path).name
        if destination.exists() or destination.is_symlink():
            return _problem(operation, "DECISION_CONFLICT", "rejection destination already exists")
        updated = _closed_proposal_text(document, status="rejected")
        write_note_file(document.absolute_path, updated, overwrite=True, expected_sha256=document.sha256)
        os.replace(document.absolute_path, destination)
        fsync_directory(destination.parent)
        _create_only_json(receipt_path, payload)
        return {
            "status": "PASS",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": True,
            "replayed": False,
            "receipt": payload,
            "closed_path": destination.relative_to(_vault(workspace)).as_posix(),
        }, EXIT_OK
    except RecoveryConflict as error:
        return _problem(operation, "DECISION_CONFLICT", str(error))
    except (OSError, UnicodeError, UnsafePathError, ValueError, KeyError) as error:
        return _problem(operation, "REJECTION_INVALID", str(error))


def _validate_decision(value: Mapping[str, Any]) -> str | None:
    errors = sorted(Draft202012Validator(decision_schema()).iter_errors(value), key=lambda item: item.json_path)
    return errors[0].message if errors else None


def _closed_proposal_text(document: ProposalDocument, *, status: str) -> str:
    properties = dict(document.typed.properties)
    properties["status"] = status
    properties["ai_status"] = status
    properties["modified"] = _now()
    return render_frontmatter(properties, document.typed.body)


def _replay_apply(workspace: Path, requested_path: str) -> tuple[dict[str, Any], int] | None:
    receipts = workspace / "runtime/receipts"
    if receipts.is_symlink() or not receipts.is_dir():
        return None
    for path in sorted(receipts.glob("*-proposal-apply.json")):
        receipt = _load_json(path)
        if receipt is not None and receipt.get("proposal_path") == requested_path:
            return {
                "status": "NO_OP",
                "operation": "ai apply",
                "provider_called": False,
                "mutation_performed": False,
                "replayed": True,
                "receipt": receipt,
            }, EXIT_OK
    return None


def _apply_receipt(
    document: ProposalDocument,
    plan: MutationPlan,
    binding: Mapping[str, Any],
    *,
    closed_path: str,
    journal_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "proposal_apply",
        "status": "applied",
        "proposal_id": document.proposal_id,
        "proposal_path": document.path,
        "closed_path": closed_path,
        "proposal_sha256": document.sha256,
        "action": plan.action,
        "target_path": plan.target_path,
        "target_before_sha256": plan.target_before_sha256,
        "target_after_sha256": plan.target_after_sha256,
        "approval_binds": dict(binding),
        "journal_sha256": journal_sha256,
        "applied_at": _now(),
        "provider_called": False,
        "mutation_performed": True,
    }


def apply_proposal(
    root: str | Path,
    *,
    proposal_path: str,
    approval_path: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Apply a still-current approval and close the proposal into Resolved."""

    operation = "ai apply"
    try:
        workspace = _workspace(root)
        replay = _replay_apply(workspace, proposal_path)
        if replay is not None:
            return replay
        loaded = _load_proposal(workspace, proposal_path)
        if isinstance(loaded, tuple):
            return loaded
        document = loaded
        sources = _parse_sources(workspace, document)
        if isinstance(sources, tuple):
            return sources
        plan = _plan(workspace, document)
        if isinstance(plan, tuple):
            return plan
        binding = _binding(workspace, document, plan, sources)
        approval_file = Path(approval_path) if approval_path else _approval_path(workspace, document.proposal_id)
        if approval_file.is_absolute():
            approval = _load_json(approval_file)
        else:
            approval = _load_json(workspace / approval_file)
        if approval is None:
            return _problem(operation, "APPROVAL_MISSING", "no approval artifact exists")
        if _validate_approval(approval):
            return _problem(operation, "APPROVAL_INVALID", "approval artifact fails its schema")
        if approval.get("proposal_id") != document.proposal_id or approval.get("approval_binds") != binding:
            return _problem(operation, "APPROVAL_STALE", "approval does not bind the current proposal and Vault state")
        destination = _vault(workspace) / RESOLVED_ROOT / Path(document.path).name
        if destination.exists() or destination.is_symlink():
            return _problem(operation, "APPLY_CONFLICT", "resolved proposal destination already exists")
        closed_text = _closed_proposal_text(document, status="applied")
        try:
            target_before = plan.target_before_sha256
            if plan.action == "create_note":
                if plan.target.exists() or plan.target.is_symlink():
                    return _problem(operation, "APPLY_TARGET_DRIFT", "create target appeared after approval")
            elif not plan.target.is_file() or plan.target.is_symlink():
                return _problem(operation, "APPLY_TARGET_DRIFT", "update target disappeared after approval")
            elif _hash_bytes(plan.target.read_bytes()) != target_before:
                return _problem(operation, "APPLY_TARGET_DRIFT", "update target changed after approval")
            journal = RecoveryJournal(workspace, job_id=document.proposal_id, operation="proposal_apply")
            intent = {
                "schema_version": SCHEMA_VERSION,
                "proposal_path": document.path,
                "proposal_sha256": document.sha256,
                "target_path": plan.target_path,
                "target_before_sha256": target_before,
                "target_after_sha256": plan.target_after_sha256,
                "approval_binds": binding,
            }
            journal.start(intent)
            if journal.latest()["state"] == "completed":
                replay = _replay_apply(workspace, proposal_path)
                if replay is not None:
                    return replay
                return _problem(operation, "APPLY_CONFLICT", "completed journal has no canonical receipt")
            if plan.action == "create_note":
                write_note_file(plan.target, plan.target_markdown, overwrite=False)
            else:
                write_note_file(
                    plan.target,
                    plan.target_markdown,
                    overwrite=True,
                    expected_sha256=target_before,
                )
            journal.append(
                "applying",
                {"target_path": plan.target_path, "target_sha256": plan.target_after_sha256},
            )
            write_note_file(document.absolute_path, closed_text, overwrite=True, expected_sha256=document.sha256)
            os.replace(document.absolute_path, destination)
            fsync_directory(destination.parent)
            journal.append(
                "moved",
                {
                    "closed_path": destination.relative_to(_vault(workspace)).as_posix(),
                    "target_path": plan.target_path,
                },
            )
            receipt = _apply_receipt(
                document,
                plan,
                binding,
                closed_path=destination.relative_to(_vault(workspace)).as_posix(),
                journal_sha256="pending",
            )
            journal.append("completed", {"receipt": receipt})
            completion = dict(journal.completion_receipt())
            receipt["journal_sha256"] = completion["journal_sha256"]
            _create_only_json(_receipt_path(workspace, document.proposal_id, "apply"), receipt)
        except (RecoveryConflict, RecoveryCorruption, RecoveryError) as error:
            return _problem(operation, "APPLY_RECOVERY_CONFLICT", str(error))
        return {
            "status": "PASS",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": True,
            "replayed": False,
            "receipt": receipt,
            "closed_path": receipt["closed_path"],
            "target_path": plan.target_path,
        }, EXIT_OK
    except RecoveryConflict as error:
        return _problem(operation, "APPLY_CONFLICT", str(error))
    except (OSError, UnicodeError, UnsafePathError, ValueError, KeyError) as error:
        return _problem(operation, "APPLY_INVALID", str(error))


def approval_schema() -> dict[str, Any]:
    binding = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "proposal_sha256",
            "diff_sha256",
            "source_baselines",
            "target_baselines",
            "bridge_request_sha256_or_null",
            "committed_vault_tree_id",
            "action_config_sha256",
            "prompt_sha256",
            "policy_bundle_sha256",
            "schema_sha256",
            "provider_route_and_resolved_model",
            "candidate_set_sha256_or_null",
            "retrieval_config_sha256_or_null",
            "input_index_generation_or_null",
            "triage_selection_set_sha256_or_null",
            "control_and_vault_git_baselines",
            "expiry",
        ],
        "properties": {
            "proposal_sha256": {"$ref": "#/$defs/sha256"},
            "diff_sha256": {"$ref": "#/$defs/sha256"},
            "source_baselines": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/baseline"}},
            "target_baselines": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/baseline"}},
            "bridge_request_sha256_or_null": {"anyOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]},
            "committed_vault_tree_id": {"$ref": "#/$defs/sha256"},
            "action_config_sha256": {"$ref": "#/$defs/sha256"},
            "prompt_sha256": {"$ref": "#/$defs/sha256"},
            "policy_bundle_sha256": {"$ref": "#/$defs/sha256"},
            "schema_sha256": {"$ref": "#/$defs/sha256"},
            "provider_route_and_resolved_model": {"type": "string", "const": "provider_free:interactive_only"},
            "candidate_set_sha256_or_null": {"anyOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]},
            "retrieval_config_sha256_or_null": {"anyOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]},
            "input_index_generation_or_null": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "triage_selection_set_sha256_or_null": {"anyOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]},
            "control_and_vault_git_baselines": {
                "type": "object",
                "additionalProperties": False,
                "required": ["control_head", "vault_head"],
                "properties": {"control_head": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "vault_head": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            },
            "expiry": {"type": ["string", "null"]},
        },
        "$defs": {
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "baseline": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "sha256"],
                "properties": {"path": {"type": "string", "minLength": 1}, "sha256": {"anyOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]}},
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/approval.schema.json",
        "title": "KnowledgeOS C19 proposal approval",
        "type": "object",
        "additionalProperties": False,
        "$defs": binding["$defs"],
        "required": ["schema_version", "operation", "status", "proposal_id", "proposal_path", "action", "target_path", "approval_binds", "approved_at", "provider_called", "mutation_performed"],
        "properties": {
            "schema_version": {"const": 1},
            "operation": {"const": "proposal_approval"},
            "status": {"const": "approved"},
            "proposal_id": {"type": "string", "pattern": _UUID4.pattern},
            "proposal_path": {"type": "string", "minLength": 1},
            "action": {"enum": ["create_note", "update_note"]},
            "target_path": {"type": "string", "minLength": 1},
            "approval_binds": binding,
            "approved_at": {"type": "string", "minLength": 1},
            "provider_called": {"const": False},
            "mutation_performed": {"const": False},
        },
    }


def decision_schema() -> dict[str, Any]:
    base = approval_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/decision.schema.json",
        "title": "KnowledgeOS C19 proposal decision",
        "type": "object",
        "additionalProperties": False,
        "$defs": base["$defs"],
        "required": ["schema_version", "operation", "decision", "proposal_id", "proposal_path", "proposal_sha256", "reason_sha256", "reason_length", "approval_binds", "decided_at", "provider_called", "mutation_performed"],
        "properties": {
            "schema_version": {"const": 1},
            "operation": {"const": "proposal_decision"},
            "decision": {"const": "rejected"},
            "proposal_id": {"type": "string", "pattern": _UUID4.pattern},
            "proposal_path": {"type": "string", "minLength": 1},
            "proposal_sha256": {"type": "string", "pattern": _SHA256.pattern},
            "reason_sha256": {"type": "string", "pattern": _SHA256.pattern},
            "reason_length": {"type": "integer", "minimum": 1, "maximum": _MAX_REASON},
            "approval_binds": base["properties"]["approval_binds"],
            "decided_at": {"type": "string", "minLength": 1},
            "provider_called": {"const": False},
            "mutation_performed": {"const": True},
        },
    }


def apply_receipt_schema() -> dict[str, Any]:
    base = approval_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/apply-receipt.schema.json",
        "title": "KnowledgeOS C19 proposal apply receipt",
        "type": "object",
        "additionalProperties": False,
        "$defs": base["$defs"],
        "required": ["schema_version", "operation", "status", "proposal_id", "proposal_path", "closed_path", "proposal_sha256", "action", "target_path", "target_before_sha256", "target_after_sha256", "approval_binds", "journal_sha256", "applied_at", "provider_called", "mutation_performed"],
        "properties": {
            "schema_version": {"const": 1},
            "operation": {"const": "proposal_apply"},
            "status": {"const": "applied"},
            "proposal_id": {"type": "string", "pattern": _UUID4.pattern},
            "proposal_path": {"type": "string", "minLength": 1},
            "closed_path": {"type": "string", "minLength": 1},
            "proposal_sha256": {"type": "string", "pattern": _SHA256.pattern},
            "action": {"enum": ["create_note", "update_note"]},
            "target_path": {"type": "string", "minLength": 1},
            "target_before_sha256": {"anyOf": [{"type": "string", "pattern": _SHA256.pattern}, {"type": "null"}]},
            "target_after_sha256": {"type": "string", "pattern": _SHA256.pattern},
            "approval_binds": base["properties"]["approval_binds"],
            "journal_sha256": {"type": "string", "pattern": _SHA256.pattern},
            "applied_at": {"type": "string", "minLength": 1},
            "provider_called": {"const": False},
            "mutation_performed": {"const": True},
        },
    }


def apply_receipt_valid(receipt: Mapping[str, Any]) -> bool:
    return not list(Draft202012Validator(apply_receipt_schema()).iter_errors(receipt))


# Short aliases keep the command-oriented API easy to discover in tests and
# future facade code while the explicit names remain the public contract.
review_proposal = review_proposals
approve = approve_proposal
reject = reject_proposal
apply = apply_proposal

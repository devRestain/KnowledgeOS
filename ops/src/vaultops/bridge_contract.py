"""Offline bridge and root-sentinel contracts owned by C10.

This module deliberately stops at the control-side contract boundary.  It
does not inspect Git history, contact a remote, create a production sentinel,
publish a response, or mutate a Vault.  The response renderer is restricted
to caller-supplied fixture paths so C10 evidence cannot be mistaken for a
live bridge round trip.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

BRIDGE_STATES = (
    "committed_request",
    "ingested",
    "awaiting_remote_authorization",
    "queued",
    "rejected",
    "running",
    "proposal_ready",
    "answer_ready",
    "no_change",
    "insufficient_input",
    "refused",
    "failed",
    "conflict",
    "expired",
    "published_local",
    "human_push",
    "mobile_visible",
)

RESPONSE_STATUSES = (
    "awaiting_remote_authorization",
    "queued",
    "rejected",
    "needs_review",
    "answer_ready",
    "no_change",
    "insufficient_input",
    "refused",
    "failed",
    "conflict",
    "expired",
)

ALLOWED_TRANSITIONS = {
    "committed_request": ("ingested",),
    "ingested": ("awaiting_remote_authorization", "queued", "rejected", "conflict"),
    "awaiting_remote_authorization": ("queued", "rejected", "expired"),
    "queued": ("running", "rejected", "conflict"),
    "running": (
        "proposal_ready",
        "answer_ready",
        "no_change",
        "insufficient_input",
        "refused",
        "failed",
        "conflict",
    ),
    "proposal_ready": ("published_local", "rejected", "expired", "conflict"),
    "answer_ready": ("published_local", "expired", "conflict"),
    "no_change": ("published_local",),
    "insufficient_input": ("published_local",),
    "refused": ("published_local",),
    "failed": ("published_local",),
    "conflict": ("published_local",),
    "rejected": ("published_local",),
    "expired": ("published_local",),
    "published_local": ("human_push",),
    "human_push": ("mobile_visible",),
}

STATE_MAPPING = {
    "awaiting_remote_authorization": {
        "runtime": "awaiting_remote_authorization",
        "public_status": "awaiting_remote_authorization",
    },
    "queued": {"runtime": "queue", "public_status": "queued"},
    "rejected": {"runtime": "rejected", "public_status": "rejected"},
    "proposal_ready": {
        "runtime": "review",
        "public_status": "needs_review",
        "review_folder": "01_AI_Review/Pending",
    },
    "answer_ready": {"runtime": "review", "public_status": "answer_ready"},
    "no_change": {"runtime": "done", "public_status": "no_change"},
    "insufficient_input": {"runtime": "done", "public_status": "insufficient_input"},
    "refused": {"runtime": "done", "public_status": "refused"},
    "failed": {"runtime": "failed", "public_status": "failed"},
    "conflict": {
        "runtime": "conflict",
        "public_status": "conflict",
        "review_folder": "01_AI_Review/Conflict",
    },
    "expired": {
        "runtime": "expired",
        "public_status": "expired",
        "review_folder": "01_AI_Review/Expired",
    },
}

BRIDGE_REQUEST_SCHEMA_PATH = "ops/schemas/bridge-request.schema.json"
BRIDGE_RESPONSE_SCHEMA_PATH = "ops/schemas/bridge-response.schema.json"
ROOT_SENTINEL_SCHEMA_PATH = "ops/schemas/root-sentinel.schema.json"
PROTOCOL_REQUEST_SCHEMA_PATH = "KnowledgeHub/.vault-bridge/protocol/request.schema.json"
PROTOCOL_RESPONSE_SCHEMA_PATH = "KnowledgeHub/.vault-bridge/protocol/response.schema.json"

UUID_V4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
DATE_TIME_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
PATH_PATTERN = r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))[^\r\n\x00]+$"
BRANCH_PATTERN = r"^(?!-)(?!.*\.\.)(?!.*[ ~^:?*\[\\])[^\r\n]{1,255}$"
ROUTE_PATTERN = r"^(?:codex_chatgpt_login|openai_api_relay|local:[a-z0-9][a-z0-9._-]{0,63})$"
DEVICE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


@dataclass(frozen=True)
class ContractIssue:
    """Stable diagnostic for an C10 contract check."""

    code: str
    locator: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "locator": self.locator, "message": self.message}


@dataclass(frozen=True)
class ContractReport:
    """Read-only validation report for a JSON contract document."""

    contract: str
    issues: tuple[ContractIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "status": "PASS" if self.passed else "FAIL",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _string_schema(*, pattern: str | None = None, max_length: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "minLength": 1}
    if pattern is not None:
        result["pattern"] = pattern
    if max_length is not None:
        result["maxLength"] = max_length
    return result


def _hash_schema() -> dict[str, str]:
    return {"type": "string", "pattern": SHA256_PATTERN}


def _datetime_schema() -> dict[str, str]:
    return {"type": "string", "format": "date-time", "pattern": DATE_TIME_PATTERN}


def _path_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 500, "pattern": PATH_PATTERN}


def _target_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "expected_sha256", "type"],
                "properties": {
                    "path": _path_schema(),
                    "expected_sha256": {
                        "type": "string",
                        "pattern": r"^$|^[0-9a-f]{64}$",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["idea", "question", "knowledge", "project", "source"],
                    },
                },
            },
        ]
    }


def build_bridge_request_schema(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Build the strict request schema from the Blueprint bridge contract."""

    bridge = blueprint["bridge"]
    action_contracts = bridge["action_contracts"]
    pipeline_kinds = list(action_contracts)
    parameter_properties = {
        "selected_candidate_id": _string_schema(max_length=200),
        "selected_candidate_sha256": _hash_schema(),
        "validated_title": _string_schema(max_length=200),
        "bounded_summary_mode": _string_schema(max_length=64),
        "retrieval_profile_id": _string_schema(max_length=128),
        "scope_id": _string_schema(max_length=200),
    }
    source_properties = {
        "path": _path_schema(),
        "blob_sha256": _hash_schema(),
        "locator": _string_schema(max_length=500),
        "fragment_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/bridge-request.schema.json",
        "title": "KnowledgeOS bridge request",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "job_id",
            "created_at",
            "device_id",
            "pipeline_kind",
            "source",
            "target",
            "parameters",
            "route_id",
            "mode",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "job_id": {"type": "string", "minLength": 36, "maxLength": 36, "pattern": UUID_V4_PATTERN},
            "created_at": _datetime_schema(),
            "device_id": _string_schema(pattern=DEVICE_PATTERN, max_length=128),
            "pipeline_kind": {"type": "string", "enum": pipeline_kinds},
            "source": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "blob_sha256"],
                "properties": source_properties,
            },
            "target": _target_schema(),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": parameter_properties,
            },
            "route_id": {"type": "string", "pattern": ROUTE_PATTERN},
            "mode": {
                "type": "string",
                "enum": ["proposal", "ask", "local_only", "remote_ok", "deny"],
            },
        },
    }


def _common_response_properties() -> dict[str, Any]:
    return {
        "schema_version": {"const": 1},
        "job_id": {"type": "string", "minLength": 36, "maxLength": 36, "pattern": UUID_V4_PATTERN},
        "sequence": {"type": "integer", "minimum": 1},
        "status": {"type": "string", "enum": list(RESPONSE_STATUSES)},
        "event_at": _datetime_schema(),
        "completed_at": _datetime_schema(),
        "request_sha256": _hash_schema(),
        "request_commit": {"type": "string", "minLength": 40, "maxLength": 40, "pattern": COMMIT_PATTERN},
        "warnings": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "job_receipt_sha256": _hash_schema(),
        "output_schema_sha256": _hash_schema(),
        "proposal_id": {"type": "string", "minLength": 36, "maxLength": 36, "pattern": UUID_V4_PATTERN},
        "proposal_path": _path_schema(),
        "proposal_sha256": _hash_schema(),
        "answer_id": {"type": "string", "minLength": 1, "maxLength": 200},
        "answer_plaintext": {
            "type": "string",
            "minLength": 1,
            "maxLength": 20000,
            "pattern": r"^(?![\s\S]*<[^>]+>)(?![\s\S]*!\[\[)(?![\s\S]*(?:obsidian|command):)[\s\S]*$",
        },
        "citations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "locator", "sha256"],
                "properties": {
                    "path": _path_schema(),
                    "locator": _string_schema(max_length=500),
                    "sha256": _hash_schema(),
                },
            },
        },
        "answer_sha256": _hash_schema(),
    }


def build_bridge_response_schema(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Build the status-discriminated response schema from Blueprint values."""

    bridge = blueprint["bridge"]
    response = bridge["response"]
    statuses = tuple(response["status_enum"])
    terminal_statuses = set(response["terminal_statuses"])
    common_required = tuple(response["required_fields"])
    terminal_required = tuple(response["terminal_required_fields"])
    proposal_required = tuple(response["proposal_fields"])
    answer_required = tuple(response["answer_fields"])
    properties = _common_response_properties()
    branches: list[dict[str, Any]] = []
    for status in statuses:
        required = list(common_required)
        required.append("warnings") if "warnings" not in required else None
        if status in terminal_statuses:
            required.extend(field for field in terminal_required if field not in required)
        if status == "needs_review":
            required.extend(field for field in proposal_required if field not in required)
        if status == "answer_ready":
            required.extend(field for field in answer_required if field not in required)
        branch_properties = json.loads(json.dumps(properties))
        branch_properties["status"] = {"const": status}
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": branch_properties,
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/bridge-response.schema.json",
        "title": "KnowledgeOS bridge response event",
        "oneOf": branches,
    }


def build_root_sentinel_schema(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Build the allowlisted sentinel schema without producing a sentinel file."""

    contract = blueprint["mobile_install_gate"]["root_sentinel_contract"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/root-sentinel.schema.json",
        "title": "KnowledgeOS Vault root sentinel",
        "type": "object",
        "additionalProperties": False,
        "required": list(contract["required_fields"]),
        "properties": {
            "schema_version": {"const": contract["schema_version"]},
            "contract_id": {"const": contract["contract_id"]},
            "vault_uuid": {
                "type": "string",
                "minLength": 36,
                "maxLength": 36,
                "pattern": UUID_V4_PATTERN,
            },
            "canonical_vault_name": {"const": contract["canonical_vault_name"]},
            "remote_identity_sha256": _hash_schema(),
            "expected_branch": {"type": "string", "pattern": BRANCH_PATTERN},
        },
    }


def schema_bytes(schema: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8 schema bytes."""

    return (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize one fixture document deterministically."""

    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonicalize_github_remote(remote: str) -> str:
    """Normalize an SSH/HTTPS GitHub remote to the digest input form.

    Only ``github.com/<owner>/<repository>.git\\n`` is returned.  Credentials,
    query strings, fragments, extra path components, and non-GitHub hosts are
    rejected before hashing.
    """

    if not isinstance(remote, str) or not remote or remote != remote.strip():
        raise ValueError("remote must be a non-empty string without surrounding whitespace")
    if any(character.isspace() or ord(character) < 32 for character in remote):
        raise ValueError("remote must not contain whitespace or control characters")
    if "?" in remote or "#" in remote or "\\" in remote:
        raise ValueError("remote query, fragment, and backslash are forbidden")

    host: str
    path: str
    if remote.startswith("git@") and ":" in remote:
        user_host, path = remote.split(":", 1)
        user, separator, host = user_host.partition("@")
        if not separator or user != "git":
            raise ValueError("only the git@github.com SSH form is allowed")
    else:
        parsed = urlsplit(remote if "://" in remote else f"https://{remote}")
        if parsed.password is not None or (
            parsed.username is not None
            and (parsed.scheme != "ssh" or parsed.username != "git")
        ):
            raise ValueError("remote credentials are forbidden")
        host = parsed.hostname or ""
        path = parsed.path.removeprefix("/")
        if parsed.query or parsed.fragment:
            raise ValueError("remote query and fragment are forbidden")
        if parsed.scheme not in {"https", "ssh"}:
            raise ValueError("remote scheme must be https or ssh")

    if host.casefold() != "github.com":
        raise ValueError("remote host must be github.com")
    parts = path.split("/")
    if len(parts) != 2:
        raise ValueError("remote path must contain exactly owner/repository")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    component_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    if not component_pattern.fullmatch(owner) or not component_pattern.fullmatch(repository):
        raise ValueError("remote owner and repository contain an invalid character")
    return f"github.com/{owner.casefold()}/{repository.casefold()}.git\n"


def remote_identity_sha256(remote: str) -> str:
    """Hash the canonical GitHub remote identity bytes."""

    return hashlib.sha256(canonicalize_github_remote(remote).encode("utf-8")).hexdigest()


def validate_transition(current: str, next_state: str) -> ContractReport:
    """Validate one state transition without changing any state."""

    issues: list[ContractIssue] = []
    if current not in BRIDGE_STATES:
        issues.append(ContractIssue("BRIDGE_UNKNOWN_STATE", "/current", f"unknown state: {current}"))
    if next_state not in BRIDGE_STATES:
        issues.append(ContractIssue("BRIDGE_UNKNOWN_STATE", "/next", f"unknown state: {next_state}"))
    if not issues and next_state not in ALLOWED_TRANSITIONS.get(current, ()):
        issues.append(
            ContractIssue(
                "BRIDGE_TRANSITION_NOT_ALLOWED",
                "/transition",
                f"transition is not allowed: {current} -> {next_state}",
            )
        )
    return ContractReport("bridge-transition", tuple(issues))


def validate_state_mapping(mapping: Mapping[str, Any] | None = None) -> ContractReport:
    """Validate that every runtime-mapped state has a public status."""

    observed = STATE_MAPPING if mapping is None else mapping
    issues: list[ContractIssue] = []
    for state in set(STATE_MAPPING) - set(observed):
        issues.append(ContractIssue("BRIDGE_MAPPING_MISSING", f"/{state}", "state mapping is missing"))
    for state, value in observed.items():
        if state not in BRIDGE_STATES:
            issues.append(ContractIssue("BRIDGE_UNKNOWN_STATE", f"/{state}", "mapping state is unknown"))
        if not isinstance(value, Mapping) or not value.get("runtime") or not value.get("public_status"):
            issues.append(ContractIssue("BRIDGE_MAPPING_INCOMPLETE", f"/{state}", "runtime/public_status are required"))
        elif value["public_status"] not in RESPONSE_STATUSES:
            issues.append(ContractIssue("BRIDGE_UNKNOWN_PUBLIC_STATUS", f"/{state}/public_status", "status is unknown"))
    return ContractReport("bridge-state-mapping", tuple(issues))


def validate_append_only_history(events: Sequence[Mapping[str, Any]]) -> ContractReport:
    """Reject update, delete, and re-add attempts for transport paths.

    The fixture accepts only one ``add`` event for each path.  A real Git
    history adapter belongs to a later offline automation slice; this helper
    provides the C10 fail-closed/quarantine contract without reading Git.
    """

    seen: set[str] = set()
    issues: list[ContractIssue] = []
    for index, event in enumerate(events):
        path = event.get("path")
        action = event.get("action")
        locator = f"/events/{index}"
        if not isinstance(path, str) or not path:
            issues.append(ContractIssue("BRIDGE_HISTORY_PATH_INVALID", f"{locator}/path", "history path is required"))
            continue
        if action != "add":
            issues.append(
                ContractIssue(
                    "BRIDGE_CREATE_ONLY_VIOLATION",
                    f"{locator}/action",
                    "transport history permits add events only; quarantine the path",
                )
            )
            continue
        if path in seen:
            issues.append(
                ContractIssue(
                    "BRIDGE_READD_QUARANTINE",
                    f"{locator}/path",
                    "a transport path was added more than once; quarantine the path",
                )
            )
        seen.add(path)
    return ContractReport("bridge-append-only-history", tuple(issues))


def _schema_issues(document: Any, schema: Mapping[str, Any], contract: str) -> tuple[ContractIssue, ...]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    issues = [
        ContractIssue(
            "BRIDGE_SCHEMA_INVALID",
            "/" + "/".join(str(part) for part in error.absolute_path),
            error.message,
        )
        for error in validator.iter_errors(document)
    ]
    return tuple(sorted(issues, key=lambda issue: (issue.locator, issue.message)))


def validate_root_sentinel(
    document: Any,
    blueprint: Mapping[str, Any],
    *,
    expected: Mapping[str, str] | None = None,
) -> ContractReport:
    """Validate sentinel shape and optional expected bindings.

    ``expected`` is supplied by a later, user-confirmed deployment preflight;
    C10 tests use it only with synthetic values.  This function never reads
    or writes ``.knowledgeos-root.json``.
    """

    issues = list(_schema_issues(document, build_root_sentinel_schema(blueprint), "root-sentinel"))
    if not issues and expected is not None and isinstance(document, Mapping):
        for field in ("vault_uuid", "canonical_vault_name", "remote_identity_sha256", "expected_branch"):
            if field in expected and document.get(field) != expected[field]:
                issues.append(
                    ContractIssue(
                        "BRIDGE_SENTINEL_BINDING_MISMATCH",
                        f"/{field}",
                        f"sentinel value does not match the expected {field}",
                    )
                )
    return ContractReport("root-sentinel", tuple(issues))


def validate_remote_identity(remote: str, expected_sha256: str) -> ContractReport:
    """Validate a remote's canonical identity against an expected digest."""

    try:
        observed = remote_identity_sha256(remote)
    except ValueError as error:
        return ContractReport(
            "remote-identity",
            (ContractIssue("BRIDGE_REMOTE_INVALID", "/remote", str(error)),),
        )
    if observed != expected_sha256:
        return ContractReport(
            "remote-identity",
            (
                ContractIssue(
                    "BRIDGE_REMOTE_IDENTITY_MISMATCH",
                    "/remote_identity_sha256",
                    "remote identity digest does not match the expected value",
                ),
            ),
        )
    return ContractReport("remote-identity")


def _validate_request_semantics(document: Mapping[str, Any]) -> tuple[ContractIssue, ...]:
    pipeline = document.get("pipeline_kind")
    source = document.get("source")
    target = document.get("target")
    parameters = document.get("parameters")
    issues: list[ContractIssue] = []
    if not isinstance(source, Mapping) or not isinstance(parameters, Mapping):
        return ()
    if pipeline == "triage" and target is not None:
        issues.append(ContractIssue("BRIDGE_ACTION_TARGET_INVALID", "/target", "triage target must be null"))
    if pipeline == "draft_note":
        if not isinstance(target, Mapping) or target.get("expected_sha256") != "":
            issues.append(ContractIssue("BRIDGE_DRAFT_TARGET_INVALID", "/target/expected_sha256", "draft_note target must be create-only"))
        selected = {"selected_candidate_id", "selected_candidate_sha256"} <= set(parameters)
        titled = set(parameters) == {"validated_title"}
        if not selected and not titled:
            issues.append(ContractIssue("BRIDGE_DRAFT_PARAMETERS_INVALID", "/parameters", "draft_note needs candidate ID+digest or validated title"))
    elif pipeline == "summarize" and "bounded_summary_mode" not in parameters:
        issues.append(ContractIssue("BRIDGE_PARAMETERS_MISSING", "/parameters/bounded_summary_mode", "bounded summary mode is required"))
    elif pipeline == "link_suggestions":
        if not isinstance(target, Mapping) or target.get("path") != source.get("path") or target.get("expected_sha256") != source.get("blob_sha256"):
            issues.append(ContractIssue("BRIDGE_LINK_TARGET_INVALID", "/target", "link_suggestions target must equal source path and hash"))
        if "retrieval_profile_id" not in parameters:
            issues.append(ContractIssue("BRIDGE_PARAMETERS_MISSING", "/parameters/retrieval_profile_id", "retrieval profile ID is required"))
    elif pipeline == "answer" and "scope_id" not in parameters:
        issues.append(ContractIssue("BRIDGE_PARAMETERS_MISSING", "/parameters/scope_id", "answer scope ID is required"))
    return tuple(issues)


def validate_bridge_request(document: Any, blueprint: Mapping[str, Any]) -> ContractReport:
    """Validate bridge request schema and action-specific C10 semantics."""

    issues = list(_schema_issues(document, build_bridge_request_schema(blueprint), "bridge-request"))
    if not issues and isinstance(document, Mapping):
        issues.extend(_validate_request_semantics(document))
    return ContractReport("bridge-request", tuple(issues))


def validate_bridge_response(document: Any, blueprint: Mapping[str, Any]) -> ContractReport:
    """Validate one immutable response event against the trusted schema."""

    return ContractReport(
        "bridge-response",
        _schema_issues(document, build_bridge_response_schema(blueprint), "bridge-response"),
    )


def render_fixture_response(document: Mapping[str, Any], blueprint: Mapping[str, Any]) -> bytes:
    """Return canonical response bytes after schema validation, without writing."""

    report = validate_bridge_response(document, blueprint)
    if not report.passed:
        raise ValueError(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return canonical_json_bytes(document)


def write_fixture_response(
    document: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    destination: str | Path,
    fixture_root: str | Path,
) -> Path:
    """Create one response fixture under an explicit fixture root, only once."""

    root = Path(fixture_root).resolve()
    path = Path(destination).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError("fixture renderer destination is outside its fixture root") from error
    if path == root or path.is_symlink():
        raise ValueError("fixture renderer destination must be a regular child path")
    content = render_fixture_response(document, blueprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if path.exists():
            path.unlink()
        raise
    return path


def protocol_copy_paths() -> tuple[str, str]:
    """Return the two Vault protocol copy paths owned by C10."""

    return PROTOCOL_REQUEST_SCHEMA_PATH, PROTOCOL_RESPONSE_SCHEMA_PATH


def bridge_contract_shape() -> dict[str, Sequence[str] | Mapping[str, Sequence[str]]]:
    """Expose a compact immutable-looking snapshot for tests and diagnostics."""

    return {
        "states": BRIDGE_STATES,
        "allowed_transitions": ALLOWED_TRANSITIONS,
        "response_statuses": RESPONSE_STATUSES,
    }

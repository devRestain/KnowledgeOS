"""C41 removable, brokered Obsidian thin-client contract.

The thin client is a presentation boundary only.  It can collect a bounded
question and note/selection scope, ask an authenticated loopback broker for a
proposal, open an exact citation, show a bounded diff, and record an explicit
review decision.  It never calls a provider, writes a Vault note, changes a
plugin profile, or applies a proposal.

The module intentionally keeps the broker as a callable seam.  A future
Obsidian adapter can provide the authenticated loopback transport, while the
plugin-free CLI and Markdown fallback can use the same request and rendering
contracts without installing or operating Obsidian.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .provider_contract import canonical_json_bytes

CAPABILITY = "C41"
OPERATION = "ai thin client"
EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

MAX_QUESTION_BYTES = 8 * 1024
MAX_LOCATOR_BYTES = 512
MAX_SELECTION_BYTES = 16 * 1024
MAX_DIFF_BYTES = 32 * 1024
MAX_FALLBACK_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 128 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PATH_PART = re.compile(r"^[^\x00\r\n\\]+$")
_SCOPE_KINDS = ("current_note", "selection")
_REVIEW_DECISIONS = ("approve", "reject")


class ThinClientError(ValueError):
    """Raised when a C41 client boundary fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ThinClientBroker(Protocol):
    """Authenticated broker seam consumed by the presentation client."""

    def dispatch(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return one bounded proposal-only broker response."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ThinClientError("C41_HASH_INVALID", f"{label} must be a lowercase SHA-256")
    return value


def _validate_uuid(value: object, label: str) -> str:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ThinClientError("C41_UUID_INVALID", f"{label} must be a lowercase UUIDv4")
    return value


def _bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ThinClientError("C41_TEXT_INVALID", f"{label} must be non-empty UTF-8 text")
    if len(value.encode("utf-8")) > maximum:
        raise ThinClientError("C41_TEXT_TOO_LARGE", f"{label} exceeds its byte limit")
    return value


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or _PATH_PART.fullmatch(value) is None:
        raise ThinClientError("C41_PATH_INVALID", f"{label} must be a safe relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ThinClientError("C41_PATH_INVALID", f"{label} must be a safe relative path")
    return path.as_posix()


def _safe_locator(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, label, MAX_LOCATOR_BYTES)


def _validate_loopback_endpoint(value: object) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise ThinClientError("C41_BROKER_ENDPOINT_INVALID", "broker endpoint must be a bounded URL")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        raise ThinClientError(
            "C41_BROKER_ENDPOINT_INVALID",
            "broker endpoint must use authenticated HTTP loopback on 127.0.0.1",
        )
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ThinClientError("C41_BROKER_ENDPOINT_INVALID", "broker endpoint port is invalid")
    if parsed.query or parsed.fragment:
        raise ThinClientError("C41_BROKER_ENDPOINT_INVALID", "broker endpoint cannot contain query or fragment")
    return value


def _normalize_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "kind",
        "path",
        "locator",
        "content_sha256",
        "selection_sha256",
        "selection_byte_length",
    }
    unknown = set(scope) - allowed
    if unknown:
        raise ThinClientError("C41_SCOPE_INVALID", f"scope contains unknown fields: {sorted(unknown)}")
    kind = scope.get("kind")
    if kind not in _SCOPE_KINDS:
        raise ThinClientError("C41_SCOPE_INVALID", "scope kind must be current_note or selection")
    result: dict[str, Any] = {
        "kind": kind,
        "path": _safe_relative_path(scope.get("path"), "scope.path"),
        "locator": _safe_locator(scope.get("locator"), "scope.locator"),
        "content_sha256": _validate_hash(scope.get("content_sha256"), "scope.content_sha256"),
        "selection_sha256": None,
        "selection_byte_length": None,
    }
    if kind == "selection":
        result["selection_sha256"] = _validate_hash(
            scope.get("selection_sha256"), "scope.selection_sha256"
        )
        length = scope.get("selection_byte_length")
        if not isinstance(length, int) or not 1 <= length <= MAX_SELECTION_BYTES:
            raise ThinClientError(
                "C41_SCOPE_INVALID",
                "selection_byte_length must be within the bounded selection limit",
            )
        result["selection_byte_length"] = length
    elif scope.get("selection_sha256") is not None or scope.get("selection_byte_length") is not None:
        raise ThinClientError("C41_SCOPE_INVALID", "current_note scope cannot include selection fields")
    return result


def _unsigned_request(request: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(request)
    value.pop("request_sha256", None)
    broker = dict(value["broker"])
    broker["auth_sha256"] = ""
    value["broker"] = broker
    return value


def _request_digest(request: Mapping[str, Any]) -> str:
    value = dict(request)
    value.pop("request_sha256", None)
    return _digest_json(value)


def _authorization_digest(token: str, request: Mapping[str, Any]) -> str:
    unsigned = _unsigned_request(request)
    return _sha256(f"{token}:{_digest_json(unsigned)}".encode())


def build_thin_client_request(
    *,
    job_id: str,
    question: str,
    scope: Mapping[str, Any],
    broker_endpoint: str,
    authorization_token: str,
    action: str = "answer",
    policy_sha256: str,
    privacy_policy_sha256: str,
    index_generation_id: str,
    request_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one digest-bound request without persisting the auth token."""

    _validate_uuid(job_id, "job_id")
    if request_id is None:
        request_id = str(uuid.uuid4())
    _validate_uuid(request_id, "request_id")
    if (
        not isinstance(authorization_token, str)
        or not authorization_token
        or len(authorization_token.encode("utf-8")) > 256
        or any(character.isspace() for character in authorization_token)
    ):
        raise ThinClientError("C41_AUTH_INVALID", "authorization token must be bounded non-whitespace text")
    if not isinstance(action, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", action):
        raise ThinClientError("C41_ACTION_INVALID", "action must be a bounded lower-case identifier")
    _validate_hash(policy_sha256, "policy_sha256")
    _validate_hash(privacy_policy_sha256, "privacy_policy_sha256")
    if not isinstance(index_generation_id, str) or not index_generation_id or len(index_generation_id) > 128:
        raise ThinClientError("C41_GENERATION_INVALID", "index_generation_id is invalid")

    request: dict[str, Any] = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "operation": OPERATION,
        "job_id": job_id,
        "request_id": request_id,
        "created_at": created_at or _now(),
        "action": action,
        "question": {
            "text": _bounded_text(question, "question", MAX_QUESTION_BYTES),
            "sha256": _sha256(question.encode("utf-8")),
            "byte_length": len(question.encode("utf-8")),
        },
        "scope": _normalize_scope(scope),
        "policy": {
            "policy_sha256": policy_sha256,
            "privacy_policy_sha256": privacy_policy_sha256,
            "index_generation_id": index_generation_id,
        },
        "broker": {
            "transport": "loopback",
            "endpoint": _validate_loopback_endpoint(broker_endpoint),
            "audience": "knowledgeos-broker",
            "auth_sha256": "",
        },
        "proposal_only": True,
        "canonical_apply_allowed": False,
        "request_sha256": None,
    }
    request["broker"]["auth_sha256"] = _authorization_digest(authorization_token, request)
    request["request_sha256"] = _request_digest(request)
    return request


def validate_thin_client_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate request shape, loopback binding, and all digest bindings."""

    if not isinstance(request, Mapping):
        raise ThinClientError("C41_REQUEST_INVALID", "thin-client request must be an object")
    required = {
        "schema_version",
        "capability",
        "operation",
        "job_id",
        "request_id",
        "created_at",
        "action",
        "question",
        "scope",
        "policy",
        "broker",
        "proposal_only",
        "canonical_apply_allowed",
        "request_sha256",
    }
    if set(request) != required:
        raise ThinClientError("C41_REQUEST_INVALID", "thin-client request fields are not exact")
    if request.get("schema_version") != 1 or request.get("capability") != CAPABILITY:
        raise ThinClientError("C41_REQUEST_INVALID", "thin-client schema or capability is invalid")
    if request.get("operation") != OPERATION:
        raise ThinClientError("C41_REQUEST_INVALID", "thin-client operation is invalid")
    _validate_uuid(request.get("job_id"), "job_id")
    _validate_uuid(request.get("request_id"), "request_id")
    if not isinstance(request.get("created_at"), str) or not request["created_at"]:
        raise ThinClientError("C41_REQUEST_INVALID", "created_at is required")
    if not isinstance(request.get("action"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", request["action"]):
        raise ThinClientError("C41_ACTION_INVALID", "action is invalid")

    question = request.get("question")
    if not isinstance(question, Mapping) or set(question) != {"text", "sha256", "byte_length"}:
        raise ThinClientError("C41_QUESTION_INVALID", "question shape is invalid")
    text = _bounded_text(question.get("text"), "question.text", MAX_QUESTION_BYTES)
    _validate_hash(question.get("sha256"), "question.sha256")
    if question["sha256"] != _sha256(text.encode("utf-8")) or question.get("byte_length") != len(text.encode("utf-8")):
        raise ThinClientError("C41_QUESTION_DRIFT", "question digest or byte length differs")

    scope = request.get("scope")
    if not isinstance(scope, Mapping):
        raise ThinClientError("C41_SCOPE_INVALID", "scope must be an object")
    normalized_scope = _normalize_scope(scope)
    if dict(scope) != normalized_scope:
        raise ThinClientError("C41_SCOPE_INVALID", "scope is not canonical")

    policy = request.get("policy")
    if not isinstance(policy, Mapping) or set(policy) != {"policy_sha256", "privacy_policy_sha256", "index_generation_id"}:
        raise ThinClientError("C41_POLICY_INVALID", "policy binding is invalid")
    _validate_hash(policy.get("policy_sha256"), "policy.policy_sha256")
    _validate_hash(policy.get("privacy_policy_sha256"), "policy.privacy_policy_sha256")
    if not isinstance(policy.get("index_generation_id"), str) or not policy["index_generation_id"]:
        raise ThinClientError("C41_POLICY_INVALID", "policy index generation is invalid")

    broker = request.get("broker")
    if not isinstance(broker, Mapping) or set(broker) != {"transport", "endpoint", "audience", "auth_sha256"}:
        raise ThinClientError("C41_BROKER_INVALID", "broker binding is invalid")
    if broker.get("transport") != "loopback" or broker.get("audience") != "knowledgeos-broker":
        raise ThinClientError("C41_BROKER_INVALID", "broker transport or audience is invalid")
    _validate_loopback_endpoint(broker.get("endpoint"))
    _validate_hash(broker.get("auth_sha256"), "broker.auth_sha256")
    if request.get("proposal_only") is not True or request.get("canonical_apply_allowed") is not False:
        raise ThinClientError("C41_AUTHORITY_INVALID", "thin client must remain proposal-only")
    _validate_hash(request.get("request_sha256"), "request_sha256")
    if request["request_sha256"] != _request_digest(request):
        raise ThinClientError("C41_REQUEST_DRIFT", "request digest differs from request bytes")
    return dict(request)


def _broker_response(response: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "capability",
        "request_sha256",
        "brokered",
        "direct_provider_called",
        "content_digest_rechecked",
        "policy_digest_rechecked",
        "index_digest_rechecked",
        "mutation_performed",
        "canonical_apply_allowed",
        "proposal_only",
        "status",
        "result",
    }
    if set(response) != required:
        raise ThinClientError("C41_RESPONSE_INVALID", "broker response fields are not exact")
    if response.get("schema_version") != 1 or response.get("capability") != CAPABILITY:
        raise ThinClientError("C41_RESPONSE_INVALID", "broker response schema is invalid")
    if response.get("request_sha256") != request.get("request_sha256"):
        raise ThinClientError("C41_RESPONSE_BINDING", "broker response is not bound to the client request")
    for field in (
        "brokered",
        "direct_provider_called",
        "content_digest_rechecked",
        "policy_digest_rechecked",
        "index_digest_rechecked",
        "mutation_performed",
        "canonical_apply_allowed",
        "proposal_only",
    ):
        if not isinstance(response.get(field), bool):
            raise ThinClientError("C41_RESPONSE_INVALID", f"broker response flag is invalid: {field}")
    if response["brokered"] is not True or response["direct_provider_called"] is not False:
        raise ThinClientError("C41_BROKER_BOUNDARY", "thin client response did not come from the broker boundary")
    if not all(
        response[field] is True
        for field in ("content_digest_rechecked", "policy_digest_rechecked", "index_digest_rechecked")
    ):
        raise ThinClientError("C41_BROKER_RECHECK_REQUIRED", "broker did not recheck all digest bindings")
    if response["mutation_performed"] is not False or response["canonical_apply_allowed"] is not False:
        raise ThinClientError("C41_AUTHORITY_INVALID", "thin client response grants mutation authority")
    if response["proposal_only"] is not True:
        raise ThinClientError("C41_AUTHORITY_INVALID", "thin client response is not proposal-only")
    if response.get("status") not in {"completed", "refused", "failed", "conflict"}:
        raise ThinClientError("C41_RESPONSE_INVALID", "broker response status is invalid")
    if not isinstance(response.get("result"), Mapping):
        raise ThinClientError("C41_RESPONSE_INVALID", "broker result must be an object")
    try:
        response_bytes = canonical_json_bytes(response)
    except (TypeError, ValueError) as error:
        raise ThinClientError("C41_RESPONSE_INVALID", "broker response is not serializable") from error
    if len(response_bytes) > MAX_RESPONSE_BYTES:
        raise ThinClientError("C41_RESPONSE_TOO_LARGE", "broker response exceeds the bounded byte limit")
    return dict(response)


def dispatch_thin_client_response(
    request: Mapping[str, Any],
    broker: ThinClientBroker | Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    authorization_token: str,
) -> dict[str, Any]:
    """Return the exact C41 response from one authenticated broker dispatch."""

    normalized = validate_thin_client_request(request)
    if not isinstance(authorization_token, str) or not authorization_token:
        raise ThinClientError("C41_AUTH_INVALID", "authorization token is required")
    expected_auth = _authorization_digest(authorization_token, normalized)
    if normalized["broker"]["auth_sha256"] != expected_auth:
        raise ThinClientError("C41_AUTH_INVALID", "authorization token does not match request binding")
    if callable(broker):
        raw_response = broker(normalized)
    else:
        raw_response = broker.dispatch(normalized)
    if not isinstance(raw_response, Mapping):
        raise ThinClientError("C41_RESPONSE_INVALID", "broker returned a non-object response")
    response = _broker_response(raw_response, normalized)
    return response


def dispatch_thin_client(
    request: Mapping[str, Any],
    broker: ThinClientBroker | Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    authorization_token: str,
) -> dict[str, Any]:
    """Dispatch exactly one request through an authenticated broker seam."""

    normalized = validate_thin_client_request(request)
    response = dispatch_thin_client_response(
        normalized,
        broker,
        authorization_token=authorization_token,
    )
    return {
        "status": "PASS",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "job_id": normalized["job_id"],
        "request_sha256": normalized["request_sha256"],
        "response_sha256": _digest_json(response),
        "broker_called": True,
        "direct_provider_called": False,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
        "proposal_only": True,
        "result": response["result"],
    }


def build_citation_open_action(
    citation: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create an exact citation-opening action from a frozen candidate."""

    required = {
        "note_id",
        "path",
        "content_hash",
        "chunk_id",
        "chunk_hash",
        "chunk_locator",
        "index_generation_id",
        "excerpt",
        "excerpt_sha256",
        "excerpt_byte_length",
    }
    if set(citation) != required:
        raise ThinClientError("C41_CITATION_INVALID", "citation fields are not exact")
    path = _safe_relative_path(citation.get("path"), "citation.path")
    for field in ("content_hash", "chunk_hash", "excerpt_sha256"):
        _validate_hash(citation.get(field), f"citation.{field}")
    excerpt = _bounded_text(citation.get("excerpt"), "citation.excerpt", MAX_SELECTION_BYTES)
    if citation["excerpt_sha256"] != _sha256(excerpt.encode("utf-8")):
        raise ThinClientError("C41_CITATION_DRIFT", "citation excerpt digest differs")
    if citation["excerpt_byte_length"] != len(excerpt.encode("utf-8")):
        raise ThinClientError("C41_CITATION_DRIFT", "citation excerpt byte length differs")
    locator = _safe_locator(citation.get("chunk_locator"), "citation.chunk_locator")
    if locator is None:
        raise ThinClientError("C41_CITATION_INVALID", "citation locator is required")
    matching = [
        candidate
        for candidate in candidates
        if all(candidate.get(field) == citation.get(field) for field in ("note_id", "path", "content_hash", "chunk_id", "chunk_hash"))
        and candidate.get("chunk_locator") == locator
        and candidate.get("index_generation_id") == citation.get("index_generation_id")
    ]
    if len(matching) != 1:
        raise ThinClientError("C41_CITATION_DRIFT", "citation is not bound to exactly one frozen candidate")
    if matching[0].get("eligibility_class") not in {"local_eligible", "remote_eligible"}:
        raise ThinClientError("C41_CITATION_PRIVACY", "citation candidate is not privacy eligible")
    target = f"{path}{locator if locator.startswith(('#', '^')) else f'#{locator}'}"
    payload = {
        "action": "open_citation",
        "target": target,
        "path": path,
        "locator": locator,
        "note_id": citation["note_id"],
        "chunk_id": citation["chunk_id"],
        "chunk_hash": citation["chunk_hash"],
        "index_generation_id": citation["index_generation_id"],
        "excerpt": excerpt,
        "excerpt_sha256": citation["excerpt_sha256"],
        "exact_source": True,
        "canonical_apply_allowed": False,
    }
    payload["action_sha256"] = _digest_json(payload)
    return payload


def build_diff_preview(
    path: str,
    before: str,
    after: str,
    *,
    context_lines: int = 3,
) -> dict[str, Any]:
    """Build a bounded, review-only diff without writing either version."""

    safe_path = _safe_relative_path(path, "diff.path")
    before_text = _bounded_text(before, "diff.before", MAX_SELECTION_BYTES)
    after_text = _bounded_text(after, "diff.after", MAX_SELECTION_BYTES)
    if not isinstance(context_lines, int) or not 0 <= context_lines <= 20:
        raise ThinClientError("C41_DIFF_INVALID", "context_lines is outside the bounded range")
    diff = "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{safe_path}",
            tofile=f"b/{safe_path}",
            lineterm="",
            n=context_lines,
        )
    )
    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        raise ThinClientError("C41_DIFF_TOO_LARGE", "diff preview exceeds the bounded byte limit")
    payload = {
        "path": safe_path,
        "before_sha256": _sha256(before_text.encode("utf-8")),
        "after_sha256": _sha256(after_text.encode("utf-8")),
        "diff": diff,
        "diff_sha256": _sha256(diff.encode("utf-8")),
        "bounded": True,
        "review_required": True,
        "canonical_apply_allowed": False,
        "mutation_performed": False,
        "apply_authority": "c19_only",
    }
    return payload


def build_review_decision(
    proposal_sha256: str,
    decision: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record an explicit presentation decision without applying it."""

    _validate_hash(proposal_sha256, "proposal_sha256")
    if decision not in _REVIEW_DECISIONS:
        raise ThinClientError("C41_DECISION_INVALID", "decision must be approve or reject")
    if reason is not None:
        reason = _bounded_text(reason, "decision.reason", 1000)
    payload: dict[str, Any] = {
        "decision": decision,
        "proposal_sha256": proposal_sha256,
        "reason": reason,
        "reviewed_at": _now(),
        "canonical_apply_requested": False,
        "canonical_apply_allowed": False,
        "next_authority": "c19_review_required" if decision == "approve" else "none",
    }
    payload["decision_sha256"] = _digest_json(payload)
    return payload


def render_markdown_fallback(
    request: Mapping[str, Any],
    *,
    result_summary: str | None = None,
    citation_actions: list[Mapping[str, Any]] | None = None,
    diff_preview: Mapping[str, Any] | None = None,
) -> str:
    """Render a plugin-free, review-only Markdown surface."""

    normalized = validate_thin_client_request(request)
    question = normalized["question"]["text"].replace("\x00", " ").strip()
    scope = normalized["scope"]
    lines = [
        "# KnowledgeOS AI review",
        "",
        "> Presentation fallback only. No provider, plugin, Vault, Git, or canonical apply action is performed.",
        "",
        f"- Request: `{normalized['request_sha256']}`",
        f"- Action: `{normalized['action']}`",
        f"- Scope: `{scope['kind']}` — `{scope['path']}`",
        "",
        "## Question",
        "",
        question,
    ]
    if result_summary is not None:
        summary = _bounded_text(result_summary, "result_summary", MAX_SELECTION_BYTES)
        lines.extend(["", "## Broker result", "", summary])
    if citation_actions:
        lines.extend(["", "## Citations", ""])
        for action in citation_actions[:8]:
            if action.get("exact_source") is not True:
                raise ThinClientError("C41_CITATION_INVALID", "fallback citation is not exact")
            lines.append(f"- [{action['path']}#{action['locator']}]({action['target']}) — `{action['chunk_hash']}`")
    if diff_preview is not None:
        if diff_preview.get("bounded") is not True or diff_preview.get("canonical_apply_allowed") is not False:
            raise ThinClientError("C41_DIFF_INVALID", "fallback diff is not review-only")
        lines.extend(["", "## Diff preview", "", "```diff", str(diff_preview.get("diff", "")), "```"])
    lines.extend(["", "## Review", "", "Approve or reject explicitly; approval only hands the proposal to C19 review."])
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > MAX_FALLBACK_BYTES:
        raise ThinClientError("C41_FALLBACK_TOO_LARGE", "Markdown fallback exceeds the bounded byte limit")
    return rendered


def client_contract_report(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return a CLI-safe static report for a validated client request."""

    normalized = validate_thin_client_request(request)
    return {
        "status": "PASS",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "request_sha256": normalized["request_sha256"],
        "scope_kind": normalized["scope"]["kind"],
        "scope_path": normalized["scope"]["path"],
        "loopback_broker": True,
        "direct_provider_called": False,
        "plugin_mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
        "markdown_fallback_available": True,
        "device_evidence": "not_run",
    }


__all__ = [
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "OPERATION",
    "ThinClientBroker",
    "ThinClientError",
    "build_citation_open_action",
    "build_diff_preview",
    "build_review_decision",
    "build_thin_client_request",
    "client_contract_report",
    "dispatch_thin_client",
    "dispatch_thin_client_response",
    "render_markdown_fallback",
    "validate_thin_client_request",
]

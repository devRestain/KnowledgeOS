"""C10: authenticated compact local InterOps intake and policy-bounded keyword exports.

Compact local HTTP profile over the Owner loopback boundary (not a full Core
InterOpsEnvelope wire adapter): each authorized peer requires an explicit owner grant
and a private 0600 32-byte credential file loaded via ``operationkit.interops.load_secret``.
Callers authenticate with an HMAC-SHA256 proof binding method, route and canonical JSON
body; body ``sender_operation_id`` alone never authenticates an Operation.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from operationkit.canonical import canonical, digest
from operationkit.interops import load_secret, sender_grant

from ..notes import UUID4_RE, parse, render_scalar
from ..policy import DEFAULT_POLICY, approved, effective
from ..util import iso, new_id, new_uuid
from ..vault import resource_reference
from . import Done, Refused, WorkDraft

CAPABILITY = "C10"
INTAKE_ROUTE = "/interops/intake"
QUERY_ROUTE = "/interops/query"
SENDER_HEADER = "x-interops-sender"
SIGNATURE_HEADER = "x-interops-signature"
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*")
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
SIGNATURE_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}")
PURPOSES = ("status", "consultation", "domain_task", "experience_submission", "human_control")
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 43200


def register(reg):
    reg.command("gateway.intake", admit_intake)
    # A static Owner effect, not a new autonomous/model role.
    reg.officer("Owner", prepare_capture, finish_capture)
    reg.check("gateway.capture_valid", check_capture)
    reg.route("POST", INTAKE_ROUTE, intake)
    reg.route("POST", QUERY_ROUTE, query)


def error_record(code, message):
    return {"code": code, "message": message[:512], "retry_disposition": "never"}


def _error_status(error: dict[str, Any]) -> int:
    return 400 if error.get("code") == "INVALID_PAYLOAD" else 403


def sign_request(secret: bytes, method: str, route: str, body: dict[str, Any]) -> str:
    """HMAC-SHA256 binding HTTP method, route path and canonical JSON body."""
    payload = canonical({"method": method, "route": route, "body": body})
    return "hmac-sha256:" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _load_peer_secret(grant: Any) -> bytes | None:
    if not isinstance(grant, dict):
        return None
    credential = grant.get("credential_file")
    if not isinstance(credential, str) or not credential or not Path(credential).is_absolute():
        return None
    try:
        return load_secret(Path(credential))
    except (OSError, ValueError):
        return None


def authenticate(
    owner,
    sender_header: Any,
    signature: Any,
    method: str,
    route: str,
    body: dict[str, Any],
) -> tuple[str | None, dict[str, str] | None]:
    """Verify per-sender HMAC before any gateway State write."""
    if not isinstance(sender_header, str) or len(sender_header) > 64 or not IDENTIFIER.fullmatch(sender_header):
        return None, error_record("UNAUTHENTICATED", "Authenticated InterOps sender identity is required")
    if body.get("sender_operation_id") != sender_header:
        return None, error_record("FORBIDDEN", "Authenticated sender does not match sender_operation_id")
    if not isinstance(signature, str) or not SIGNATURE_RE.fullmatch(signature):
        return None, error_record("UNAUTHENTICATED", "Signed InterOps authentication is required")
    operations = owner.setting(CAPABILITY, "authorized_operations", {})
    grant = operations.get(sender_header) if isinstance(operations, dict) else None
    secret = _load_peer_secret(grant)
    if secret is None:
        return None, error_record("UNAUTHENTICATED", "Operation has no valid local InterOps credential")
    expected = sign_request(secret, method, route, body)
    if not hmac.compare_digest(signature, expected):
        return None, error_record("UNAUTHENTICATED", "InterOps request signature is invalid")
    return sender_header, None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40 or not TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _max_ttl(owner) -> int:
    configured = owner.setting(CAPABILITY, "max_ttl_seconds", MAX_TTL_SECONDS)
    if type(configured) is int and 1 <= configured <= MAX_TTL_SECONDS:
        return configured
    return MAX_TTL_SECONDS


def _default_ttl(owner) -> int:
    maximum = _max_ttl(owner)
    configured = owner.setting(CAPABILITY, "default_ttl_seconds", DEFAULT_TTL_SECONDS)
    if type(configured) is int and 1 <= configured <= maximum:
        return configured
    return min(DEFAULT_TTL_SECONDS, maximum)


def check_freshness(owner, body: dict[str, Any]) -> dict[str, str] | None:
    issued = _parse_timestamp(body["issued_at"])
    if issued is None:
        return error_record("INVALID_PAYLOAD", "issued_at must be an ISO-8601 timestamp with offset")
    maximum = _max_ttl(owner)
    ttl = body.get("ttl_seconds", _default_ttl(owner))
    if type(ttl) is not int or not 1 <= ttl <= maximum:
        return error_record("INVALID_PAYLOAD", f"ttl_seconds must be an integer between 1 and {maximum}")
    now = owner.clock.now()
    if issued > now:
        return error_record("INVALID_PAYLOAD", "issued_at cannot be in the future")
    if now >= issued + timedelta(seconds=ttl):
        return error_record("EXPIRED", "InterOps request lifetime has ended")
    return None


def _validate_common_fields(body: dict[str, Any]) -> str | None:
    if "recipient_operation_id" in body:
        recipient = body["recipient_operation_id"]
        if not isinstance(recipient, str) or len(recipient) > 64 or not IDENTIFIER.fullmatch(recipient):
            return "invalid recipient_operation_id"
    if "scope" in body:
        scope = body["scope"]
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 256:
            return "invalid scope"
    if "ttl_seconds" in body:
        ttl = body["ttl_seconds"]
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL_SECONDS:
            return "invalid ttl_seconds"
    if "hop_count" in body:
        hop = body["hop_count"]
        if type(hop) is not int or hop < 0:
            return "invalid hop_count"
    return None


def validate_envelope(body):
    required = {
        "message_id", "sender_operation_id", "idempotency_key",
        "purpose", "issued_at", "payload", "export_policy",
    }
    optional = {"recipient_operation_id", "scope", "ttl_seconds", "hop_count"}
    if not required <= set(body) or set(body) - (required | optional):
        return (
            "intake requires message_id, sender_operation_id, idempotency_key, purpose, "
            "issued_at, payload and export_policy"
        )
    for key in ("message_id", "sender_operation_id"):
        value = body[key]
        maximum = 64 if key == "sender_operation_id" else 96
        if not isinstance(value, str) or len(value) > maximum or not IDENTIFIER.fullmatch(value):
            return f"invalid {key}"
    key = body["idempotency_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
        return "invalid idempotency_key"
    if body["purpose"] not in PURPOSES:
        return "invalid purpose"
    if _parse_timestamp(body["issued_at"]) is None:
        return "issued_at must be an ISO-8601 timestamp with offset"
    common_error = _validate_common_fields(body)
    if common_error:
        return common_error
    payload = body["payload"]
    if (
        not isinstance(payload, dict)
        or not {"title", "text"} <= set(payload)
        or set(payload) - {"title", "text", "capture_kind", "source_url"}
    ):
        return "payload requires title and text; only capture_kind and source_url are optional"
    for field, maximum in (("title", 200), ("text", 16384), ("source_url", 2048)):
        if field in payload and (
            not isinstance(payload[field], str)
            or not payload[field].strip()
            or len(payload[field].encode("utf-8")) > maximum
        ):
            return f"invalid payload {field}"
    if payload.get("capture_kind", "text") not in ("text", "link", "voice", "image"):
        return "invalid capture_kind"
    export = body["export_policy"]
    if (
        not isinstance(export, dict)
        or set(export) != {"git_sync_allowed"}
        or not isinstance(export["git_sync_allowed"], bool)
    ):
        return "export_policy requires boolean git_sync_allowed"
    return None


def validate_query(body):
    required = {"sender_operation_id", "purpose", "query", "issued_at"}
    optional = {
        "request_id", "recipient_operation_id", "scope", "ttl_seconds",
        "hop_count", "max_results", "max_excerpt_chars",
    }
    if not required <= set(body) or set(body) - (required | optional):
        return "Query requires sender_operation_id, purpose, issued_at and bounded nonempty query"
    sender = body.get("sender_operation_id")
    if not isinstance(sender, str) or len(sender) > 64 or not IDENTIFIER.fullmatch(sender):
        return "invalid sender_operation_id"
    if "request_id" in body:
        req_id = body["request_id"]
        if not isinstance(req_id, str) or len(req_id) > 96 or not IDENTIFIER.fullmatch(req_id):
            return "invalid request_id"
    if body.get("purpose") not in PURPOSES:
        return "invalid purpose"
    if not isinstance(body.get("query"), str) or not body["query"].strip() or len(body["query"]) > 1024:
        return "Query requires bounded nonempty query"
    if _parse_timestamp(body.get("issued_at")) is None:
        return "issued_at must be an ISO-8601 timestamp with offset"
    return _validate_common_fields(body)


def request_id(body):
    return "gateway-" + digest([body["sender_operation_id"], body["idempotency_key"]])


def ensure_record(owner, gateway_id, body, kind):
    with owner.store.lock(f"gateway/{gateway_id}"):
        existing = owner.store.get("gateway", gateway_id)
        if existing:
            if existing["digest"] != digest(body):
                from ..owner import Conflict
                raise Conflict(gateway_id)
            return existing
        record = {
            "gateway_id": gateway_id, "kind": kind, "digest": digest(body),
            "received_at": iso(owner.clock.now()), "decisions": [], "error": None,
        }
        owner.store.put("gateway", gateway_id, record)
        return record


def authorization(owner, sender, body, action, *, check_expiry=True):
    if sender == owner.config.operation_id:
        return None, error_record("SCOPE_DENIED", "Cross-Operation request sender must differ from recipient")
    if "recipient_operation_id" in body and body["recipient_operation_id"] != owner.config.operation_id:
        return None, error_record("SCOPE_DENIED", "Recipient operation does not match this Operation")
    if body.get("hop_count", 0) > 1:
        return None, error_record("HOP_LIMIT", "InterOps request exceeds the one-hop bound")
    operations = owner.setting(CAPABILITY, "authorized_operations", {})
    grant, code = sender_grant(operations, sender, action, body["purpose"])
    if code is not None:
        message = (
            f"Operation is not authorized for {action}" if code == "FORBIDDEN" else "Purpose is not authorized"
        )
        return None, error_record(code, message)
    if _load_peer_secret(grant) is None:
        return None, error_record("UNAUTHENTICATED", "Operation has no valid local InterOps credential")
    scope = body.get("scope", action)
    allowed_scopes = grant.get("scopes")
    if allowed_scopes is not None:
        if not isinstance(allowed_scopes, (list, tuple)) or scope not in allowed_scopes:
            return None, error_record("SCOPE_DENIED", "Scope is not authorized")
    elif scope != action:
        return None, error_record("SCOPE_DENIED", "Scope is not authorized")
    if check_expiry:
        freshness_error = check_freshness(owner, body)
        if freshness_error is not None:
            return None, freshness_error
    if action == "intake" and not body["export_policy"]["git_sync_allowed"]:
        return None, error_record("FORBIDDEN", "Export policy forbids storage in a Git-synchronized Vault")
    return grant, None


def decide(owner, gateway_id, sender, body, action, stage, *, check_expiry=True):
    grant, error = authorization(owner, sender, body, action, check_expiry=check_expiry)
    principal = (grant.get("principal_id") if isinstance(grant, dict) else None) or ("operation." + sender)
    decision = {
        "action": "submit" if action == "intake" else "export",
        "decision": "deny" if error else "allow",
        "policy_revision": owner.catalog.version(CAPABILITY),
        "principal_id": principal,
        "purpose": body["purpose"],
        "reason_code": error["code"] if error else "AUTHORIZED",
        "resource_kind": "vault",
        "resource_owner_operation_id": owner.config.operation_id,
        "scope": body.get("scope", action),
        "stage": stage,
        "observed_at": iso(owner.clock.now()),
    }

    def change(record):
        record["decisions"].append(decision)
        record["error"] = error

    owner.store.update("gateway", gateway_id, change)
    return grant, error


def intake(owner, req):
    body = req.body or {}
    invalid = validate_envelope(body)
    if invalid:
        return 400, {"error": error_record("INVALID_PAYLOAD", invalid)}
    sender, auth_error = authenticate(
        owner,
        req.headers.get(SENDER_HEADER),
        req.headers.get(SIGNATURE_HEADER),
        req.method,
        INTAKE_ROUTE,
        body,
    )
    if auth_error or sender is None:
        return 403, {"status": "refused", "error": auth_error}
    gateway_id = request_id(body)
    ensure_record(owner, gateway_id, body, "intake")
    # Replays return the committed receipt rather than repeating a write or policy effect.
    _, error = authorization(owner, sender, body, "intake", check_expiry=True)
    if error:
        decide(owner, gateway_id, sender, body, "intake", "boundary", check_expiry=True)
        return _error_status(error), {"gateway_id": gateway_id, "status": "refused", "error": error}
    receipt, created = owner.submit(
        {
            "request_id": gateway_id,
            "command": "gateway.intake",
            "params": {"envelope": body, "signature": req.headers[SIGNATURE_HEADER]},
        },
        actor="operation/" + sender,
    )
    return (202 if created else 200), receipt


def admit_intake(adm):
    body = adm.params["envelope"]
    signature = adm.params["signature"]
    invalid = validate_envelope(body)
    if invalid:
        raise Refused(invalid)
    if not adm.actor.startswith("operation/"):
        raise Refused("Gateway sender and stable request identity must match the envelope")
    actor_sender = adm.actor.split("/", 1)[1]
    if actor_sender != body["sender_operation_id"] or adm.request_id != request_id(body):
        raise Refused("Gateway sender and stable request identity must match the envelope")
    sender, auth_error = authenticate(adm.owner, actor_sender, signature, "POST", INTAKE_ROUTE, body)
    if auth_error or sender is None:
        return Done(status="refused", outcome={"error": auth_error}, notice=auth_error["message"])
    ensure_record(adm.owner, adm.request_id, body, "intake")
    _, error = decide(adm.owner, adm.request_id, sender, body, "intake", "admission", check_expiry=True)
    if error:
        return Done(status="refused", outcome={"error": error}, notice=error["message"])
    return WorkDraft(mode="intake", params={"envelope": body, "signature": signature})


def prepare_capture(ctx):
    body = ctx.params["envelope"]
    payload = body["payload"]
    received = ctx.owner.store.get("gateway", ctx.work["request_id"])["received_at"]
    fields = {
        "id": new_uuid(), "type": "capture", "status": "unprocessed", "created": received,
        "ai_policy": DEFAULT_POLICY, "title": payload["title"],
        "captured_from": "operation/" + body["sender_operation_id"],
        "capture_kind": payload.get("capture_kind", "text"),
        "kos_provenance": body["message_id"], "kos_provenance_sender": body["sender_operation_id"],
        "kos_provenance_received_at": received,
    }
    if "source_url" in payload:
        fields["source_url"] = payload["source_url"]
    text = (
        "---\n"
        + "".join(f"{key}: {render_scalar(value)}\n" for key, value in fields.items())
        + "---\n"
        + payload["text"]
    )
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", payload["title"]).strip(" .")[:100] or "Capture"
    return {"path": f"00_Inbox/{title}.md", "blob": ctx.put_blob(text.encode("utf-8"))}


def check_capture(ctx, artifact):
    note = parse(artifact["path"], ctx.blob(artifact["blob"]))
    body = ctx.params["envelope"]
    ok = (
        note.valid and bool(UUID4_RE.fullmatch(note.data.get("id", ""))) and note.type == "capture"
        and note.status == "unprocessed" and note.body == body["payload"]["text"]
        and note.data.get("captured_from") == "operation/" + body["sender_operation_id"]
        and note.data.get("ai_policy") == DEFAULT_POLICY
    )
    return [{
        "target": None,
        "verdict": "satisfied" if ok else "unmet",
        "reason": "Capture preserves intake and default policy",
    }]


def finish_capture(ctx, artifact):
    body = ctx.params["envelope"]
    signature = ctx.params.get("signature")
    sender, auth_error = authenticate(
        ctx.owner, body["sender_operation_id"], signature, "POST", INTAKE_ROUTE, body
    )
    if auth_error or sender is None:
        return Done(status="refused", outcome={"error": auth_error}, notice=auth_error["message"])
    _, error = decide(
        ctx.owner, ctx.work["request_id"], sender, body, "intake", "effect", check_expiry=False
    )
    if error:
        return Done(status="refused", outcome={"error": error}, notice=error["message"])
    path = ctx.create(artifact["path"], ctx.blob(artifact["blob"]))
    ctx.owner.store.update(
        "gateway", ctx.work["request_id"], lambda record: record.update(path=path, work_id=ctx.work_id)
    )
    return Done(outcome={"path": path}, notice="Captured submission in " + path)


def query(owner, req):
    body = req.body or {}
    invalid = validate_query(body)
    if invalid:
        return 400, {"error": error_record("INVALID_PAYLOAD", invalid)}
    sender, auth_error = authenticate(
        owner,
        req.headers.get(SENDER_HEADER),
        req.headers.get(SIGNATURE_HEADER),
        req.method,
        QUERY_ROUTE,
        body,
    )
    if auth_error or sender is None:
        return 403, {"error": auth_error}
    gateway_id = new_id("gateway-query")
    correlation_id = body.get("request_id") or gateway_id
    ensure_record(owner, gateway_id, body, "query")
    grant, error = decide(owner, gateway_id, sender, body, "query", "admission", check_expiry=True)
    if error:
        return _error_status(error), {
            "gateway_id": gateway_id, "correlation_id": correlation_id, "error": error,
        }
    limits = {}
    for key, hard_maximum in (("max_results", 100), ("max_excerpt_chars", 160)):
        maximum = grant.get(key)
        requested = body.get(key, maximum)
        if type(maximum) is not int or maximum < 1 or type(requested) is not int or requested < 1:
            return 400, {
                "error": error_record("INVALID_PAYLOAD", "Positive integer result and excerpt limits are required")
            }
        limits[key] = min(requested, maximum, hard_maximum)
    admitted = {}

    def exportable(doc):
        if not approved(doc.policy):
            return False
        admitted[doc.path] = doc.sha
        return True

    found = owner.index.keyword(body["query"], audience="role", allow=exportable, limit=limits["max_results"])
    _, error = decide(owner, gateway_id, sender, body, "query", "effect", check_expiry=False)
    if error:
        return _error_status(error), {
            "gateway_id": gateway_id, "correlation_id": correlation_id, "error": error,
        }
    results = []
    for item in found["results"]:
        current = owner.vault.read(item["path"])
        if (
            current is None
            or current.sha != admitted.get(item["path"])
            or current.conflict
            or current.type in ("report", "proposal")
            or not approved(effective(current))
        ):
            continue
        lines = current.text.splitlines()
        # A fresh policy check must not disclose stale indexed bytes after an edit.
        if current.title != item["title"] or not 0 < item["line"] <= len(lines):
            continue
        excerpt = lines[item["line"] - 1].strip()[:limits["max_excerpt_chars"]]
        results.append({
            "title": current.title,
            "path": item["path"],
            "line": item["line"],
            "locator": {"path": item["path"], "line": item["line"]},
            "excerpt": excerpt,
            "resource_reference": resource_reference(item["path"], current, effective(current)),
        })
    scope = body.get("scope", "query")
    owner.store.update(
        "gateway",
        gateway_id,
        lambda record: record.update(correlation_id=correlation_id, result_count=len(results), limits=limits),
    )
    return {
        "status": "ok",
        "gateway_id": gateway_id,
        "correlation_id": correlation_id,
        "recipient_operation_id": owner.config.operation_id,
        "purpose": body["purpose"],
        "scope": scope,
        "results": results,
    }

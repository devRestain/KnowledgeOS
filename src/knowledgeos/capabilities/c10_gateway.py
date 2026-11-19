"""C10: authorized local InterOps intake and policy-bounded keyword exports."""
from __future__ import annotations

import re
from datetime import datetime

from ..notes import UUID4_RE, parse, render_scalar
from ..policy import effective, gateway_exportable
from ..util import digest, iso, new_id, new_uuid
from . import Done, Refused, WorkDraft

CAPABILITY = "C10"
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*")
PURPOSES = ("status", "consultation", "domain_task", "experience_submission", "human_control")


def register(reg):
    reg.command("gateway.intake", admit_intake)
    # A static Owner effect, not a new autonomous/model role.
    reg.officer("Owner", prepare_capture, finish_capture)
    reg.check("gateway.capture_valid", check_capture)
    reg.route("POST", "/interops/intake", intake)
    reg.route("POST", "/interops/query", query)


def error_record(code, message):
    return {"code": code, "message": message[:512], "retry_disposition": "never"}


def validate_envelope(body):
    required = {"message_id", "sender_operation_id", "idempotency_key", "purpose", "issued_at", "payload", "export_policy"}
    if set(body) != required:
        return "intake requires exactly message_id, sender_operation_id, idempotency_key, purpose, issued_at, payload and export_policy"
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
    issued = body["issued_at"]
    if not isinstance(issued, str) or len(issued) > 40 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", issued):
        return "issued_at must be an ISO-8601 timestamp with offset"
    try:
        datetime.fromisoformat(issued)
    except ValueError:
        return "invalid issued_at"
    payload = body["payload"]
    if not isinstance(payload, dict) or not {"title", "text"} <= set(payload) or set(payload) - {"title", "text", "capture_kind", "source_url"}:
        return "payload requires title and text; only capture_kind and source_url are optional"
    for field, maximum in (("title", 200), ("text", 16384), ("source_url", 2048)):
        if field in payload and (not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field].encode("utf-8")) > maximum):
            return f"invalid payload {field}"
    if payload.get("capture_kind", "text") not in ("text", "link", "voice", "image"):
        return "invalid capture_kind"
    export = body["export_policy"]
    if not isinstance(export, dict) or set(export) != {"git_sync_allowed"} or not isinstance(export["git_sync_allowed"], bool):
        return "export_policy requires boolean git_sync_allowed"
    return None


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
        record = {"gateway_id": gateway_id, "kind": kind, "digest": digest(body),
                  "received_at": iso(owner.clock.now()), "decisions": [], "error": None}
        owner.store.put("gateway", gateway_id, record)
        return record


def authorization(owner, body, action):
    operations = owner.setting(CAPABILITY, "authorized_operations", {})
    grant = operations.get(body["sender_operation_id"]) if isinstance(operations, dict) else None
    if not isinstance(grant, dict) or grant.get(action) is not True:
        return None, error_record("FORBIDDEN", f"Operation is not authorized for {action}")
    if body["purpose"] not in grant.get("purposes", []):
        return None, error_record("SCOPE_DENIED", "Purpose is not authorized")
    if action == "intake" and not body["export_policy"]["git_sync_allowed"]:
        return None, error_record("FORBIDDEN", "Export policy forbids storage in a Git-synchronized Vault")
    return grant, None


def decide(owner, gateway_id, body, action, stage):
    grant, error = authorization(owner, body, action)
    decision = {"action": "submit" if action == "intake" else "export", "decision": "deny" if error else "allow",
                "policy_revision": owner.catalog.version(CAPABILITY),
                "principal_id": "operation/" + body["sender_operation_id"], "purpose": body["purpose"],
                "reason_code": error["code"] if error else "AUTHORIZED", "resource_kind": "vault",
                "resource_owner_operation_id": owner.config.operation_id, "scope": action,
                "stage": stage, "observed_at": iso(owner.clock.now())}
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
    gateway_id = request_id(body)
    ensure_record(owner, gateway_id, body, "intake")
    # Replays return the committed receipt rather than repeating a write or policy effect.
    _, error = authorization(owner, body, "intake")
    if error:
        decide(owner, gateway_id, body, "intake", "boundary")
        return 403, {"gateway_id": gateway_id, "status": "refused", "error": error}
    receipt, created = owner.submit({"request_id": gateway_id, "command": "gateway.intake", "params": {"envelope": body}},
                                    actor="operation/" + body["sender_operation_id"])
    return (202 if created else 200), receipt


def admit_intake(adm):
    body = adm.params["envelope"]
    invalid = validate_envelope(body)
    if invalid:
        raise Refused(invalid)
    if adm.actor != "operation/" + body["sender_operation_id"] or adm.request_id != request_id(body):
        raise Refused("Gateway sender and stable request identity must match the envelope")
    ensure_record(adm.owner, adm.request_id, body, "intake")
    _, error = decide(adm.owner, adm.request_id, body, "intake", "admission")
    if error:
        return Done(status="refused", outcome={"error": error}, notice=error["message"])
    return WorkDraft(mode="intake", params={"envelope": body})


def prepare_capture(ctx):
    body = ctx.params["envelope"]
    payload = body["payload"]
    received = ctx.owner.store.get("gateway", ctx.work["request_id"])["received_at"]
    fields = {"id": new_uuid(), "type": "capture", "status": "unprocessed", "created": received,
              "ai_policy": ctx.owner.config.default_policy, "title": payload["title"],
              "captured_from": "operation/" + body["sender_operation_id"], "capture_kind": payload.get("capture_kind", "text"),
              "kos_provenance": body["message_id"], "kos_provenance_sender": body["sender_operation_id"],
              "kos_provenance_received_at": received}
    if "source_url" in payload:
        fields["source_url"] = payload["source_url"]
    text = "---\n" + "".join(f"{key}: {render_scalar(value)}\n" for key, value in fields.items()) + "---\n" + payload["text"]
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", payload["title"]).strip(" .")[:100] or "Capture"
    return {"path": f"00_Inbox/{title}.md", "blob": ctx.put_blob(text.encode("utf-8"))}


def check_capture(ctx, artifact):
    note = parse(artifact["path"], ctx.blob(artifact["blob"]))
    body = ctx.params["envelope"]
    ok = (note.valid and bool(UUID4_RE.fullmatch(note.data.get("id", ""))) and note.type == "capture"
          and note.status == "unprocessed" and note.body == body["payload"]["text"]
          and note.data.get("captured_from") == "operation/" + body["sender_operation_id"]
          and note.data.get("ai_policy") == ctx.owner.config.default_policy)
    return [{"target": None, "verdict": "satisfied" if ok else "unmet", "reason": "Capture preserves intake and default policy"}]


def finish_capture(ctx, artifact):
    _, error = decide(ctx.owner, ctx.work["request_id"], ctx.params["envelope"], "intake", "effect")
    if error:
        return Done(status="refused", outcome={"error": error}, notice=error["message"])
    path = ctx.create(artifact["path"], ctx.blob(artifact["blob"]))
    ctx.owner.store.update("gateway", ctx.work["request_id"], lambda record: record.update(path=path, work_id=ctx.work_id))
    return Done(outcome={"path": path}, notice="Captured submission in " + path)


def query(owner, req):
    body = req.body or {}
    if (set(body) - {"sender_operation_id", "purpose", "query", "max_results", "max_excerpt_chars"}
            or not isinstance(body.get("sender_operation_id"), str) or not IDENTIFIER.fullmatch(body["sender_operation_id"])
            or body.get("purpose") not in PURPOSES or not isinstance(body.get("query"), str)
            or not body["query"].strip() or len(body["query"]) > 1024):
        return 400, {"error": error_record("INVALID_PAYLOAD", "Query requires sender_operation_id, purpose and bounded nonempty query")}
    gateway_id = new_id("gateway-query")
    ensure_record(owner, gateway_id, body, "query")
    grant, error = decide(owner, gateway_id, body, "query", "admission")
    if error:
        return 403, {"gateway_id": gateway_id, "error": error}
    limits = {}
    for key, hard_maximum in (("max_results", 100), ("max_excerpt_chars", 160)):
        maximum = grant.get(key)
        requested = body.get(key, maximum)
        if (type(maximum) is not int or maximum < 1 or type(requested) is not int or requested < 1):
            return 400, {"error": error_record("INVALID_PAYLOAD", "Positive integer result and excerpt limits are required")}
        limits[key] = min(requested, maximum, hard_maximum)
    admitted = {}
    def exportable(doc):
        if not gateway_exportable(doc.policy):
            return False
        admitted[doc.path] = doc.sha
        return True
    found = owner.index.keyword(body["query"], audience="role", allow=exportable, limit=limits["max_results"])
    _, error = decide(owner, gateway_id, body, "query", "effect")
    if error:
        return 403, {"gateway_id": gateway_id, "error": error}
    results = []
    for item in found["results"]:
        current = owner.vault.read(item["path"])
        if (current is None or current.sha != admitted.get(item["path"]) or current.conflict or current.type in ("report", "proposal")
                or not gateway_exportable(effective(current, owner.config.default_policy))):
            continue
        lines = current.text.splitlines()
        # A fresh policy check must not disclose stale indexed bytes after an edit.
        if current.title != item["title"] or not 0 < item["line"] <= len(lines):
            continue
        excerpt = lines[item["line"] - 1].strip()[:limits["max_excerpt_chars"]]
        results.append({"title": current.title, "path": item["path"], "line": item["line"],
                        "locator": {"path": item["path"], "line": item["line"]}, "excerpt": excerpt})
    owner.store.update("gateway", gateway_id, lambda record: record.update(result_count=len(results), limits=limits))
    return {"status": "ok", "gateway_id": gateway_id, "results": results}

"""C10 acceptance through the authenticated loopback InterOps boundary and persisted State."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import core_validate

from knowledgeos.capabilities.c10_gateway import (
    INTAKE_ROUTE,
    QUERY_ROUTE,
    request_id,
    sign_request,
)
from knowledgeos.notes import UUID4_RE, parse
from knowledgeos.util import iso

RESEARCH_SECRET = b"r" * 32


def write_secret(tmp_path, name="research.key", secret=RESEARCH_SECRET, mode=0o600):
    path = tmp_path / name
    path.write_bytes(secret)
    path.chmod(mode)
    return path


def gateway(make_kos, tmp_path, *, secret=RESEARCH_SECRET, **grant):
    key = write_secret(tmp_path, secret=secret)
    return make_kos(settings={"C10": {"authorized_operations": {"research": {
        "credential_file": str(key),
        "intake": True,
        "query": True,
        "purposes": ["domain_task", "consultation"],
        "max_results": 3,
        "max_excerpt_chars": 50,
        **grant,
    }}}})


def signed_post(kos, route, body, secret=RESEARCH_SECRET, *, sender=None, signature=None):
    headers = {"Content-Type": "application/json"}
    if sender is not False:
        headers["X-InterOps-Sender"] = body["sender_operation_id"] if sender is None else sender
    if signature is not False:
        headers["X-InterOps-Signature"] = (
            sign_request(secret, "POST", route, body) if signature is None else signature
        )
    status, payload, _ = kos.raw("POST", route, json.dumps(body).encode("utf-8"), headers)
    return status, payload


def envelope(clock, **overrides):
    return {
        "message_id": "submission.one",
        "sender_operation_id": "research",
        "idempotency_key": "capture:1",
        "purpose": "domain_task",
        "issued_at": iso(clock.now()),
        "payload": {"title": "Captured finding", "text": "A finding worth keeping.\n", "capture_kind": "text"},
        "export_policy": {"git_sync_allowed": True},
        **overrides,
    }


def query_body(clock, **overrides):
    return {
        "sender_operation_id": "research",
        "purpose": "consultation",
        "query": "Sharedneedle",
        "issued_at": iso(clock.now()),
        **overrides,
    }


def test_GW_1_intake_commits_one_capture_and_replays_receipt(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    body = envelope(clock)
    status, received = signed_post(kos, INTAKE_ROUTE, body)
    assert status == 202
    done = kos.wait(received["request_id"])
    assert done["status"] == "completed", done
    status, repeated = signed_post(kos, INTAKE_ROUTE, body)
    assert status == 200 and repeated == done
    files = list(vault.root.rglob("*.md"))
    assert len(files) == 1
    path = done["outcome"]["path"]
    assert path.startswith("00_Inbox/")
    note = parse(path, vault.read(path))
    assert UUID4_RE.fullmatch(note.data["id"])
    assert note.type == "capture" and note.status == "unprocessed"
    assert note.data["ai_policy"] == "approve"
    assert note.data["created"] == iso(clock.now())
    assert note.data["captured_from"] == "operation/research"
    assert note.data["capture_kind"] == "text"
    assert note.data["kos_provenance"] == body["message_id"]
    assert note.data["kos_provenance_sender"] == "research"
    assert note.data["kos_provenance_received_at"] == iso(clock.now())
    assert note.body == body["payload"]["text"]
    work = kos.get("/api/works/" + done["work_id"])[1]
    assert work["workspec"]["origin"] == "gateway" and work["actor"] == "operation/research"
    record = json.loads((tmp_path / "state/gateway" / f"{received['request_id']}.json").read_text())
    assert [(d["stage"], d["decision"]) for d in record["decisions"]] == [("admission", "allow"), ("effect", "allow")]
    assert (tmp_path / "state/requests" / f"{received['request_id']}.json").is_file()
    # Captures participate in ordinary C2 without a Gateway-only standardization lane.
    preview = kos.run("standardize.preview", {"paths": [path], "digests": {path: vault.sha(path)}})
    assert preview["status"] == "completed"


def test_GW_1_parallel_duplicate_and_changed_content(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    body = envelope(clock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: signed_post(kos, INTAKE_ROUTE, body), range(2)))
    assert sorted(status for status, _ in replies) == [200, 202]
    assert replies[0][1]["request_id"] == replies[1][1]["request_id"]
    assert kos.wait(replies[0][1]["request_id"])["status"] == "completed"
    changed = {**body, "payload": {**body["payload"], "text": "Different text"}}
    assert signed_post(kos, INTAKE_ROUTE, changed)[0] == 409
    assert len(list(vault.root.rglob("*.md"))) == 1


def test_GW_1_git_sync_forbidden_retains_explicit_error(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    before = vault.snapshot()
    status, refusal = signed_post(
        kos, INTAKE_ROUTE, envelope(clock, export_policy={"git_sync_allowed": False})
    )
    assert status == 403 and refusal["status"] == "refused"
    error = refusal["error"]
    assert set(error) == {"code", "message", "retry_disposition"}
    assert error["code"] == "FORBIDDEN" and error["retry_disposition"] == "never"
    assert "Git" in error["message"]
    saved = json.loads((tmp_path / "state/gateway" / f"{refusal['gateway_id']}.json").read_text())
    assert saved["error"] == error
    assert saved["decisions"][-1]["decision"] == "deny"
    assert vault.snapshot() == before


def test_GW_1_unauthorized_sender_and_purpose_are_refused(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    for overrides in ({"sender_operation_id": "intruder"}, {"purpose": "status"}):
        status, refusal = signed_post(kos, INTAKE_ROUTE, envelope(clock, **overrides))
        assert status == 403 and refusal["error"]["code"] in ("UNAUTHENTICATED", "FORBIDDEN", "SCOPE_DENIED")
    assert not vault.snapshot()


@pytest.mark.parametrize("change", [
    {"issued_at": "yesterday"},
    {"message_id": "../bad"},
    {"idempotency_key": ""},
    {"payload": {"title": "Missing body"}},
    {"export_policy": {"git_sync_allowed": "false"}},
])
def test_GW_1_malformed_envelope_is_rejected(make_kos, clock, tmp_path, change):
    kos = gateway(make_kos, tmp_path)
    status, refusal = signed_post(kos, INTAKE_ROUTE, envelope(clock, **change))
    assert status == 400 and refusal["error"]["code"] == "INVALID_PAYLOAD"


def test_GW_2_query_exports_only_approve_sources(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    for name, policy, note_type, extra in [
        ("Shared", "approve", "idea", ""), ("Ask", "ask", "idea", ""),
        ("Local", "local_only", "idea", ""), ("Denied", "deny", "idea", ""),
        ("Secret", "approve", "idea", "sensitivity: confidential\n"),
        ("Report", "approve", "report", ""), ("Proposal", "approve", "proposal", ""),
    ]:
        vault.write(
            f"Notes/{name}.md",
            f"---\ntitle: {name}\ntype: {note_type}\nai_policy: {policy}\n{extra}---\nSharedneedle evidence.\n",
        )
    status, found = signed_post(kos, QUERY_ROUTE, query_body(clock, request_id="query.shared.1"))
    assert status == 200
    assert found["correlation_id"] == "query.shared.1"
    assert found["recipient_operation_id"] == "knowledgeos"
    assert found["purpose"] == "consultation" and found["scope"] == "query"
    assert {item["title"] for item in found["results"]} == {"Shared", "Secret"}
    for item in found["results"]:
        assert item["locator"] == {"path": item["path"], "line": item["line"]}
        assert vault.text(item["path"]).splitlines()[item["line"] - 1] == item["excerpt"]
        core_validate("ResourceReference", item["resource_reference"])
        assert item["resource_reference"]["content_digest"] == f"sha256:{vault.sha(item['path'])}"
    saved = json.loads((tmp_path / "state/gateway" / f"{found['gateway_id']}.json").read_text())
    assert saved["result_count"] == 2 and saved["correlation_id"] == "query.shared.1"
    assert [d["stage"] for d in saved["decisions"]] == ["admission", "effect"]


def test_GW_2_bounds_and_purpose_are_enforced(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path, max_results=2, max_excerpt_chars=12)
    for index in range(5):
        vault.write(f"Notes/{index}.md", "---\nai_policy: approve\n---\nBoundneedle " + "x" * 300)
    body = query_body(clock, query="Boundneedle", max_results=99, max_excerpt_chars=1000)
    status, found = signed_post(kos, QUERY_ROUTE, body)
    assert status == 200 and len(found["results"]) == 2
    assert all(len(item["excerpt"]) <= 12 for item in found["results"])
    smaller = signed_post(kos, QUERY_ROUTE, {**body, "max_results": 1, "max_excerpt_chars": 4})[1]
    assert len(smaller["results"]) == 1 and len(smaller["results"][0]["excerpt"]) <= 4
    for override in ({"purpose": "status"}, {"sender_operation_id": "intruder"}):
        status, refusal = signed_post(kos, QUERY_ROUTE, {**body, **override})
        assert status == 403 and "results" not in refusal
    for override in ({"max_results": True}, {"max_excerpt_chars": 0}):
        assert signed_post(kos, QUERY_ROUTE, {**body, **override})[0] == 400


def test_GW_1_default_deny_and_foundation_role_grants_no_access(kos, vault, clock, tmp_path):
    vault.write("Notes/Visible.md", "---\nai_policy: approve\n---\nSharedneedle\n")
    for sender in ("research", "executiveoffice", "hermestrace"):
        body = envelope(clock, sender_operation_id=sender)
        status, refusal = signed_post(kos, INTAKE_ROUTE, body)
        assert status == 403 and refusal["error"]["code"] == "UNAUTHENTICATED"
        qbody = query_body(clock, sender_operation_id=sender)
        status, refusal = signed_post(kos, QUERY_ROUTE, qbody)
        assert status == 403 and "results" not in refusal
    assert not (tmp_path / "state/gateway").exists()


def test_GW_1_authentication_rejects_forgery_without_idempotency_poisoning(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    valid_body = envelope(clock)
    forged_body = envelope(clock, payload={"title": "Poison", "text": "Attacker text.\n"})
    wrong_route_sig = sign_request(RESEARCH_SECRET, "POST", QUERY_ROUTE, forged_body)

    # Unsigned body, wrong signature, mismatched sender header and cross-route signature all fail before write.
    for headers in (
        {"sender": False, "signature": False},
        {"signature": "hmac-sha256:" + "0" * 64},
        {"sender": "other"},
        {"signature": wrong_route_sig},
    ):
        status, refusal = signed_post(kos, INTAKE_ROUTE, forged_body, **headers)
        assert status == 403 and refusal["error"]["code"] in ("UNAUTHENTICATED", "FORBIDDEN")

    gateway_dir = tmp_path / "state/gateway"
    assert not gateway_dir.exists() or not list(gateway_dir.glob("*.json"))
    assert not list(vault.root.rglob("*.md"))

    # A valid signed intake with the same idempotency_key still succeeds afterward.
    status, received = signed_post(kos, INTAKE_ROUTE, valid_body)
    assert status == 202
    assert kos.wait(received["request_id"])["status"] == "completed"


def test_GW_1_direct_api_requests_cannot_bypass_intake_authentication(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    valid_body = envelope(clock)
    gid = request_id(valid_body)
    forged_body = envelope(clock, payload={"title": "Bypass", "text": "Forged via /api/requests.\n"})
    bad_sig = sign_request(RESEARCH_SECRET, "POST", QUERY_ROUTE, forged_body)

    # Generic POST /api/requests cannot invoke gateway.intake or poison its request_id.
    for params in ({"envelope": forged_body}, {"envelope": forged_body, "signature": bad_sig}):
        status, payload = kos.post(
            "/api/requests",
            {"request_id": gid, "command": "gateway.intake", "actor": "operation/research", "params": params},
        )
        assert status == 403 and "gateway.intake" in payload["error"]
    assert not (tmp_path / "state/requests" / f"{gid}.json").exists()

    # Even in-process admission re-verifies the route-bound signature before writing gateway State.
    direct_body = envelope(clock, idempotency_key="capture:inprocess",
                           payload={"title": "Direct", "text": "Forged in-process.\n"})
    direct_gid = request_id(direct_body)
    direct_bad_sig = sign_request(RESEARCH_SECRET, "POST", QUERY_ROUTE, direct_body)
    receipt, created = kos.owner.submit(
        {
            "request_id": direct_gid,
            "command": "gateway.intake",
            "params": {"envelope": direct_body, "signature": direct_bad_sig},
        },
        actor="operation/research",
    )
    assert created is True
    settled = kos.wait(receipt["request_id"])
    assert settled["status"] == "refused"
    assert settled["outcome"]["error"]["code"] == "UNAUTHENTICATED"
    assert not (tmp_path / "state/gateway" / f"{direct_gid}.json").exists()
    assert not list(vault.root.rglob("*.md"))

    # The unpoisoned legitimate intake still succeeds afterward.
    status, received = signed_post(kos, INTAKE_ROUTE, valid_body)
    assert status == 202
    assert kos.wait(received["request_id"])["status"] == "completed"


def test_GW_2_expiry_recipient_scope_hop_limit_and_correlation(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos, tmp_path)
    vault.write("Notes/Shared.md", "---\nai_policy: approve\n---\nSharedneedle evidence.\n")

    expired = query_body(clock, issued_at=iso(clock.now() - timedelta(seconds=600)), ttl_seconds=300)
    status, refusal = signed_post(kos, QUERY_ROUTE, expired)
    assert status == 403 and refusal["error"]["code"] == "EXPIRED"

    future = query_body(clock, issued_at=iso(clock.now() + timedelta(seconds=60)))
    status, refusal = signed_post(kos, QUERY_ROUTE, future)
    assert status == 400 and refusal["error"]["code"] == "INVALID_PAYLOAD"

    wrong_recipient = query_body(clock, recipient_operation_id="other_op")
    status, refusal = signed_post(kos, QUERY_ROUTE, wrong_recipient)
    assert status == 403 and refusal["error"]["code"] == "SCOPE_DENIED"

    wrong_scope = query_body(clock, scope="intake")
    status, refusal = signed_post(kos, QUERY_ROUTE, wrong_scope)
    assert status == 403 and refusal["error"]["code"] == "SCOPE_DENIED"

    over_hop = query_body(clock, hop_count=2)
    status, refusal = signed_post(kos, QUERY_ROUTE, over_hop)
    assert status == 403 and refusal["error"]["code"] == "HOP_LIMIT"

    bad_corr = query_body(clock, correlation_id="req.1")
    status, refusal = signed_post(kos, QUERY_ROUTE, bad_corr)
    assert status == 400 and refusal["error"]["code"] == "INVALID_PAYLOAD"

    expired_intake = envelope(clock, issued_at=iso(clock.now() - timedelta(seconds=600)), ttl_seconds=300)
    status, refusal = signed_post(kos, INTAKE_ROUTE, expired_intake)
    assert status == 403 and refusal["error"]["code"] == "EXPIRED"

    # Non-0600 credential file fails closed before any export.
    (tmp_path / "research.key").chmod(0o644)
    status, refusal = signed_post(kos, QUERY_ROUTE, query_body(clock))
    assert status == 403 and refusal["error"]["code"] == "UNAUTHENTICATED"

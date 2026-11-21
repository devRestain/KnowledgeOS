"""C10 acceptance through the loopback InterOps boundary and persisted State."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from knowledgeos.notes import UUID4_RE, parse
from knowledgeos.util import iso


def gateway(make_kos, **grant):
    return make_kos(settings={"C10": {"authorized_operations": {"research": {
        "intake": True, "query": True, "purposes": ["domain_task", "consultation"],
        "max_results": 3, "max_excerpt_chars": 50, **grant}}}})


def envelope(clock, **overrides):
    return {"message_id": "submission.one", "sender_operation_id": "research", "idempotency_key": "capture:1",
            "purpose": "domain_task", "issued_at": iso(clock.now()),
            "payload": {"title": "Captured finding", "text": "A finding worth keeping.\n", "capture_kind": "text"},
            "export_policy": {"git_sync_allowed": True}, **overrides}


def test_GW_1_intake_commits_one_capture_and_replays_receipt(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos)
    body = envelope(clock)
    status, received = kos.post("/interops/intake", body)
    assert status == 202
    done = kos.wait(received["request_id"])
    assert done["status"] == "completed", done
    status, repeated = kos.post("/interops/intake", body)
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


def test_GW_1_parallel_duplicate_and_changed_content(make_kos, vault, clock):
    kos = gateway(make_kos)
    body = envelope(clock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: kos.post("/interops/intake", body), range(2)))
    assert sorted(status for status, _ in replies) == [200, 202]
    assert replies[0][1]["request_id"] == replies[1][1]["request_id"]
    assert kos.wait(replies[0][1]["request_id"])["status"] == "completed"
    changed = {**body, "payload": {**body["payload"], "text": "Different text"}}
    assert kos.post("/interops/intake", changed)[0] == 409
    assert len(list(vault.root.rglob("*.md"))) == 1


def test_GW_1_git_sync_forbidden_retains_explicit_error(make_kos, vault, clock, tmp_path):
    kos = gateway(make_kos)
    before = vault.snapshot()
    status, refusal = kos.post("/interops/intake", envelope(clock, export_policy={"git_sync_allowed": False}))
    assert status == 403 and refusal["status"] == "refused"
    error = refusal["error"]
    assert set(error) == {"code", "message", "retry_disposition"}
    assert error["code"] == "FORBIDDEN" and error["retry_disposition"] == "never"
    assert "Git" in error["message"]
    saved = json.loads((tmp_path / "state/gateway" / f"{refusal['gateway_id']}.json").read_text())
    assert saved["error"] == error
    assert saved["decisions"][-1]["decision"] == "deny"
    assert vault.snapshot() == before


def test_GW_1_unauthorized_sender_and_purpose_are_refused(make_kos, vault, clock):
    kos = gateway(make_kos)
    for overrides in ({"sender_operation_id": "intruder"}, {"purpose": "status"}):
        status, refusal = kos.post("/interops/intake", envelope(clock, **overrides))
        assert status == 403 and refusal["error"]["code"] in ("FORBIDDEN", "SCOPE_DENIED")
    assert not vault.snapshot()


@pytest.mark.parametrize("change", [
    {"issued_at": "yesterday"}, {"message_id": "../bad"}, {"idempotency_key": ""},
    {"payload": {"title": "Missing body"}}, {"export_policy": {"git_sync_allowed": "false"}},
])
def test_GW_1_malformed_envelope_is_rejected(make_kos, clock, change):
    kos = gateway(make_kos)
    status, refusal = kos.post("/interops/intake", envelope(clock, **change))
    assert status == 400 and refusal["error"]["code"] == "INVALID_PAYLOAD"


def test_GW_2_query_exports_only_approve_sources(make_kos, vault, tmp_path):
    kos = gateway(make_kos)
    for name, policy, note_type, extra in [
        ("Shared", "approve", "idea", ""), ("Ask", "ask", "idea", ""),
        ("Local", "local_only", "idea", ""), ("Denied", "deny", "idea", ""),
        ("Secret", "approve", "idea", "sensitivity: confidential\n"),
        ("Report", "approve", "report", ""), ("Proposal", "approve", "proposal", ""),
    ]:
        vault.write(f"Notes/{name}.md", f"---\ntitle: {name}\ntype: {note_type}\nai_policy: {policy}\n{extra}---\nSharedneedle evidence.\n")
    status, found = kos.post("/interops/query", {"sender_operation_id": "research", "purpose": "consultation", "query": "Sharedneedle"})
    assert status == 200
    assert {item["title"] for item in found["results"]} == {"Shared", "Secret"}
    for item in found["results"]:
        assert item["locator"] == {"path": item["path"], "line": item["line"]}
        assert vault.text(item["path"]).splitlines()[item["line"] - 1] == item["excerpt"]
    saved = json.loads((tmp_path / "state/gateway" / f"{found['gateway_id']}.json").read_text())
    assert saved["result_count"] == 2
    assert [d["stage"] for d in saved["decisions"]] == ["admission", "effect"]


def test_GW_2_bounds_and_purpose_are_enforced(make_kos, vault):
    kos = gateway(make_kos, max_results=2, max_excerpt_chars=12)
    for index in range(5):
        vault.write(f"Notes/{index}.md", "---\nai_policy: approve\n---\nBoundneedle " + "x" * 300)
    body = {"sender_operation_id": "research", "purpose": "consultation", "query": "Boundneedle", "max_results": 99, "max_excerpt_chars": 1000}
    status, found = kos.post("/interops/query", body)
    assert status == 200 and len(found["results"]) == 2
    assert all(len(item["excerpt"]) <= 12 for item in found["results"])
    smaller = kos.post("/interops/query", {**body, "max_results": 1, "max_excerpt_chars": 4})[1]
    assert len(smaller["results"]) == 1 and len(smaller["results"][0]["excerpt"]) <= 4
    for override in ({"purpose": "status"}, {"sender_operation_id": "intruder"}):
        status, refusal = kos.post("/interops/query", {**body, **override})
        assert status == 403 and "results" not in refusal
    for override in ({"max_results": True}, {"max_excerpt_chars": 0}):
        assert kos.post("/interops/query", {**body, **override})[0] == 400

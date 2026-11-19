"""C11 surfaces: the maintenance CLI (SUR-5) and ExOps admission safety (Blueprint §7)."""

from __future__ import annotations

import json
import uuid

from knowledgeos.notes import parse

IDEAS = "40_Knowledge/Ideas/"


def test_SUR_5_cli_dry_run_prints_plan_and_writes_nothing(kos, vault):
    vault.write(IDEAS + "One.md", "first idea\n")
    vault.write(IDEAS + "Two.md", "second idea\n")
    before = vault.snapshot()

    code, out, err = kos.cli("standardize", "40_Knowledge/Ideas", "--dry-run")
    assert code == 0, out + err
    assert IDEAS + "One.md" in out and IDEAS + "Two.md" in out
    assert "type: null -> \"idea\" (type)" in out
    assert "+type: idea" in out
    assert "dry run: nothing written" in out
    assert vault.snapshot() == before
    assert kos.changes() == []


def test_cli_standardize_apply_writes_through_the_owner(kos, vault):
    vault.write(IDEAS + "One.md", "first idea\n")
    code, out, err = kos.cli("standardize", IDEAS + "One.md", "--apply")
    assert code == 0, out + err
    assert parse("One.md", vault.read(IDEAS + "One.md")).data["type"] == "idea"
    [change] = kos.changes()
    assert change["kind"] == "batch"


def test_cli_health_vault_check_and_work_export(kos, vault):
    code, out, err = kos.cli("health")
    assert code == 0, err
    health = json.loads(out)
    assert health["alive"] and health["ready"] and health["operation_id"] == "knowledgeos"

    shared = str(uuid.uuid4())
    vault.write(IDEAS + "A.md", f"---\nid: {shared}\n---\na\n")
    vault.write(IDEAS + "B.md", f"---\nid: {shared}\n---\nb\n")
    vault.write(IDEAS + "Broken.md", "---\ntitle: [x\n---\n")
    code, out, _ = kos.cli("vault-check")
    assert code == 1
    report = json.loads(out)
    assert report["duplicate_ids"] == {shared: [IDEAS + "A.md", IDEAS + "B.md"]}
    assert report["invalid_yaml"] == [IDEAS + "Broken.md"]

    receipt = kos.run("standardize.batch", {"path": IDEAS + "A.md", "apply": False})
    code, out, _ = kos.cli("work", "export", receipt["work_id"])
    assert code == 0
    work = json.loads(out)
    assert work["workspec"]["capability"] == "C2" and work["status"] == "completed"
    assert work["segments"][0]["assessment"]["status"] == "satisfied"
    assert kos.cli("work", "export", "work-missing")[0] == 1


def test_requests_are_idempotent_by_request_id(kos, vault):
    vault.write(IDEAS + "One.md", "first idea\n")
    request_id = str(uuid.uuid4())
    params = {"path": IDEAS + "One.md", "apply": False}
    status, first = kos.submit("standardize.batch", params, request_id=request_id)
    assert status == 202 and first["status"] == "received" and first["request_id"] == request_id
    settled = kos.wait(request_id)
    status, again = kos.submit("standardize.batch", params, request_id=request_id)
    assert status == 200 and again == settled
    status, conflict = kos.submit("standardize.batch", {**params, "apply": True}, request_id=request_id)
    assert status == 409 and conflict == {"error": "conflict"}
    assert vault.text(IDEAS + "One.md") == "first idea\n"


def test_unknown_command_is_refused_with_a_reason(kos):
    receipt = kos.run("no.such.command", {})
    assert receipt["status"] == "refused" and "unknown command" in receipt["notice"]
    assert receipt["work_id"] is None


def test_exops_rejects_bad_host(kos):
    status, payload, _ = kos.raw("GET", "/api/health", headers={"Host": "evil.example"})
    assert status == 403 and payload == {"error": "forbidden host"}
    status, _, _ = kos.raw("GET", "/api/health", headers={"Host": f"127.0.0.1:{kos.port + 1}"})
    assert status == 403
    status, _, _ = kos.raw("GET", "/api/health", headers={"Host": f"localhost:{kos.port}"})
    assert status == 200


def test_exops_rejects_foreign_origin(kos):
    request_id = str(uuid.uuid4())
    body = json.dumps({"request_id": request_id, "command": "standardize.batch", "params": {"path": "x"}}).encode()
    status, payload, headers = kos.raw(
        "POST",
        "/api/requests",
        body,
        {"Content-Type": "application/json", "Origin": "http://evil.example"},
    )
    assert status == 403 and payload == {"error": "forbidden origin"}
    assert not any(name.startswith("access-control-") for name in headers)
    assert kos.get(f"/api/requests/{request_id}")[0] == 404
    status, _, _ = kos.raw("GET", "/api/works", headers={"Origin": "null"})
    assert status == 403
    status, _, _ = kos.raw("GET", "/api/works", headers={"Origin": f"http://127.0.0.1:{kos.port}"})
    assert status == 200


def test_exops_rejects_non_json_post(kos):
    request_id = str(uuid.uuid4())
    body = json.dumps({"request_id": request_id, "command": "standardize.batch", "params": {"path": "x"}}).encode()
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        status, _, headers = kos.raw("POST", "/api/requests", body, {"Content-Type": content_type})
        assert status == 415, content_type
        assert not any(name.startswith("access-control-") for name in headers)
    status, _, _ = kos.raw("POST", "/api/requests", b"[1, 2]", {"Content-Type": "application/json"})
    assert status == 400
    status, _, _ = kos.raw("POST", "/api/requests", b"not json", {"Content-Type": "application/json"})
    assert status == 400
    assert kos.get(f"/api/requests/{request_id}")[0] == 404
    status, _, _ = kos.raw("OPTIONS", "/api/requests")
    assert status == 405


def test_unregistered_routes_answer_404_json(kos):
    for path in ("/api/nope", "/api/works/not-a-work", "/interops/nope"):
        status, payload = kos.get(path)
        assert status == 404 and payload == {"error": "not found"}, path
    status, payload = kos.post("/interops/nope", {})
    assert status == 404

"""Real local console HTTP boundary, public shell and authorized evidence projections."""
from __future__ import annotations

import base64
import copy
import http.client
import json
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import OwnerActor
from vaultops.application.work import record_digest
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.interfaces.loopback_owner import LoopbackIngress, create_loopback_owner


@pytest.fixture
def owner(tmp_path):
    exops, fixture = make_exops(tmp_path)
    server = create_loopback_owner(exops, actor_id="owner.user", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield exops, fixture, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_public_shell_is_allowlisted_without_private_data_or_effects(owner):
    exops, _fixture, server = owner
    before = exops.application.journal.read()
    for path, content_type in (("/", "text/html"), ("/console", "text/html"),
                               ("/console/", "text/html"), ("/console/app.js", "text/javascript"),
                               ("/console/style.css", "text/css")):
        status, headers, body = request(server, "GET", path)
        assert status == 200 and headers["Content-Type"].startswith(content_type)
        assert 0 < len(body) <= 262144
        assert str(exops.application.roots.vault).encode() not in body
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path in ("/owner/status", "/console/../exops_web.py", "/console/%2e%2e/exops_web.py",
                 "/console/app.js?token=private", "/console//app.js", "/.git/config",
                 "/console/index.html", "/console/style.css/extra"):
        assert request(server, "GET", path)[0] == 404
    assert exops.application.journal.read() == before


def test_local_json_posts_need_no_token_and_reject_foreign_browser_ingress(owner):
    exops, _fixture, server = owner
    before = exops.application.journal.read()
    headers = {"Content-Type": "application/json"}
    status, response_headers, _body = request(server, "POST", "/owner/owner/status", "{}", headers)
    assert status == 200
    assert not any(name.lower().startswith("access-control-allow-") for name in response_headers)
    own_origin = "http://127.0.0.1:" + str(server.server_port)
    assert request(server, "POST", "/owner/owner/status", "{}",
                   {**headers, "Origin": own_origin, "Sec-Fetch-Site": "same-origin"})[0] == 200
    for extra in ({"Origin": "https://foreign.invalid"}, {"Origin": "null"},
                  {"Sec-Fetch-Site": "cross-site"}):
        assert request(server, "POST", "/owner/owner/status", "{}", {**headers, **extra})[0] == 403
    assert request(server, "GET", "/console/", headers={"Host": "foreign.invalid"})[0] == 403
    assert request(server, "POST", "/owner/owner/status", "{}",
                   {**headers, "Host": "foreign.invalid",
                    "Origin": "http://foreign.invalid"})[0] == 403
    assert request(server, "POST", "/owner/owner/status", "x" * 16385, headers)[0] == 413
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        assert request(server, "POST", "/owner/owner/status", "{}",
                       {"Content-Type": content_type})[0] == 404
    assert request(server, "POST", "/owner/owner/status", "{}")[0] == 404
    status, response_headers, _body = request(
        server, "OPTIONS", "/owner/owner/status",
        headers={"Origin": "https://foreign.invalid", "Access-Control-Request-Method": "POST"})
    assert status != 200
    assert not any(name.lower().startswith("access-control-allow-") for name in response_headers)
    assert exops.application.journal.read() == before


def test_inspection_exports_only_exact_independent_assessment_and_admitted_lineage(tmp_path):
    exops, fixture = make_exops(tmp_path)
    exops.submit_work(fixture.spec, fixture.run, actor=OwnerActor("owner.user", "web"),
        action_id="action.web.read", expires_at=exops.application.clock() + timedelta(hours=1))
    key = "exops.work." + fixture.run["work_run_id"]
    flow = fixture.flow()
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"][key]
        record["run"]["latest_assessment_digest"] = record_digest(flow["assessment1"])
        record["assessment"] = copy.deepcopy(flow["assessment1"])
        record["evaluations"] = [flow["eval1"]]
        record["admitted_method_segments"] = {"first": {
            "segment": flow["first"]["segment"], "assignments": flow["first"]["assignments"],
            "private_scaffold": "not exported"}}
        writer.commit(exops.application.clock())
    actor = OwnerActor("owner.user", "web")
    backend = ExOpsWebBackend(exops, actor)
    ingress = LoopbackIngress("knowledgeos", "POST", "work/inspection",
                              json.dumps({"work_run_id": fixture.run["work_run_id"]}).encode())
    before = exops.application.journal.read()
    inspection = backend(ingress)
    assert inspection["assessment"] == flow["assessment1"]
    assert inspection["evaluations"] == [flow["eval1"]]
    assert inspection["segments"] == [{"segment": flow["first"]["segment"],
                                        "assignments": flow["first"]["assignments"]}]
    assert inspection["runtime_observation"] == "unavailable"
    assert exops.application.journal.read() == before
    with pytest.raises(AdmissionError):
        ExOpsWebBackend(exops, OwnerActor("other", "web"))(ingress)
    with exops.application.journal.writer() as writer:
        writer.value["intents"][key]["assessment"]["completion_state"] = "complete"
        writer.commit(exops.application.clock())
    assert backend(ingress)["assessment"] is None
    assert backend(ingress)["evaluations"] == []
    exops.read_only = True
    assert backend(ingress)["assessment"] is None
    for route in ("work/steering/artifact", "work/steering/request"):
        with pytest.raises(AdmissionError) as failure:
            backend(replace(ingress, resource_path=route, body=b"{}"))
        assert failure.value.code == "READ_ONLY"


def test_owner_capabilities_do_not_claim_missing_runtime_or_mutation_bindings(owner):
    _exops, _fixture, server = owner
    status, _headers, body = request(server, "POST", "/owner/owner/status", "{}", {
        "Content-Type": "application/json"})
    value = json.loads(body)
    assert status == 200 and value["runtime_observation"] == "unavailable"
    assert value["observed_at"]
    assert not any(value["bindings"].values())


def test_pending_source_diff_approval_and_apply_are_separate_owner_transitions(tmp_path):
    exops, _fixture = make_exops(tmp_path)
    exops.authorize_actor = lambda actor, _action: actor.actor_id == "owner.user"
    source = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    report, code = exops.application.create_normalize_proposal(
        source_path=source, expected_sha256=exops.application.reference(source)["source_revision"])
    assert code == 0, report
    actor = OwnerActor("owner.user", "web")
    backend = ExOpsWebBackend(exops, actor)

    def call(route, payload):
        return backend(LoopbackIngress("knowledgeos", "POST", route,
                       json.dumps(payload).encode()))

    reference = call("proposal/list", {})["proposals"][0]["proposal_reference"]
    pending = call("proposal/read", {"proposal_reference": reference})
    assert pending["source_text"] == (exops.application.roots.vault / source).read_text()
    assert pending["source_truncated"] is False and pending["diff"]
    assert pending["decision"] is None
    before = {path.relative_to(exops.application.roots.vault): path.read_bytes()
              for path in exops.application.roots.vault.rglob("*") if path.is_file()}
    approved = call("proposal/decide", {"proposal_reference": reference,
                                      "decision": "approve", "reason": "Reviewed exact source and diff"})
    assert approved["canonical_target_changed"] is False
    assert {path.relative_to(exops.application.roots.vault): path.read_bytes()
            for path in exops.application.roots.vault.rglob("*") if path.is_file()} == before
    reread = call("proposal/read", {"proposal_reference": reference})
    assert reread["decision"]["response"]["chosen_action"] == "approve"
    applied = call("proposal/apply", {"proposal_reference": reference})
    assert applied["canonical_target_changed"] is True


def test_mount_free_relay_serves_shell_and_preserves_local_browser_boundary(owner):
    from vaultops.interfaces.owner_relay import create_owner_relay

    exops, _fixture, upstream = owner
    relay = create_owner_relay(upstream_host="127.0.0.1",
                               upstream_port=upstream.server_port, listen_port=0)
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    thread.start()
    before = exops.application.journal.read()
    try:
        for path, content_type in (("/console/", "text/html"),
                                   ("/console/app.js", "text/javascript"),
                                   ("/console/style.css", "text/css")):
            status, headers, body = request(relay, "GET", path)
            assert status == 200 and headers["Content-Type"].startswith(content_type)
            assert body == request(upstream, "GET", path)[2]
            assert headers["Cache-Control"] == "no-store"
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        for path in ("/owner/owner/status", "/console/../exops_web.py",
                     "/console/%2e%2e/exops_web.py", "/console/app.js?token=private"):
            assert request(relay, "GET", path)[0] == 404
        headers = {"Content-Type": "application/json",
                   "Origin": f"http://127.0.0.1:{relay.server_port}"}
        status, _headers, body = request(relay, "POST", "/owner/owner/status", "{}", headers)
        assert status == 200 and json.loads(body)["owner_operation_id"] == "knowledgeos"
        for selected in ({"Origin": "https://foreign.invalid"},
                         {"Origin": "null"},
                         {"Host": "foreign.invalid", "Origin": "http://foreign.invalid"},
                         {"Sec-Fetch-Site": "cross-site"}):
            assert request(relay, "POST", "/owner/owner/status", "{}",
                           {**headers, **selected})[0] == 403
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            assert request(relay, "POST", "/owner/owner/status", "{}",
                           {**headers, "Content-Type": content_type})[0] == 404
        assert request(relay, "POST", "/owner/owner/status", "{}")[0] == 404
        assert not any(name.lower().startswith("access-control-allow-")
                       for name in request(relay, "POST", "/owner/owner/status", "{}", headers)[1])
        assert exops.application.journal.read() == before
    finally:
        relay.shutdown()
        relay.server_close()
        thread.join(timeout=5)


def test_browser_fixture_search_scope_and_template_admission_use_eligible_knowledge(tmp_path):
    from support.web_console_smoke import ACTOR, QUERY, SEARCH_SOURCE, compose

    exops, _officer, _admitted, intake, templates = compose(tmp_path)
    actor = OwnerActor(ACTOR, "web")
    backend = ExOpsWebBackend(exops, actor, admit_request=intake, admit_template=templates)

    def call(route, payload):
        return backend(LoopbackIngress("knowledgeos", "POST", route,
                       json.dumps(payload).encode()))

    search = call("search/query", {"query": QUERY, "mode": "lexical"})
    selected = next(item for item in search["results"] if item["path"] == SEARCH_SOURCE)
    assert QUERY in selected["excerpt"]
    source = exops.application.roots.vault / SEARCH_SOURCE
    before = source.read_bytes()
    scope = {"path": selected["path"],
             "content_sha256": selected["resource_reference"]["source_revision"]}
    admitted = call("work/intake-template", {"template_id": "note_metadata", "scope": scope,
                    "request_id": "14710203-0405-4607-8809-001122334455"})
    assert len(admitted["receipts"]) == 2
    works = call("work/list", {"path": SEARCH_SOURCE})["works"]
    assert {item["team_id"] for item in works} == {"corpus-stewardship", "relation-ontology"}
    assert all(item["dispatch_state"] == "pending" and not item["source_stale"] for item in works)
    assert source.read_bytes() == before


@pytest.mark.parametrize("change_during_director", [False, True])
def test_freeform_native_method_intake_preserves_exact_director_source(tmp_path, change_during_director):
    from test_obsidian_sidebar_owner import _group

    exops, source, scope, admissions = _group(tmp_path, template_id="document_enhancement")
    actor = OwnerActor("owner.user", "web")

    def intake(_actor, _payload):
        if change_during_director:
            path = exops.application.roots.vault / scope["path"]
            path.write_bytes(path.read_bytes() + b"\nChanged during trusted Director admission.\n")
        return admissions[0][:4]

    backend = ExOpsWebBackend(exops, actor, admit_request=intake)
    ingress = LoopbackIngress("knowledgeos", "POST", "work/intake",
        json.dumps({"question": "Produce a cited improvement report", "scope": scope}).encode())
    if change_during_director:
        with pytest.raises(AdmissionError) as error:
            backend(ingress)
        assert error.value.code == "RESOURCE_STALE"
        assert not any(key.startswith("exops.work.") for key in exops.application.journal.read()["intents"])
    else:
        receipt = backend(ingress)
        record = exops.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
        assert record["method_source_reference"] == source
        assert record["selected_scope"] == scope
        assert receipt["dispatch_state"] == "pending"


def _completed_steering(root, *, amended=False):
    from support.steering_factory import apply, receive, waiting

    controller, _spec, run, successor, authority, path, _canary = waiting(root)
    work_run_id = run["work_run_id"]
    apply(controller, work_run_id, receive(controller, work_run_id, "answer", {"answer": "Owner label"}))
    apply(controller, work_run_id, receive(controller, work_run_id, "cutover", {"reason": "Replace unexecuted stage"}))
    if amended:
        authority["amendment"] = True
        apply(controller, work_run_id, receive(controller, work_run_id, "amend_work",
            {"successor_spec_digest": successor["spec_digest"]}, successor_spec=successor))
    apply(controller, work_run_id, receive(controller, work_run_id, "resume", {"reason": "Run exact successor"}))
    token = controller.successor(work_run_id, temporal_run_id="native-steering-unit")
    controller.normalize(token, temporal_run_id="native-steering-unit")
    controller.finish(work_run_id, temporal_run_id="native-steering-unit")
    return controller, work_run_id, authority, path


@pytest.mark.parametrize("amended", [False, True])
def test_common_result_exports_real_journal_backed_steering_bytes_without_mcp_registration(tmp_path, amended):
    from support.steering_factory import ACTOR

    controller, work_run_id, _authority, _path = _completed_steering(tmp_path, amended=amended)
    expected = controller.export(work_run_id, actor=ACTOR)
    server = create_loopback_owner(controller.exops, actor_id=ACTOR.actor_id, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = controller.app.journal.read()
    try:
        status, _headers, body = request(server, "POST", "/owner/work/result",
            json.dumps({"work_run_id": work_run_id}),
            {"Content-Type": "application/json"})
        result = json.loads(body)
        raw = base64.b64decode(expected["content_base64"], validate=True)
        assert status == 200, result
        assert result["state"] == "completed" and result["reason"] is None
        assert result["text"].encode("utf-8") == raw and result["truncated"] is False
        assert result["report"] == json.loads(raw)
        assert result["result_reference"] == expected["reference"]
        assert result["assessment"] == expected["assessment"]
        assert result["evidence"] == {
            "source_reference": before["intents"]["exops.work." + work_run_id]["method_source_reference"],
            "result_reference": expected["reference"],
            "criterion_outcomes": expected["assessment"]["criterion_outcomes"],
        }
        assert result["truth_evaluated"] is False and result["apply_allowed"] is False
        assert result["proposal_reference"] is None
        assert controller.app.journal.read() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("changed", ["foreign_actor", "source", "report", "assessment", "result_reference", "director"])
def test_common_steering_result_denies_current_foreign_stale_and_forged_evidence_without_writes(tmp_path, changed):
    from support.steering_factory import ACTOR

    controller, work_run_id, authority, path = _completed_steering(tmp_path)
    actor_id = ACTOR.actor_id
    if changed == "foreign_actor":
        actor_id = "owner.other_reader"
        original = controller.exops.authorize_actor
        controller.exops.authorize_actor = lambda actor, action: original(actor, action) or (
            actor.actor_id == actor_id and action == "read_work")
    elif changed == "source":
        path.write_bytes(path.read_bytes() + b"\nChanged after independent completion.\n")
    elif changed == "director":
        authority["director"] = False
    else:
        with controller.app.journal.writer() as writer:
            record = writer.value["intents"]["exops.work." + work_run_id]
            if changed == "report":
                record["steering_report"]["bytes"] = base64.b64encode(b"{}").decode()
            elif changed == "assessment":
                record["assessment"]["criterion_outcomes"][0]["outcome"] = "unmet"
            else:
                record["run"]["result_reference"]["content_digest"] = "sha256:" + "0" * 64
            writer.commit(controller.app.clock())
    server = create_loopback_owner(controller.exops, actor_id=actor_id, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = controller.app.journal.read()
    try:
        status, _headers, body = request(server, "POST", "/owner/work/result",
            json.dumps({"work_run_id": work_run_id}),
            {"Content-Type": "application/json"})
        assert status in {403, 409} and json.loads(body)["status"] == "FAIL"
        assert controller.app.journal.read() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

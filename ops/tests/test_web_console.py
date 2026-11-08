"""Real local console HTTP boundary, public shell and authorized evidence projections."""
from __future__ import annotations

import asyncio
import base64
import copy
import http.client
import json
import shutil
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
        record["run"]["latest_assessment_id"] = flow["assessment1"]["assessment_id"]
        record["run"]["latest_assessment_digest"] = record_digest(flow["assessment1"])
        record["assessment"] = copy.deepcopy(flow["assessment1"])
        record["evaluations"] = [flow["eval1"]]
        record["continuation_state"] = "human_required"
        record["remaining_work"] = copy.deepcopy(flow["remaining"])
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
    assert inspection["run_revision"] == before["intents"][key]["run"]["revision"]
    assert inspection["work_spec_digest"] == fixture.spec["spec_digest"]
    assert inspection["assessment"] == flow["assessment1"]
    assert inspection["evaluations"] == [flow["eval1"]]
    assert inspection["remaining_work"] == flow["remaining"]
    assert inspection["continuation_state"] == "human_required"
    assert inspection["segments"][0]["segment"] == flow["first"]["segment"]
    assert inspection["segments"][0]["assignments"] == flow["first"]["assignments"]
    assert inspection["segments"][0]["graph_availability"] == "unavailable"
    assert inspection["segments"][0]["graph_spec"] is None
    assert inspection["invocations"] == []
    assert inspection["runtime_observation"] == "unavailable"
    assert exops.application.journal.read() == before
    with pytest.raises(AdmissionError):
        ExOpsWebBackend(exops, OwnerActor("other", "web"))(ingress)
    with exops.application.journal.writer() as writer:
        writer.value["intents"][key]["assessment"]["completion_state"] = "complete"
        writer.commit(exops.application.clock())
    assert backend(ingress)["assessment"] is None
    assert backend(ingress)["evaluations"] == []
    assert backend(ingress)["remaining_work"] is None
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



def test_selected_adapter_requires_parameters_and_exposes_readonly_schema(tmp_path):
    from vaultops.application.owner_runtime import selected_parameter_schema

    exops, _fixture = make_exops(tmp_path)
    actor = OwnerActor("owner.user", "web")
    received = []
    backend = ExOpsWebBackend(exops, actor, admit_selected_request=lambda _actor, value: received.append(value) or {"accepted": True})
    path = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    payload = {"method_id": "knowledgeos.note_metadata_team",
               "scope": {"path": path, "content_sha256": exops.application.reference(path)["source_revision"]},
               "request_id": "14710203-0405-4607-8809-001122334455", "parameters": {}}
    def call(route, value):
        return backend(LoopbackIngress("knowledgeos", "POST", route, json.dumps(value).encode()))

    assert call("work/intake-selected", payload) == {"accepted": True}
    assert received == [payload]
    for invalid in ({key: value for key, value in payload.items() if key != "parameters"},
                    {**payload, "parameters": []}, {**payload, "request_id": "not-a-uuid"}):
        with pytest.raises(AdmissionError) as error:
            call("work/intake-selected", invalid)
        assert error.value.code == "INVALID_PAYLOAD"
    exops.read_only = True
    descriptors = call("work/methods", {})["methods"]
    assert len(descriptors) == 11
    assert all(not item["available"] and item["parameter_schema"] == selected_parameter_schema(item["method_id"])
               for item in descriptors)
    assert received == [payload]

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


def test_disposable_console_seed_is_observable_without_browser_document_intake(tmp_path):
    from support.web_console_smoke import ACTOR, SEARCH_SOURCE, administrative_seed, compose

    exops, _officer, _admitted, intake, _templates = compose(tmp_path)
    source = exops.application.roots.vault / SEARCH_SOURCE
    before = source.read_bytes()
    seed = administrative_seed(exops, intake)
    observed = exops.read_work(seed["work_run_id"], actor=OwnerActor(ACTOR, "web"))
    assert observed["run"]["state"] == "admitted"
    assert observed["receipt"]["receipt_state"] == "accepted"
    assert observed["dispatch_state"] == "pending"
    assert observed["application_state"] != "applied"
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
    async def complete():
        token = await controller.successor(work_run_id, temporal_run_id="native-steering-unit")
        await controller.normalize(token, temporal_run_id="native-steering-unit")
        await controller.finish(work_run_id, temporal_run_id="native-steering-unit")

    asyncio.run(complete())
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
        status, _headers, body = request(server, "POST", "/owner/work/inspection",
            json.dumps({"work_run_id": work_run_id}), {"Content-Type": "application/json"})
        inspection = json.loads(body)
        assert status == 200, inspection
        assert [item["segment"]["segment_index"] for item in inspection["segments"]] == [1, 2]
        assert all(item["graph_availability"] == "retained" for item in inspection["segments"])
        assert inspection["role_observations"]["manager"] == "observed"
        assert inspection["role_observations"]["evaluator"] == "observed"
        assert any(item["source"] == "steering_partial" for item in inspection["prior_assessments"])
        assert len(inspection["work_spec_history"]) == int(amended)
        assert inspection["run_revision"] == before["intents"]["exops.work." + work_run_id]["run"]["revision"]
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


def test_recent_history_is_bounded_retained_work_not_source_or_whole_journal(tmp_path):
    from support.web_console_smoke import ACTOR, SEARCH_SOURCE, administrative_seed, compose

    exops, _officer, _admitted, intake, _templates = compose(tmp_path)
    observed_at = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: observed_at)
    ids = {administrative_seed(exops, intake)["work_run_id"] for _ in range(3)}
    actor = OwnerActor(ACTOR, "web")
    backend = ExOpsWebBackend(exops, actor)
    with exops.application.journal.writer() as writer:
        unknown = max(ids)
        writer.value["intents"]["exops.work." + unknown]["application_state"] = "unknown"
        writer.value["intents"]["owner.private.unrelated"] = {
            "state": "completed", "recorded_at": observed_at.isoformat(), "secret": "not-a-work"}
        writer.commit(exops.application.clock())
    (exops.application.roots.vault / SEARCH_SOURCE).unlink()
    before = exops.application.journal.read()
    ingress = LoopbackIngress("knowledgeos", "POST", "work/history", b'{"limit":2}')
    recent = backend(ingress)
    assert [item["work_run_id"] for item in recent["works"]] == sorted(ids, reverse=True)[:2]
    assert recent["works"][0]["run_state"] == "unknown"
    assert recent["works"][0]["application_state"] == "unknown"
    older = backend(replace(ingress, body=json.dumps({"before": recent["next_before"], "limit": 2}).encode()))
    assert {item["work_run_id"] for item in recent["works"] + older["works"]} == ids
    assert older["next_before"] is None
    assert not any("source" in key or "secret" in key for item in recent["works"] for key in item)
    for body in ({"limit": True}, {"limit": 51}, {"limit": 0}, {"path": SEARCH_SOURCE},
                 {"before": "/private/credential"}, {"before": "workrun.missing"}):
        with pytest.raises(AdmissionError):
            backend(replace(ingress, body=json.dumps(body).encode()))
    with pytest.raises(AdmissionError):
        ExOpsWebBackend(exops, OwnerActor("other", "web"))(ingress)
    exops.read_only = True
    assert backend(ingress)["works"] == recent["works"]
    assert exops.application.journal.read() == before


def test_role_policy_projects_current_routes_and_exact_prompt_sources_without_private_config(tmp_path):
    import hashlib

    from support.web_console_smoke import ACTOR, compose

    exops, *_rest = compose(tmp_path)
    actor = OwnerActor(ACTOR, "web")
    backend = ExOpsWebBackend(exops, actor)
    control = exops.application.roots.control
    manifest_path = control / "ops/config/agent-profiles.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["routes"]["complex"]["api_key"] = "credential-canary-not-exported"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ingress = LoopbackIngress("knowledgeos", "POST", "agents/policy", b"{}")
    before = exops.application.journal.read()
    policy = backend(ingress)
    assert policy["kind"] == "current_configured_policy" and policy["availability"] == "available"
    assert len(policy["roles"]) == len(manifest["profiles"]) == 37
    assert policy["routes"]["hard"]["reasoning_effort"] == "xhigh"
    assert policy["routes"]["trivial"]["think"] is False
    assert "credential-canary" not in json.dumps(policy) and "base_url" not in json.dumps(policy)
    role = next(item for item in policy["roles"] if item["role_id"] == "knowledgeos.normalization_officer")
    assert role["manual_homes"] == ["kos-normalization"] and role["purpose"]
    prompt = backend(replace(ingress, resource_path="agents/prompt",
        body=json.dumps({"role_id": role["role_id"]}).encode()))
    assert prompt["kind"] == "current_configured_policy"
    assert prompt["common"]["text"].encode() == (control / prompt["common"]["path"]).read_bytes()
    assert prompt["soul"]["text"].encode() == (control / prompt["soul"]["path"]).read_bytes()
    raw = prompt["common"]["text"].encode() + b"\n" + prompt["soul"]["text"].encode()
    assert prompt["prompt_digest"] == role["prompt_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert prompt["policy_digest"] == policy["policy_digest"]
    for body in ({"path": "/private/credential"}, {"role_id": "../../credential"}, {"role_id": []}):
        with pytest.raises(AdmissionError):
            backend(replace(ingress, resource_path="agents/prompt", body=json.dumps(body).encode()))
    for route in ("agents/policy", "agents/prompt"):
        selected = replace(ingress, resource_path=route,
                           body=json.dumps({"role_id": role["role_id"]} if route.endswith("prompt") else {}).encode())
        with pytest.raises(AdmissionError):
            ExOpsWebBackend(exops, OwnerActor("other", "web"))(selected)
        exops.read_only = True
        assert backend(selected)["availability"] == "available"
    (control / "ops/clients/hermes/role_policy.py").unlink()
    assert backend(ingress)["availability"] == "unavailable"
    assert exops.application.journal.read() == before


def _stored_role_invocation(role, responsibility, graph_id=None, stage=None):
    """Retained native response shape with synthetic identities; never calls a model."""
    agent = {"role_id": role, "responsibility": responsibility, "instructions": "Retained request instruction",
             "route": "historical-route", **({"graph_run_id": graph_id} if graph_id else {})}
    request = {"agent_request": agent, "stage_id": stage or responsibility,
               "runner_binding": {"agent_policy_digest": "sha256:" + "1" * 64, "credential": "do-not-export"}}
    response = {"role_id": role, "responsibility": responsibility, "provider": "historical-provider",
                "model": "historical-model", "reasoning_effort": "historical-effort",
                "route_name": "historical-route", "session_id": role + "." + (graph_id or responsibility),
                "prompt_digest": "sha256:" + "2" * 64, "instructions_digest": "sha256:" + "3" * 64,
                "request_digest": record_digest(agent),
                "tool_calls": [{"name": "knowledge_read", "call_id": "call.retained",
                                "arguments": {"secret": "do-not-export"},
                                "result": {"status": "ok", "canonical_target_changed": False, "private": "do-not-export"}}],
                "transport": {"credential": "do-not-export", "requests": [
                    {"model": "historical-model", "reasoning_effort": "historical-effort", "status_code": 200,
                     "wire_instructions_digest": "sha256:" + "4" * 64,
                     "request_digest": "sha256:" + "5" * 64, "headers": {"Authorization": "do-not-export"}}]}}
    evidence = {"request": request, "response": response, "decision": {"retained": True}}
    return {**evidence, "content_digest": record_digest(evidence)}


def test_inspection_retains_multisegment_graph_and_actual_role_identities_without_current_policy_replay(tmp_path):
    namespace = "curation"
    exops, fixture = make_exops(tmp_path)
    actor = OwnerActor("owner.user", "web")
    exops.submit_work(fixture.spec, fixture.run, actor=actor,
        action_id="action.web.history", expires_at=exops.application.clock() + timedelta(hours=1))
    flow = fixture.flow()
    bundles = [flow["first"], flow["second"]]
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + fixture.run["work_run_id"]]
        record["admitted_method_segments"] = {}
        record["agent_plans"] = {}
        for bundle in bundles:
            graph_id = bundle["segment"]["graph_run_id"]
            record["admitted_method_segments"][graph_id] = {
                "segment": bundle["segment"], "assignments": bundle["assignments"], "private": "do-not-export"}
            step = {"stage_id": "write", "node_id": "node.write", "role_id": "knowledgeos.writer",
                    "executor_id": "knowledgeos.writer", "instruction": "Retained graph instruction",
                    "route": "historical-route", "criterion_ids": bundle["segment"]["target_criterion_ids"]}
            plan = {"steps": [step], "bundle_digest": record_digest(bundle), "rationale": "Retained Manager rationale"}
            plan["content_digest"] = record_digest(plan)
            record["agent_plans"][graph_id] = {"bundle": bundle, "plan": plan, "evidence": {
                "manager_decision": _stored_role_invocation("knowledgeos.curation_manager", "manager", graph_id)}}
        initial = bundles[0]["segment"]["graph_run_id"]
        successor = bundles[1]["segment"]["graph_run_id"]
        record[namespace + "_stage_attempt"] = _stored_role_invocation("knowledgeos.writer", "specialist", initial, "write")
        record[namespace + "_stage"] = copy.deepcopy(record[namespace + "_stage_attempt"])
        record[namespace + "_semantic_assessment"] = {
            "evidence": _stored_role_invocation("knowledgeos.eval_officer", "evaluator", successor)}
        record["curation_initial_semantic"] = {"assessment": flow["assessment1"], "remaining_work": flow["remaining"],
                                               "evaluations": [flow["eval1"]]}
        record["assessment"] = flow["assessment2"]
        record["evaluations"] = [flow["eval1"], flow["eval2"]]
        record["run"]["latest_assessment_digest"] = record_digest(flow["assessment2"])
        writer.value["intents"]["owner.director." + fixture.spec["admission_receipt_id"]] = {
            "state": "completed", "recorded_at": exops.application.clock().isoformat(),
            "director_evidence": _stored_role_invocation("knowledgeos.director", "director")}
        writer.commit(exops.application.clock())
    backend = ExOpsWebBackend(exops, actor)
    ingress = LoopbackIngress("knowledgeos", "POST", "work/inspection",
        json.dumps({"work_run_id": fixture.run["work_run_id"]}).encode())
    before = exops.application.journal.read()
    inspection = backend(ingress)
    assert len(inspection["segments"]) == 2
    for item, bundle in zip(inspection["segments"], bundles):
        assert item["graph_spec"] == bundle["graph_spec"] and item["nodes"] == bundle["nodes"]
        assert item["edges"] == bundle["edges"] and item["assignments"] == bundle["assignments"]
        assert item["graph_availability"] == "retained" and item["graph_integrity"] == "matches"
    actual = [item for item in inspection["invocations"] if item["observation_state"] == "observed"]
    assert len(actual) == 5  # Director, two Managers, executed specialist, independent evaluator.
    assert {item["responsibility"] for item in actual} == {"director", "manager", "specialist", "evaluator"}
    assert all(item["observed"]["model"] == "historical-model" for item in actual)
    assert all(item["historical_prompt_text"] == "not_retained" for item in actual)
    assert all(item["integrity"] == "matches" for item in actual)
    assert inspection["role_observations"]["director"] == "observed"
    assert inspection["role_observations"]["evaluator"] == "observed"
    assert any(item["observation_state"] == "not_observed" and item["graph_run_id"] == successor
               for item in inspection["invocations"])
    assert inspection["prior_assessments"][0]["remaining_work"] == flow["remaining"]
    assert inspection["assessment"] == flow["assessment2"]
    assert "do-not-export" not in json.dumps(inspection)
    assert exops.application.journal.read() == before
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + fixture.run["work_run_id"]]
        record["agent_plans"][initial]["bundle"]["nodes"][0]["max_attempts"] += 1
        writer.commit(exops.application.clock())
    invalid = backend(ingress)["segments"][0]
    assert invalid["graph_integrity"] == "mismatch" and invalid["graph_spec"] is None and invalid["plan"] is None


def test_inspection_projects_stored_officer_and_steering_optional_evidence_without_inventing_graphs(tmp_path):
    from vaultops.application.console_reads import retained_execution

    exops, fixture = make_exops(tmp_path)
    bundle = fixture.bundle()
    graph_id = bundle["segment"]["graph_run_id"]
    evidence = _stored_role_invocation("knowledgeos.normalization_officer", "officer", graph_id, "normalization")
    optional = _stored_role_invocation("knowledgeos.source_extractor", "specialist", graph_id, "optional")
    record = {"spec": fixture.spec, "run": fixture.run,
              "admitted_officer_segments": {graph_id: {"segment": bundle["segment"], "assignment": bundle["assignments"][0]}},
              "normalization_model_execution": {"request": evidence["request"],
                  "response": {"agent_evidence": evidence["response"]}},
              "steering_optional_requests": {"optional": optional["request"]},
              "steering_optional": [optional["response"]]}
    before = exops.application.journal.read()
    result = retained_execution(record, {})
    assert result["segments"][0]["graph_availability"] == "unavailable"
    assert result["segments"][0]["assignments"] == bundle["assignments"]
    assert {item["role_id"] for item in result["invocations"]} == {
        "knowledgeos.normalization_officer", "knowledgeos.source_extractor"}
    assert all(item["observed"]["model"] == "historical-model" for item in result["invocations"])
    assert exops.application.journal.read() == before


def test_console_html_and_javascript_headless_behavior():
    from support.web_console_smoke import headless_console

    if shutil.which("node") is None:
        pytest.skip("Node is host tooling; run web_console_smoke.py --headless on the host")
    result = headless_console()
    assert result["headless_console"] == "pass"
    assert result["actual_html_js"] is True and result["synthetic_owner"] is True
    assert result["browser"] is False and result["model_calls"] == 0


@pytest.mark.parametrize("route,payload", [
    ("work/history", {}), ("agents/policy", {}), ("agents/prompt", {"role_id": "knowledgeos.director"})])
def test_administrative_reads_retain_http_loopback_json_and_readonly_boundaries(owner, route, payload):
    exops, _fixture, server = owner
    exops.read_only = True
    before = exops.application.journal.read()
    headers = {"Content-Type": "application/json"}
    body = json.dumps(payload)
    assert request(server, "POST", "/owner/" + route, body, headers)[0] == 200
    for extra in ({"Origin": "https://foreign.invalid"}, {"Host": "foreign.invalid"},
                  {"Sec-Fetch-Site": "cross-site"}):
        assert request(server, "POST", "/owner/" + route, body, {**headers, **extra})[0] == 403
    assert request(server, "POST", "/owner/" + route, body, {"Content-Type": "text/plain"})[0] == 404
    assert exops.application.journal.read() == before

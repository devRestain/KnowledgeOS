"""Disposable actual-provider metadata two-Team and daily provisional owner proof."""
from __future__ import annotations

import argparse
import asyncio
import copy
import http.client
import json
import os
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import native_team_smoke_host as harness
from support import normalization_work_smoke as officer

MARKER = "KNOWLEDGEOS_METADATA_EVIDENCE="
STAGES = ("coverage_evidence", "metadata_candidate", "relation_evidence", "ontology_alignment",
          "coverage_evidence", "enrichment_candidate", "coverage_evidence", "enrichment_candidate",
          "coverage_evidence", "enrichment_candidate")


class MetadataRendezvous:
    def __init__(self, binding, app):
        self.binding, self.app = copy.deepcopy(binding), app
        self.requests, self.responses, self.denials = [], [], []

    async def __call__(self, request):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        parameters = StdioServerParameters(command=sys.executable, args=["-m", "vaultops.interfaces.mcp_server",
            "--control-root", request["control_root"], "--work-run-id", request["work_run_id"],
            "--executor-id", request["executor_id"], "--graph-run-id", request["graph_run_id"]],
            env={"PYTHONPATH": "/workspace/control/ops/src", "PYTHONDONTWRITEBYTECODE": "1"})
        probes = [("ungranted_tool", "index_status_read", {})]
        if request["stage_id"] in {"coverage_evidence", "relation_evidence"}:
            probes.append(("wrong_source", "knowledge_read", {"resource_reference": self.app.reference(officer.BASE_CAPTURE),
                "chunk_locator": request["source_chunks"][0]}))
        if request["stage_id"] in {"coverage_evidence", "ontology_alignment"}:
            probes.append(("wrong_registry", "semantic_registry_read", {"category": "types"}))
        async with Client(parameters, mode="legacy") as client:
            for label, tool, arguments in probes:
                result = await client.call_tool(tool, arguments)
                assert result.is_error and result.structured_content["effect"] == "none", result
                self.denials.append({"stage": request["stage_id"], "probe": label, "envelope": result.structured_content})
        self.requests.append(copy.deepcopy(request))
        print(officer.MODEL_REQUEST + json.dumps(request, ensure_ascii=False), flush=True)
        async with asyncio.timeout(360):
            line = await asyncio.to_thread(sys.stdin.readline)
        if not line or len(line) > 1024 * 1024:
            raise RuntimeError("No bounded native metadata response; no retry")
        response = json.loads(line)
        self.responses.append(response)
        return response


def owner_http(exops, callback):
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    server = create_loopback_owner(exops, actor_id="owner.officer_smoke", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(route, payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            connection.request("POST", "/owner/" + route, json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        return callback(call)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=15)


async def execute(exops, executor, run_id):
    from clients.temporal import TemporalBinding
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from vaultops.application.metadata_temporal import metadata_worker_components
    from vaultops.application.temporal_contract import NATIVE_TEAM_ROUTES
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work

    route = NATIVE_TEAM_ROUTES[executor.policy.method_id]
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=route[1], workflow_id_prefix="knowledgeos/",
        update_name="team.update.disabled", signal_name="team.signal.disabled", query_name="team.query.disabled", tls=False)
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect(transport.target_host, namespace=transport.namespace, tls=False)
                await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(namespace=transport.namespace,
                    workflow_execution_retention_period={"seconds": 3600}))
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    scoped = await connect_owner_temporal(exops, transport)
    workflow, plugin = metadata_worker_components(exops, executor)
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin], identity=exops.worker_identity):
        await dispatch_admitted_work(exops, scoped, run_id)
        handle = sdk.get_workflow_handle("knowledgeos/" + run_id)
        result = await asyncio.wait_for(handle.result(), timeout=780)
        assert result["application_state"] == "recorded", result
        history = await handle.fetch_history()
        description = await handle.describe()
    from vaultops.application.temporal_contract import (
        DAILY_GRAPH_NAME,
        METADATA_GRAPH_NAME,
        RELATION_GRAPH_NAME,
    )
    graph_name = {"note_metadata": METADATA_GRAPH_NAME, "relation_ontology": RELATION_GRAPH_NAME,
                  "daily_corpus": DAILY_GRAPH_NAME}[executor.policy.namespace]
    scheduled = [event.activity_task_scheduled_event_attributes.activity_type.name for event in history.events
                 if event.HasField("activity_task_scheduled_event_attributes")]
    assert scheduled == [graph_name + "." + node for node in ("manager", *executor.policy.stages, "assess")], scheduled
    assert all(event.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
               for event in history.events if event.HasField("activity_task_scheduled_event_attributes"))
    return {"workflow_id": description.id, "temporal_run_id": description.run_id,
            "workflow_type": description.workflow_type, "activities": scheduled, "events": len(history.events)}


def composition(app, bindings, admitted):
    from support.metadata_fixture import SOURCE
    from vaultops.application.exops import KnowledgeExOps

    return KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.officer_smoke" and actor.ingress in {"cli", "web"}
            and action in {"submit_work", "read_work", "proposal_read", "proposal_list", "proposal_decide", "proposal_apply",
                           "daily_list", "daily_read", "daily_decide"},
        director_admitted=lambda spec: any(dict(spec) == selected for selected in admitted),
        teams=bindings, current_team_binding=lambda team: bindings.get(team), worker_identity="knowledgeos.worker.native_metadata",
        director_method_source_admitted=lambda spec, source: any(dict(spec) == selected for selected in admitted)
            and app.reference(SOURCE) == dict(source))


async def sidebar(root, route):
    from support.metadata_fixture import SOURCE, application, binding
    from vaultops.adapters.core import AdmissionError, digest
    from vaultops.application.exops import OwnerActor
    from vaultops.application.intake_templates import build_template_admissions
    from vaultops.application.metadata_work import (
        METADATA_METHOD_ID,
        RELATION_METHOD_ID,
        MetadataTeamExecutor,
        validate_candidate,
    )

    app, canary = application(root)
    source = app.reference(SOURCE)
    before = {p.relative_to(app.roots.vault).as_posix(): digest(p.read_bytes()) for p in app.roots.vault.rglob("*") if p.is_file()}
    original = (app.roots.vault / SOURCE).read_bytes()
    bindings = {team.team_id: team for team in (binding(app, route, METADATA_METHOD_ID), binding(app, route, RELATION_METHOD_ID))}
    admitted = []
    exops = composition(app, bindings, admitted)
    request_id = "metadata." + uuid4().hex
    scope = {"path": SOURCE, "content_sha256": source["source_revision"]}
    admissions = build_template_admissions(exops, "note_metadata", scope, request_id,
        issue_director_receipt=lambda template, team, selected, request: "admission." + uuid4().hex,
        native_metadata_runner_binding=route, now=datetime.now(UTC))
    admitted.extend(copy.deepcopy(item[0]) for item in admissions)
    try:
        exops.submit_template_group("note_metadata", scope, "denied." + request_id, admissions, actor=OwnerActor("other", "web"))
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor admission accepted")
    group = exops.submit_template_group("note_metadata", scope, request_id, admissions, actor=OwnerActor("owner.officer_smoke", "web"))
    runner = MetadataRendezvous(route, app)
    histories, candidates, records = [], [], []
    for entry in group["receipts"]:
        record = app.journal.read()["intents"]["exops.work." + entry["work_run_id"]]
        method = record["spec"]["method_contract_ref"]["method_id"]
        executor = MetadataTeamExecutor(exops, runner=runner, method_id=method)
        histories.append(await execute(exops, executor, entry["work_run_id"]))
        record = app.journal.read()["intents"]["exops.work." + entry["work_run_id"]]
        records.append(record)
        call = record[executor.policy.namespace + "_" + executor.policy.stages[-1]]["response"]["tool_calls"][-1]
        candidate = json.loads(call["arguments"]["content"])
        candidates.append(candidate)
        assert record["assessment"]["completion_state"] == "complete" and record["run"]["profile_invocations"] == 2
        assert canary not in json.dumps(runner.requests[-2]), "Source-hidden canary leaked in initial request"
        assert canary in "".join(c["result"]["data"]["text"] for c in runner.responses[-2]["tool_calls"] if c["name"] == "knowledge_read")
    assert candidates[0]["fields"].get("tags") == [canary], candidates
    assert candidates[1]["fields"].get("related"), candidates
    durable_group = app.journal.read()["intents"]["exops.template." + request_id]
    proposal = durable_group["canonical_proposal_reference"]
    assert (app.roots.vault / SOURCE).read_bytes() == original
    combined = [i for i in app.journal.read()["intents"].values() if i.get("template_request_id") == request_id and i.get("action") == "update_note"]
    assert len(combined) == 1
    denials = {}
    for label, mutation in (("wrong_registry", lambda c: c.update(registry_digest="sha256:" + "0" * 64)),
                            ("wrong_source", lambda c: c.update(source_reference=app.reference(officer.BASE_CAPTURE))),
                            ("wrong_quote", lambda c: c["evidence"]["tags"].update({canary: ["fabricated quote"]})),
                            ("populated_overwrite", lambda c: (c["fields"].update(title=[canary]), c["evidence"].update(title={canary: ["tags: " + canary]})))):
        bad = copy.deepcopy(candidates[0]); mutation(bad)
        try:
            validate_candidate(app, source, bad, method_id=METADATA_METHOD_ID)
        except AdmissionError as error:
            denials[label] = error.code
        else:
            raise AssertionError("Candidate denial missing: " + label)
    selected = records[0]
    changed = copy.deepcopy(selected["run"]); changed["revision"] += 1
    try:
        exops.worker_finished(selected["run"]["work_run_id"], worker_identity=exops.worker_identity,
            action_id=selected["action_id"], next_run=changed)
    except AdmissionError as error:
        denials["conflicting_receipt"] = error.code
    else:
        raise AssertionError("Conflicting result receipt replay accepted")

    def review(call):
        status, result = call("proposal/read", {"proposal_reference": proposal})
        assert status == 200 and result["field_evidence"] and result["diff"], result
        exports = []
        for record, expected in zip(records, candidates):
            exported_status, exported = call("work/result", {"work_run_id": record["run"]["work_run_id"]})
            assert exported_status == 200 and exported["report"] == expected, exported
            assert exported["evidence"] == expected["evidence"]
            assert exported["apply_allowed"] is False and exported["truth_evaluated"] is False
            artifact_reference = record["submitted_artifacts"][0]
            artifact_path = app.resolve_reference(artifact_reference, proposal=True)
            assert exported["text"].encode() == (app.roots.vault / artifact_path).read_bytes()
            exports.append({"work_run_id": record["run"]["work_run_id"], "status": exported_status,
                            "artifact_digest": record["run"]["result_reference"]["content_digest"]})
        status, _ = call("work/result", {"work_run_id": records[0]["run"]["work_run_id"], "actor_id": "other"})
        assert status == 403
        path = app.roots.vault / SOURCE
        path.write_bytes(original + b"\nConcurrent owner edit.\n")
        try:
            stale_status, _ = call("work/result", {"work_run_id": records[0]["run"]["work_run_id"]})
            assert stale_status >= 400
        finally:
            path.write_bytes(original)
        return {"route": "POST /owner/proposal/read", "status": 200, "field_count": len(result["field_evidence"]),
                "body_actor_status": 403, "stale_source_status": stale_status,
                "evaluated_result_exports": exports,
                "approval_and_apply_exercised": False}
    http = await asyncio.to_thread(owner_http, exops, review)
    after = {p.relative_to(app.roots.vault).as_posix(): digest(p.read_bytes()) for p in app.roots.vault.rglob("*") if p.is_file()}
    assert all(after[p] == raw for p, raw in before.items())
    assert len(set(after) - set(before)) == 3, "Expected two candidate artifacts and one combined Pending"
    return {"histories": histories, "candidates": candidates, "canary": canary, "source_unchanged": True,
            "existing_vault_files_unchanged": True, "combined_pending_count": 1, "owner_http": http,
            "denials": denials, "tool_denials": runner.denials}


async def daily(root, route, decision):
    from support.metadata_fixture import SOURCE, application, binding
    from vaultops.adapters.core import digest
    from vaultops.application.exops import OwnerActor
    from vaultops.application.metadata_work import (
        DAILY_METHOD_ID,
        MetadataTeamExecutor,
        metadata_admission,
    )
    from vaultops.note_engine import parse_frontmatter

    app, canary = application(root, daily=True, no_evidence=decision == "no_evidence")
    source = app.reference(SOURCE)
    before = (app.roots.vault / SOURCE).read_bytes()
    steward = app.daily_stewardship()
    due = steward.due_check()
    assert any(r["source_reference"] == source for r in due["work_requests"])
    team = binding(app, route, DAILY_METHOD_ID)
    suffix = uuid4().hex
    spec, run = metadata_admission(app, team, source, method_id=DAILY_METHOD_ID, work_id="work.daily." + suffix,
        work_run_id="workrun.daily." + suffix, admission_receipt_id="admission.daily." + suffix, runner_binding=route)
    exops = composition(app, {team.team_id: team}, [spec])
    exops.submit_work(spec, run, actor=OwnerActor("owner.officer_smoke", "cli"), action_id="action.daily." + suffix,
        expires_at=datetime.now(UTC) + timedelta(hours=1), method_source_reference=source)
    runner = MetadataRendezvous(route, app)
    executor = MetadataTeamExecutor(exops, runner=runner, method_id=DAILY_METHOD_ID)
    history = await execute(exops, executor, run["work_run_id"])
    candidate = json.loads(runner.responses[-1]["tool_calls"][-1]["arguments"]["content"])
    assert (app.roots.vault / SOURCE).read_bytes() == before, "Team itself wrote canonical source"
    if decision == "no_evidence":
        assert candidate["fields"] == candidate["evidence"] == {}, candidate
        result = steward.record_no_evidence(run["work_run_id"], candidate)
        assert result["status"] == "no_evidence" and (app.roots.vault / SOURCE).read_bytes() == before
        assert not any(r["source_reference"] == source for r in steward.due_check()["work_requests"])
        http = None
    else:
        assert candidate["fields"] == {"tags": [canary]}, candidate
        effect = steward.record_candidate(run["work_run_id"], candidate)
        provisional = parse_frontmatter((app.roots.vault / SOURCE).read_text()).properties
        assert provisional["tags"] == [canary, "ai/pending"]
        def review(call):
            status, detail = call("daily/read", {"effect_id": effect["effect_id"]})
            assert status == 200, detail
            status, reviewed = call("daily/decide", {"effect_id": effect["effect_id"], "decision": decision})
            assert status == 200, reviewed
            return {"read_status": 200, "decision_status": status, "decision": decision}
        http = await asyncio.to_thread(owner_http, exops, review)
        final = parse_frontmatter((app.roots.vault / SOURCE).read_text()).properties
        assert final["tags"] == ([canary] if decision == "confirm" else [])
    return {"decision": decision, "history": history, "candidate": candidate, "owner_http": http,
            "team_canonical_effect": False, "provisional_owner_only": True, "tool_denials": runner.denials,
            "source_before_digest": digest(before)}


async def native_roundtrip(root, route):
    from vaultops.adapters.core import digest
    report = {"status": "PASS", "scope": "disposable native metadata Teams and daily exact-delta review",
        "sidebar": await sidebar(root / "sidebar", route),
        "daily": [await daily(root / decision, route, decision) for decision in ("confirm", "reject", "no_evidence")],
        "installed_modules": {}, "disposable_roots_removed": False,
        "excluded": ["production activation", "scheduler", "GUI", "combined Pending approval/apply", "truth evaluation"]}
    for relative in ("application/native_team.py", "application/native_team_temporal.py", "application/metadata_work.py",
        "application/metadata_temporal.py", "application/metadata_workflow.py", "application/temporal_contract.py",
        "application/temporal_work.py", "application/exops.py", "application/work_tools.py", "application/work_methods.py",
        "application/read_tools.py", "application/mcp_effects.py", "application/metadata_proposals.py",
        "application/daily_stewardship.py", "application/intake_templates.py", "interfaces/mcp_server.py",
        "interfaces/exops_web.py", "interfaces/loopback_owner.py"):
        installed = Path("/opt/knowledgeos/ops/src/vaultops", relative).read_bytes()
        assert installed == Path("/workspace/control/ops/src/vaultops", relative).read_bytes(), relative
        report["installed_modules"][relative] = digest(installed)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    args = parser.parse_args()
    if args.action == "host":
        if args.runner_binding is not None:
            parser.error("Host binds exact native adapter")
        path = args.evidence or Path(__file__).resolve().parents[5] / "Tmp/knowledgeos-native-metadata" / (uuid4().hex + ".json")
        harness.host(path, smoke_name="metadata_work_smoke.py", adapter_name="native_metadata.py", marker=MARKER, stages=STAGES)
    else:
        if args.runner_binding is None or args.evidence is not None:
            parser.error("Container needs only runner binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-metadata-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), args.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

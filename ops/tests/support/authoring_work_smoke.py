"""Two isolated actual Hermes/Temporal authoring cases with exact trusted-local Owner exports."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import normalization_work_smoke as officer

MARKER = "KNOWLEDGEOS_AUTHORING_EVIDENCE="
STAGES = ("source_evidence", "cited_draft", "project_evidence", "review_proposal")


class AuthoringRendezvous:
    def __init__(self, binding, app):
        self.binding, self.app = copy.deepcopy(binding), app
        self.requests, self.responses, self.denials = [], [], {}

    async def __call__(self, request):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        from support.authoring_fixture import CAPTURE
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "vaultops.interfaces.mcp_server", "--control-root", request["control_root"],
            "--work-run-id", request["work_run_id"], "--executor-id", request["executor_id"],
            "--graph-run-id", request["graph_run_id"],
        ], env={"PYTHONPATH": "/workspace/control/ops/src", "PYTHONDONTWRITEBYTECODE": "1"})
        stage = request["stage_id"]
        other = self.app.reference(CAPTURE)
        probes = {"ungranted_tool": ("index_status_read", {})}
        if stage == "source_evidence":
            probes["wrong_source"] = ("knowledge_read", {"resource_reference": other, "chunk_locator": request["source_chunks"][0]})
        elif stage == "project_evidence":
            probes["wrong_project"] = ("task_query", {"path_prefix": CAPTURE, "limit": 20})
        else:
            probes["writer_read"] = ("knowledge_read", {"resource_reference": request["source_reference"], "chunk_locator": request["source_chunks"][0]})
            if stage == "cited_draft":
                probes["wrong_source_proposal"] = ("proposal_draft_note", {"source_reference": other, **request["scope"], "draft_body": "Denied foreign source."})
            else:
                content = {"observations": ["Denied"], "interpretation": "Denied", "next_actions": ["Denied"], "citations": []}
                probes["wrong_source_proposal"] = ("proposal_project_review", {"source_reference": other, "content": json.dumps(content)})
        async with Client(parameters, mode="legacy") as client:
            for label, (name, args) in probes.items():
                result = await client.call_tool(name, args)
                assert result.is_error and result.structured_content["effect"] == "none", result
                self.denials[stage + "." + label] = result.structured_content
        self.requests.append(copy.deepcopy(request))
        print(officer.MODEL_REQUEST + json.dumps(request, ensure_ascii=False), flush=True)
        async with asyncio.timeout(360):
            line = await asyncio.to_thread(sys.stdin.readline)
        if not line or len(line) > 1024 * 1024:
            raise RuntimeError("Native authoring returned no bounded response; no retry")
        response = json.loads(line)
        self.responses.append(response)
        return response


async def connect_sdk():
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    async with asyncio.timeout(180):
        while True:
            try:
                client = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
                await client.workflow_service.register_namespace(RegisterNamespaceRequest(
                    namespace="knowledgeos", workflow_execution_retention_period={"seconds": 3600}))
                return client
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    return client
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)


def owner_http(exops, run_id, artifact, path, original):
    import http.client
    import threading

    from vaultops.interfaces.loopback_owner import create_loopback_owner
    server = create_loopback_owner(exops, actor_id="owner.authoring", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            connection.request("POST", "/owner/work/result", json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        foreign, _ = call({"work_run_id": run_id, "actor_id": "foreign"})
        assert foreign == 403
        status, response = call({"work_run_id": run_id})
        assert status == 200 and response["text"].encode() == artifact and response["truncated"] is False, response
        assert response["assessment"]["completion_state"] == "complete"
        path.write_bytes(original + b"\nStale authoring source.\n")
        stale_status, stale = call({"work_run_id": run_id})
        assert stale_status >= 400 and stale["errors"][0]["code"] == "RESOURCE_STALE", stale
        return {"route": "POST /owner/work/result", "status": status, "exact_bytes": True,
                "body_actor_status": foreign, "stale_source_status": stale_status}
    finally:
        path.write_bytes(original)
        server.shutdown()
        server.server_close()
        thread.join(timeout=15)


async def native_case(root, runner_binding, method_id, sdk):
    from clients.temporal import TemporalBinding
    from temporalio.worker import Worker

    from support.authoring_fixture import prepare
    from vaultops.adapters.core import AdmissionError, digest
    from vaultops.application.authoring_temporal import authoring_worker_components
    from vaultops.application.authoring_work import DRAFT_METHOD, AuthoringExecutor
    from vaultops.application.exops import OwnerActor
    from vaultops.application.temporal_contract import (
        AUTHORING_DRAFT_GRAPH_NAME,
        AUTHORING_DRAFT_WORKFLOW_TYPE,
        AUTHORING_REVIEW_GRAPH_NAME,
        AUTHORING_REVIEW_WORKFLOW_TYPE,
    )
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    exops, spec, run, binding, source, path, canary = prepare(root, runner_binding, method_id)
    app = exops.application
    before = {p.relative_to(app.roots.vault).as_posix(): digest(p.read_bytes()) for p in app.roots.vault.rglob("*") if p.is_file()}
    original = path.read_bytes()
    args = {"action_id": "action.authoring." + run["work_run_id"], "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "method_source_reference": source, "selected_scope": {"path": path.relative_to(app.roots.vault).as_posix(), "content_sha256": source["source_revision"]}}
    try:
        exops.submit_work(spec, run, actor=OwnerActor("foreign", "web"), **args)
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor authoring admission succeeded")
    exops.submit_work(spec, run, actor=OwnerActor("owner.authoring", "web"), **args)
    runner = AuthoringRendezvous(runner_binding, app)
    executor = AuthoringExecutor(exops, runner=runner, method_id=method_id)
    wire, graph_name = ((AUTHORING_DRAFT_WORKFLOW_TYPE, AUTHORING_DRAFT_GRAPH_NAME) if method_id == DRAFT_METHOD
                        else (AUTHORING_REVIEW_WORKFLOW_TYPE, AUTHORING_REVIEW_GRAPH_NAME))
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=wire, workflow_id_prefix="knowledgeos/",
        update_name="team.update.disabled", signal_name="team.signal.disabled", query_name="team.query.disabled", tls=False)
    scoped = await connect_owner_temporal(exops, transport)
    workflow, plugin = authoring_worker_components(exops, executor)
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin], identity=exops.worker_identity):
        receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"])
        result = await asyncio.wait_for(handle.result(), timeout=780)
        assert result["application_state"] == "recorded", result
        history = await handle.fetch_history()
        description = await handle.describe()
    scheduled = [e.activity_task_scheduled_event_attributes for e in history.events if e.HasField("activity_task_scheduled_event_attributes")]
    assert len(scheduled) == 4 and all(e.retry_policy.maximum_attempts == 1 for e in scheduled)
    assert [e.activity_type.name for e in scheduled] == [
        graph_name + "." + node for node in ("manager", *executor.policy.stages, "assess")]
    assert description.workflow_type == wire and description.task_queue == transport.task_queue
    record = app.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert record["spec"] == spec and record["method_source_reference"] == source
    assert description.namespace == transport.namespace and description.id == "knowledgeos/" + run["work_run_id"]
    started = history.events[0].workflow_execution_started_event_attributes
    payloads = await sdk.data_converter.decode(started.input.payloads)
    assert payloads == [{"owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
                         "action_id": record["action_id"], "content_digest": record["content_digest"],
                         "work_spec_digest": spec["spec_digest"]}]
    assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
    assert record["assessment"]["assessor_id"] == binding.eval_officer_executor_id
    assert binding.eval_officer_executor_id not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]]
    assert [r["stage_id"] for r in runner.requests] == list(executor.policy.stages)
    assert canary not in json.dumps(runner.requests[0]) and canary not in spec["objective"]
    assert canary in json.dumps(runner.responses[0]["tool_calls"])
    assert canary in runner.responses[1]["tool_calls"][0]["arguments"].get("draft_body", runner.responses[1]["tool_calls"][0]["arguments"].get("content", ""))
    pending = list((app.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1 and path.read_bytes() == original
    artifact = pending[0].read_bytes()
    assert record["run"]["result_reference"]["content_digest"] == digest(artifact)
    call = runner.responses[1]["tool_calls"][0]
    assert call["result"]["data"]["apply_allowed"] is (method_id == DRAFT_METHOD)
    if method_id == DRAFT_METHOD:
        assert not (app.roots.vault / json.loads(spec["objective"])["target_path"]).exists()
        assert "## 원문 발췌" in artifact.decode() and "## 제안된 내용" in artifact.decode()
        assert app.journal.read()["human_requests"] and not app.journal.read()["decisions"]
    else:
        plan = json.loads(call["arguments"]["content"])
        tasks = runner.requests[1]["evidence"]["task_locations"]
        assert tasks and all(t["locator"] in json.dumps(plan) and t["line_sha256"] in json.dumps(plan) for t in tasks)
        rows = runner.requests[1]["evidence"]["base_view"]["rows"]
        assert len(rows) == 1 and rows[0]["resource_reference"] == source
    http = await asyncio.to_thread(owner_http, exops, run["work_run_id"], artifact, path, original)
    after = {p.relative_to(app.roots.vault).as_posix(): digest(p.read_bytes()) for p in app.roots.vault.rglob("*") if p.is_file()}
    assert all(after.get(p) == d for p, d in before.items())
    assert set(after) - set(before) == {pending[0].relative_to(app.roots.vault).as_posix()}
    return {"status": "PASS", "method_id": method_id, "work_run_id": run["work_run_id"], "workflow_id": description.id,
            "temporal_run_id": description.run_id, "workflow_type": wire, "receipt": receipt,
            "history_activities": [e.activity_type.name for e in scheduled], "history_events": len(history.events),
            "spec": spec, "source_reference": source, "native_binding_digest": binding.native_binding_digest,
            "assessment": record["assessment"], "evaluations": record["evaluations"],
            "stages": [record[executor.policy.namespace + "_" + s] for s in executor.policy.stages],
            "artifact_digest": digest(artifact), "pending_count": 1, "apply_allowed": method_id == DRAFT_METHOD,
            "source_unchanged": True, "existing_vault_files_unchanged": True, "model_canary": canary,
            "model_canary_observed": True, "foreign_actor_denied": True, "model_scope_denials": runner.denials, "owner_http": http}


async def native_roundtrip(root, binding):
    from vaultops.adapters.core import digest
    from vaultops.application.authoring_work import DRAFT_METHOD, REVIEW_METHOD
    sdk = await connect_sdk()
    cases = []
    for index, method in enumerate((DRAFT_METHOD, REVIEW_METHOD)):
        case_root = root / str(index)
        case_root.mkdir()
        cases.append(await native_case(case_root, binding, method, sdk))
    modules = {}
    for relative in ("authoring_work.py", "authoring_temporal.py", "authoring_workflow.py", "native_team.py", "native_team_temporal.py",
                     "exops.py", "temporal_contract.py", "temporal_work.py", "work_tools.py", "knowledge.py"):
        installed = Path("/opt/knowledgeos/ops/src/vaultops/application", relative).read_bytes()
        maintained = Path("/workspace/control/ops/src/vaultops/application", relative).read_bytes()
        assert installed == maintained, "Installed authoring module differs: " + relative
        modules[relative] = digest(installed)
    return {"status": "PASS", "scope": "two disposable exact native authoring Teams", "cases": cases,
            "native_model_invocations": 4, "installed_modules": modules,
            "claims_excluded": ["production", "GUI", "canonical apply", "retries", "recovery", "model claim truth"],
            "disposable_roots_removed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    args = parser.parse_args()
    if args.action == "host":
        from support.native_team_smoke_host import host
        if args.runner_binding is not None or args.evidence is None:
            parser.error("Host needs --evidence only")
        host(args.evidence, adapter_name="native_authoring.py", smoke_name="authoring_work_smoke.py", stages=STAGES, marker=MARKER)
    else:
        if args.runner_binding is None or args.evidence is not None:
            parser.error("Container needs --runner-binding only")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-authoring-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), args.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()

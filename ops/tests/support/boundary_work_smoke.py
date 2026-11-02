"""Disposable real native accepted-Gateway Exchange and selected Maintenance proof."""
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
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.normalization_work_smoke import MODEL_REQUEST

MARKER = "KNOWLEDGEOS_BOUNDARY_EVIDENCE="
STAGES = ("gateway_evidence", "adaptation", "schema_diagnosis", "recovery_plan")


def plan(request):
    from vaultops.application.boundary_contract import disclosure
    if request["boundary_kind"] == "exchange":
        evidence = request["evidence"]
        return {"gateway_receipt_id": request["gateway_receipt_id"], "disclosure_scope": disclosure(evidence),
                "adaptation": "Review disclosed metadata only; foreign contents remain unavailable; human review required.",
                "citations": evidence["payload"]["resources"]}
    evidence = request["evidence"]
    return {"preimage_digest": request["source_reference"]["content_digest"],
            "unresolved_effect_ids": evidence["diagnostic"]["unresolved_effect_ids"], "steps": [{
                "action": "human_review", "diagnostic_digest": evidence["diagnostic_digest"],
                "diagnosis_complete": False, "unexamined": evidence["diagnostic"]["unexamined"],
                "instruction": "Operator must reconcile unresolved effects and examine backups before any repair."}]}


class BoundaryRendezvous:
    def __init__(self, binding, application):
        self.binding, self.application = copy.deepcopy(binding), application
        self.requests, self.responses, self.denials = [], [], {}

    async def __call__(self, request):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        from support.boundary_case import BASE
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "vaultops.interfaces.mcp_server", "--control-root", request["control_root"],
            "--work-run-id", request["work_run_id"], "--executor-id", request["executor_id"],
            "--graph-run-id", request["graph_run_id"]],
            env={"PYTHONPATH": "/workspace/control/ops/src", "PYTHONDONTWRITEBYTECODE": "1"})
        stage = request["stage_id"]
        probes = {"ungranted_tool": ("index_status_read", {})}
        if stage == "gateway_evidence":
            probes["foreign_receipt"] = ("gateway_evidence_read", {"receipt_id": "admission." + "0" * 32})
            probes["foreign_filesystem_reference"] = ("knowledge_read", {
                "resource_reference": {**request["source_reference"], "owner_operation_id": "syntheticexchange"},
                "chunk_locator": "/body"})
        elif stage == "schema_diagnosis":
            probes["foreign_source"] = ("vault_diagnose", {"resource_reference": self.application.reference(BASE)})
        else:
            action = "proposal_adaptation" if stage == "adaptation" else "proposal_recovery_plan"
            valid = plan(request)
            fields = ("gateway_receipt_id", "disclosure_scope", "citations") if stage == "adaptation" else (
                "preimage_digest", "unresolved_effect_ids", "steps")
            for field in fields:
                changed = copy.deepcopy(valid)
                if field == "gateway_receipt_id":
                    changed[field] = "admission." + "0" * 32
                elif field == "disclosure_scope":
                    changed[field]["scope"] = "unrestricted"
                elif field == "citations":
                    changed[field] = [request["source_reference"]]
                elif field == "preimage_digest":
                    changed[field] = "sha256:" + "0" * 64
                elif field == "unresolved_effect_ids":
                    changed[field] = []
                else:
                    changed[field][0]["diagnosis_complete"] = True
                probes["changed_" + field] = (action, {"source_reference": request["source_reference"],
                                                       "content": json.dumps(changed)})
            probes["foreign_source"] = (action, {"source_reference": self.application.reference(BASE),
                                                 "content": json.dumps(valid)})
        async with Client(parameters, mode="legacy") as client:
            for label, (name, arguments) in probes.items():
                result = await client.call_tool(name, arguments)
                assert result.is_error and result.structured_content["effect"] == "none", result
                self.denials[stage + "." + label] = result.structured_content
        self.requests.append(copy.deepcopy(request))
        print(MODEL_REQUEST + json.dumps(request, ensure_ascii=False), flush=True)
        async with asyncio.timeout(360):
            line = await asyncio.to_thread(sys.stdin.readline)
        if not line or len(line) > 1024 * 1024:
            raise RuntimeError("Boundary native response unavailable; no retry")
        response = json.loads(line)
        self.responses.append(response)
        return response


def owner_http(case, artifact):
    from vaultops.interfaces.loopback_owner import create_loopback_owner
    server = create_loopback_owner(case["exops"], actor_id="owner.officer_smoke", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run_id = case["run"]["work_run_id"]

    def call(body):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            connection.request("POST", "/owner/work/result", json.dumps(body), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    original = case["source_path"].read_bytes()
    try:
        injected, _ = call({"work_run_id": run_id, "actor_id": "foreign"})
        assert injected == 403
        status, result = call({"work_run_id": run_id})
        assert status == 200 and result["text"].encode() == artifact and result["apply_allowed"] is False, result
        assert result["assessment"]["completion_state"] == "complete" and result["evidence"]
        case["source_path"].write_bytes(original + b"\nStale selected source.\n")
        stale_status, _ = call({"work_run_id": run_id})
        assert stale_status >= 400
        return {"status": status, "body_actor_status": injected,
                "stale_source_status": stale_status, "report": result["report"], "evidence": result["evidence"]}
    finally:
        case["source_path"].write_bytes(original)
        server.shutdown()
        server.server_close()
        thread.join(timeout=15)


def gateway_denials(case):
    from vaultops.adapters.core import AdmissionError
    from vaultops.application.read_tools import KnowledgeReadTools
    app = case["exops"].application
    receipt_id = case["accepted"][0]["receipt_id"]
    denied = {}
    expired = replace(app, clock=lambda: app.clock() + timedelta(seconds=43201))
    try:
        KnowledgeReadTools(expired).gateway_evidence_read(receipt_id)
    except AdmissionError as error:
        denied["expiry"] = error.code
    else:
        raise AssertionError("Expired accepted Gateway evidence remained readable")
    state = app.journal.read()
    correlations = copy.deepcopy(state["correlations"])
    with app.journal.writer() as writer:
        writer.value["correlations"] = {}
        writer.commit(app.clock())
    try:
        KnowledgeReadTools(app).gateway_evidence_read(receipt_id)
    except AdmissionError as error:
        denied["correlation"] = error.code
    else:
        raise AssertionError("Uncorrelated evidence remained readable")
    finally:
        with app.journal.writer() as writer:
            writer.value["correlations"] = correlations
            writer.commit(app.clock())
    return denied


async def run_case(root, binding, kind, sdk):
    from clients.temporal import TemporalBinding
    from temporalio.worker import Worker

    from support.boundary_case import make_case
    from vaultops.adapters.core import digest
    from vaultops.application.boundary_temporal import (
        exchange_worker_components,
        maintenance_worker_components,
    )
    from vaultops.application.temporal_contract import (
        EXCHANGE_GRAPH_NAME,
        EXCHANGE_WORKFLOW_TYPE,
        MAINTENANCE_GRAPH_NAME,
        MAINTENANCE_WORKFLOW_TYPE,
    )
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    case = make_case(root, binding, BoundaryRendezvous, kind, unresolved=kind == "maintenance")
    exops, run, runner = case["exops"], case["run"], case["runner"]
    before = {p.relative_to(exops.application.roots.vault).as_posix(): digest(p.read_bytes())
              for p in exops.application.roots.vault.rglob("*") if p.is_file()}
    gateway_checks = gateway_denials(case) if kind == "exchange" else {}
    workflow_type = EXCHANGE_WORKFLOW_TYPE if kind == "exchange" else MAINTENANCE_WORKFLOW_TYPE
    graph_name = EXCHANGE_GRAPH_NAME if kind == "exchange" else MAINTENANCE_GRAPH_NAME
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=workflow_type, workflow_id_prefix="knowledgeos/",
        update_name="team.update.disabled", signal_name="team.signal.disabled", query_name="team.query.disabled", tls=False)
    scoped = await connect_owner_temporal(exops, transport)
    workflow, plugin = (exchange_worker_components if kind == "exchange" else maintenance_worker_components)(exops, case["executor"])
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin], identity=exops.worker_identity):
        receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"])
        result = await asyncio.wait_for(handle.result(), timeout=780)
        assert result["application_state"] == "recorded", result
        history = await handle.fetch_history()
        description = await handle.describe()
    scheduled = [event.activity_task_scheduled_event_attributes for event in history.events
                 if event.HasField("activity_task_scheduled_event_attributes")]
    expected = ["manager", *case["policy"].stages, "assess"]
    assert [item.activity_type.name for item in scheduled] == [graph_name + "." + name for name in expected]
    assert all(item.retry_policy.maximum_attempts == 1 for item in scheduled)
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert record["run"]["state"] == "completed" and record["assessment"]["completion_state"] == "complete"
    assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
    assert record["assessment"]["assessor_id"] not in record["spec"]["allowed_executor_ids"]
    assert len(runner.requests) == len(runner.responses) == 2
    pending = list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1
    artifact = pending[0].read_bytes()
    assert record["run"]["result_reference"]["content_digest"] == digest(artifact)
    if kind == "exchange":
        assert case["canary"] not in json.dumps(runner.requests[0])
        assert case["canary"] in json.dumps(runner.responses[0]["tool_calls"][0]["result"])
        assert case["canary"] in runner.responses[0]["final_text"] and case["canary"].encode() in artifact
    else:
        diagnostic = runner.responses[0]["tool_calls"][0]["result"]["data"]
        assert diagnostic["unresolved_effect_ids"] == [case["diagnostic_canary"]] and diagnostic["diagnosis_complete"] is False
        assert case["diagnostic_canary"] not in json.dumps(runner.requests[0])
        assert case["diagnostic_canary"] in runner.responses[0]["final_text"]
        assert case["diagnostic_canary"].encode() in artifact
    http = await asyncio.to_thread(owner_http, case, artifact)
    after = {p.relative_to(exops.application.roots.vault).as_posix(): digest(p.read_bytes())
             for p in exops.application.roots.vault.rglob("*") if p.is_file()}
    assert all(after.get(path) == value for path, value in before.items())
    assert set(after) - set(before) == {pending[0].relative_to(exops.application.roots.vault).as_posix()}
    return {"method_id": case["policy"].method_id, "work_run_id": run["work_run_id"],
            "native_binding_digest": case["binding"].native_binding_digest, "receipt": receipt,
            "workflow_type": description.workflow_type, "temporal_run_id": description.run_id,
            "history_activities": [item.activity_type.name for item in scheduled], "history_event_count": len(history.events),
            "assessment": record["assessment"], "evaluations": record["evaluations"], "owner_http": http,
            "model_scope_denials": runner.denials, "gateway_denials": gateway_checks,
            "stages": {stage: record[kind + "_" + stage] for stage in case["policy"].stages},
            "source_unchanged": True, "pending_count": 1, "artifact_digest": digest(artifact),
            "accepted_gateway": case["accepted"], "native_model_invocations": 2}


async def native_roundtrip(root, binding):
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    from vaultops.adapters.core import digest
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
                await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(
                    namespace="knowledgeos", workflow_execution_retention_period={"seconds": 3600}))
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    cases = []
    for kind in ("exchange", "maintenance"):
        cases.append(await run_case(root / kind, binding, kind, sdk))
    modules = {}
    for relative in ("application/boundary_work.py", "application/boundary_contract.py", "application/boundary_temporal.py",
                     "application/boundary_workflow.py", "application/native_team.py", "application/native_team_temporal.py",
                     "application/gateway.py", "application/read_tools.py", "application/mcp_effects.py", "application/work_tools.py",
                     "application/exops.py", "application/temporal_contract.py", "application/temporal_work.py",
                     "adapters/owner_journal.py", "interfaces/mcp_server.py", "interfaces/exops_web.py", "interfaces/loopback_owner.py"):
        installed = Path("/opt/knowledgeos/ops/src/vaultops", relative).read_bytes()
        assert installed == Path("/workspace/control/ops/src/vaultops", relative).read_bytes(), relative
        modules[relative] = digest(installed)
    return {"status": "PASS", "cases": cases, "installed_modules": modules,
            "claims_excluded": ["external Gateway deployment", "real foreign domain usefulness", "production", "GUI",
                                "canonical adoption", "repair execution", "complete Vault diagnosis", "provider/effect retries"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    args = parser.parse_args()
    if args.action == "host":
        from support.native_team_smoke_host import host
        if args.runner_binding is not None:
            parser.error("Host binds the exact native adapter itself")
        evidence = args.evidence or Path(__file__).resolve().parents[5] / "Tmp/knowledgeos-native-boundary" / (uuid4().hex + ".json")
        host(evidence, adapter_name="native_boundary.py", smoke_name=Path(__file__).name, stages=STAGES, marker=MARKER)
    else:
        if args.runner_binding is None or args.evidence is not None:
            parser.error("Container run requires only --runner-binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-boundary-") as temporary:
            report = asyncio.run(native_roundtrip(Path(temporary), args.runner_binding))
        report["disposable_roots_removed"] = not Path(temporary).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

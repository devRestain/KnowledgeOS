"""Owner-pinned Team method, Graph gate, and scoped MCP wire pilot."""

from __future__ import annotations

import asyncio
import copy
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.exops import OwnerActor
from vaultops.application.team_catalog import SpecialistBinding
from vaultops.application.work import graph_content_digest, segment_digest, work_spec_digest
from vaultops.application.work_methods import WorkMethodCatalog
from vaultops.application.work_tools import TeamToolSession

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"


def test_six_team_methods_bind_exact_manager_and_stage_tools(tmp_path):
    exops, fixture = make_exops(tmp_path)
    methods = WorkMethodCatalog.load(exops.application.roots.control)
    assert {method["team_id"] for method in methods.methods.values()} == set(exops.catalog.teams)
    for method_id, method in methods.methods.items():
        spec = copy.deepcopy(fixture.spec)
        spec["team_id"] = method["team_id"]
        spec["method_contract_ref"] = methods.reference(method_id)
        spec["allowed_profiles"] = [exops.catalog.profile(stage["role_id"])
                                    for stage in method["required_stages"]]
        spec["allowed_executor_ids"] = [stage["role_id"] + ".one"
                                        for stage in method["required_stages"]]
        spec["manager_executor_id"] = exops.catalog.teams[method["team_id"]]["manager_role_id"] + ".one"
        spec["spec_digest"] = work_spec_digest(spec)
        assert methods.admitted_method(spec, exops.contracts)["method_id"] == method_id
        manager = exops.catalog.teams[method["team_id"]]["manager_role_id"]
        assert methods.allowed_tools(spec, manager, exops.contracts) == (
            "work_context_read", "method_read", "graph_segment_propose")
        for stage in method["required_stages"]:
            assert methods.allowed_tools(spec, stage["role_id"], exops.contracts) == tuple(stage["tools"])
        wrong = copy.deepcopy(spec)
        wrong["team_id"] = next(team for team in exops.catalog.teams if team != method["team_id"])
        wrong["spec_digest"] = work_spec_digest(wrong)
        with pytest.raises(AdmissionError):
            methods.admitted_method(wrong, exops.contracts)


def _pilot(tmp_path):
    exops, fixture = make_exops(tmp_path)
    catalog = exops.catalog
    methods = WorkMethodCatalog.load(exops.application.roots.control)
    original = exops.teams["knowledge-curation"]
    specialists = (
        SpecialistBinding("knowledgeos.triage", "knowledgeos.triage.one",
                          catalog.profile("knowledgeos.triage")),
        SpecialistBinding("knowledgeos.normalizer", "knowledgeos.normalizer.one",
                          catalog.profile("knowledgeos.normalizer")),
    )
    candidate = replace(original, specialists=specialists)
    binding = replace(candidate, binding_reference={
        **original.binding_reference, "content_digest": digest(canonical(candidate.content()))})
    exops.teams["knowledge-curation"] = binding
    exops.current_team_binding = lambda team_id: binding if team_id == binding.team_id else None
    fixture.spec["allowed_executor_ids"] = [item.executor_id for item in specialists]
    fixture.spec["allowed_profiles"] = [dict(item.profile) for item in specialists]
    fixture.spec["method_contract_ref"] = methods.reference("knowledgeos.capture_normalization_team")
    fixture.spec["spec_digest"] = work_spec_digest(fixture.spec)
    fixture.run["work_spec_digest"] = fixture.spec["spec_digest"]
    fixture.run["binding_reference"] = dict(binding.binding_reference)
    source_reference = exops.application.reference(SOURCE)
    exops.director_method_source_admitted = (
        lambda spec, selected: spec["admission_receipt_id"] == "admission.synthetic.one"
        and dict(selected) == source_reference)
    source_artifact = {**fixture.artifacts["a"],
                       "artifact_id": "artifact.synthetic.capture",
                       "source_revision": source_reference["source_revision"],
                       "content_digest": source_reference["content_digest"]}
    artifacts = {source_artifact["artifact_id"]: source_artifact}
    receipt = exops.submit_work(
        fixture.spec, fixture.run, actor=OwnerActor("owner.user", "cli"),
        action_id="action.method.start", expires_at=datetime.now(UTC) + timedelta(hours=1),
        method_source_reference=source_reference)
    exops.record_dispatch(fixture.run["work_run_id"], action_id=receipt["action_id"],
                          outcome="accepted", workflow_id="knowledgeos/" + fixture.run["work_run_id"])
    running = exops.worker_started(
        fixture.run["work_run_id"], worker_identity="knowledgeos.worker.one",
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        workflow_id="knowledgeos/" + fixture.run["work_run_id"])
    bundle = fixture.bundle()
    graph = bundle["graph_spec"]
    base = {"graph_id": graph["graph_id"], "graph_revision": graph["graph_revision"]}
    triage = {**bundle["nodes"][1], "node_id": "node.triage"}
    bundle["nodes"].insert(1, triage)
    bundle["edges"] = [
        {**base, "edge_id": "edge.start.triage", "source_node_id": "node.start",
         "target_node_id": "node.triage", "condition": "always"},
        {**base, "edge_id": "edge.triage.write", "source_node_id": "node.triage",
         "target_node_id": "node.write", "condition": "always"},
        {**base, "edge_id": "edge.write.finish", "source_node_id": "node.write",
         "target_node_id": "node.finish", "condition": "always"},
    ]
    graph["node_ids"] = [item["node_id"] for item in bundle["nodes"]]
    graph["edge_ids"] = [item["edge_id"] for item in bundle["edges"]]
    graph["graph_digest"] = graph_content_digest(graph, bundle["nodes"], bundle["edges"])
    first = bundle["assignments"][0]
    first.update(executor_id="knowledgeos.normalizer.one",
                 **catalog.profile("knowledgeos.normalizer"),
                 work_spec_digest=fixture.spec["spec_digest"])
    bundle["assignments"].insert(0, {
        **first, "node_id": "node.triage", "executor_id": "knowledgeos.triage.one",
        **catalog.profile("knowledgeos.triage")})
    bundle["segment"]["work_spec_digest"] = fixture.spec["spec_digest"]
    bundle["segment"]["graph_digest"] = graph["graph_digest"]
    bundle["segment"]["input_references"] = [source_artifact]
    bundle["segment"]["segment_digest"] = segment_digest(bundle["segment"])
    return exops, fixture, running, bundle, artifacts, source_reference


def test_method_graph_and_profile_grants_fail_closed(tmp_path):
    exops, fixture, _running, bundle, artifacts, source = _pilot(tmp_path)
    manager = TeamToolSession(exops.application, fixture.run["work_run_id"],
                              fixture.spec["manager_executor_id"])
    assert manager.tools() == ("work_context_read", "method_read", "graph_segment_propose")
    assert manager.method_view()["method_contract_ref"] == fixture.spec["method_contract_ref"]
    with pytest.raises(AdmissionError):
        manager.authorize("proposal_create", {"source_reference": source})
    altered = copy.deepcopy(bundle)
    altered["assignments"][0]["node_id"] = "node.write"
    altered["assignments"][1]["node_id"] = "node.triage"
    with pytest.raises(AdmissionError, match="method stages"):
        WorkMethodCatalog.load(exops.application.roots.control).validate_segment(
            spec=fixture.spec, run=_running, bundle=altered,
            contracts=exops.contracts, artifacts=artifacts)
    stale = copy.deepcopy(fixture.spec)
    stale["method_contract_ref"]["content_digest"] = "sha256:" + "0" * 64
    stale["spec_digest"] = work_spec_digest(stale)
    with pytest.raises(AdmissionError, match="method differs"):
        WorkMethodCatalog.load(exops.application.roots.control).admitted_method(
            stale, exops.contracts)
    foreign_team = copy.deepcopy(fixture.spec)
    foreign_team["team_id"] = "evidence-research"
    foreign_team["spec_digest"] = work_spec_digest(foreign_team)
    with pytest.raises(AdmissionError, match="does not apply"):
        WorkMethodCatalog.load(exops.application.roots.control).admitted_method(
            foreign_team, exops.contracts)
    missing = copy.deepcopy(bundle)
    missing["nodes"] = [node for node in missing["nodes"] if node["node_id"] != "node.triage"]
    missing["edges"] = [
        {**missing["edges"][0], "edge_id": "edge.start.write",
         "target_node_id": "node.write"},
        missing["edges"][2],
    ]
    missing["assignments"] = [item for item in missing["assignments"]
                              if item["node_id"] != "node.triage"]
    missing["graph_spec"]["node_ids"] = [node["node_id"] for node in missing["nodes"]]
    missing["graph_spec"]["edge_ids"] = [edge["edge_id"] for edge in missing["edges"]]
    missing["graph_spec"]["graph_digest"] = graph_content_digest(
        missing["graph_spec"], missing["nodes"], missing["edges"])
    missing["segment"]["graph_digest"] = missing["graph_spec"]["graph_digest"]
    missing["segment"]["segment_digest"] = segment_digest(missing["segment"])
    with pytest.raises(AdmissionError) as denied_missing:
        WorkMethodCatalog.load(exops.application.roots.control).validate_segment(
            spec=fixture.spec, run=_running, bundle=missing,
            contracts=exops.contracts, artifacts=artifacts)
    assert denied_missing.value.code == "METHOD_STAGE_DENIED"
    source_admission = exops.director_method_source_admitted
    exops.director_method_source_admitted = lambda _spec, _source: False
    with pytest.raises(AdmissionError, match="Director source admission"):
        exops.admit_method_segment(
            fixture.run["work_run_id"], manager_executor_id=fixture.spec["manager_executor_id"],
            bundle=bundle, artifacts=artifacts, source_reference=source)
    exops.director_method_source_admitted = source_admission
    wrong_pair = copy.deepcopy(bundle)
    wrong_pair["assignments"][0]["executor_id"] = "knowledgeos.normalizer.one"
    with pytest.raises(AdmissionError, match="Profile"):
        exops.admit_method_segment(
            fixture.run["work_run_id"], manager_executor_id=fixture.spec["manager_executor_id"],
            bundle=wrong_pair, artifacts=artifacts, source_reference=source)
    exops.admit_method_segment(
        fixture.run["work_run_id"], manager_executor_id=fixture.spec["manager_executor_id"],
        bundle=bundle, artifacts=artifacts, source_reference=source)
    current = exops.application.journal.read()["intents"][
        "exops.work." + fixture.run["work_run_id"]]["run"]
    waiting = {**current, "revision": current["revision"] + 1,
               "control_revision": current["control_revision"] + 1,
               "state": "awaiting_human", "active_graph_run_id": None}
    with pytest.raises(AdmissionError, match="proposal receipt"):
        exops.worker_finished(
            fixture.run["work_run_id"], worker_identity="knowledgeos.worker.one",
            action_id="action.method.start", next_run=waiting,
            segments=(bundle["segment"],))
    triage = TeamToolSession(exops.application, fixture.run["work_run_id"],
                             "knowledgeos.triage.one", bundle["segment"]["graph_run_id"])
    normalizer = TeamToolSession(exops.application, fixture.run["work_run_id"],
                                 "knowledgeos.normalizer.one", bundle["segment"]["graph_run_id"])
    assert triage.tools() == ()
    assert normalizer.tools() == ("proposal_create",)
    with pytest.raises(AdmissionError):
        normalizer.authorize("knowledge_retrieve", {"query": "all"})
    with pytest.raises(AdmissionError):
        normalizer.authorize("proposal_create", {"source_reference": {**source, "content_digest": "sha256:" + "0" * 64}})
    normalizer.authorize("proposal_create", {"source_reference": source})


def test_single_mcp_wire_lists_all_tools_but_checks_manager_and_profile(tmp_path):
    exops, fixture, _running, bundle, artifacts, source = _pilot(tmp_path)
    run_id = fixture.run["work_run_id"]

    def params(executor_id, graph_run_id=None):
        args = ["-m", "vaultops.mcp_server", "--control-root",
                str(exops.application.roots.control), "--work-run-id", run_id,
                "--executor-id", executor_id]
        if graph_run_id:
            args.extend(["--graph-run-id", graph_run_id])
        return StdioServerParameters(command=sys.executable, args=args,
                                     env={"PYTHONPATH": "/workspace/control/ops/src"}, cwd=tmp_path)

    async def exercise():
        async with Client(params(fixture.spec["manager_executor_id"]), mode="legacy") as client:
            assert len((await client.list_tools()).tools) == 24
            view = await client.call_tool("method_read", {})
            assert view.structured_content["data"]["method_contract_ref"] == fixture.spec["method_contract_ref"]
            denied = await client.call_tool("proposal_create", {"source_reference": source})
            assert denied.is_error
        exops.admit_method_segment(run_id, manager_executor_id=fixture.spec["manager_executor_id"],
                                   bundle=bundle, artifacts=artifacts, source_reference=source)
        async with Client(params("knowledgeos.normalizer.one", bundle["segment"]["graph_run_id"]),
                          mode="legacy") as client:
            assert len((await client.list_tools()).tools) == 24
            denied = await client.call_tool("proposal_create", {"source_reference": {**source,
                "content_digest": "sha256:" + "0" * 64}})
            assert denied.is_error and denied.structured_content["effect"] == "none"
            created = await client.call_tool("proposal_create", {"source_reference": source})
            assert not created.is_error and created.structured_content["effect"] == "proposal_created"
            replay = await client.call_tool("proposal_create", {"source_reference": source})
            assert not replay.is_error and replay.structured_content["effect"] == "proposal_replayed"

    asyncio.run(exercise())

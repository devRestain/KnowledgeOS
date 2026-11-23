"""AgentFabric Core conformance (Blueprint §7): exact adoption pins, and records produced
through the ExOps API validated against the pinned ``Core/schemas``."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from conftest import conforming_note, core_root, core_validate, enhance_scripts, evaluator_script
from operationkit.canonical import canonical

import knowledgeos
from knowledgeos.config import ROOT
from knowledgeos.core_records import pins

IDEA = "40_Knowledge/Ideas/Tools shape thinking.md"
RELATED = "40_Knowledge/Notes/Related.md"
NOT_CORE = ("work_id", "pins", "unsupported")
SOURCE_MANIFEST = Path(knowledgeos.__file__).resolve().parents[2] / "pyproject.toml"
SOURCE_VERSION = tomllib.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))["project"]["version"]


def core_digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def without(record, key):
    return {name: value for name, value in record.items() if name != key}


def core_of(kos, work_id):
    status, core = kos.get(f"/api/works/{work_id}/core")
    assert status == 200, core
    assert core["work_id"] == work_id and core["pins"] == pins()
    validated = 0
    for name, records in core.items():
        if name in NOT_CORE:
            continue
        for record in records:
            core_validate(name, record)
            validated += 1
    assert validated
    return core


def assert_bound(core):
    """Every run-bound record names the one WorkSpec by its recomputable digest."""
    [spec] = core["WorkSpec"]
    assert spec["spec_digest"] == core_digest(without(spec, "spec_digest"))
    for name in ("GraphAssignment", "GraphSegment", "RunEvaluation", "CompletionAssessment", "RemainingWork",
                 "HumanActionResponse"):
        for record in core[name]:
            if "work_spec_digest" in record:
                assert record["work_spec_digest"] == spec["spec_digest"], name
    for segment in core["GraphSegment"]:
        assert segment["segment_digest"] == core_digest(without(segment, "segment_digest"))
    return spec


@pytest.fixture
def notes(vault):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n\nA notebook changes what I notice.\n"))
    vault.write(RELATED, conforming_note("knowledge", "seed", body="Writing by hand slows me down. [[Tools shape thinking]]\n"))
    return vault


def test_CORE_1_adoption_pins_match_the_core_release():
    root = core_root()
    manifest_bytes = (root / "release" / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    catalog = json.loads((root / "contracts" / "catalog.json").read_text(encoding="utf-8"))
    ontology = json.loads((root / "ontology" / "common.generated.json").read_text(encoding="utf-8"))
    adoption = json.loads((ROOT / "config" / "core-adoption.json").read_text(encoding="utf-8"))
    selected = pins()

    assert (
        selected["core_version"]
        == manifest["core_release_version"]
        == catalog["core_release_version"]
        == catalog["catalog_version"]
    )
    assert selected["core_digest"] == manifest["release_digest"]
    assert manifest["release_digest"] == core_digest(
        {"core_release_version": manifest["core_release_version"], "files": manifest["files"]})
    assert selected["schema_version"] == catalog["schema_bundle_version"]
    assert selected["semantic_version"] == ontology["version"] == catalog["semantic_bundle"]["version"]
    assert selected["semantic_digest"] == core_digest(ontology)
    assert any(combination["core_release"] == selected["core_version"]
               and combination["schema_bundle"] == selected["schema_version"]
               and combination["semantic_bundle"] == selected["semantic_version"]
               for combination in catalog["supported_combinations"])
    work_spec = json.loads((root / "schemas" / "WorkSpec.schema.json").read_text(encoding="utf-8"))["properties"]
    for key in ("core_version", "schema_version", "semantic_version", "semantic_digest"):
        assert work_spec[key]["const"] == selected[key]
    assert adoption["core_manifest_sha256"] == "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    assert adoption["semantic_package_id"] == ontology["package_id"] == catalog["semantic_bundle"]["package_id"]


def test_CORE_2_health_is_a_core_health_report(kos):
    status, health = kos.get("/api/health")
    assert status == 200
    schema = json.loads((core_root() / "schemas" / "HealthReport.schema.json").read_text(encoding="utf-8"))
    assert set(health) - set(schema["properties"]) == {"dependencies"}  # the KnowledgeOS extension
    core_validate("HealthReport", without(health, "dependencies"))
    assert health["ready"] and health["healthy"] and health["blockers"] == []


def test_CORE_3_officer_work_and_human_action_are_core_records(kos, vault):
    path = "40_Knowledge/Ideas/Thought.md"
    vault.write(path, "# Tools\n\nTools shape how we think.\n")
    preview = kos.run("standardize.preview", {"paths": [path], "digests": {path: vault.sha(path)}})
    assert preview["status"] == "completed", preview
    done = kos.run("standardize.confirm", {"preview_id": preview["outcome"]["preview_id"]})
    assert done["status"] == "completed", done

    core = core_of(kos, preview["work_id"])
    assert core["unsupported"] == [], "Core conformance remains incomplete until an allowed engine is adopted"
    spec = assert_bound(core)
    assert spec["target_kind"] == "officer" and spec["delegation_mode"] == "officer_direct"
    assert spec["officer_executor_id"] == "standardization_officer"
    assert {c["criterion_id"] for c in spec["acceptance_criteria"]} >= {"std.result_valid", "std.minimal_diff"}
    [graph] = core["GraphSpec"]
    assert graph["engine_id"] == "agentfabric.operation.temporal_langgraph"
    assert graph["engine_version"] == SOURCE_VERSION
    assert graph["graph_digest"] == core_digest(without(graph, "graph_digest"))
    assert graph["node_ids"] == ["standardization_officer", "evaluation"]
    assert {a["node_id"]: a["selection_authority"] for a in core["GraphAssignment"]} == {
        "standardization_officer": "owner_officer", "evaluation": "owner_officer"}
    [segment] = core["GraphSegment"]
    assert segment["segment_index"] == 1 and segment["trigger_kind"] == "initial"
    [evaluation] = core["RunEvaluation"]
    assert set(evaluation["passed_criterion_ids"]) == set(evaluation["criterion_ids"])
    assert evaluation["evidence_references"] and all(
        ref["availability"] == "unavailable" for ref in evaluation["evidence_references"])
    [assessment] = core["CompletionAssessment"]
    assert assessment["completion_state"] == "complete" and core["RemainingWork"] == []

    [response] = core["HumanActionResponse"]
    assert response["chosen_action"] == "approve" and response["actor_id"] == "user.local"
    assert response["submission_state"] == "submitted"
    assert response["work_run_id"] == evaluation["work_run_id"]


def test_CORE_4_team_work_report_citations_and_teamreview_are_core_records(kos, notes, vault):
    enhance_scripts(kos.runner)
    kos.runner.script("eval_officer", [evaluator_script(unmet={"enh.tensions_supported": "unsupported"}),
                                       evaluator_script()])
    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    considered = kos.run("teamreview.considered", {"work_id": receipt["work_id"], "proposal_index": 0})
    assert considered["status"] == "completed", considered

    for citation in receipt["outcome"]["citations"]:
        core_validate("ResourceReference", citation["resource_reference"])
        assert citation["resource_reference"]["content_digest"] in {f"sha256:{vault.sha(p)}" for p in (IDEA, RELATED)}
        assert citation["chunk_hash"].startswith("sha256:") and len(citation["chunk_hash"]) == 71

    core = core_of(kos, receipt["work_id"])
    assert core["unsupported"] == [], "Core conformance remains incomplete until an allowed engine is adopted"
    spec = assert_bound(core)
    assert spec["target_kind"] == "team" and spec["delegation_mode"] == "manager_bounded"
    assert spec["team_id"] == "thinking_team" and spec["manager_executor_id"] == "thinking_manager"
    assert spec["eval_officer_executor_id"] == "eval_officer"
    assert all(graph["engine_id"] == "agentfabric.operation.temporal_langgraph" for graph in core["GraphSpec"])
    assert all(graph["engine_version"] == SOURCE_VERSION for graph in core["GraphSpec"])
    assert all(graph["graph_digest"] == core_digest(without(graph, "graph_digest")) for graph in core["GraphSpec"])

    first, second = core["GraphSegment"]
    [remaining] = core["RemainingWork"]
    assert remaining["unmet_criterion_ids"] == ["enh.tensions_supported"]
    assert second["segment_index"] == 2 and second["trigger_kind"] == "remaining_work"
    assert second["predecessor_graph_run_id"] == first["graph_run_id"]
    assert second["trigger_reference_id"] == remaining["remaining_work_id"]
    assert second["trigger_digest"] == core_digest(remaining)
    assert second["input_references"]  # the first segment's handoffs
    assessments = core["CompletionAssessment"]
    assert [a["completion_state"] for a in assessments] == ["incomplete", "complete"]
    assert remaining["assessment_digest"] == core_digest(assessments[0])
    evaluation_nodes = [a for a in core["GraphAssignment"] if a["node_id"] == "evaluation"]
    assert len(evaluation_nodes) == 2
    assert all(a["selection_authority"] == "owner_officer" and a["executor_id"] == "eval_officer" for a in evaluation_nodes)
    assert all(a["selection_authority"] == "bounded_manager" for a in core["GraphAssignment"] if a["node_id"] != "evaluation")

    report_bytes = vault.read(receipt["outcome"]["report"])
    by_id = {ref["artifact_id"]: ref for ref in core["ArtifactReference"]}
    report = by_id[f"{receipt['work_id']}.report.1"]
    assert report["content_digest"] == "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    assert report["size_bytes"] == len(report_bytes) and report["media_type"] == "text/markdown"
    assert f"{receipt['work_id']}.team_review" in by_id
    [action] = core["HumanActionResponse"]
    assert action["chosen_action"] == "inspect" and action["work_spec_digest"] == spec["spec_digest"]


def test_CORE_5_unmet_team_work_reports_remaining_work(kos, notes):
    enhance_scripts(kos.runner)
    kos.runner.script("eval_officer", evaluator_script(unmet={"enh.tensions_supported": "the notes do not disagree"}))
    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "unmet", receipt

    core = core_of(kos, receipt["work_id"])
    assert_bound(core)
    assert [a["completion_state"] for a in core["CompletionAssessment"]] == ["incomplete", "incomplete"]
    assert len(core["RemainingWork"]) == 2
    last = core["RemainingWork"][-1]
    assert last["unmet_criterion_ids"] == ["enh.tensions_supported"]
    assert [gap["criterion_id"] for gap in last["gaps"]] == ["enh.tensions_supported"]
    assert core["ArtifactReference"] == []  # nothing was written


def test_CORE_6_unknown_work_has_no_core_records(kos):
    status, payload = kos.get("/api/works/work-0000000000000000/core")
    assert status == 404 and payload == {"error": "not found"}


def test_CORE_7_native_history_proves_distinct_durable_graph_boundaries(kos, vault):
    """Real SDK history, not decorators or an engine-shaped projection, proves dispatch."""
    from temporalio.client import Client

    path = "40_Knowledge/Ideas/Durable graph.md"
    vault.write(path, "# Durable graph\n\nAn isolated source fixture.\n")
    receipt = kos.run("standardize.preview", {"paths": [path], "digests": {path: vault.sha(path)}})
    assert receipt["status"] == "completed", receipt
    status, work = kos.get(f"/api/works/{receipt['work_id']}")
    assert status == 200
    runtime = work["runtime"]
    binding = runtime["binding"]
    assert binding["engine_id"] == "agentfabric.operation.temporal_langgraph"
    assert binding["engine_version"] == SOURCE_VERSION
    assert binding["engine_release"] == {
        "mode": "source_project",
        "manifest_digest": "sha256:" + hashlib.sha256(SOURCE_MANIFEST.read_bytes()).hexdigest(),
    }
    assert binding["libraries"] == {name: version(name) for name in
                                    ("temporalio", "temporalio-langgraph", "langgraph")}
    assert all(value.startswith("sha256:") and len(value) == 71
               for value in binding["library_source_digests"].values())
    assert runtime["binding_digest"] == core_digest(binding).removeprefix("sha256:")
    assert binding["implementation_source_digest"].startswith("sha256:")
    package = Path(knowledgeos.__file__).resolve().parent
    source = hashlib.sha256()
    for module in sorted(package.rglob("*.py")):
        source.update(module.relative_to(package).as_posix().encode() + b"\0" + module.read_bytes() + b"\0")
    assert binding["implementation_source_digest"] == "sha256:" + source.hexdigest()

    async def history():
        client = await Client.connect(binding["address"], namespace=binding["namespace"])
        handle = client.get_workflow_handle(runtime["workflow_id"])
        description = await handle.describe()
        events = (await handle.fetch_history()).events
        calls, completions = [], {}
        for event in events:
            if event.HasField("activity_task_scheduled_event_attributes"):
                scheduled = event.activity_task_scheduled_event_attributes
                assert scheduled.activity_type.name == "knowledgeos.phase"
                assert scheduled.retry_policy.maximum_attempts == 1
                [payload] = await client.data_converter.decode(scheduled.input.payloads)
                calls.append({"id": scheduled.activity_id, "scheduled_event_id": event.event_id, "payload": payload})
            elif event.HasField("activity_task_completed_event_attributes"):
                completed = event.activity_task_completed_event_attributes
                [result] = await client.data_converter.decode(completed.result.payloads)
                completions[completed.scheduled_event_id] = {"event_id": event.event_id, "result": result}
        return description, calls, completions

    description, calls, completions = asyncio.run(history())
    assert description.workflow_type == "knowledgeos.work.v1"
    assert description.task_queue == binding["task_queue"]
    assert description.status.name == "COMPLETED"
    trace = {call["id"]: call for call in calls}
    assert len(trace) == len(calls)
    assert set(trace) == set(runtime["phases"])
    for phase_id, retained in runtime["phases"].items():
        assert retained["state"] == "completed"
        assert retained["result_digest"] == core_digest(retained["result"]).removeprefix("sha256:")
        assert trace[phase_id]["payload"]["operation"] == retained["operation"]
        assert trace[phase_id]["scheduled_event_id"] in completions
        assert completions[trace[phase_id]["scheduled_event_id"]]["result"] == retained["result"]
    core = core_of(kos, receipt["work_id"])
    assert core["unsupported"] == []
    [evaluation] = core["RunEvaluation"]
    checkpoint = work["segments"][0]["checkpoint"]
    assert evaluation["checkpoint_digest"] == core_digest(without(checkpoint, "checkpoint_digest"))
    [graph] = core["GraphSpec"]
    assert graph["node_ids"] == work["segments"][0]["runtime_topology"]["node_ids"]
    assert {(edge["source_node_id"], edge["target_node_id"]) for edge in core["GraphEdge"]} == {
        tuple(pair) for pair in work["segments"][0]["runtime_topology"]["edges"]}

    [spec] = core["WorkSpec"]
    [segment] = work["segments"]
    [producer] = segment["steps"]
    [producer_assignment] = [item for item in core["GraphAssignment"]
                             if item["executor_id"] == spec["officer_executor_id"]]
    [evaluation_assignment] = [item for item in core["GraphAssignment"]
                               if item["executor_id"] == spec["eval_officer_executor_id"]]
    [producer_call] = [call for call in calls if call["payload"].get("role") == producer["role"]]
    [assessment_phase] = [phase_id for phase_id, retained in runtime["phases"].items()
                          if retained["result"].get("assessment") == segment["assessment"]]
    [effect_phase] = [
        phase_id for phase_id, retained in runtime["phases"].items()
        if isinstance(retained["result"].get("done"), dict)
        and retained["result"]["done"]["outcome"] == receipt["outcome"]
        and retained["result"]["done"]["status"] == receipt["status"]
    ]
    node_activities = {producer_assignment["node_id"]: producer_call["id"],
                       evaluation_assignment["node_id"]: assessment_phase}
    assert set(node_activities) == set(graph["node_ids"])
    assert len(set(node_activities.values()) | {effect_phase}) == len(graph["node_ids"]) + 1
    for edge in core["GraphEdge"]:
        source = trace[node_activities[edge["source_node_id"]]]["scheduled_event_id"]
        target = trace[node_activities[edge["target_node_id"]]]["scheduled_event_id"]
        assert completions[source]["event_id"] < target  # Follow exported edges, not private phases.
    assert (completions[trace[assessment_phase]["scheduled_event_id"]]["event_id"]
            < trace[effect_phase]["scheduled_event_id"])

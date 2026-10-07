"""Offline Capture-to-Officer-to-proposal owner boundary on disposable documents."""

from __future__ import annotations

import asyncio
import copy
import sys
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError, digest
from vaultops.application.exops import OwnerActor
from vaultops.application.work import record_digest, segment_digest, work_spec_digest
from vaultops.application.work_tools import OfficerToolSession
from vaultops.projection import generate_projection

BASE_CAPTURE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
SOURCE = "00_Inbox/Captures/2026/09/Offline normalization pilot.md"
OTHER = "00_Inbox/Captures/2026/09/Other capture.md"
ACTOR = OwnerActor("owner.user", "cli")


def _admitted_officer(tmp_path):
    exops, fixture = make_exops(tmp_path, with_officer=True)
    app = exops.application
    template = (app.roots.vault / BASE_CAPTURE).read_text()

    def capture(title, note_id):
        return (template.replace("11111111-1111-4111-8111-111111111111", note_id)
                .replace("20260909-090000-mac-deadbeef", title)
                .replace("방명록 괴담의 첫 장면은 비어 있는 정보로 시작한다.",
                         f"{title}: source text remains owner controlled."))

    source_path = app.roots.vault / SOURCE
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(capture("Offline normalization pilot",
                                   "a4d3c653-d15c-4c4e-9a75-b6d54a046e78"))
    other_path = app.roots.vault / OTHER
    other_path.write_text(capture("Other capture", "2e174ab5-497b-45ef-8062-776d241d9c63"))
    projection, code = generate_projection(app.roots)
    assert code == 0, projection

    officer = exops.officers["knowledgeos.normalization_officer.one"]
    fixture.spec.pop("team_id")
    fixture.spec.pop("manager_executor_id")
    fixture.spec.update(
        target_kind="officer", officer_executor_id=officer.officer_executor_id,
        delegation_mode="officer_direct",
        allowed_executor_ids=[officer.officer_executor_id],
        allowed_profiles=[dict(officer.officer_profile)],
        objective="Prepare one Pending proposal for the selected synthetic Capture",
        acceptance_criteria=[{"criterion_id": "criterion.proposal",
                              "description": "Pending proposal has exact source evidence and no canonical apply"}],
    )
    fixture.spec["spec_digest"] = work_spec_digest(fixture.spec)
    fixture.run["work_spec_digest"] = fixture.spec["spec_digest"]
    fixture.run["binding_reference"] = dict(officer.binding_reference)
    source = app.reference(SOURCE)
    other = app.reference(OTHER)
    exops.director_officer_source_admitted = (
        lambda spec, selected: spec["admission_receipt_id"] == "admission.synthetic.one"
        and dict(selected) == source)
    non_capture = app.reference("40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md")
    with pytest.raises(AdmissionError, match="selected Capture"):
        exops.submit_work(
            fixture.spec, fixture.run, actor=ACTOR, action_id="action.officer.not-capture",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            officer_source_reference=non_capture)
    with pytest.raises(AdmissionError, match="Director did not admit"):
        exops.submit_work(
            fixture.spec, fixture.run, actor=ACTOR, action_id="action.officer.other",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            officer_source_reference=other)

    assert app.resolve_reference(source) == SOURCE
    receipt = exops.submit_work(
        fixture.spec, fixture.run, actor=ACTOR, action_id="action.officer.pilot",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        officer_source_reference=source)
    assert receipt["dispatch_state"] == "pending"
    record = exops.authorize_dispatch(
        work_run_id=fixture.run["work_run_id"], action_id=receipt["action_id"],
        content_digest=receipt["content_digest"])
    assert record["officer_source_reference"] == source
    exops.record_dispatch(fixture.run["work_run_id"], action_id=receipt["action_id"],
                          outcome="accepted", workflow_id="knowledgeos/" + fixture.run["work_run_id"])
    running = exops.worker_started(
        fixture.run["work_run_id"], worker_identity="knowledgeos.worker.one",
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        workflow_id="knowledgeos/" + fixture.run["work_run_id"])
    bundle = fixture.bundle(targets=["criterion.proposal"])
    bundle["assignments"][0].update(
        executor_id=officer.officer_executor_id, selection_authority="owner_officer",
        **officer.officer_profile, work_spec_digest=fixture.spec["spec_digest"])
    source_artifact = {
        **fixture.artifact("capture"), "source_revision": source["source_revision"],
        "content_digest": source["content_digest"], "size_bytes": len(source_path.read_bytes()),
        "media_type": "text/markdown",
    }
    bundle["segment"]["work_spec_digest"] = fixture.spec["spec_digest"]
    bundle["segment"]["input_references"] = [source_artifact]
    bundle["segment"]["segment_digest"] = segment_digest(bundle["segment"])
    artifacts = {source_artifact["artifact_id"]: source_artifact}
    return exops, fixture, running, bundle, artifacts, source, other, source_path


def test_selected_capture_officer_graph_and_profile_permissions(tmp_path):
    exops, fixture, _running, bundle, artifacts, source, other, source_path = _admitted_officer(tmp_path)
    run_id = fixture.run["work_run_id"]
    graph_run_id = bundle["segment"]["graph_run_id"]
    officer_id = fixture.spec["officer_executor_id"]
    session = OfficerToolSession(exops.application, run_id, officer_id, graph_run_id)
    with pytest.raises(AdmissionError):
        session.tools()  # Work admission alone confers no effect grant.
    with pytest.raises(AdmissionError, match="Only the admitted Officer"):
        exops.admit_officer_segment(run_id, officer_executor_id="knowledgeos.other",
                                    bundle=bundle, artifacts=artifacts, source_reference=source)
    with pytest.raises(AdmissionError, match="source admission"):
        exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                    bundle=bundle, artifacts=artifacts, source_reference=other)
    original_bytes = source_path.read_bytes()
    source_path.write_bytes(original_bytes + b"\nChanged before Graph admission.\n")
    with pytest.raises(AdmissionError, match="resource digest or revision changed"):
        exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                    bundle=bundle, artifacts=artifacts, source_reference=source)
    source_path.write_bytes(original_bytes)
    wrong_input = copy.deepcopy(bundle)
    wrong_input["segment"]["input_references"][0]["content_digest"] = other["content_digest"]
    wrong_input["segment"]["segment_digest"] = segment_digest(wrong_input["segment"])
    with pytest.raises(AdmissionError, match="Graph input"):
        exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                    bundle=wrong_input, artifacts=artifacts, source_reference=source)
    wrong_profile = copy.deepcopy(bundle)
    wrong_profile["assignments"][0].update(exops.catalog.profile("knowledgeos.cited_answer_officer"))
    with pytest.raises(AdmissionError):
        exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                    bundle=wrong_profile, artifacts=artifacts, source_reference=source)

    admitted = exops.admit_officer_segment(
        run_id, officer_executor_id=officer_id,
        bundle=bundle, artifacts=artifacts, source_reference=source)
    assert admitted["source_digest"] == source["content_digest"]
    assert session.tools() == ("work_context_read", "knowledge_read", "proposal_create")
    for rejected in (
        OfficerToolSession(exops.application, run_id, "knowledgeos.other", graph_run_id),
        OfficerToolSession(exops.application, run_id, officer_id, "graphrun.other"),
        OfficerToolSession(exops.application, run_id,
                           fixture.spec["eval_officer_executor_id"], graph_run_id),
    ):
        with pytest.raises(AdmissionError):
            rejected.tools()
    with pytest.raises(AdmissionError):
        session.authorize("knowledge_retrieve", {"query": "all"})
    with pytest.raises(AdmissionError):
        session.authorize("proposal_create", {"source_reference": other})
    with pytest.raises(AdmissionError):
        session.authorize("proposal_create", {"source_reference": {
            **source, "content_digest": "sha256:" + "0" * 64}})
    source_path.write_bytes(source_path.read_bytes() + b"\nChanged after Graph admission.\n")
    with pytest.raises(AdmissionError, match="resource digest or revision changed"):
        session.tools()


def test_proposal_replay_and_exact_independent_assessment(tmp_path):
    exops, fixture, _running, bundle, artifacts, source, _other, source_path = _admitted_officer(tmp_path)
    run_id = fixture.run["work_run_id"]
    graph_run_id = bundle["segment"]["graph_run_id"]
    officer_id = fixture.spec["officer_executor_id"]
    exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                bundle=bundle, artifacts=artifacts, source_reference=source)
    session = OfficerToolSession(exops.application, run_id, officer_id, graph_run_id)
    source_bytes = source_path.read_bytes()
    created, code = session.create_proposal({"source_reference": source})
    assert code == 0 and created["status"] == "PASS", created
    assert created["provider_called"] is False
    assert created["effects"] == {"pending_artifact_created": True, "canonical_note_changed": False}
    replay, code = session.create_proposal({"source_reference": source})
    assert code == 0 and replay["replayed"]
    assert replay["effects"]["pending_artifact_created"] is False
    assert source_path.read_bytes() == source_bytes
    assert len(list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    journal = exops.application.journal.read()
    proposals = [item for item in journal["intents"].values()
                 if isinstance(item, dict) and item.get("work_binding", {}).get("work_run_id") == run_id]
    assert len(proposals) == 1
    binding = proposals[0]["work_binding"]
    assert binding == {"work_run_id": run_id, "work_spec_digest": fixture.spec["spec_digest"],
                       "graph_run_id": graph_run_id, "executor_id": officer_id,
                       "source_reference": source}

    pending = exops.application.roots.vault / created["proposal_path"]
    proposal_bytes = pending.read_bytes()
    proposal_ref = {**fixture.artifact("proposal"),
                    "source_revision": created["resource_reference"]["source_revision"],
                    "content_digest": digest(proposal_bytes), "size_bytes": len(proposal_bytes),
                    "media_type": "text/markdown"}
    assert proposal_ref["content_digest"] == created["resource_reference"]["content_digest"]
    checkpoint = record_digest({"graph_run_id": graph_run_id,
                                "source_digest": source["content_digest"],
                                "proposal_digest": proposal_ref["content_digest"]})
    evaluation = {
        "owner_operation_id": "knowledgeos", "run_id": graph_run_id,
        "checkpoint_digest": checkpoint, "criteria_revision": 1,
        "criterion_ids": ["criterion.proposal"],
        "passed_criterion_ids": ["criterion.proposal"], "failed_criterion_ids": [],
        "not_run_criterion_ids": [], "work_run_id": run_id,
        "work_spec_revision": 1, "work_spec_digest": fixture.spec["spec_digest"],
        "evaluation_id": "evaluation.officer.pilot",
        "evaluator_id": fixture.spec["eval_officer_executor_id"],
        "evidence_references": [proposal_ref],
    }
    assessment = {
        "owner_operation_id": "knowledgeos", "work_id": fixture.spec["work_id"],
        "work_run_id": run_id, "work_spec_revision": 1,
        "work_spec_digest": fixture.spec["spec_digest"],
        "assessment_id": "assessment.officer.pilot", "criteria_revision": 1,
        "assessor_id": fixture.spec["eval_officer_executor_id"],
        "assessed_at": datetime.now(UTC).isoformat(),
        "graph_run_ids": [graph_run_id], "completion_state": "complete",
        "criterion_outcomes": [{"criterion_id": "criterion.proposal", "outcome": "satisfied",
                                "evaluation_ids": [evaluation["evaluation_id"]],
                                "evidence_references": [proposal_ref]}],
        **fixture.pins,
    }
    current = exops.application.journal.read()["intents"]["exops.work." + run_id]["run"]
    completed = {**current, "revision": current["revision"] + 1,
                 "control_revision": current["control_revision"] + 1,
                 "state": "completed",
                 "latest_assessment_id": assessment["assessment_id"],
                 "latest_assessment_digest": record_digest(assessment),
                 "result_reference": proposal_ref}
    completed.pop("active_graph_run_id")
    finish = {"work_run_id": run_id, "worker_identity": "knowledgeos.worker.one",
              "action_id": "action.officer.pilot", "next_run": completed,
              "assessment": assessment, "segments": (bundle["segment"],),
              "evaluations": (evaluation,), "artifacts": {proposal_ref["artifact_id"]: proposal_ref},
              "checkpoints": {graph_run_id: checkpoint}}
    with pytest.raises(AdmissionError, match="independent EvalOfficer"):
        exops.worker_finished(**finish, candidate_assessor_id=officer_id)
    wrong_spec = copy.deepcopy(finish)
    wrong_spec["evaluations"][0]["work_spec_digest"] = "sha256:" + "0" * 64
    with pytest.raises(AdmissionError, match="WorkSpec"):
        exops.worker_finished(**wrong_spec, candidate_assessor_id=fixture.spec["eval_officer_executor_id"])
    wrong_checkpoint = copy.deepcopy(finish)
    wrong_checkpoint["checkpoints"][graph_run_id] = "sha256:" + "0" * 64
    with pytest.raises(AdmissionError, match="checkpoint"):
        exops.worker_finished(**wrong_checkpoint,
                              candidate_assessor_id=fixture.spec["eval_officer_executor_id"])
    wrong_proposal = copy.deepcopy(finish)
    wrong_proposal["next_run"]["result_reference"]["content_digest"] = "sha256:" + "0" * 64
    with pytest.raises(AdmissionError, match="proposal effect"):
        exops.worker_finished(**wrong_proposal,
                              candidate_assessor_id=fixture.spec["eval_officer_executor_id"])
    pending.write_bytes(proposal_bytes + b"\nChanged after receipt.\n")
    with pytest.raises(AdmissionError):
        exops.worker_finished(**finish,
                              candidate_assessor_id=fixture.spec["eval_officer_executor_id"])
    pending.write_bytes(proposal_bytes)
    finished = exops.worker_finished(
        **finish, candidate_assessor_id=fixture.spec["eval_officer_executor_id"])
    assert finished["application_state"] == "recorded"
    assert exops.read_work(run_id, actor=ACTOR)["run"]["state"] == "completed"
    assert source_path.read_bytes() == source_bytes


def test_single_mcp_wire_checks_officer_proposal_binding(tmp_path):
    exops, fixture, _running, bundle, artifacts, source, other, _source_path = _admitted_officer(tmp_path)
    run_id = fixture.run["work_run_id"]
    officer_id = fixture.spec["officer_executor_id"]
    graph_run_id = bundle["segment"]["graph_run_id"]
    exops.admit_officer_segment(run_id, officer_executor_id=officer_id,
                                bundle=bundle, artifacts=artifacts, source_reference=source)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vaultops.mcp_server", "--control-root",
              str(exops.application.roots.control), "--work-run-id", run_id,
              "--executor-id", officer_id, "--graph-run-id", graph_run_id],
        env={"PYTHONPATH": "/workspace/control/ops/src"}, cwd=tmp_path)

    async def exercise():
        async with Client(params, mode="legacy") as client:
            assert len((await client.list_tools()).tools) == 24
            denied = await client.call_tool("proposal_create", {"source_reference": other})
            assert denied.is_error and denied.structured_content["effect"] == "none"
            created = await client.call_tool("proposal_create", {"source_reference": source})
            assert not created.is_error and created.structured_content["effect"] == "proposal_created"
            replay = await client.call_tool("proposal_create", {"source_reference": source})
            assert not replay.is_error and replay.structured_content["effect"] == "proposal_replayed"

    asyncio.run(exercise())

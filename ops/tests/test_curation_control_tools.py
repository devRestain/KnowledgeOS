"""Separate MCP applications must prove resume at tool and final effect boundaries."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from test_curation_control import ACTOR, NATIVE_RUN, checkpoint_ready, pause, resume

from vaultops.adapters.core import AdmissionError
from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity
from vaultops.application.work_tools import TeamToolSession

RESUME = "command.tools.resume"


def resumed_tools(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    for kind, command_id in (("pause", "command.tools.pause"), ("resume", RESUME)):
        controller.request_control(
            run["work_run_id"], kind=kind, actor=ACTOR, command_id=command_id,
            target=target, expires_at=exops.application.clock() + timedelta(hours=1),
        )
        receipt = controller.apply_control(
            run["work_run_id"], command_id, temporal_run_id=NATIVE_RUN,
        )
        if kind == "pause":
            target = receipt["next_target"]
    asyncio.run(executor.manager_successor(run["work_run_id"]))
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    graph_id = record["run"]["active_graph_run_id"]
    assignment = record["admitted_method_segments"][graph_id]["assignments"][0]
    app = KnowledgeApplication.from_roots(
        exops.application.roots,
        identity=LocalIdentity("local.mcp", "mcp", local_execution=True),
    )
    tools = TeamToolSession(app, run["work_run_id"], assignment["executor_id"], graph_id)
    return app, tools, record["method_source_reference"], path


def invalidate_release(app, damage):
    with app.journal.writer() as writer:
        command = writer.value["intents"]["curation.control." + RESUME]
        if damage == "decision":
            writer.value["decisions"][command["request"]["request_id"]]["receipt"]["decision_state"] = "rejected"
        else:
            key = next(key for key, item in writer.value["receipts"].items()
                       if item.get("receipt", {}).get("effect_id")
                          == command["execution_intent"]["effect_id"])
            del writer.value["receipts"][key]
        writer.commit(app.clock())


@pytest.mark.parametrize("damage", ("decision", "receipt"))
def test_separate_mcp_tools_recheck_canonical_resume_proof(tmp_path, monkeypatch, damage):
    app, tools, source, path = resumed_tools(tmp_path, monkeypatch)
    assert tools.work_view()["source_reference"] == source
    invalidate_release(app, damage)
    before = app.journal.read()
    source_bytes = path.read_bytes()
    with pytest.raises(AdmissionError):
        tools.work_view()
    assert app.journal.read() == before and path.read_bytes() == source_bytes


@pytest.mark.parametrize("damage", ("decision", "receipt"))
def test_cached_work_binding_cannot_bypass_final_proposal_resume_check(tmp_path, monkeypatch, damage):
    app, tools, source, path = resumed_tools(tmp_path, monkeypatch)
    binding = tools.proposal_binding(source)
    invalidate_release(app, damage)
    before = app.journal.read()
    source_bytes = path.read_bytes()
    report, code = app.create_normalize_proposal(source_reference=source, work_binding=binding)
    assert code == 10 and report["status"] == "FAIL"
    assert report["execution_outcome"] == "not_started"
    assert report["effects"] == {"pending_artifact_created": False, "canonical_note_changed": False}
    assert app.journal.read() == before and path.read_bytes() == source_bytes
    assert list((app.roots.vault / "01_AI_Review/Pending").glob("*.md")) == []


@pytest.mark.parametrize("kind", ("cancel", "resume"))
def test_released_control_target_is_not_offered_as_a_new_checkpoint(tmp_path, monkeypatch, kind):
    controller, target, exops, _executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    kinds = ("cancel",) if kind == "cancel" else ("pause", "resume")
    last_command = None
    for action in kinds:
        last_command = "command.target." + action
        controller.request_control(
            run["work_run_id"], kind=action, actor=ACTOR, command_id=last_command,
            target=target, expires_at=exops.application.clock() + timedelta(hours=1),
        )
        applied = controller.apply_control(run["work_run_id"], last_command, temporal_run_id=NATIVE_RUN)
        if action == "pause":
            target = applied["next_target"]
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        controller.target(run["work_run_id"], actor=ACTOR)
    assert controller.read_command(run["work_run_id"], last_command, actor=ACTOR) == applied
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("boundary", ("request", "dispatch"))
def test_nonstring_control_kind_is_rejected_without_owner_mutation(tmp_path, monkeypatch, boundary):
    controller, target, exops, *_ = checkpoint_ready(tmp_path, monkeypatch)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        if boundary == "request":
            controller.request_control(
                target["work_run_id"], kind=[], actor=ACTOR, command_id="command.invalid.kind",
                target=target, expires_at=exops.application.clock() + timedelta(hours=1),
            )
        else:
            controller.authorize_dispatch({"command_id": "command.invalid.kind", "kind": [], "target": target})
    assert exops.application.journal.read() == before


def test_control_revocation_after_assessment_blocks_final_work_commit(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    paused_target, _receipt = pause(controller, target)
    resume(controller, paused_target)
    asyncio.run(executor.manager_successor(run["work_run_id"]))
    asyncio.run(executor.normalization_proposal(run["work_run_id"]))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.assessment["completion_state"] == "complete"
    before = exops.application.journal.read()
    record = before["intents"]["exops.work." + run["work_run_id"]]
    exops.authorize_actor = lambda _actor, action: action != "control_work"
    with pytest.raises(AdmissionError):
        exops.worker_finished(
            run["work_run_id"], worker_identity=exops.worker_identity, action_id=record["action_id"],
            next_run=result.next_run, assessment=result.assessment, segments=result.segments,
            evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
            candidate_assessor_id=result.candidate_assessor_id,
        )
    assert exops.application.journal.read() == before
    assert record["run"]["state"] == "running" and record["application_state"] == "started"

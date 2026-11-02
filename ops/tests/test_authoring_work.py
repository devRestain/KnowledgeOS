"""Exact authoring boundaries; synthetic runner makes real Owner reads and Pending effects."""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.authoring_fixture import prepare
from test_normalization_work import MODEL_BINDING

from vaultops.adapters.core import AdmissionError
from vaultops.application.authoring_work import (
    DRAFT_METHOD,
    REVIEW_METHOD,
    AuthoringExecutor,
    authoring_admission,
)
from vaultops.application.exops import OwnerActor
from vaultops.application.work_tools import TeamToolSession
from vaultops.interfaces.mcp_server import _convert_report, _operation


class AuthoringToolRunner:
    binding = MODEL_BINDING

    def __init__(self, exops, alter=None):
        self.exops, self.alter, self.requests = exops, alter, []

    async def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        app = self.exops.application
        session = TeamToolSession(app, request["work_run_id"], request["executor_id"], request["graph_run_id"], self.exops)
        calls = []
        def call(name, args):
            session.authorize(name, args)
            envelope, error = _convert_report(SimpleNamespace(services=app), name, args,
                                              _operation(app, name, args, session=session)())
            assert not error, envelope
            calls.append({"name": name, "arguments": args, "result": envelope})
            return envelope["data"]
        stage, scope = request["stage_id"], request["scope"]
        if stage == "source_evidence":
            for chunk in request["source_chunks"]:
                offset = 0
                while True:
                    page = call("knowledge_read", {"resource_reference": request["source_reference"], "chunk_locator": chunk, "offset": offset})
                    offset += len(page["text"])
                    if not page["truncated"]:
                        break
        elif stage == "cited_draft":
            citations = request["evidence"]["citations"]
            body = "An authored interpretation; calibration is uncertain.\n\nSources\n[[" + request["source_path"] + "]]\n"
            body += request["source_reference"]["content_digest"] + "\n"
            body += "\n".join(c["chunk_locator"] + " " + c["chunk_hash"] for c in citations)
            call("proposal_draft_note", {"source_reference": request["source_reference"], **scope, "draft_body": body})
        elif stage == "project_evidence":
            call("knowledge_retrieve", {"query": scope["query"], "hops": 0,
                "expected_generation_id": request["index_generation_id"], "limit": 20})
            call("base_view_query", {"base": scope["base"], "view": scope["view"], "filters": {}, "limit": 20})
            call("task_query", {"path_prefix": request["source_path"], "limit": 20})
        else:
            observations = ["Review period " + scope["period_start"] + " to " + scope["period_end"]]
            observations += [t["locator"] + " " + t["line_sha256"] for t in request["evidence"]["task_locations"]]
            content = {"observations": observations, "interpretation": "Interpretation: remaining tasks need human review.",
                       "next_actions": ["Review open task without changing original task bytes."], "citations": request["evidence"]["citations"]}
            call("proposal_project_review", {"source_reference": request["source_reference"], "content": json.dumps(content)})
        response = {"request_digest": request["request_digest"], "runner_binding": dict(self.binding),
                    "provider": self.binding["provider"], "model": self.binding["model"], "session_id": "boundary-" + stage,
                    "final_text": "Boundary-only synthetic runner.", "tool_calls": calls, "exposed_tools": list(session.tools())}
        if self.alter:
            self.alter(response)
        return response


def started(tmp_path, method_id, *, alter=None):
    exops, spec, run, _binding, source, path, _ = prepare(tmp_path, MODEL_BINDING, method_id)
    receipt = exops.submit_work(spec, run, actor=OwnerActor("owner.authoring", "cli"),
        action_id="action.authoring", expires_at=datetime.now(UTC) + timedelta(hours=1), method_source_reference=source,
        selected_scope={"path": path.relative_to(exops.application.roots.vault).as_posix(), "content_sha256": source["source_revision"]})
    workflow_id = "knowledgeos/" + run["work_run_id"]
    exops.record_dispatch(run["work_run_id"], action_id=receipt["action_id"], outcome="accepted", workflow_id=workflow_id)
    running = exops.worker_started(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"], work_spec_digest=spec["spec_digest"], workflow_id=workflow_id)
    executor = AuthoringExecutor(exops, runner=AuthoringToolRunner(exops, alter), method_id=method_id)
    return exops, executor, spec, running, path, receipt


async def produce(executor, spec, run):
    await executor.manager(spec, run)
    for stage in executor.policy.stages:
        await executor.execute_stage(run["work_run_id"], stage)


@pytest.mark.parametrize("method_id", [DRAFT_METHOD, REVIEW_METHOD])
def test_authoring_exact_pending_effect_and_independent_assessment(tmp_path, method_id):
    exops, executor, spec, run, path, receipt = started(tmp_path, method_id)
    original = path.read_bytes()
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id == spec["eval_officer_executor_id"]
    assert len(executor.runner.requests) == 2
    assert executor.runner.requests[0]["evidence"] is None
    assert len(list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    assert path.read_bytes() == original
    assert not exops.application.journal.read()["decisions"]
    if method_id == DRAFT_METHOD:
        gates = list(exops.application.journal.read()["human_requests"].values())
        assert len(gates) == 1 and gates[0]["lifecycle"] == "pending"
        assert gates[0]["question"] == "Approve this exact cited note draft proposal?"
        assert not (exops.application.roots.vault / json.loads(spec["objective"])["target_path"]).exists()
    else:
        assert executor.runner.requests[1]["evidence"]["task_locations"]
        assert executor.runner.requests[1]["evidence"]["citations"]
        rows = executor.runner.requests[1]["evidence"]["base_view"]["rows"]
        assert len(rows) == 1
        assert rows[0]["resource_reference"] == executor.runner.requests[0]["source_reference"]
        execution = executor.current(run["work_run_id"])["project_review_review_proposal"]
        formal = executor.result_call(execution)["result"]["data"]["work_artifact_reference"]
        assert formal["artifact_id"].startswith("artifact.mcp.")
        assert result.next_run["result_reference"] == formal
        assert result.artifacts == {formal["artifact_id"]: formal}
        assert result.evaluations[0]["evidence_references"] == [formal]
        assert executor.current(run["work_run_id"])["artifact_references"] == result.artifacts
    finished = exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id)
    assert finished["application_state"] == "recorded"


@pytest.mark.parametrize("method_id", [DRAFT_METHOD, REVIEW_METHOD])
def test_authoring_source_evidence_survives_unrelated_note_changes(tmp_path, method_id):
    exops, executor, spec, run, path, _ = started(tmp_path, method_id)
    original = path.read_bytes()
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[0]))
    unrelated = exops.application.roots.vault / "40_Knowledge/Notes/Unrelated ordinary note.md"
    unrelated.write_text("New unrelated note.\n")
    unrelated.write_text("Edited unrelated note.\n")
    unrelated.rename(unrelated.with_name("Moved unrelated ordinary note.md"))
    asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[-1]))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "completed"
    assert path.read_bytes() == original


@pytest.mark.parametrize("method_id", [DRAFT_METHOD, REVIEW_METHOD])
@pytest.mark.parametrize("tamper", ["foreign_tool", "wrong_source", "stale_read", "route"])
def test_authoring_rejects_foreign_tool_source_stale_trace_and_route(tmp_path, method_id, tamper):
    def alter(response):
        if tamper == "foreign_tool":
            response["tool_calls"][0]["name"] = "index_status_read"
        elif tamper == "wrong_source":
            response["tool_calls"][0]["arguments"]["resource_reference"] = {"content_digest": "sha256:" + "0" * 64}
        elif tamper == "stale_read":
            response["tool_calls"][0]["result"]["data"]["changed"] = True
        else:
            response["model"] = "foreign-model"
    exops, executor, spec, run, _, _ = started(tmp_path, method_id, alter=alter)
    asyncio.run(executor.manager(spec, run))
    with pytest.raises(AdmissionError):
        asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[0]))
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


@pytest.mark.parametrize("method_id", [DRAFT_METHOD, REVIEW_METHOD])
def test_authoring_stale_source_and_revoked_actor_block_final_commit(tmp_path, method_id):
    exops, executor, spec, run, path, receipt = started(tmp_path, method_id)
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    before = path.read_bytes()
    path.write_bytes(before + b"\nChanged after assessment.\n")
    with pytest.raises(AdmissionError):
        asyncio.run(executor.assess(run["work_run_id"]))
    path.write_bytes(before)
    exops.authorize_actor = lambda actor, action: False
    with pytest.raises(AdmissionError):
        exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
            action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment, segments=result.segments,
            evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
            candidate_assessor_id=result.candidate_assessor_id)


def test_draft_explicit_target_pin_rejected_before_pending_effect(tmp_path):
    exops, executor, spec, run, _, _ = started(tmp_path, DRAFT_METHOD)
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.source_evidence(run["work_run_id"]))
    record = executor.current(run["work_run_id"])
    request = executor.request(record, "cited_draft")
    session = TeamToolSession(exops.application, run["work_run_id"], spec["allowed_executor_ids"][1],
                              record["run"]["active_graph_run_id"], exops)
    args = {"source_reference": request["source_reference"], **request["scope"], "draft_body": "Foreign draft."}
    args["target_path"] = "40_Knowledge/Notes/Foreign Target.md"
    with pytest.raises(AdmissionError):
        session.authorize("proposal_draft_note", args)
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_project_foreign_query_base_and_task_scope_rejected_before_reads(tmp_path):
    exops, executor, spec, run, _, _ = started(tmp_path, REVIEW_METHOD)
    asyncio.run(executor.manager(spec, run))
    record = executor.current(run["work_run_id"])
    session = TeamToolSession(exops.application, run["work_run_id"], spec["allowed_executor_ids"][0],
                              record["run"]["active_graph_run_id"], exops)
    for name, args in (
        ("task_query", {"path_prefix": "20_Projects", "limit": 20}),
        ("base_view_query", {"base": "Projects.base", "view": "Mobile", "filters": {}, "limit": 20}),
        ("base_view_query", {"base": "Projects.base", "view": "Now", "filters": {"type": "project"}, "limit": 20}),
        ("knowledge_retrieve", {"query": "foreign", "hops": 0, "expected_generation_id": "foreign", "limit": 20}),
    ):
        with pytest.raises(AdmissionError):
            session.authorize(name, args)


@pytest.mark.parametrize("method_id", [DRAFT_METHOD, REVIEW_METHOD])
def test_authoring_forged_effect_receipt_rejected_by_fresh_assessor(tmp_path, method_id):
    exops, executor, spec, run, _, _ = started(tmp_path, method_id)
    asyncio.run(produce(executor, spec, run))
    with exops.application.journal.writer() as writer:
        effects = [item for item in writer.value["intents"].values()
                   if item.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
                   or item.get("identity", {}).get("work_run_id") == run["work_run_id"]]
        assert len(effects) == 1
        writer.value["receipts"][effects[0]["effect_id"]]["execution"]["result_digest"] = "sha256:" + "0" * 64
        writer.commit(exops.application.clock())
    with pytest.raises(AdmissionError):
        asyncio.run(executor.assess(run["work_run_id"]))


@pytest.mark.parametrize("base,view", [("Decisions.base", "Open"), ("Projects.base", "Unknown")])
def test_project_admission_requires_declared_canonical_project_view(tmp_path, base, view):
    exops, spec, run, binding, source, _, _ = prepare(tmp_path, MODEL_BINDING, REVIEW_METHOD)
    scope = {**json.loads(spec["objective"]), "base": base, "view": view}
    with pytest.raises(AdmissionError):
        authoring_admission(exops.application, binding, source, method_id=REVIEW_METHOD, scope=scope,
            work_id=spec["work_id"], work_run_id=run["work_run_id"],
            admission_receipt_id=spec["admission_receipt_id"], runner_binding=MODEL_BINDING)

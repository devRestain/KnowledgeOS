"""Disposable Team behavior with synthetic runner and real Owner tool effects."""

from __future__ import annotations

import asyncio
import copy
import http.client
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.agent_fixture import (
    decision_response,
    install_agent_policy,
    model_binding,
    retain_director_context,
    role_response,
    specialist_response,
    synthetic_decision,
)
from support.control_factory import make_separate_portable_fixture_roots
from support.work_factory import synthetic_role_decision
from test_normalization_work import BASE, MODEL_BINDING, SOURCE

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.research_work import (
    CRITERION,
    ResearchTeamExecutor,
    research_admission,
    research_native_digest,
)
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.application.work import work_spec_digest
from vaultops.application.work_tools import TeamToolSession
from vaultops.interfaces.mcp_server import _convert_report, _operation
from vaultops.projection import generate_projection


class ResearchToolRunner:
    """Boundary-only test: no provider claim; all tool receipts are real Owner effects."""
    binding = MODEL_BINDING

    def __init__(self, exops, alter=None):
        self.exops, self.alter, self.requests = exops, alter, []
        self.decisions = []
        self.binding = model_binding(exops.application.roots.control)
        self.plan_decision = None
        self.query_options = {}

    async def __call__(self, request):
        if request.get("request_kind") == "agent_decision":
            self.decisions.append(copy.deepcopy(request))
            if self.plan_decision and request["agent_request"]["responsibility"] == "manager":
                return decision_response(request, self.plan_decision(request),
                                         control_root=self.exops.application.roots.control)
            return synthetic_role_decision(request, control_root=self.exops.application.roots.control)
        import json
        self.requests.append(copy.deepcopy(request))
        app = self.exops.application
        session = TeamToolSession(app, request["work_run_id"], request["executor_id"], request["graph_run_id"])
        calls = []

        def call(name, arguments):
            session.authorize(name, arguments)
            result, error = _convert_report(SimpleNamespace(services=app), name, arguments,
                                             _operation(app, name, arguments, session=session)())
            assert not error, result
            calls.append({"name": name, "arguments": arguments, "result": result})
            return result["data"]
        if request.get("request_kind") == "role_invocation":
            call("knowledge_search", {"query": request["question"], "mode": "lexical",
                                      "expected_generation_id": request["index_generation_id"]})
            return role_response(
                request["agent_request"], control_root=app.roots.control, calls=calls,
                final_text="Synthetic query plan: search the exact admitted question; corroboration remains absent.")

        if request["stage_id"] == "retrieve_evidence":
            query = {"query": request["question"], "expected_generation_id": request["index_generation_id"],
                     **self.query_options}
            call("knowledge_search", {**query, "mode": "lexical"})
            call("knowledge_retrieve", {**query, "hops": 0})
            citations = []
            for locator in request["source_chunks"]:
                offset = 0
                while True:
                    data = call("knowledge_read", {"resource_reference": request["source_reference"],
                                                   "chunk_locator": locator, "offset": offset})
                    offset += len(data["text"])
                    if not data["truncated"]:
                        citations.append({k: data[k] for k in ("resource_reference", "chunk_locator", "chunk_hash")})
                        break
            call("citation_verify", {"citations": citations})
        else:
            report = {"source_reference": request["source_reference"],
                      "index_generation_id": request["index_generation_id"],
                      "citations": request["evidence"]["citations"],
                      "improvements": [{"point": "Retain the cited source measurement and its uncertainty.",
                                        "citation_indices": [0]}],
                      "uncertainty": ["No external corroboration was admitted."], "missing_materials": []}
            call("artifact_submit", {"artifact_kind": "document_enhancement_report", "content": json.dumps(report)})
        response = specialist_response(
            {**request, "method_id": "knowledgeos.cited_research_team"}, calls,
            control_root=app.roots.control, final_text="Synthetic cited report fixture; no provider call.")
        if self.alter:
            self.alter(response)
        return response


def started(tmp_path, *, alter=None, policy=None, mutate_spec=None, source_suffix="", extra_roles=()):
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
    install_agent_policy(roots.control)
    app = KnowledgeApplication.from_roots(roots)
    path = roots.vault / SOURCE
    text = (
        (roots.vault / BASE)
        .read_text()
        .replace("11111111-1111-4111-8111-111111111111", "14b927ae-76b0-4c25-989f-299e680c5cdf")
        .replace("20260909-090000-mac-deadbeef", "Deterministic Officer capture")
    )
    if policy:
        text = text.replace("ai_policy: ask", "ai_policy: " + policy)
    path.write_text(text + source_suffix)
    report, code = generate_projection(roots)
    assert code == 0, report
    source = app.reference(SOURCE)
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        "evidence-research",
        "knowledgeos.research_manager.unit",
        catalog.profile("knowledgeos.research_manager"),
        "knowledgeos.eval_officer.unit",
        catalog.profile("knowledgeos.eval_officer"),
        tuple(
            SpecialistBinding(role, role + ".unit", catalog.profile(role))
            for role in ("knowledgeos.retriever", "knowledgeos.synthesizer", *extra_roles)
        ),
        catalog.digest,
        research_native_digest(MODEL_BINDING),
        {},
    )
    raw = canonical(candidate.content())
    binding = replace(
        candidate,
        binding_reference={
            "owner_operation_id": "knowledgeos",
            "artifact_id": "artifact.research.binding",
            "source_revision": digest(raw)[7:],
            "content_digest": digest(raw),
            "media_type": "application/json",
            "size_bytes": len(raw),
            "classification": "internal",
            "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.binding_read",
        },
    )
    spec, run = research_admission(
        app,
        binding,
        source,
        work_id="work.research.unit",
        work_run_id="workrun.research.unit",
        admission_receipt_id="admission.research.unit",
        runner_binding=MODEL_BINDING,
        question="Capture",
    )
    spec["delegation_mode"] = "manager_bounded"
    spec["explicit_assignments"] = []
    if mutate_spec:
        mutate_spec(spec)
    spec["spec_digest"] = work_spec_digest(spec)
    run["work_spec_digest"] = spec["spec_digest"]
    admitted = copy.deepcopy(spec)
    retain_director_context(app, spec, run, source, owner_scope={"question": "Capture"})
    exops = KnowledgeExOps(
        app,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.user",
        director_admitted=lambda value: dict(value) == admitted,
        current_team_binding=lambda team_id: binding if team_id == binding.team_id else None,
        teams={binding.team_id: binding},
        worker_identity="knowledgeos.worker.unit",
        director_method_source_admitted=lambda value, selected: (
            dict(value) == admitted and dict(selected) == source
        ),
    )
    receipt = exops.submit_work(
        spec,
        run,
        actor=OwnerActor("owner.user", "cli"),
        action_id="action.research.unit",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        method_source_reference=source,
    )
    workflow_id = "knowledgeos/" + run["work_run_id"]
    exops.record_dispatch(
        run["work_run_id"],
        action_id=receipt["action_id"],
        outcome="accepted",
        workflow_id=workflow_id,
    )
    running = exops.worker_started(
        run["work_run_id"],
        worker_identity=exops.worker_identity,
        action_id=receipt["action_id"],
        content_digest=receipt["content_digest"],
        work_spec_digest=spec["spec_digest"],
        workflow_id=workflow_id,
    )
    runner = ResearchToolRunner(exops, alter)
    return exops, ResearchTeamExecutor(exops, runner=runner), spec, running, path, receipt


async def produce(executor, spec, run):
    await executor.manager(spec, run)
    await executor.execute_plan(run["work_run_id"])


def test_research_ordered_pages_receipt_independent_assessment(tmp_path):
    exops, executor, spec, run, source_path, receipt = started(tmp_path, source_suffix="\n" + "measured sample " * 700)
    executor.runner.query_options = {"path_prefix": SOURCE, "limit": 1}
    before = source_path.read_bytes()
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id == spec["eval_officer_executor_id"]
    assert result.evaluations[0]["passed_criterion_ids"] == [CRITERION["criterion_id"]]
    assert len(executor.runner.requests) == 2
    assert executor.runner.requests[0]["evidence"] is None
    assert executor.runner.requests[1]["evidence"]["pages"]
    assert any(page["offset"] > 0 for page in executor.runner.requests[1]["evidence"]["pages"])
    finished = exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment,
        segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)
    assert finished["application_state"] == "recorded" and source_path.read_bytes() == before


def test_owner_evidence_audit_keeps_async_activity_loop_responsive(tmp_path):
    _exops, executor, spec, run, _path, _receipt = started(tmp_path)
    asyncio.run(executor.manager(spec, run))
    entered, release = threading.Event(), threading.Event()
    original = executor._response

    def audit(*args):
        entered.set()
        assert release.wait(2), "Owner audit blocked the activity heartbeat loop"
        return original(*args)

    executor._response = audit

    async def execute():
        task = asyncio.create_task(executor.retrieve_evidence(run["work_run_id"]))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            release.set()
            await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(execute())


def test_profile_reads_only_exact_outputs_submitted_to_its_work(tmp_path):
    def admitted(spec):
        spec["continuation_policy"]["max_profile_invocations"] = 3

    exops, executor, spec, run, _path, _receipt = started(
        tmp_path, mutate_spec=admitted, extra_roles=("knowledgeos.evidence_checker",))

    def plan(request):
        decision = synthetic_decision(request)
        context = request["agent_request"]["context"]
        teammate = next(item for item in context["allowed_teammates"]
                        if item["role_id"] == "knowledgeos.evidence_checker")
        decision["steps"].append({
            "stage_id": "artifact_check", "node_id": "node.artifact_check",
            "role_id": teammate["role_id"], "instruction": "Read the exact report submitted by this Work.",
            "route": teammate["default_route"], "criterion_ids": context["target_criterion_ids"],
        })
        return decision

    executor.runner.plan_decision = plan
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.retrieve_evidence(run["work_run_id"]))
    asyncio.run(executor.answer_artifact(run["work_run_id"]))
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    reference = record["submitted_artifacts"][0]
    assignment = next(item for item in record["admitted_method_segments"][record["run"]["active_graph_run_id"]]["assignments"]
                      if item["role_id"] == "knowledgeos.evidence_checker")
    session = TeamToolSession(exops.application, run["work_run_id"], assignment["executor_id"],
                              record["run"]["active_graph_run_id"], exops)
    arguments = {"artifact_reference": reference}
    session.authorize("artifact_read", arguments)
    result, error = _convert_report(
        SimpleNamespace(services=exops.application), "artifact_read", arguments,
        _operation(exops.application, "artifact_read", arguments, session=session)())
    assert not error and result["effect"] == "read_only"
    assert record["method_source_reference"]["content_digest"] in json.dumps(result["data"])
    for changed in ({**reference, "content_digest": "sha256:" + "0" * 64},
                    {**reference, "logical_id": "proposal.unbound"}):
        with pytest.raises(AdmissionError):
            session.authorize("artifact_read", {"artifact_reference": changed})


def test_research_executes_optional_plan_step_and_keeps_original_question(tmp_path):
    def admitted(spec):
        spec["objective"] = "Strengthen the measurements without inventing corroboration."
        spec["delegation_mode"] = "manager_bounded"
        spec["explicit_assignments"] = []
        spec["continuation_policy"]["max_profile_invocations"] = 3

    exops, executor, spec, run, path, receipt = started(
        tmp_path, mutate_spec=admitted, extra_roles=("knowledgeos.query_planner",))
    before = path.read_bytes()

    def plan(request):
        decision = synthetic_decision(request)
        context = request["agent_request"]["context"]
        teammate = next(item for item in context["allowed_teammates"]
                        if item["role_id"] == "knowledgeos.query_planner")
        decision["steps"].insert(0, {
            "stage_id": "query_plan", "node_id": "node.query_plan",
            "role_id": teammate["role_id"], "instruction": "Search the admitted question before source extraction.",
            "route": teammate["default_route"], "criterion_ids": context["target_criterion_ids"],
        })
        return decision

    executor.runner.plan_decision = plan
    asyncio.run(produce(executor, spec, run))
    assert [request["stage_id"] for request in executor.runner.requests] == [
        "query_plan", "retrieve_evidence", "answer_artifact"]
    assert all(request["question"] == "Capture" for request in executor.runner.requests)
    assert executor.runner.requests[1]["agent_handoffs"][0]["agent_step"]["stage_id"] == "query_plan"
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["profile_invocations"] == 3
    assert result.assessment["completion_state"] == "complete"
    exops.worker_finished(
        run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
        next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["edit", "move"])
def test_completed_report_remains_readable_without_current_authority(tmp_path, change):
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    exops, executor, spec, run, source_path, receipt = started(tmp_path)
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment,
        segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)
    server = create_loopback_owner(exops, actor_id="owner.user", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read_result():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            connection.request("POST", "/owner/work/result", json.dumps({"work_run_id": run["work_run_id"]}),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, current = read_result()
        assert status == 200 and current["assessment"]["completion_state"] == "complete"
        if change == "edit":
            source_path.write_bytes(source_path.read_bytes() + b"\nA later independent user edit.\n")
        else:
            source_path.rename(source_path.with_name("Moved source.md"))
        status, historical = read_result()
        assert status == 200 and historical["text"] == current["text"]
        assert historical["historical"] and historical["source_stale"]
        assert historical["current_evidence_valid"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=15)


@pytest.mark.parametrize("tamper", ["source", "citation", "order", "omitted_page", "route", "scope"])
def test_research_rejects_trace_source_citation_scope_and_route_tamper(tmp_path, tamper):
    def alter(response):
        if response["exposed_tools"] == ["artifact_submit"]:
            return
        calls = response["tool_calls"]
        if tamper == "source":
            calls[2]["arguments"]["resource_reference"]["content_digest"] = "sha256:" + "0" * 64
        elif tamper == "citation":
            calls[-1]["arguments"]["citations"][0]["chunk_hash"] = "0" * 64
        elif tamper == "order":
            calls[0], calls[1] = calls[1], calls[0]
        elif tamper == "omitted_page":
            del calls[2]
        elif tamper == "route":
            response["model"] = "another-model"
        else:
            response["exposed_tools"].append("artifact_submit")
    exops, executor, spec, run, _, _ = started(tmp_path, alter=alter)
    asyncio.run(executor.manager(spec, run))
    with pytest.raises((AdmissionError, KeyError)):
        asyncio.run(executor.retrieve_evidence(run["work_run_id"]))
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_research_rejects_changed_source_and_revoked_actor_at_final_commit(tmp_path):
    exops, executor, spec, run, source_path, receipt = started(tmp_path)
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    original = source_path.read_bytes()
    source_path.write_bytes(original + b"\nChanged.\n")
    with pytest.raises(AdmissionError):
        asyncio.run(executor.assess(run["work_run_id"]))
    source_path.write_bytes(original)
    exops.authorize_actor = lambda actor, action: False
    with pytest.raises(AdmissionError):
        exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
            action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment,
            segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
            checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)


@pytest.mark.parametrize("policy", ["local_only", "deny"])
def test_research_never_exports_private_native_source(tmp_path, policy):
    with pytest.raises(AdmissionError):
        started(tmp_path, policy=policy)


def test_native_research_dispatch_never_accepts_curation_team(tmp_path):
    from vaultops.application.exops import work_workflow_type
    _, _, spec, _, _, _ = started(tmp_path)
    assert work_workflow_type({"spec": spec}) == "KnowledgeOSEvidenceResearchV1"
    wrong = copy.deepcopy(spec)
    wrong["team_id"] = "knowledge-curation"
    with pytest.raises(AdmissionError):
        work_workflow_type({"spec": wrong})
    wrong = copy.deepcopy(spec)
    wrong["method_contract_ref"]["method_id"] = "knowledgeos.capture_normalization_team"
    with pytest.raises(AdmissionError):
        work_workflow_type({"spec": wrong})

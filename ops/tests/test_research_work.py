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
from support.control_factory import make_separate_portable_fixture_roots
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

    async def __call__(self, request):
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

        if request["stage_id"] == "retrieve_evidence":
            query = {"query": request["question"], "expected_generation_id": request["index_generation_id"]}
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
        response = {"request_digest": request["request_digest"], "runner_binding": dict(self.binding),
                    "provider": self.binding["provider"], "model": self.binding["model"],
                    "session_id": "boundary-test-" + request["stage_id"], "final_text": "Test evidence only.",
                    "tool_calls": calls, "exposed_tools": list(session.tools())}
        if self.alter:
            self.alter(response)
        return response


def started(tmp_path, *, alter=None, policy=None, mutate_spec=None, source_suffix=""):
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
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
            for role in ("knowledgeos.retriever", "knowledgeos.synthesizer")
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
    if mutate_spec:
        mutate_spec(spec)
        spec["spec_digest"] = work_spec_digest(spec)
        run["work_spec_digest"] = spec["spec_digest"]
    admitted = copy.deepcopy(spec)
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
    await executor.retrieve_evidence(run["work_run_id"])
    await executor.answer_artifact(run["work_run_id"])


def test_research_ordered_pages_receipt_independent_assessment(tmp_path):
    exops, executor, spec, run, source_path, receipt = started(tmp_path, source_suffix="\n" + "measured sample " * 700)
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


def test_document_enhancement_native_cutover_uses_exact_research_constructor(tmp_path):
    from vaultops.application.intake_templates import build_template_admissions

    exops, executor, _, _, source_path, _ = started(tmp_path)
    source = exops.application.reference(source_path.relative_to(exops.application.roots.vault).as_posix())
    scope = {"path": SOURCE, "content_sha256": source["source_revision"]}
    admissions = build_template_admissions(
        exops, "document_enhancement", scope, "native.research.template",
        issue_director_receipt=lambda template, team, selected, request: "admission.template.research",
        native_research_runner_binding=executor.runner_binding,
    )
    spec, run, _, _, selected = admissions[0]
    expected, expected_run = research_admission(
        exops.application, exops.teams["evidence-research"], source,
        work_id=spec["work_id"], work_run_id=run["work_run_id"],
        admission_receipt_id=spec["admission_receipt_id"], runner_binding=executor.runner_binding,
        question=spec["objective"],
    )
    assert spec == expected and run == expected_run and selected == source
    assert spec["continuation_policy"] == {
        "max_segments": 1, "max_repeated_gap_assessments": 1, "max_profile_invocations": 2,
    }


def test_native_retrieval_sequence_diagnostic_never_contains_source_or_arguments():
    import runpy
    from pathlib import Path

    adapter = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "clients/hermes/native_research.py")
    )
    calls = [{
        "name": "knowledge_search",
        "arguments": {"query": "private-source-question"},
        "result": {"operation": "knowledge_search", "status": "empty",
                   "data": {"text": "private-source-excerpt"}},
    }]
    with pytest.raises(adapter["NativeOfficerError"]) as raised:
        adapter["_validate_trace"]({"stage_id": "retrieve_evidence"}, calls)
    message = str(raised.value)
    assert '"name":"knowledge_search","status":"empty"' in message
    assert "private-source" not in message


def test_native_retrieval_empty_query_observations_still_require_exact_reads_and_citations():
    import runpy
    from pathlib import Path

    adapter = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "clients/hermes/native_research.py")
    )
    source = {"owner": "exact-source"}
    request = {"stage_id": "retrieve_evidence", "source_reference": source,
               "source_chunks": ["/body"]}
    calls = [
        {"name": "knowledge_search", "arguments": {},
         "result": {"operation": "knowledge_search", "status": "empty", "data": {}}},
        {"name": "knowledge_retrieve", "arguments": {},
         "result": {"operation": "knowledge_retrieve", "status": "empty", "data": {}}},
        {"name": "knowledge_read", "arguments": {"resource_reference": source, "chunk_locator": "/body"},
         "result": {"operation": "knowledge_read", "data": {"truncated": False}}},
        {"name": "citation_verify", "arguments": {},
         "result": {"operation": "citation_verify", "data": {"all_valid": True}}},
    ]
    adapter["_validate_trace"](request, calls)
    with pytest.raises(adapter["NativeOfficerError"]):
        adapter["_validate_trace"](request, calls[:2])

"""Disposable Team behavior with synthetic runner and real Owner tool effects."""

from __future__ import annotations

import asyncio
import copy
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.agent_fixture import (
    install_agent_policy,
    model_binding,
    readonly_role_calls,
    retain_director_context,
    specialist_response,
)
from support.control_factory import make_separate_portable_fixture_roots
from support.work_factory import synthetic_role_decision
from test_normalization_work import BASE, MODEL_BINDING, SOURCE

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.curation_work import (
    CRITERION,
    CurationTeamExecutor,
    curation_admission,
    curation_native_digest,
)
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.application.work import record_digest, work_spec_digest
from vaultops.application.work_tools import TeamToolSession
from vaultops.interfaces.mcp_server import _convert_report, _operation
from vaultops.projection import generate_projection


class OwnerToolRunner:
    """No provider: exercises the production scoped MCP operation boundary."""

    binding = MODEL_BINDING

    def __init__(self, exops, alter=None):
        self.exops, self.alter, self.requests = exops, alter, []
        self.decisions = []
        self.binding = model_binding(exops.application.roots.control)
        self.evaluation_outcome = None
        self.triage_alter = None

    async def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        if request.get("request_kind") == "agent_decision":
            self.requests.pop()
            self.decisions.append(copy.deepcopy(request))
            return synthetic_role_decision(
                request, control_root=self.exops.application.roots.control,
                evaluation_outcome=self.evaluation_outcome)
        if request.get("request_kind") == "role_invocation":
            self.requests.pop()
            self.decisions.append(copy.deepcopy(request))
            calls = readonly_role_calls(request, exops=self.exops)
            response = synthetic_role_decision(
                request, control_root=self.exops.application.roots.control, calls=calls)
            if self.triage_alter:
                self.triage_alter(response)
            return response
        app = self.exops.application
        session = TeamToolSession(
            app, request["work_run_id"], request["executor_id"], request["graph_run_id"]
        )
        calls = []

        def call(name, arguments):
            session.authorize(name, arguments)
            result, error = _convert_report(
                SimpleNamespace(services=app),
                name,
                arguments,
                _operation(app, name, arguments, session=session)(),
            )
            assert not error, result
            calls.append({"name": name, "arguments": arguments, "result": result})

        call("work_context_read", {})
        for locator in request["source_chunks"]:
            call(
                "knowledge_read",
                {"resource_reference": request["source_reference"], "chunk_locator": locator},
            )
        call("proposal_create", {"source_reference": request["source_reference"]})
        response = specialist_response(request, calls, control_root=app.roots.control,
                                       final_text="Synthetic Pending proposal fixture; no provider call.")
        if self.alter:
            self.alter(response)
        return response


def started(tmp_path, *, alter=None, policy=None, mutate_spec=None, source_suffix="", worker_start=True):
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
        "knowledge-curation",
        "knowledgeos.curation_manager.unit",
        catalog.profile("knowledgeos.curation_manager"),
        "knowledgeos.eval_officer.unit",
        catalog.profile("knowledgeos.eval_officer"),
        tuple(
            SpecialistBinding(role, role + ".unit", catalog.profile(role))
            for role in ("knowledgeos.triage", "knowledgeos.normalizer")
        ),
        catalog.digest,
        curation_native_digest(MODEL_BINDING),
        {},
    )
    raw = canonical(candidate.content())
    binding = replace(
        candidate,
        binding_reference={
            "owner_operation_id": "knowledgeos",
            "artifact_id": "artifact.curation.binding",
            "source_revision": digest(raw)[7:],
            "content_digest": digest(raw),
            "media_type": "application/json",
            "size_bytes": len(raw),
            "classification": "internal",
            "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.binding_read",
        },
    )
    spec, run = curation_admission(
        app,
        binding,
        source,
        work_id="work.curation.unit",
        work_run_id="workrun.curation.unit",
        admission_receipt_id="admission.curation.unit",
        runner_binding=MODEL_BINDING,
    )
    spec["delegation_mode"] = "manager_bounded"
    spec["explicit_assignments"] = []
    if mutate_spec:
        mutate_spec(spec)
    spec["spec_digest"] = work_spec_digest(spec)
    run["work_spec_digest"] = spec["spec_digest"]
    admitted = copy.deepcopy(spec)
    retain_director_context(app, spec, run, source)
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
        action_id="action.curation.unit",
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
    running = run
    if worker_start:
        running = exops.worker_started(
            run["work_run_id"],
            worker_identity=exops.worker_identity,
            action_id=receipt["action_id"],
            content_digest=receipt["content_digest"],
            work_spec_digest=spec["spec_digest"],
            workflow_id=workflow_id,
        )
    runner = OwnerToolRunner(exops, alter)
    return exops, CurationTeamExecutor(exops, runner=runner), spec, running, path, receipt


async def produce(executor, spec, run):
    await executor.manager(spec, run)
    await executor.execute_plan(run["work_run_id"])


@pytest.mark.parametrize("phase", ["_request", "_response"])
def test_normalization_owner_work_keeps_activity_loop_responsive(tmp_path, phase):
    exops, executor, spec, run, path, _receipt = started(tmp_path)
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.capture_evidence(run["work_run_id"]))
    source = path.read_bytes()
    entered, release = threading.Event(), threading.Event()
    original = getattr(executor, phase)

    def blocked(*args):
        if phase == "_response":
            record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
            assert record["curation_model_attempt"] == {"request": args[1], "response": args[2]}
            assert "curation_model_execution" not in record
            assert not record.get("submitted_artifacts")
        entered.set()
        assert release.wait(2), "Curation Owner work blocked the activity heartbeat loop"
        return original(*args)

    setattr(executor, phase, blocked)

    async def execute():
        task = asyncio.create_task(executor.normalization_proposal(run["work_run_id"]))
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
    setattr(executor, phase, original)
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "completed"
    snapshot = exops.application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    effects = [effect for effect in snapshot["intents"].values()
               if effect.get("work_binding", {}).get("work_run_id") == run["work_run_id"]]
    assert len(effects) == 1
    effect = effects[0]
    assert effect["work_binding"] == {
        "work_run_id": run["work_run_id"],
        "work_spec_digest": spec["spec_digest"],
        "graph_run_id": record["run"]["active_graph_run_id"],
        "executor_id": record["curation_model_request"]["executor_id"],
        "source_reference": record["method_source_reference"],
    }
    assert effect["state"] == "completed"
    proposal = record["curation_model_execution"]["response"]["tool_calls"][-1]["result"]["data"][
        "proposal_reference"]
    assert record["submitted_artifacts"] == [proposal]
    pending = list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert pending == [exops.application.roots.vault / effect["artifact_path"]]
    assert digest(pending[0].read_bytes()) == effect["content_digest"]
    assert path.read_bytes() == source


@pytest.mark.parametrize(("phase", "outcome"), [
    ("_assess", "satisfied"), ("_semantic_evidence", "satisfied"),
    ("_retain_semantic", "satisfied"), ("_retain_semantic", "unmet"),
])
def test_curation_assessment_owner_work_keeps_activity_loop_responsive(tmp_path, monkeypatch, phase, outcome):
    exops, executor, spec, run, path, receipt = started(tmp_path)
    source = path.read_bytes()
    assessment_loop = None

    class LoopBoundRunner(OwnerToolRunner):
        async def __call__(self, request):
            if request.get("stage_id") == "evaluator":
                assert asyncio.get_running_loop() is assessment_loop
            return await super().__call__(request)

    executor.runner = LoopBoundRunner(exops)
    executor.runner.evaluation_outcome = outcome
    asyncio.run(produce(executor, spec, run))
    entered, release = threading.Event(), threading.Event()
    original = getattr(executor, phase)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(2), "Curation assessment blocked the parallel heartbeat loop"
        return original(*args, **kwargs)

    async def execute():
        nonlocal assessment_loop
        assessment_loop = asyncio.get_running_loop()
        task = asyncio.create_task(executor.assess(run["work_run_id"]))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert not task.done()
            release.set()
            return await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    with monkeypatch.context() as patch:
        patch.setattr(executor, phase, blocked)
        result = asyncio.run(execute())
    expected = "completed" if outcome == "satisfied" else "awaiting_human"
    assert result.next_run["state"] == expected
    snapshot = exops.application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    assert executor.final_result(record) == result
    assert record["curation_semantic_assessment"]["assessment"] == result.assessment
    assert record["continuation_state"] == ("completed" if outcome == "satisfied" else "human_required")
    if outcome == "unmet":
        assert record["remaining_work"]["unmet_criterion_ids"] == [CRITERION["criterion_id"]]
        assert "result_reference" not in result.next_run
    effects = [effect for effect in snapshot["intents"].values()
               if effect.get("work_binding", {}).get("work_run_id") == run["work_run_id"]]
    assert len(effects) == 1 and effects[0]["state"] == "completed"
    assert path.read_bytes() == source
    assert not snapshot["decisions"]
    finished = exops.worker_finished(
        run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
        next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id)
    assert finished["application_state"] == "recorded"



def test_ordered_team_real_owner_effect_and_independent_result(tmp_path):
    exops, executor, spec, run, path, receipt = started(tmp_path)
    before = path.read_bytes()
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id == spec["eval_officer_executor_id"]
    assert result.candidate_assessor_id not in spec["allowed_executor_ids"]
    assert result.evaluations[0]["passed_criterion_ids"] == [CRITERION["criterion_id"]]
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert record["run"]["profile_invocations"] == 2 and record["run"]["segments_started"] == 1
    assignments = next(iter(record["admitted_method_segments"].values()))["assignments"]
    assert all(assignment["selection_authority"] == "bounded_manager" for assignment in assignments)
    assert record["curation_capture_evidence"]["reads"]
    for read in record["curation_capture_evidence"]["reads"]:
        assert (
            read["data"]["text"]
            and read["arguments"]["resource_reference"] == record["method_source_reference"]
        )
    triage = record["curation_capture_evidence"]
    agent = triage["agent_evidence"]
    assert agent["decision"] == triage["decision"]
    assert agent["content_digest"] == record_digest(agent, ("content_digest",))
    assert agent["request"]["request_kind"] == "role_invocation"
    assert agent["response"]["request_digest"] == record_digest(agent["request"]["agent_request"])
    calls = agent["response"]["tool_calls"]
    assert calls[0]["name"] == "work_context_read"
    assert calls[0]["result"]["data"] == triage["context"]
    assert [{"arguments": call["arguments"], "data": call["result"]["data"]}
            for call in calls if call["name"] == "knowledge_read"] == triage["reads"]
    assert len(executor.runner.requests) == 1
    assert len(record["submitted_artifacts"]) == 1
    assert path.read_bytes() == before
    with pytest.raises(AdmissionError):
        exops.worker_finished(
            run["work_run_id"],
            worker_identity=exops.worker_identity,
            action_id=receipt["action_id"],
            next_run=result.next_run,
            assessment=result.assessment,
            segments=result.segments,
            evaluations=result.evaluations,
            artifacts=result.artifacts,
            checkpoints=result.checkpoints,
            candidate_assessor_id=spec["allowed_executor_ids"][1],
        )
    finished = exops.worker_finished(
        run["work_run_id"],
        worker_identity=exops.worker_identity,
        action_id=receipt["action_id"],
        next_run=result.next_run,
        assessment=result.assessment,
        segments=result.segments,
        evaluations=result.evaluations,
        artifacts=result.artifacts,
        checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id,
    )
    assert finished["application_state"] == "recorded"


@pytest.mark.parametrize("stage", ["capture", "normalize", "assess"])
def test_missing_manager_or_previous_stage_is_denied(tmp_path, stage):
    _exops, executor, spec, run, _path, _receipt = started(tmp_path)
    if stage != "capture":
        asyncio.run(executor.manager(spec, run))
    method = {
        "capture": executor.capture_evidence,
        "normalize": executor.normalization_proposal,
        "assess": executor.assess,
    }[stage]
    with pytest.raises(AdmissionError):
        asyncio.run(method(run["work_run_id"]))
    assert executor.runner.requests == []


def test_manager_and_capture_cannot_repeat(tmp_path):
    _exops, executor, spec, run, _path, _receipt = started(tmp_path)
    asyncio.run(executor.manager(spec, run))
    with pytest.raises(AdmissionError):
        asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.capture_evidence(run["work_run_id"]))
    with pytest.raises(AdmissionError):
        asyncio.run(executor.capture_evidence(run["work_run_id"]))



@pytest.mark.parametrize("damage", [
    "missing_read", "wrong_source", "ungranted_tool", "altered_payload", "empty_decision",
])
def test_required_triage_rejects_invalid_owner_reads_before_proposal(tmp_path, damage):
    exops, executor, spec, run, path, _receipt = started(tmp_path)
    asyncio.run(executor.manager(spec, run))
    before = exops.application.journal.read()
    source_bytes = path.read_bytes()

    def alter(response):
        read = next(call for call in response["tool_calls"] if call["name"] == "knowledge_read")
        if damage == "missing_read":
            response["tool_calls"] = [call for call in response["tool_calls"]
                                      if call["name"] != "knowledge_read"]
        elif damage == "wrong_source":
            read["arguments"]["resource_reference"] = exops.application.reference(BASE)
        elif damage == "ungranted_tool":
            read["name"] = "proposal_create"
        elif damage == "altered_payload":
            read["result"]["data"]["text"] += "\nInvented source observation."
        else:
            response["final_text"] = '{"summary":"","issues":[],"normalization_instructions":""}'

    executor.runner.triage_alter = alter
    with pytest.raises(AdmissionError):
        asyncio.run(executor.capture_evidence(run["work_run_id"]))
    snapshot = exops.application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    assert "curation_capture_evidence" not in record
    assert "curation_model_execution" not in record
    assert not record.get("submitted_artifacts")
    assert snapshot["human_requests"] == before["human_requests"]
    assert snapshot["receipts"] == before["receipts"]
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert path.read_bytes() == source_bytes
    calls = copy.deepcopy(executor.runner.decisions)
    with pytest.raises(AdmissionError):
        asyncio.run(executor.capture_evidence(run["work_run_id"]))
    with pytest.raises(AdmissionError):
        asyncio.run(executor.normalization_proposal(run["work_run_id"]))
    assert executor.runner.decisions == calls
    assert executor.runner.requests == []

@pytest.mark.parametrize(
    "phase", ["before_assessment", "before_commit"]
)
@pytest.mark.parametrize(
    "changed", ["triage", "response", "receipt", "gate", "source", "effect", "segment", "runtime"]
)
def test_fresh_assessment_rejects_changed_evidence_and_effects(tmp_path, changed, phase):
    exops, executor, spec, run, path, _receipt = started(tmp_path)
    asyncio.run(produce(executor, spec, run))
    if phase == "before_commit":
        asyncio.run(executor.assess(run["work_run_id"]))
    app = exops.application
    if changed == "source":
        path.write_bytes(path.read_bytes() + b"\nChanged after proposal.\n")
    elif changed == "runtime":
        executor.runner.binding = {**MODEL_BINDING, "adapter_digest": digest(b"changed")}
    else:
        with app.journal.writer() as writer:
            record = writer.value["intents"]["exops.work." + run["work_run_id"]]
            effect = next(e for e in writer.value["intents"].values() if e.get("work_binding"))
            if changed == "triage":
                record["curation_capture_evidence"]["reads"][0]["data"]["text"] = "fabricated"
            elif changed == "response":
                evidence = record["curation_model_execution"]
                evidence["response"]["tool_calls"][1]["result"]["data"]["text"] = "fabricated"
                evidence["evidence_digest"] = record_digest(
                    {"request": evidence["request"], "response": evidence["response"]}
                )
            elif changed == "receipt":
                writer.value["receipts"][effect["effect_id"]]["execution"]["result_digest"] = (
                    digest(b"wrong")
                )
            elif changed == "gate":
                writer.value["human_requests"][effect["human_request_id"]]["lifecycle"] = "resolved"
            elif changed == "effect":
                writer.value["intents"]["unknown.effect"] = {
                    "work_binding": {"work_run_id": run["work_run_id"]},
                    "state": "unknown",
                }
            elif changed == "segment":
                next(iter(record["admitted_method_segments"].values()))["assignments"][0][
                    "executor_id"
                ] = spec["allowed_executor_ids"][1]
            writer.commit(app.clock())
    with pytest.raises(AdmissionError):
        if phase == "before_commit":
            executor.final_result(app.journal.read()["intents"]["exops.work." + run["work_run_id"]])
        else:
            asyncio.run(executor.assess(run["work_run_id"]))


@pytest.mark.parametrize(
    "alter",
    [
        lambda response: response["tool_calls"].pop(1),
        lambda response: response.update(
            exposed_tools=["work_context_read", "knowledge_read", "canonical_apply"]
        ),
        lambda response: response.update(provider="other-provider"),
    ],
)
def test_native_trace_cannot_skip_read_or_widen_tools_or_route(tmp_path, alter):
    _exops, executor, spec, run, _path, _receipt = started(tmp_path, alter=alter)
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.capture_evidence(run["work_run_id"]))
    with pytest.raises(AdmissionError):
        asyncio.run(executor.normalization_proposal(run["work_run_id"]))


def test_pending_effect_does_not_override_independent_unmet_verdict(tmp_path):
    exops, executor, spec, run, path, receipt = started(tmp_path)
    before = path.read_bytes()
    asyncio.run(produce(executor, spec, run))
    executor.runner.evaluation_outcome = "unmet"
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert result.next_run["state"] == "awaiting_human"
    assert result.assessment["completion_state"] == "incomplete"
    assert result.evaluations[0]["failed_criterion_ids"] == [CRITERION["criterion_id"]]
    calls = copy.deepcopy((executor.runner.requests, executor.runner.decisions))
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert executor.final_result(record) == result
    assert (executor.runner.requests, executor.runner.decisions) == calls
    exops.worker_finished(
        run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
        next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id)
    view = exops.read_work(run["work_run_id"], actor=OwnerActor("owner.user", "cli"))
    assert view["remaining_work"]["unmet_criterion_ids"] == [CRITERION["criterion_id"]]
    assert len(list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    assert path.read_bytes() == before


def test_external_model_policy_is_checked_before_manager_graph(tmp_path):
    _exops, executor, spec, run, _path, _receipt = started(tmp_path, policy="local_only")
    with pytest.raises(AdmissionError):
        asyncio.run(executor.manager(spec, run))
    assert executor.runner.requests == []


def test_truncated_native_read_cannot_certify_the_full_source(tmp_path):
    suffix = "\n" + "source page evidence " * 500
    exops, executor, spec, run, _path, _receipt = started(tmp_path, source_suffix=suffix)
    asyncio.run(executor.manager(spec, run))
    asyncio.run(executor.capture_evidence(run["work_run_id"]))
    reads = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]][
        "curation_capture_evidence"
    ]["reads"]
    assert suffix.strip() in "".join(read["data"]["text"] for read in reads)
    with pytest.raises(AdmissionError):
        asyncio.run(executor.normalization_proposal(run["work_run_id"]))


def test_manager_preserves_an_explicit_owner_assignment(tmp_path):
    def pin(spec):
        profile = next(item for item in spec["allowed_profiles"]
                       if item["role_id"] == "knowledgeos.normalizer")
        spec["explicit_assignments"] = [{
            "node_id": "node.owner_pinned_normalizer",
            "executor_id": "knowledgeos.normalizer.unit", **profile,
            "criterion_ids": [CRITERION["criterion_id"]],
        }]

    exops, executor, spec, run, _path, _receipt = started(tmp_path, mutate_spec=pin)
    asyncio.run(produce(executor, spec, run))
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assignments = next(iter(record["admitted_method_segments"].values()))["assignments"]
    pinned = next(item for item in assignments if item["role_id"] == "knowledgeos.normalizer")
    assert pinned["node_id"] == "node.owner_pinned_normalizer"
    assert pinned["executor_id"] == "knowledgeos.normalizer.unit"
    assert pinned["selection_authority"] == "explicit_user"
    assert len(record["submitted_artifacts"]) == 1

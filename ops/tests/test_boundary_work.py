"""Behavioral owner boundaries; synthetic runner is not a native-provider proof."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from support.boundary_case import BASE, make_case
from support.boundary_fixture import accepted_exchange
from support.boundary_work_smoke import gateway_denials, owner_http, plan
from test_normalization_work import MODEL_BINDING

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.read_tools import KnowledgeReadTools
from vaultops.application.work_tools import TeamToolSession
from vaultops.interfaces.mcp_server import _convert_report, _operation


class OwnerToolRunner:
    def __init__(self, binding, application):
        self.binding, self.application = copy.deepcopy(binding), application
        self.requests, self.responses = [], []
        self.alter = None

    async def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        app = self.application
        session = TeamToolSession(app, request["work_run_id"], request["executor_id"], request["graph_run_id"])
        stage = request["stage_id"]
        if stage == "gateway_evidence":
            name, arguments = "gateway_evidence_read", {"receipt_id": request["gateway_receipt_id"]}
        elif stage == "schema_diagnosis":
            name, arguments = "vault_diagnose", {"resource_reference": request["source_reference"]}
        else:
            name = "proposal_adaptation" if stage == "adaptation" else "proposal_recovery_plan"
            arguments = {"source_reference": request["source_reference"], "content": json.dumps(plan(request))}
        session.authorize(name, arguments)
        result, error = _convert_report(SimpleNamespace(services=app), name, arguments,
                                         _operation(app, name, arguments, session=session)())
        assert not error, result
        response = {"request_digest": request["request_digest"], "runner_binding": dict(self.binding),
                    "model": self.binding["model"], "provider": self.binding["provider"],
                    "session_id": "boundary-test-" + stage, "final_text": "Boundary test actual owner effect only.",
                    "tool_calls": [{"name": name, "arguments": arguments, "result": result}],
                    "exposed_tools": list(session.tools())}
        if self.alter:
            self.alter(response)
        self.responses.append(copy.deepcopy(response))
        return response


def started(tmp_path, kind):
    case = make_case(tmp_path, MODEL_BINDING, OwnerToolRunner, kind, unresolved=kind == "maintenance")
    exops, run, receipt = case["exops"], case["run"], case["receipt"]
    workflow_id = "knowledgeos/" + run["work_run_id"]
    exops.record_dispatch(run["work_run_id"], action_id=receipt["action_id"], outcome="accepted", workflow_id=workflow_id)
    case["run"] = exops.worker_started(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=case["spec"]["spec_digest"], workflow_id=workflow_id)
    asyncio.run(case["executor"].manager(case["spec"], case["run"]))
    return case


async def produce(case):
    for stage in case["policy"].stages:
        await case["executor"].execute_stage(case["run"]["work_run_id"], stage)
    return await case["executor"].assess(case["run"]["work_run_id"])


@pytest.mark.parametrize("kind", ["exchange", "maintenance"])
def test_actual_owner_effect_independent_assessment_and_authenticated_export(tmp_path, kind):
    case = started(tmp_path, kind)
    before = case["source_path"].read_bytes()
    result = asyncio.run(produce(case))
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id not in case["spec"]["allowed_executor_ids"]
    assert result.evaluations[0]["passed_criterion_ids"] == [case["policy"].criterion["criterion_id"]]
    exops = case["exops"]
    receipt = exops.worker_finished(case["run"]["work_run_id"], worker_identity=exops.worker_identity,
        action_id=case["receipt"]["action_id"], next_run=result.next_run, assessment=result.assessment,
        segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)
    assert receipt["application_state"] == "recorded"
    pending = list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1 and case["source_path"].read_bytes() == before
    exported = owner_http(case, pending[0].read_bytes())
    assert exported["status"] == 200
    assert not exops.application.journal.read()["decisions"]
    if kind == "maintenance":
        assert exported["report"]["unresolved_effect_ids"] == [case["diagnostic_canary"]]
        assert all(step["diagnosis_complete"] is False for step in exported["report"]["steps"])
    else:
        evidence = KnowledgeReadTools(exops.application).gateway_evidence_read(case["accepted"][0]["receipt_id"])
        assert any(reference["content_digest"] == digest(canonical(evidence))
                   for reference in result.segments[0]["input_references"])


def test_accepted_gateway_requires_current_correlation_ttl_and_authentic_retained_bytes(tmp_path):
    case = started(tmp_path, "exchange")
    assert set(gateway_denials(case)) == {"expiry", "correlation"}
    app = case["exops"].application
    receipt_id = case["accepted"][0]["receipt_id"]
    with app.journal.writer() as writer:
        record = next(iter(writer.value["gateway"].values()))
        record["payload"]["resources"][0]["content_digest"] = "sha256:" + "0" * 64
        writer.commit(app.clock())
    with pytest.raises(AdmissionError):
        KnowledgeReadTools(app).gateway_evidence_read(receipt_id)


@pytest.mark.parametrize("kind", ["exchange", "maintenance"])
def test_selected_source_and_tool_cannot_be_chosen_by_profile(tmp_path, kind):
    case = started(tmp_path, kind)
    record = case["exops"].application.journal.read()["intents"]["exops.work." + case["run"]["work_run_id"]]
    session = TeamToolSession(case["exops"].application, case["run"]["work_run_id"],
                             case["spec"]["allowed_executor_ids"][0], record["run"]["active_graph_run_id"])
    with pytest.raises(AdmissionError):
        session.authorize("index_status_read", {})
    tool = "gateway_evidence_read" if kind == "exchange" else "vault_diagnose"
    arguments = {"receipt_id": "admission." + "0" * 32} if kind == "exchange" else {
        "resource_reference": case["exops"].application.reference(BASE)}
    with pytest.raises(AdmissionError):
        session.authorize(tool, arguments)
    foreign = {**case["source"], "owner_operation_id": "syntheticexchange"}
    with pytest.raises(AdmissionError):
        case["exops"].application.resolve_reference(foreign)


@pytest.mark.parametrize("kind,field", [("exchange", "gateway_receipt_id"), ("exchange", "disclosure_scope"),
    ("exchange", "citations"), ("maintenance", "preimage_digest"), ("maintenance", "unresolved_effect_ids"),
    ("maintenance", "steps")])
def test_plan_denies_changed_receipt_disclosure_preimage_unresolved_and_complete_claim(tmp_path, kind, field):
    case = started(tmp_path, kind)
    executor, run_id = case["executor"], case["run"]["work_run_id"]
    asyncio.run(executor.execute_stage(run_id, case["policy"].stages[0]))
    record = executor.current(run_id)
    request = executor.request(record, case["policy"].stages[-1])
    payload = plan(request)
    if field == "gateway_receipt_id":
        other, _, _ = accepted_exchange(case["exops"].application, case["source"])
        payload[field] = other["receipt_id"]
    elif field == "disclosure_scope":
        payload[field]["scope"] = "unrestricted"
    elif field == "citations":
        payload[field] = [case["source"]]
    elif field == "preimage_digest":
        payload[field] = "sha256:" + "0" * 64
    elif field == "unresolved_effect_ids":
        payload[field] = []
    else:
        payload[field][0]["diagnosis_complete"] = True
    session = TeamToolSession(case["exops"].application, run_id, request["executor_id"], request["graph_run_id"])
    with pytest.raises(AdmissionError):
        session.authorize(case["policy"].tools[request["stage_id"]][0], {
            "source_reference": case["source"], "content": json.dumps(payload)})
    assert not list((case["exops"].application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


@pytest.mark.parametrize("tamper", ["receipt", "route", "diagnostic"])
def test_native_trace_cannot_substitute_fabricated_owner_evidence(tmp_path, tamper):
    case = started(tmp_path, "maintenance")
    def alter(response):
        if tamper == "route":
            response["model"] = "other-model"
        elif tamper == "receipt":
            response["tool_calls"][0]["result"]["request_id"] = "fake-only-request-id"
            response["tool_calls"][0]["result"]["effect"] = "proposal_created"
        else:
            response["tool_calls"][0]["result"]["data"]["link_count"] += 1
    case["runner"].alter = alter
    with pytest.raises(AdmissionError):
        asyncio.run(case["executor"].execute_stage(case["run"]["work_run_id"], "schema_diagnosis"))
    assert not list((case["exops"].application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_owner_receipt_not_model_text_is_required_at_independent_assessment(tmp_path):
    case = started(tmp_path, "maintenance")
    asyncio.run(produce(case))
    app = case["exops"].application
    with app.journal.writer() as writer:
        effect_id = next(key for key, item in writer.value["intents"].items()
                         if item.get("action") == "proposal_recovery_plan")
        writer.value["receipts"][effect_id]["execution"]["outcome"] = "failed"
        writer.commit(app.clock())
    with pytest.raises(AdmissionError):
        asyncio.run(case["executor"].assess(case["run"]["work_run_id"]))


def test_new_unresolved_outcome_after_diagnosis_denies_plan_before_intent(tmp_path):
    from vaultops.application.mcp_effects import submit_pending

    case = started(tmp_path, "maintenance")
    executor, run_id = case["executor"], case["run"]["work_run_id"]
    asyncio.run(executor.execute_stage(run_id, "schema_diagnosis"))
    request = executor.request(executor.current(run_id), "recovery_plan")
    app = case["exops"].application
    with app.journal.writer() as writer:
        writer.value["intents"]["uncertain.new_after_diagnosis"] = {
            "state": "unknown", "source_path": app.resolve_reference(case["source"]),
            "content_digest": digest(b"new unresolved outcome"), "generation": writer.generation,
            "fencing_token": "generation.g" + str(writer.generation)}
        writer.commit(app.clock())
    before = app.journal.read()
    session = TeamToolSession(app, run_id, request["executor_id"], request["graph_run_id"])
    with pytest.raises(AdmissionError):
        submit_pending(app, "proposal_recovery_plan", {
            "source_reference": case["source"], "content": json.dumps(plan(request))}, session=session)
    assert app.journal.read() == before
    assert not list((app.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_recovery_plan_live_owner_revocation_after_prepare_prevents_artifact(tmp_path, monkeypatch):
    from support.owner_runtime_fixture import write_policy

    from vaultops.adapters.owner_journal import JournalSession
    from vaultops.application.mcp_effects import submit_pending
    from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime

    case = make_case(tmp_path, MODEL_BINDING, OwnerToolRunner, "maintenance", unresolved=True)
    binding, source = case["binding"], case["source"]
    method = case["policy"].method_id
    document = {
        "version": 1, "actor_id": "owner.officer_smoke", "actions": ["submit_work", "read_work"],
        "director_enabled": True, "worker_identity": "knowledgeos.worker.live_recovery",
        "methods": {method: {
            "team_binding": {**binding.content(), "binding_reference": dict(binding.binding_reference)},
            "runner_binding": dict(MODEL_BINDING),
            "admission_scope": {"source_paths": [case["source_path"].relative_to(
                case["exops"].application.roots.vault).as_posix()], "parameters": {}}}},
        "templates": [], "freeform_research": False, "daily_enabled": False}
    policy_path = tmp_path / "owner-policy.json"
    write_policy(policy_path, document)
    runtime = WritableOwnerRuntime(case["exops"].application.roots,
        policy=OwnerRuntimePolicy(policy_path), runners={method: case["runner"]})
    receipt = runtime.admit_selected(method, source, request_id="live-recovery-revocation", request={})
    exops, executor = runtime.exops, runtime.executors[method]
    app = runtime.application
    run_id = receipt["work_run_id"]
    record = app.journal.read()["intents"]["exops.work." + run_id]
    workflow_id = "knowledgeos/" + run_id
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="accepted", workflow_id=workflow_id)
    run = exops.worker_started(run_id, worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=record["spec"]["spec_digest"], workflow_id=workflow_id)
    asyncio.run(executor.manager(record["spec"], run))
    asyncio.run(executor.execute_stage(run_id, "schema_diagnosis"))
    request = executor.request(executor.current(run_id), "recovery_plan")
    original_commit = JournalSession.commit

    def revoke_after_prepare(writer, now):
        original_commit(writer, now)
        if any(intent.get("action") == "proposal_recovery_plan" and intent.get("state") == "unknown"
               and intent.get("identity", {}).get("work_run_id") == run_id
               for intent in writer.value["intents"].values()):
            document["director_enabled"] = False
            write_policy(policy_path, document)

    monkeypatch.setattr(JournalSession, "commit", revoke_after_prepare)
    original_source = case["source_path"].read_bytes()
    session = TeamToolSession(app, run_id, request["executor_id"], request["graph_run_id"])
    with pytest.raises(AdmissionError) as denied:
        submit_pending(app, "proposal_recovery_plan", {
            "source_reference": source, "content": json.dumps(plan(request))}, session=session)
    assert denied.value.code == "ADMISSION_REQUIRED"
    snapshot = app.journal.read()
    assert snapshot["intents"][case["diagnostic_canary"]]["state"] == "unknown"
    assert any(intent.get("action") == "proposal_recovery_plan" and intent["state"] == "unknown"
               for intent in snapshot["intents"].values())
    assert not list((app.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert case["source_path"].read_bytes() == original_source

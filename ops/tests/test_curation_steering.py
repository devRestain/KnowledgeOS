"""Consumer-visible safe boundary and exact owner HTTP receipt regressions."""
from __future__ import annotations

import asyncio
import base64
import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from support.agent_fixture import decision_response
from support.steering_factory import ACTOR, apply, fixture, receive, waiting

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_steering import _command, _receipt_key
from vaultops.application.curation_steering_contract import WORKFLOW
from vaultops.application.curation_steering_temporal import _SteeringActivities
from vaultops.application.work import record_digest, work_spec_digest
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.interfaces.loopback_owner import LoopbackIngress

NATIVE = "native-steering-unit"


def saved(controller, work_run_id):
    return controller.app.journal.read()["intents"]["exops.work." + work_run_id]


def answer(controller, work_run_id):
    return apply(controller, work_run_id, receive(controller, work_run_id, "answer", {"answer": "Owner label"}))


def close(controller, work_run_id):
    answer(controller, work_run_id)
    return apply(controller, work_run_id, receive(controller, work_run_id, "cutover", {"reason": "Replace only the unexecuted stage"}))


def test_owner_supervisor_applies_received_answer_once(tmp_path):
    pytest.importorskip("temporalio")
    from vaultops.application.owner_runtime import DispatchSupervisor

    controller, _spec, run, *_rest = waiting(tmp_path)
    work_id = run["work_run_id"]
    receipt = receive(controller, work_id, "answer", {"answer": "Owner ordering decision"})
    controller.exops.curation_steering = controller

    class NativeUpdate:
        sends = 0

        def get_workflow_handle(self, workflow_id, *, run_id):
            assert workflow_id == "knowledgeos/" + work_id and run_id == NATIVE
            return self

        async def execute_update(self, _update, payload, *, id):
            assert id == receipt["command_id"]
            self.sends += 1
            return controller.apply(payload, temporal_run_id=NATIVE)

    client = NativeUpdate()
    runtime = SimpleNamespace(application=controller.app, exops=controller.exops)
    supervisor = DispatchSupervisor(runtime, {}, steering_client=client)
    assert asyncio.run(supervisor._commands()) == [{"command_id": receipt["command_id"], "state": "applied"}]
    assert saved(controller, work_id)["steering_context"]["answer"] == "Owner ordering decision"
    commands = controller.view(work_id, actor=ACTOR)["commands"]
    assert next(item for item in commands if item["command_id"] == receipt["command_id"])["application_state"] == "applied"
    assert asyncio.run(supervisor._commands()) == []
    assert client.sends == 1


def test_question_parameter_cutover_amendment_successor_and_exact_export(tmp_path):
    controller, _spec, run, successor, authority, path, canary = waiting(tmp_path)
    work_id = run["work_run_id"]
    source_bytes = path.read_bytes()
    initial = saved(controller, work_id)
    initial_graph = copy.deepcopy(initial["steering_bundles"])
    evidence = initial["steering_triage"]["agent_evidence"]
    assert evidence["request"]["request_kind"] == "role_invocation"
    assert evidence["response"]["request_digest"] == record_digest(evidence["request"]["agent_request"])
    reads = [call for call in evidence["response"]["tool_calls"] if call["name"] == "knowledge_read"]
    assert reads and canary in "".join(call["result"]["data"]["text"] for call in reads)
    assert all(call["arguments"]["resource_reference"] == initial["method_source_reference"] for call in reads)
    assert initial["run"]["state"] == "awaiting_human" and initial["run"].get("active_graph_run_id")
    assert initial["steering_question"]["allowed_actions"] == ["approve"]
    answer(controller, work_id)
    parameter = receive(controller, work_id, "parameter", {"value": "alphabetical"}, parameter_id="report.order")
    apply(controller, work_id, parameter)
    assert saved(controller, work_id)["steering_bundles"] == initial_graph
    cutover = receive(controller, work_id, "cutover", {"reason": "Use declared future context"})
    payload = controller.command_dispatch(work_id, cutover["command_id"])
    with pytest.raises(AdmissionError):
        controller.apply(payload, temporal_run_id=NATIVE)
    partial = asyncio.run(controller.partial(payload, temporal_run_id=NATIVE))
    assert partial["outcome"] == "not_run"
    applied = controller.apply(payload, temporal_run_id=NATIVE)
    closed = saved(controller, work_id)
    assert closed["run"]["state"] == "paused" and "active_graph_run_id" not in closed["run"]
    assert closed["run"]["generation"] == 2 and closed["steering_bundles"] == initial_graph
    command = controller.app.journal.read()["intents"][_command(cutover["command_id"])]
    assert command["cutting_over_run"]["state"] == "cutting_over"
    assert command["execution_receipt"]["generation"] == 1
    assert applied["application_state"] == "applied"
    partial_snapshot = copy.deepcopy(closed["steering_partial"])
    ref = controller.register_directive(work_id, actor=ACTOR, content={"successor_spec_digest": successor["spec_digest"]})
    request = {"actor": ACTOR, "command_id": "command.amend", "kind": "amend_work",
               "target": controller.view(work_id, actor=ACTOR)["target"], "expires_at": controller.app.clock() + timedelta(minutes=5),
               "directive_reference": ref, "successor_spec": successor}
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        controller.request(work_id, **request)
    assert controller.app.journal.read() == before
    authority["amendment"] = True
    amendment = controller.request(work_id, **request)
    apply(controller, work_id, amendment)
    assert saved(controller, work_id)["steering_partial"] == partial_snapshot
    assert saved(controller, work_id)["spec"] == successor
    resume = receive(controller, work_id, "resume", {"reason": "Run the owner-admitted successor"})
    apply(controller, work_id, resume)
    token = asyncio.run(controller.successor(work_id, temporal_run_id=NATIVE))
    live = saved(controller, work_id)
    assert live["steering_successor_context"] == {"order": "alphabetical", "answer": "Owner label"}
    assert live["run"]["segments_started"] == 2 and live["run"]["profile_invocations"] == 3
    assert live["steering_bundles"][token["graph_run_id"]]["segment"]["trigger_kind"] == "work_revision"
    old = {**token, "generation": 1, "fencing_token": initial["run"]["fencing_token"]}
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        asyncio.run(controller.normalize(old, temporal_run_id=NATIVE))
    assert controller.app.journal.read() == before
    asyncio.run(controller.normalize(token, temporal_run_id=NATIVE))
    asyncio.run(controller.finish(work_id, temporal_run_id=NATIVE))
    exported = controller.export(work_id, actor=ACTOR)
    report = json.loads(base64.b64decode(exported["content_base64"]))
    assert report["lines"] == sorted(report["lines"]) and any(canary in line for line in report["lines"])
    assert exported["assessment"]["completion_state"] == "complete"
    assert saved(controller, work_id)["steering_partial"] == partial_snapshot
    assert path.read_bytes() == source_bytes

@pytest.mark.parametrize("owner", ["knowledgeos", "foreign"])
def test_native_start_honors_canonical_owner_dispatch_and_refuses_foreign_owner(tmp_path, owner):
    controller, spec, run, _successor, _authority, path, _canary = fixture(tmp_path)
    work_id = run["work_run_id"]
    before = saved(controller, work_id)
    source = path.read_bytes()
    dispatch = {"owner_operation_id": owner, "work_run_id": work_id,
                "action_id": before["action_id"], "content_digest": before["content_digest"],
                "work_spec_digest": spec["spec_digest"]}
    if owner != "knowledgeos":
        with pytest.raises(AdmissionError):
            asyncio.run(controller.start(dispatch, temporal_run_id=NATIVE))
        assert saved(controller, work_id) == before
    else:
        asyncio.run(controller.start(dispatch, temporal_run_id=NATIVE))
        started = saved(controller, work_id)
        assert started["run"]["state"] == "running"
        assert started["temporal_run_id"] == NATIVE
        assert started["run"]["active_graph_run_id"] in started["steering_bundles"]
    assert path.read_bytes() == source


def test_observed_native_dispatch_is_not_steering_application(tmp_path):
    controller, _spec, run, *_rest = fixture(tmp_path)
    work_id = run["work_run_id"]
    dispatch = controller.dispatch(work_id)
    controller.exops.record_dispatch(work_id, action_id=dispatch["action_id"], outcome="unknown")
    controller.exops.record_observed_dispatch(
        work_id, action_id=dispatch["action_id"], observation=SimpleNamespace(
            workflow_id="knowledgeos/" + work_id, run_id=NATIVE, namespace="knowledgeos",
            workflow_type=WORKFLOW, task_queue="knowledgeos.work.v1", payload=dispatch))
    admitted = saved(controller, work_id)
    assert admitted["application_state"] == "not_started" and admitted["run"]["state"] == "admitted"
    asyncio.run(controller.start(dispatch, temporal_run_id=NATIVE))
    started = saved(controller, work_id)
    assert started["application_state"] == "started" and started["run"]["state"] == "running"
    assert started["run"]["active_graph_run_id"] in started["steering_bundles"]
    assert asyncio.run(controller.start(dispatch, temporal_run_id=NATIVE)) == started["run"]
    assert saved(controller, work_id) == started


def test_native_manager_question_reports_work_uncertainty_and_reason(tmp_path, monkeypatch):
    controller, _spec, run, _successor, _authority, path, _canary = fixture(tmp_path)
    source = path.read_bytes()
    reason = "The selected project scope needs an Owner decision."

    async def question(_runner, request):
        return decision_response(
            request, {"status": "question", "rationale": reason},
            control_root=controller.app.roots.control)

    monkeypatch.setattr(type(controller.runner), "__call__", question)
    activities = _SteeringActivities(controller)
    monkeypatch.setattr(activities, "native", lambda _work_id: NATIVE)
    with pytest.raises(AdmissionError) as failure:
        asyncio.run(activities.manager(controller.dispatch(run["work_run_id"])))
    assert reason in str(failure.value)
    record = saved(controller, run["work_run_id"])
    assert record["application_state"] == "unknown" and record["receipt"]["application_state"] == "unknown"
    assert not record.get("steering_bundles") and not record.get("steering_triage")
    assert not record["run"]["unresolved_effect_ids"] and path.read_bytes() == source


def test_foreign_native_failure_cannot_mark_started_work_unknown(tmp_path, monkeypatch):
    controller, _spec, run, *_rest = fixture(tmp_path)
    work_id = run["work_run_id"]
    dispatch = controller.dispatch(work_id)
    asyncio.run(controller.start(dispatch, temporal_run_id=NATIVE))
    before = controller.app.journal.read()
    activities = _SteeringActivities(controller)
    monkeypatch.setattr(activities, "native", lambda _work_id: "foreign-native-run")
    with pytest.raises(AdmissionError):
        asyncio.run(activities.triage(dispatch))
    assert controller.app.journal.read() == before




def test_steering_triage_without_owner_read_cannot_open_human_control(tmp_path, monkeypatch):
    controller, _spec, run, _successor, _authority, path, _canary = fixture(tmp_path)
    work_id = run["work_run_id"]
    original = type(controller.runner).__call__

    async def omit_read(runner, request):
        response = await original(runner, request)
        if request.get("request_kind") == "role_invocation":
            response["tool_calls"] = []
        return response

    monkeypatch.setattr(type(controller.runner), "__call__", omit_read)
    asyncio.run(controller.start(controller.dispatch(work_id), temporal_run_id=NATIVE))
    before = controller.app.journal.read()
    source_bytes = path.read_bytes()
    with pytest.raises(AdmissionError):
        asyncio.run(controller.triage(work_id, temporal_run_id=NATIVE))
    snapshot = controller.app.journal.read()
    record = saved(controller, work_id)
    assert "steering_triage" not in record
    assert "steering_question" not in record
    assert "steering_report" not in record
    assert snapshot["human_requests"] == before["human_requests"]
    assert snapshot["receipts"] == before["receipts"]
    assert path.read_bytes() == source_bytes
    with pytest.raises(AdmissionError):
        controller.open_boundary(work_id, pending_nodes=["normalization_report"], temporal_run_id=NATIVE)


def test_duplicate_conflict_expiry_revoke_and_unresolved_effect_boundaries(tmp_path):
    controller, _spec, run, _successor, authority, _path, _canary = waiting(tmp_path)
    work_id = run["work_run_id"]
    ref = controller.register_directive(work_id, actor=ACTOR, content={"answer": "Exact answer"})
    body = {"actor": ACTOR, "command_id": "command.answer", "kind": "answer",
            "target": controller.view(work_id, actor=ACTOR)["target"], "expires_at": controller.app.clock() + timedelta(minutes=5),
            "directive_reference": ref}
    receipt = controller.request(work_id, **body)
    before = controller.app.journal.read()
    assert controller.request(work_id, **body) == receipt and controller.app.journal.read() == before
    with pytest.raises(AdmissionError):
        controller.request(work_id, **{**body, "kind": "cutover"})
    authority["actor"] = False
    with pytest.raises(AdmissionError):
        controller.apply(controller.command_dispatch(work_id, receipt["command_id"]), temporal_run_id=NATIVE)
    assert controller.app.journal.read() == before
    authority["actor"] = True
    applied = apply(controller, work_id, receipt)
    assert controller.request(work_id, **body) == applied
    assert apply(controller, work_id, receipt) == applied
    with pytest.raises(AdmissionError):
        receive(controller, work_id, "parameter", {"value": "unsupported"}, parameter_id="report.order")
    with pytest.raises(AdmissionError):
        receive(controller, work_id, "parameter", {"value": "alphabetical"}, parameter_id="objective")
    with controller.app.journal.writer() as writer:
        writer.value["intents"]["exops.work." + work_id]["run"]["unresolved_effect_ids"] = ["effect.unknown"]
        writer.commit(controller.app.clock())
    ref = controller.register_directive(work_id, actor=ACTOR, content={"reason": "Cannot undo unknown effects"})
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        controller.request(work_id, actor=ACTOR, command_id="command.unsafe", kind="cutover",
            target=controller.view(work_id, actor=ACTOR)["target"], expires_at=controller.app.clock() + timedelta(minutes=5), directive_reference=ref)
    assert controller.app.journal.read() == before


def test_director_admission_does_not_bypass_implemented_amendment_envelope(tmp_path):
    controller, _spec, run, successor, authority, _path, _canary = waiting(tmp_path)
    work_id = run["work_run_id"]
    close(controller, work_id)
    authority["amendment"] = True
    changed = copy.deepcopy(successor)
    changed["delegation_mode"] = "explicit_assignments"
    changed["spec_digest"] = work_spec_digest(changed)
    ref = controller.register_directive(work_id, actor=ACTOR, content={"successor_spec_digest": changed["spec_digest"]})
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        controller.request(work_id, actor=ACTOR, command_id="command.reassign", kind="amend_work",
            target=controller.view(work_id, actor=ACTOR)["target"], expires_at=controller.app.clock() + timedelta(minutes=5),
            directive_reference=ref, successor_spec=changed)
    assert controller.app.journal.read() == before


def test_actual_controller_http_question_artifact_receipt_and_read_only_denial(tmp_path):
    controller, _spec, run, _successor, _authority, _path, _canary = waiting(tmp_path)
    backend = ExOpsWebBackend(controller.exops, ACTOR)
    def call(route, payload):
        return backend(LoopbackIngress("knowledgeos", "POST", "work/steering/" + route,
                                      json.dumps(payload).encode()))
    work_id = run["work_run_id"]
    view = call("view", {"work_run_id": work_id})
    assert view["supported_kinds"] == ["answer"]
    reference = call("artifact", {"work_run_id": work_id, "content": {"answer": "HTTP answer"}})
    body = {"work_run_id": work_id, "command_id": "command.http.answer", "kind": "answer", "target": view["target"],
            "expires_at": (controller.app.clock() + timedelta(minutes=5)).isoformat(), "directive_reference": reference}
    received = call("request", body)
    assert received["application_state"] == "received"
    assert call("request", body) == received
    with pytest.raises(AdmissionError):
        call("request", {**body, "kind": "running_reassign"})
    controller.exops.read_only = True
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        call("artifact", {"work_run_id": work_id, "content": {"answer": "Denied"}})
    assert controller.app.journal.read() == before


def test_queued_question_expiry_does_not_apply_or_write(tmp_path):
    from dataclasses import replace
    controller, _spec, run, _successor, _authority, _path, _canary = waiting(tmp_path)
    work_id = run["work_run_id"]
    now = controller.app.clock()
    reference = controller.register_directive(work_id, actor=ACTOR, content={"answer": "Too late"})
    receipt = controller.request(work_id, actor=ACTOR, command_id="command.expiring", kind="answer",
        target=controller.view(work_id, actor=ACTOR)["target"], expires_at=now + timedelta(seconds=5),
        directive_reference=reference)
    controller.exops.application = replace(controller.app, clock=lambda: now + timedelta(seconds=6))
    before = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        apply(controller, work_id, receipt)
    assert controller.app.journal.read() == before
    assert saved(controller, work_id)["run"]["state"] == "awaiting_human"


@pytest.mark.parametrize("changed", [
    "missing_context", "assessment", "evaluation", "checkpoint", "report",
    "segment", "assessor", "effect_receipt", "work_receipt",
])
def test_completed_owner_export_revalidates_independent_immutable_evidence(tmp_path, changed):
    controller, _spec, run, _successor, _authority, _path, _canary = waiting(tmp_path)
    work_id = run["work_run_id"]
    close(controller, work_id)
    apply(controller, work_id, receive(controller, work_id, "resume", {"reason": "Run the immutable successor"}))
    token = asyncio.run(controller.successor(work_id, temporal_run_id=NATIVE))
    asyncio.run(controller.normalize(token, temporal_run_id=NATIVE))
    completed_receipt = asyncio.run(controller.finish(work_id, temporal_run_id=NATIVE))
    before = controller.app.journal.read()
    exported = controller.export(work_id, actor=ACTOR)
    assert exported["assessment"]["completion_state"] == "complete"
    assert controller.view(work_id, actor=ACTOR)["run"]["state"] == "completed"
    assert asyncio.run(controller.finish(work_id, temporal_run_id=NATIVE)) == completed_receipt
    assert controller.app.journal.read() == before
    with controller.app.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + work_id]
        if changed == "missing_context":
            record.pop("steering_completion")
        elif changed == "assessment":
            record["assessment"]["criterion_outcomes"][0]["outcome"] = "unmet"
        elif changed == "evaluation":
            record["evaluations"][0]["evaluator_id"] = record["spec"]["manager_executor_id"]
        elif changed == "checkpoint":
            record["checkpoints"][token["graph_run_id"]] = "sha256:" + "0" * 64
        elif changed == "report":
            record["steering_report"]["bytes"] = base64.b64encode(b"{}").decode()
        elif changed == "segment":
            record["steering_bundles"][token["graph_run_id"]]["nodes"][1]["max_attempts"] = 2
        elif changed == "assessor":
            record["steering_completion"]["result"]["assessment"]["assessor_id"] = record["spec"]["manager_executor_id"]
        elif changed == "effect_receipt":
            identity = record["steering_report_intent"]["effect_id"]
            writer.value["receipts"][_receipt_key(identity)]["receipt"]["result_digest"] = "sha256:" + "0" * 64
        elif changed == "work_receipt":
            writer.value["receipts"]["exops.work." + record["action_id"]]["receipt"]["result_digest"] = "sha256:" + "0" * 64
        writer.commit(controller.app.clock())
    before_denial = controller.app.journal.read()
    with pytest.raises(AdmissionError):
        controller.export(work_id, actor=ACTOR)
    assert controller.app.journal.read() == before_denial

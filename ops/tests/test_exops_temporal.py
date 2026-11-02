"""Owner ExOps admission and Temporal delivery without a live service."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import types
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.control_factory import make_separate_portable_fixture_roots
from support.work_factory import WorkFixture

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.exops import KnowledgeExOps, OwnerActor, TeamBinding
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.team_catalog import OfficerBinding, SpecialistBinding, TeamCatalog
from vaultops.application.temporal_contract import TEAM_WORKFLOW_TYPE, WORKFLOW_TYPE
from vaultops.application.temporal_work import (
    connect_owner_temporal,
    dispatch_admitted_work,
    reconcile_unknown_dispatch,
)
from vaultops.application.work import work_spec_digest
from vaultops.interfaces.exops_web import ExOpsWebBackend


def make_exops(tmp_path, *, with_officer=False):
    roots = make_separate_portable_fixture_roots(
        tmp_path, extra_inputs=("ops/config/team-catalog.json",
                                "ops/config/work-methods.json"), review_queues=True)
    app = KnowledgeApplication.from_roots(roots)
    fixture = WorkFixture()
    catalog = TeamCatalog.load(roots.control)
    fixture.spec.update(team_id="knowledge-curation",
                        manager_executor_id="knowledgeos.curation_manager.one",
                        eval_officer_executor_id="knowledgeos.eval_officer.one",
                        allowed_executor_ids=["knowledgeos.normalizer.one"],
                        allowed_profiles=[catalog.profile("knowledgeos.normalizer")])
    fixture.spec["spec_digest"] = work_spec_digest(fixture.spec)
    fixture.run["work_spec_digest"] = fixture.spec["spec_digest"]
    candidate = TeamBinding(
        team_id="knowledge-curation",
        manager_executor_id=fixture.spec["manager_executor_id"],
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id=fixture.spec["eval_officer_executor_id"],
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=(SpecialistBinding("knowledgeos.normalizer", "knowledgeos.normalizer.one",
                                       catalog.profile("knowledgeos.normalizer")),),
        catalog_digest=catalog.digest,
        native_binding_digest=digest(canonical({"synthetic_native_binding": "no Hermes"})),
        binding_reference={},
    )
    reference = {**fixture.artifacts["binding"], "content_digest": digest(canonical(candidate.content()))}
    team = replace(candidate, binding_reference=reference)
    fixture.artifacts["binding"] = reference
    fixture.run["binding_reference"] = reference
    officers = {}
    if with_officer:
        officer_candidate = OfficerBinding(
            officer_executor_id="knowledgeos.normalization_officer.one",
            officer_profile=catalog.profile("knowledgeos.normalization_officer"),
            eval_officer_executor_id=fixture.spec["eval_officer_executor_id"],
            eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
            catalog_digest=catalog.digest,
            native_binding_digest=digest(canonical({"synthetic_officer_binding": "no Hermes"})),
            binding_reference={},
        )
        officer_reference = {**fixture.artifacts["binding"],
                             "content_digest": digest(canonical(officer_candidate.content()))}
        officer = replace(officer_candidate, binding_reference=officer_reference)
        officers[officer.officer_executor_id] = officer
    exops = KnowledgeExOps(
        app, authorize_actor=lambda actor, action: actor.actor_id == "owner.user"
        and action in {"submit_work", "read_work"},
        director_admitted=lambda spec: spec["admission_receipt_id"] == "admission.synthetic.one",
        current_team_binding=lambda team_id: team if team_id == team.team_id else None,
        teams={team.team_id: team}, worker_identity="knowledgeos.worker.one",
        current_officer_binding=lambda executor_id: officers.get(executor_id),
        officers=officers,
    )
    return exops, fixture


def test_standalone_officer_admission_uses_exact_owner_binding(tmp_path):
    exops, fixture = make_exops(tmp_path, with_officer=True)
    source = exops.application.reference(
        "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md")
    exops.director_officer_source_admitted = lambda _spec, selected: dict(selected) == source
    officer = exops.officers["knowledgeos.normalization_officer.one"]
    fixture.spec.pop("team_id")
    fixture.spec.pop("manager_executor_id")
    fixture.spec.update(target_kind="officer", officer_executor_id=officer.officer_executor_id,
                        delegation_mode="officer_direct",
                        allowed_executor_ids=[officer.officer_executor_id],
                        allowed_profiles=[dict(officer.officer_profile)])
    fixture.spec["spec_digest"] = work_spec_digest(fixture.spec)
    fixture.run["work_spec_digest"] = fixture.spec["spec_digest"]
    fixture.run["binding_reference"] = dict(officer.binding_reference)
    receipt = exops.submit_work(
        fixture.spec, fixture.run, actor=OwnerActor("owner.user", "cli"),
        action_id="action.synthetic.start", expires_at=datetime.now(UTC) + timedelta(hours=1),
        officer_source_reference=source)
    assert receipt["dispatch_state"] == "pending"
    wrong = copy.deepcopy(fixture.spec)
    wrong["allowed_profiles"] = [exops.catalog.profile("knowledgeos.normalizer")]
    wrong["spec_digest"] = work_spec_digest(wrong)
    wrong_run = {**fixture.run, "work_spec_digest": wrong["spec_digest"]}
    with pytest.raises(AdmissionError, match="standalone Officer"):
        exops.submit_work(wrong, wrong_run, actor=OwnerActor("owner.user", "cli"),
                          action_id="action.officer.wrong",
                          expires_at=datetime.now(UTC) + timedelta(hours=1),
                          officer_source_reference=source)


def admit(exops, fixture):
    return exops.submit_work(fixture.spec, fixture.run, actor=OwnerActor("owner.user", "cli"),
                             action_id="action.synthetic.start",
                             expires_at=datetime.now(UTC) + timedelta(hours=1))


def test_work_admission_replay_and_owner_team_boundary(tmp_path):
    exops, fixture = make_exops(tmp_path)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    actor = OwnerActor("owner.user", "cli")
    first = exops.submit_work(fixture.spec, fixture.run, actor=actor,
                              action_id="action.synthetic.start", expires_at=expiry)
    intent = exops.application.journal.read()["intents"]["exops.work." + fixture.run["work_run_id"]]["execution_intent"]
    exops.application.core.validate("ExecutionIntent", intent)
    replay = exops.submit_work(fixture.spec, fixture.run, actor=actor,
                               action_id="action.synthetic.start", expires_at=expiry)
    assert first["dispatch_state"] == "pending" and not first["replayed"]
    assert replay["replayed"] and replay["content_digest"] == first["content_digest"]
    assert exops.read_work(fixture.run["work_run_id"], actor=actor)["run"] == fixture.run
    with pytest.raises(AdmissionError):
        exops.read_work(fixture.run["work_run_id"], actor=OwnerActor("other", "web"))
    with pytest.raises(AdmissionError, match="different content"):
        exops.submit_work(fixture.spec, fixture.run, actor=actor,
                          action_id="action.changed", expires_at=expiry)
    wrong = copy.deepcopy(fixture.spec)
    wrong["manager_executor_id"] = "knowledgeos.other_manager"
    wrong["spec_digest"] = work_spec_digest(wrong)
    wrong_run = {**fixture.run, "work_spec_digest": wrong["spec_digest"]}
    with pytest.raises(AdmissionError, match="WorkSpec does not match"):
        exops.submit_work(wrong, wrong_run, actor=actor,
                          action_id="action.wrong", expires_at=expiry)


@pytest.mark.parametrize("recorded", [False, True])
def test_action_identity_cannot_be_reused_by_another_work(tmp_path, recorded):
    exops, fixture = make_exops(tmp_path)
    actor = OwnerActor("owner.user", "cli")
    expiry = datetime.now(UTC) + timedelta(hours=1)
    receipt = exops.submit_work(fixture.spec, fixture.run, actor=actor,
                                action_id="action.synthetic.start", expires_at=expiry)
    if recorded:
        run_id = fixture.run["work_run_id"]
        started = exops.worker_started(
            run_id, worker_identity=exops.worker_identity,
            action_id=receipt["action_id"], content_digest=receipt["content_digest"],
            work_spec_digest=fixture.spec["spec_digest"], workflow_id=f"knowledgeos/{run_id}")
        next_run = {**started, "state": "awaiting_human", "revision": 3, "control_revision": 3}
        exops.worker_finished(run_id, worker_identity=exops.worker_identity,
                               action_id=receipt["action_id"], next_run=next_run)
    other_spec = {**fixture.spec, "work_id": fixture.spec["work_id"] + ".second"}
    other_spec["spec_digest"] = work_spec_digest(other_spec)
    other_run = {**fixture.run, "work_id": other_spec["work_id"],
                 "work_run_id": fixture.run["work_run_id"] + ".second",
                 "work_spec_digest": other_spec["spec_digest"]}
    before = exops.application.journal.read()

    with pytest.raises(AdmissionError, match="Work or action identity"):
        exops.submit_work(other_spec, other_run, actor=actor,
                          action_id=receipt["action_id"], expires_at=expiry)

    assert exops.application.journal.read() == before
    replay = exops.submit_work(fixture.spec, fixture.run, actor=actor,
                               action_id=receipt["action_id"], expires_at=expiry)
    assert replay["replayed"] is True
    if recorded:
        assert exops.worker_finished(
            run_id, worker_identity=exops.worker_identity,
            action_id=receipt["action_id"], next_run=next_run)["application_state"] == "recorded"


def test_worker_start_is_fenced_and_result_is_owner_committed(tmp_path):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="accepted",
                          workflow_id=f"knowledgeos/{run_id}")
    started = exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"],
                                   content_digest=receipt["content_digest"],
                                   work_spec_digest=fixture.spec["spec_digest"],
                                   workflow_id=f"knowledgeos/{run_id}")
    assert started["state"] == "running" and started["revision"] == 2
    with pytest.raises(AdmissionError, match="reconciled"):
        exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                             action_id=receipt["action_id"],
                             content_digest=receipt["content_digest"],
                             work_spec_digest=fixture.spec["spec_digest"],
                             workflow_id=f"knowledgeos/{run_id}")
    next_run = {**started, "state": "awaiting_human", "revision": 3,
                "control_revision": 3}
    result = exops.worker_finished(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"], next_run=next_run)
    assert result["application_state"] == "recorded"
    effect_receipt = exops.application.journal.read()["receipts"]["exops.work." + receipt["action_id"]]["receipt"]
    exops.application.core.validate("ExecutionReceipt", effect_receipt)
    assert effect_receipt["outcome"] == "succeeded"
    assert exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))["run"] == next_run


def test_metadata_worker_completion_invokes_promotion_after_receipt_commit(tmp_path, monkeypatch):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + run_id]
        record["template_id"] = "note_metadata"
        record["template_request_id"] = "request.synthetic.metadata"
        writer.commit(exops.application.clock())
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="accepted",
                          workflow_id=f"knowledgeos/{run_id}")
    started = exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"],
                                   content_digest=receipt["content_digest"],
                                   work_spec_digest=fixture.spec["spec_digest"],
                                   workflow_id=f"knowledgeos/{run_id}")
    observed = []

    def record_promotion(selected, request_id, *, actor):
        current = selected.application.journal.read()["intents"]["exops.work." + run_id]
        assert current["application_state"] == "recorded"
        observed.append((request_id, actor))
        if len(observed) == 1:
            raise OSError("synthetic interruption after receipt commit")

    monkeypatch.setattr("vaultops.application.metadata_proposals.auto_prepare_metadata_proposal",
                        record_promotion)
    next_run = {**started, "state": "awaiting_human", "revision": 3,
                "control_revision": 3}
    with pytest.raises(OSError, match="synthetic interruption"):
        exops.worker_finished(run_id, worker_identity="knowledgeos.worker.one",
                              action_id=receipt["action_id"], next_run=next_run)
    result = exops.worker_finished(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"], next_run=next_run)
    assert result["application_state"] == "recorded"
    assert observed == [("request.synthetic.metadata", OwnerActor("owner.user", "web"))] * 2
    with pytest.raises(AdmissionError, match="replay differs"):
        exops.worker_finished(run_id, worker_identity="knowledgeos.worker.one",
                              action_id=receipt["action_id"], next_run={**next_run, "revision": 4})


def test_temporal_start_uncertainty_does_not_blindly_retry(tmp_path):
    exops, fixture = make_exops(tmp_path)
    admit(exops, fixture)
    run_id = fixture.run["work_run_id"]

    class UncertainClient:
        calls = 0

        async def start(self, dispatch):
            self.calls += 1
            assert dispatch.workflow_id == f"knowledgeos/{run_id}"
            raise ConnectionError("ack lost")

    client = UncertainClient()
    with pytest.raises(ConnectionError):
        asyncio.run(dispatch_admitted_work(exops, client, run_id))
    assert client.calls == 1
    record = exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))
    assert record["dispatch_state"] == "unknown"
    with pytest.raises(AdmissionError, match="pending"):
        asyncio.run(dispatch_admitted_work(exops, client, run_id))
    assert client.calls == 1


def test_worker_start_preceding_lost_ack_returns_accepted_receipt(tmp_path):
    exops, fixture = make_exops(tmp_path)
    admit(exops, fixture)
    run_id = fixture.run["work_run_id"]

    class StartedBeforeAck:
        async def start(self, dispatch):
            exops.worker_started(
                run_id, worker_identity="knowledgeos.worker.one",
                action_id=dispatch.action_id,
                content_digest=dispatch.payload["content_digest"],
                work_spec_digest=dispatch.payload["work_spec_digest"],
                workflow_id=dispatch.workflow_id)
            raise ConnectionError("start acknowledgement lost after Worker launch")

    receipt = asyncio.run(dispatch_admitted_work(exops, StartedBeforeAck(), run_id))
    assert receipt["dispatch_state"] == "accepted"
    assert receipt["application_state"] == "started"


def test_uncertain_start_reconciles_only_exact_observed_history(tmp_path):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="unknown")
    payload = {"owner_operation_id": "knowledgeos", "work_run_id": run_id,
               "action_id": receipt["action_id"], "content_digest": receipt["content_digest"],
               "work_spec_digest": fixture.spec["spec_digest"]}

    class Observer:
        observation = SimpleNamespace(
            workflow_id=f"knowledgeos/{run_id}", run_id="temporal-run-1",
            namespace="knowledgeos", workflow_type=TEAM_WORKFLOW_TYPE,
            task_queue="knowledgeos.work.v1", payload=payload)

        async def observe_start(self, dispatch):
            assert dispatch.payload == payload
            return self.observation

    observer = Observer()
    observer.observation = SimpleNamespace(**{**vars(observer.observation),
                                              "payload": {**payload, "content_digest": "wrong"}})
    with pytest.raises(AdmissionError, match="Observed Workflow start"):
        asyncio.run(reconcile_unknown_dispatch(exops, observer, run_id))
    assert exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))["dispatch_state"] == "unknown"
    observer.observation = SimpleNamespace(**{**vars(observer.observation), "payload": payload})
    observer.observation.workflow_type = WORKFLOW_TYPE
    with pytest.raises(AdmissionError, match="Observed Workflow start"):
        asyncio.run(reconcile_unknown_dispatch(exops, observer, run_id))
    assert exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))["dispatch_state"] == "unknown"
    observer.observation.workflow_type = TEAM_WORKFLOW_TYPE
    reconciled = asyncio.run(reconcile_unknown_dispatch(exops, observer, run_id))
    assert reconciled["dispatch_state"] == "accepted"
    record = exops.application.journal.read()["intents"]["exops.work." + run_id]
    assert record["temporal_run_id"] == "temporal-run-1"
    with pytest.raises(AdmissionError, match="uncertain"):
        asyncio.run(reconcile_unknown_dispatch(exops, observer, run_id))


def test_actual_worker_callback_resolves_unknown_start_with_exact_pins(tmp_path):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="unknown")
    with pytest.raises(AdmissionError, match="correlated"):
        exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                             action_id=receipt["action_id"],
                             content_digest=receipt["content_digest"],
                             work_spec_digest="wrong", workflow_id=f"knowledgeos/{run_id}")
    assert exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))["dispatch_state"] == "unknown"
    started = exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"],
                                   content_digest=receipt["content_digest"],
                                   work_spec_digest=fixture.spec["spec_digest"],
                                   workflow_id=f"knowledgeos/{run_id}")
    assert started["state"] == "running"
    assert exops.read_work(run_id, actor=OwnerActor("owner.user", "cli"))["dispatch_state"] == "accepted"


def test_native_client_binding_rechecks_exact_owner_intent(tmp_path, monkeypatch):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    binding = SimpleNamespace(
        owner_operation_id="knowledgeos", target_host="server:7233",
        namespace="knowledgeos", task_queue="knowledgeos.work.v1",
        workflow_type=TEAM_WORKFLOW_TYPE, workflow_id_prefix="knowledgeos/", tls=False,
    )
    captured = {}

    async def fake_connect(selected, authorize, authorize_observation):
        captured["binding"] = selected
        captured["observe"] = authorize_observation
        return authorize

    clients = types.ModuleType("clients")
    clients.__path__ = []
    temporal = types.ModuleType("clients.temporal")
    temporal.BindingError = ValueError
    temporal.TemporalDispatch = SimpleNamespace
    temporal.connect = fake_connect
    monkeypatch.setitem(sys.modules, "clients", clients)
    monkeypatch.setitem(sys.modules, "clients.temporal", temporal)

    authorize = asyncio.run(connect_owner_temporal(exops, binding))
    assert captured["binding"] is binding
    payload = {
        "owner_operation_id": "knowledgeos", "work_run_id": run_id,
        "action_id": receipt["action_id"], "content_digest": receipt["content_digest"],
        "work_spec_digest": fixture.spec["spec_digest"],
    }
    dispatch = SimpleNamespace(workflow_id=f"knowledgeos/{run_id}",
                               action_id=receipt["action_id"], payload=payload)
    asyncio.run(authorize(dispatch))
    with pytest.raises(AdmissionError, match="uncertain"):
        asyncio.run(captured["observe"](dispatch))
    with pytest.raises(ValueError, match="WorkSpec"):
        asyncio.run(authorize(SimpleNamespace(
            workflow_id=dispatch.workflow_id, action_id=dispatch.action_id,
            payload={**payload, "work_spec_digest": "changed"})))
    with pytest.raises(ValueError, match="intent shape"):
        asyncio.run(authorize(SimpleNamespace(
            workflow_id="knowledgeos/another-run", action_id=dispatch.action_id,
            payload=payload)))
    officer_authorize = asyncio.run(connect_owner_temporal(
        exops, SimpleNamespace(**{**vars(binding), "workflow_type": WORKFLOW_TYPE})))
    with pytest.raises(ValueError, match="Workflow"):
        asyncio.run(officer_authorize(dispatch))
    with pytest.raises(AdmissionError, match="selected owner route"):
        asyncio.run(connect_owner_temporal(exops, SimpleNamespace(
            **{**vars(binding), "namespace": "other-operation"})))


def test_web_backend_binds_startup_actor_and_rechecks_live_authority(tmp_path):
    exops, fixture = make_exops(tmp_path)
    backend = ExOpsWebBackend(exops, OwnerActor("owner.user", "web"))

    @dataclass
    class Ingress:
        owner_operation_id: str
        method: str
        resource_path: str
        body: bytes

    payload = {"spec": fixture.spec, "run": fixture.run,
               "action_id": "action.synthetic.start",
               "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    request = Ingress("knowledgeos", "POST", "work/submit", json.dumps(payload).encode())
    assert backend(request)["receipt_state"] == "accepted"
    exops.authorize_actor = lambda _actor, _action: False
    with pytest.raises(AdmissionError):
        backend(request)

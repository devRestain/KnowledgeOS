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
from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
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


def test_worker_start_is_fenced_and_result_is_owner_committed(tmp_path):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    exops.record_dispatch(run_id, action_id=receipt["action_id"], outcome="accepted",
                          workflow_id=f"knowledgeos/{run_id}")
    started = exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                                   action_id=receipt["action_id"],
                                   content_digest=receipt["content_digest"],
                                   workflow_id=f"knowledgeos/{run_id}")
    assert started["state"] == "running" and started["revision"] == 2
    with pytest.raises(AdmissionError, match="reconciled"):
        exops.worker_started(run_id, worker_identity="knowledgeos.worker.one",
                             action_id=receipt["action_id"],
                             content_digest=receipt["content_digest"],
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


def test_native_client_binding_rechecks_exact_owner_intent(tmp_path, monkeypatch):
    exops, fixture = make_exops(tmp_path)
    receipt = admit(exops, fixture)
    run_id = fixture.run["work_run_id"]
    binding = SimpleNamespace(
        owner_operation_id="knowledgeos", target_host="server:7233",
        namespace="knowledgeos", task_queue="knowledgeos.work.v1",
        workflow_type="knowledgeos.work.v1", workflow_id_prefix="knowledgeos/", tls=False,
    )
    captured = {}

    async def fake_connect(selected, authorize):
        captured["binding"] = selected
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
    with pytest.raises(ValueError, match="WorkSpec"):
        asyncio.run(authorize(SimpleNamespace(
            workflow_id=dispatch.workflow_id, action_id=dispatch.action_id,
            payload={**payload, "work_spec_digest": "changed"})))
    with pytest.raises(ValueError, match="intent shape"):
        asyncio.run(authorize(SimpleNamespace(
            workflow_id="knowledgeos/another-run", action_id=dispatch.action_id,
            payload=payload)))
    with pytest.raises(AdmissionError, match="selected owner route"):
        asyncio.run(connect_owner_temporal(exops, SimpleNamespace(
            **{**vars(binding), "namespace": "other-operation"})))


def test_web_backend_requires_owner_session_and_csrf(tmp_path):
    exops, fixture = make_exops(tmp_path)
    backend = ExOpsWebBackend(
        exops, authenticate_login=lambda login: OwnerActor("owner.user", "web")
        if login == "owner@example.invalid" else None,
        validate_csrf=lambda actor, token: actor.actor_id == "owner.user" and token == "bound-token",
    )

    @dataclass
    class Ingress:
        owner_operation_id: str
        actor_login: str
        method: str
        resource_path: str
        body: bytes
        csrf_token: str | None

    payload = {"spec": fixture.spec, "run": fixture.run,
               "action_id": "action.synthetic.start",
               "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    request = Ingress("knowledgeos", "owner@example.invalid", "POST", "work/submit",
                      json.dumps(payload).encode(), "bound-token")
    assert backend(request)["receipt_state"] == "accepted"
    request.csrf_token = "wrong"
    with pytest.raises(AdmissionError, match="CSRF"):
        backend(request)

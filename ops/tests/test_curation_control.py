"""Real disposable owner journal control transitions; no provider or SDK execution."""

from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import test_curation_continuation as continuation
import test_curation_work as ordinary

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_continuation import curation_continuation_admission
from vaultops.application.curation_control import CurationRunControl, validate_control_release
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.work import record_digest

ACTOR = OwnerActor("owner.user", "web")
COMMAND = "command.curation.cancel"
NATIVE_RUN = "native-control-unit"
DENIED = AdmissionError


def checkpoint_ready(tmp_path, monkeypatch):
    submit = KnowledgeExOps.submit_work

    def controlled(self, *args, **kwargs):
        return submit(self, *args, **kwargs, control_checkpoint=True)

    with monkeypatch.context() as patch:
        patch.setattr(KnowledgeExOps, "submit_work", controlled)
        exops, executor, spec, run, path, _receipt = continuation.started(tmp_path, patch)
    asyncio.run(continuation.initial(executor, spec, run))
    controller = CurationRunControl(exops, executor)
    target = controller.open_checkpoint(run["work_run_id"], temporal_run_id=NATIVE_RUN)
    return controller, target, exops, executor, spec, run, path


def receive(controller, target, *, kind="cancel", command_id=COMMAND, actor=ACTOR, expires_at=None):
    return controller.request_control(
        target["work_run_id"], kind=kind, actor=actor, command_id=command_id, target=target,
        expires_at=expires_at or controller.exops.application.clock() + timedelta(hours=1),
    )


def apply(controller, target, command_id=COMMAND):
    return controller.apply_control(target["work_run_id"], command_id, temporal_run_id=NATIVE_RUN)


def saved(exops, target):
    return exops.application.journal.read()["intents"]["exops.work." + target["work_run_id"]]


def payload(target, *, kind="cancel", command_id=COMMAND):
    return {"command_id": command_id, "kind": kind, "target": target}


def mutate(exops, target, change):
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + target["work_run_id"]]
        change(record, writer.value)
        writer.commit(exops.application.clock())


def test_received_and_applied_are_distinct_exact_canonical_receipts(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    before = path.read_bytes()
    checkpoint = saved(exops, target)
    original = copy.deepcopy(checkpoint["curation_initial_assessment"])
    closed = checkpoint["curation_control_checkpoint"]["initial_run"]
    assert checkpoint["run"]["state"] == "awaiting_human"
    assert checkpoint["run"]["revision"] == closed["revision"] + 1
    assert checkpoint["run"]["control_revision"] == closed["control_revision"] + 1
    expiry = exops.application.clock() + timedelta(hours=1)
    receipt = receive(controller, target, expires_at=expiry)
    pending = saved(exops, target)
    assert receipt["application_state"] == "received"
    assert "execution_receipt_id" not in receipt and "applied_run_revision" not in receipt
    assert pending["run"]["revision"] == checkpoint["run"]["revision"] + 1
    assert pending["run"]["control_revision"] == target["target_control_revision"]
    assert pending["run"]["pending_action_ids"] == [COMMAND]
    assert pending["run"]["state"] == "awaiting_human"
    command = exops.application.journal.read()["intents"]["curation.control." + COMMAND]
    assert command["actor_ingress"] == "web" and pending["actor_ingress"] == "cli"
    assert command["directive"]["application_state"] == "received"
    assert controller.authorize_dispatch(payload(target)) == receipt
    journal_revision = exops.application.journal.read()["revision"]
    assert receive(controller, target, expires_at=expiry) == receipt
    assert controller.open_checkpoint(run["work_run_id"], temporal_run_id=NATIVE_RUN) == target
    assert exops.application.journal.read()["revision"] == journal_revision
    applied = apply(controller, target)
    assert applied["application_state"] == "applied"
    final = saved(exops, target)
    assert final["run"]["state"] == "cancelled"
    assert final["run"]["revision"] == pending["run"]["revision"] + 1
    assert final["run"]["control_revision"] == target["target_control_revision"] + 1
    assert final["run"]["pending_action_ids"] == final["run"]["unresolved_effect_ids"] == []
    assert final["curation_initial_assessment"] == original
    assert final["assessment"]["completion_state"] == "incomplete"
    assert final["assessment"]["criterion_outcomes"][0]["outcome"] == "not_run"
    assert final["run"]["graph_run_ids"] == closed["graph_run_ids"]
    assert final["run"]["profile_invocations"] == final["run"]["segments_started"] == 1
    snapshot = exops.application.journal.read()
    command = snapshot["intents"]["curation.control." + COMMAND]
    control_execution = next(value for value in snapshot["receipts"].values()
                             if value.get("receipt", {}).get("effect_id")
                                == command["execution_intent"]["effect_id"])
    assert control_execution["receipt"]["result_digest"] == record_digest(final["run"])
    assert snapshot["receipts"]["exops.work." + final["action_id"]]["receipt"]["result_digest"] == record_digest(final["run"])
    assert snapshot["intents"]["curation.control." + COMMAND]["directive"]["application_state"] == "applied"
    assert apply(controller, target) == applied
    assert controller.authorize_dispatch(payload(target)) == applied
    assert receive(controller, target, expires_at=expiry) == applied
    assert controller.read_command(run["work_run_id"], COMMAND, actor=ACTOR) == applied
    assert exops.application.journal.read() == snapshot
    assert path.read_bytes() == before
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


def test_concurrent_duplicate_application_commits_one_transition(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    before = exops.application.journal.read()
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: apply(controller, target), range(2)))
    assert receipts[0] == receipts[1]
    after = exops.application.journal.read()
    assert after["revision"] == before["revision"] + 1
    assert len([event for event in after["history"] if event["event"] == "curation_control_applied"]) == 1
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


@pytest.mark.parametrize("change", ["expiry", "target", "target_bool", "target_float", "ingress", "different_id", "foreign_actor"])
def test_conflicting_command_identity_and_one_pending_fence(tmp_path, monkeypatch, change):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    expiry = exops.application.clock() + timedelta(hours=1)
    receive(controller, target, expires_at=expiry)
    before = exops.application.journal.read()
    args = {"command_id": COMMAND, "actor": ACTOR, "expires_at": expiry}
    changed_target = copy.deepcopy(target)
    if change == "expiry":
        args["expires_at"] += timedelta(seconds=1)
    elif change == "target":
        changed_target["generation"] += 1
    elif change == "target_bool":
        changed_target["generation"] = True
    elif change == "target_float":
        changed_target["generation"] = float(target["generation"])
    elif change == "ingress":
        args["actor"] = OwnerActor(ACTOR.actor_id, "cli")
    elif change == "different_id":
        args["command_id"] = "command.other"
    else:
        exops.authorize_actor = lambda _actor, _action: True
        args["actor"] = OwnerActor("owner.foreign", "web")
    with pytest.raises(DENIED):
        receive(controller, changed_target, **args)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("change", [
    "source", "triage", "assessment", "evaluation", "remaining", "native_run", "generation",
    "fence", "revision", "active", "unknown_effect", "actual_effect", "model_request",
    "director", "source_authority", "team", "runner", "submit_revoked", "control_revoked",
    "stage", "decision", "public_receipt", "execution_intent",
])
def test_application_rechecks_exact_evidence_authority_and_quiescence(tmp_path, monkeypatch, change):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    if change == "source":
        path.write_text(path.read_text() + "\nChanged after receipt.\n")
    elif change == "director":
        exops.director_admitted = lambda _spec: False
    elif change == "source_authority":
        exops.director_method_source_admitted = lambda _spec, _source: False
    elif change == "team":
        exops.current_team_binding = lambda _team: None
    elif change == "runner":
        executor.runner.binding = {**executor.runner.binding, "adapter_digest": "sha256:" + "f" * 64}
    elif change in {"submit_revoked", "control_revoked"}:
        action = "submit_work" if change == "submit_revoked" else "control_work"
        exops.authorize_actor = lambda _actor, requested: requested != action
    elif change == "stage":
        method_file = exops.application.roots.control / "ops/config/work-methods.json"
        document = json.loads(method_file.read_text())
        method = next(item for item in document["methods"]
                      if item["method_id"] == _spec["method_contract_ref"]["method_id"])
        method["required_stages"][0]["tools"].remove("knowledge_read")
        method_file.write_text(json.dumps(document))
    else:
        def alter(record, snapshot):
            if change == "triage":
                record["curation_capture_evidence"]["reads"][0]["data"]["text"] += " forged"
            elif change == "assessment":
                record["curation_initial_assessment"]["assessment"]["completion_state"] = "complete"
            elif change == "evaluation":
                record["evaluations"][0]["evaluator_id"] = record["spec"]["manager_executor_id"]
            elif change == "remaining":
                record["remaining_work"]["gaps"][0]["summary"] = "Changed"
            elif change == "native_run":
                record["temporal_run_id"] = "native-other"
            elif change in {"generation", "revision"}:
                record["run"][change] += 1
            elif change == "fence":
                record["run"]["fencing_token"] = "fence.other"
            elif change == "active":
                record["run"]["active_graph_run_id"] = record["run"]["graph_run_ids"][0]
            elif change == "unknown_effect":
                record["run"]["unresolved_effect_ids"] = ["effect.unknown"]
            elif change == "actual_effect":
                snapshot["intents"]["effect.unexpected"] = {
                    "state": "unknown", "work_binding": {"work_run_id": run["work_run_id"]},
                }
            elif change == "model_request":
                record["curation_model_request"] = {"request_digest": "sha256:" + "f" * 64}
            else:
                command = snapshot["intents"]["curation.control." + COMMAND]
                if change == "decision":
                    snapshot["decisions"][command["request"]["request_id"]]["receipt"]["chosen_action"] = "resume"
                elif change == "public_receipt":
                    command["receipt"]["expires_at"] = "2099-01-01T00:00:00+00:00"
                else:
                    command["execution_intent"]["content_digest"] = "sha256:" + "f" * 64
        mutate(exops, target, alter)
    known = copy.deepcopy(saved(exops, target))
    with pytest.raises(DENIED):
        apply(controller, target)
    assert saved(exops, target) == known
    assert saved(exops, target)["run"]["state"] != "cancelled"
    assert saved(exops, target)["run"]["pending_action_ids"] == [COMMAND]
    assert executor.runner.requests == [] and continuation.effects(exops, run) == ([] if change != "actual_effect" else [
        {"state": "unknown", "work_binding": {"work_run_id": run["work_run_id"]}},
    ])
    assert controller.read_command(run["work_run_id"], COMMAND, actor=ACTOR)["application_state"] == "rejected"


def test_expiry_is_durable_without_clearing_pending_uncertainty(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    now = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: now)
    receive(controller, target, expires_at=now + timedelta(seconds=5))
    before = copy.deepcopy(saved(exops, target))
    exops.application = replace(exops.application, clock=lambda: now + timedelta(seconds=5))
    with pytest.raises(DENIED):
        controller.authorize_dispatch(payload(target))
    with pytest.raises(DENIED):
        apply(controller, target)
    assert saved(exops, target) == before
    receipt = controller.read_command(target["work_run_id"], COMMAND, actor=ACTOR)
    assert receipt["application_state"] == "expired"
    assert receive(controller, target, expires_at=now + timedelta(seconds=5)) == receipt
    with pytest.raises(DENIED):
        receive(controller, target, command_id="command.after.expiry")
    assert saved(exops, target) == before


def test_native_mismatch_and_wire_shape_cannot_select_actor(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        controller.apply_control(target["work_run_id"], COMMAND, temporal_run_id="native-other")
    with pytest.raises(DENIED):
        controller.open_checkpoint(target["work_run_id"], temporal_run_id="native-other")
    for bad in ({**payload(target), "actor": ACTOR.actor_id}, {"command_id": COMMAND},
                {"command_id": "command.foreign", "target": target},
                {"command_id": COMMAND, "target": {**target, "generation": True}}):
        with pytest.raises(DENIED):
            controller.authorize_dispatch(bad)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("state", ["received", "applied"])
def test_read_authorization_and_applied_replay_revocation(tmp_path, monkeypatch, state):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    if state == "applied":
        apply(controller, target)
    before = exops.application.journal.read()
    exops.authorize_actor = lambda _actor, _action: True
    for reader in (lambda: controller.target(target["work_run_id"], actor=OwnerActor("owner.other", "web")),
                   lambda: controller.read_command(target["work_run_id"], COMMAND, actor=OwnerActor("owner.other", "web"))):
        with pytest.raises(DENIED):
            reader()
    exops.authorize_actor = lambda actor, action: not (action == "control_work" and actor.ingress == "web")
    with pytest.raises(DENIED):
        controller.authorize_dispatch(payload(target))
    with pytest.raises(DENIED):
        apply(controller, target)
    assert saved(exops, target) == before["intents"]["exops.work." + target["work_run_id"]]
    if state == "applied":
        assert exops.application.journal.read() == before
    exops.authorize_actor = lambda _actor, _action: False
    with pytest.raises(DENIED):
        controller.read_command(target["work_run_id"], COMMAND, actor=ACTOR)
    with pytest.raises(DENIED):
        controller.target(target["work_run_id"], actor=ACTOR)


def test_initial_quiescence_and_control_admission_are_not_optional(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        exops, executor, spec, run, _path, _receipt = continuation.started(tmp_path, patch)
    asyncio.run(continuation.initial(executor, spec, run))
    controller = CurationRunControl(exops, executor)
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        controller.open_checkpoint(run["work_run_id"], temporal_run_id=NATIVE_RUN)
    assert exops.application.journal.read() == before


def test_controlled_manager_cannot_admit_successor_before_receipt(tmp_path, monkeypatch):
    submit = KnowledgeExOps.submit_work
    with monkeypatch.context() as patch:
        def controlled(self, *args, **kwargs):
            return submit(self, *args, **kwargs, control_checkpoint=True)
        patch.setattr(KnowledgeExOps, "submit_work", controlled)
        exops, executor, spec, run, _path, _receipt = continuation.started(tmp_path, patch)
    asyncio.run(continuation.initial(executor, spec, run))
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        asyncio.run(executor.manager_successor(run["work_run_id"]))
    assert exops.application.journal.read() == before
    assert executor.runner.requests == []


@pytest.mark.parametrize("controlled", [False, True])
def test_submission_control_flag_cannot_replay_opposite_admission(tmp_path, monkeypatch, controlled):
    if controlled:
        controller, target, exops, executor, spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
        del controller
        work_run_id = target["work_run_id"]
    else:
        with monkeypatch.context() as patch:
            exops, executor, spec, run, _path, _receipt = continuation.started(tmp_path, patch)
        work_run_id = run["work_run_id"]
    record = exops.application.journal.read()["intents"]["exops.work." + work_run_id]
    _, admitted = curation_continuation_admission(
        exops.application, exops.teams[spec["team_id"]], record["method_source_reference"],
        work_id=spec["work_id"], work_run_id=work_run_id, admission_receipt_id=spec["admission_receipt_id"],
        runner_binding=executor.runner_binding,
    )
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        exops.submit_work(spec, admitted, actor=OwnerActor("owner.user", "cli"),
                          action_id=record["action_id"], expires_at=datetime.fromisoformat(record["expires_at"]),
                          method_source_reference=record["method_source_reference"], control_checkpoint=not controlled)
    assert exops.application.journal.read() == before


def test_ordinary_method_cannot_borrow_control_flag(tmp_path):
    exops, _executor, _spec, _run, _path, _receipt = ordinary.started(tmp_path)
    record = next(value for name, value in exops.application.journal.read()["intents"].items()
                  if name.startswith("exops.work."))
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        exops.submit_work(record["spec"], record["run"], actor=ACTOR,
                          action_id=record["action_id"], expires_at=datetime.fromisoformat(record["expires_at"]),
                          method_source_reference=record["method_source_reference"], control_checkpoint=True)
    assert exops.application.journal.read() == before


def test_applied_reconciliation_survives_expiry_and_transient_receipt_pruning(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    now = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: now)
    expiry = now + timedelta(seconds=5)
    receive(controller, target, expires_at=expiry)
    receipt = apply(controller, target)
    exops.application = replace(exops.application, clock=lambda: now + timedelta(days=2))
    with exops.application.journal.writer() as writer:
        writer.commit(exops.application.clock())
    before = exops.application.journal.read()
    assert apply(controller, target) == receipt
    assert controller.authorize_dispatch(payload(target)) == receipt
    assert receive(controller, target, expires_at=expiry) == receipt
    assert exops.application.journal.read() == before


def test_original_submission_and_actual_command_ingress_are_both_reauthorized(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    calls = []

    def authorize(actor, action):
        calls.append((actor.ingress, action))
        return not (actor.ingress == "cli" and action == "submit_work")

    exops.authorize_actor = authorize
    before = copy.deepcopy(saved(exops, target))
    with pytest.raises(DENIED):
        apply(controller, target)
    assert ("web", "control_work") in calls
    assert ("cli", "submit_work") in calls
    assert saved(exops, target) == before


@pytest.mark.parametrize("change", ["expired", "unbounded", "target_revision", "checkpoint", "source"])
def test_receipt_admission_denies_stale_targets_and_expiry_without_pending_write(tmp_path, monkeypatch, change):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    expires = exops.application.clock() + timedelta(hours=1)
    changed = copy.deepcopy(target)
    if change == "expired":
        expires = exops.application.clock() - timedelta(seconds=1)
    elif change == "unbounded":
        expires += timedelta(days=2)
    elif change == "target_revision":
        changed["target_control_revision"] += 1
    elif change == "checkpoint":
        changed["checkpoint_digest"] = "sha256:" + "f" * 64
    else:
        path.write_text(path.read_text() + "\nSource changed before receipt.\n")
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        receive(controller, changed, expires_at=expires)
    assert exops.application.journal.read() == before
    assert saved(exops, target)["run"]["pending_action_ids"] == []
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


def test_checkpoint_requires_independent_closed_assessment(tmp_path, monkeypatch):
    submit = KnowledgeExOps.submit_work
    with monkeypatch.context() as patch:
        def controlled(self, *args, **kwargs):
            return submit(self, *args, **kwargs, control_checkpoint=True)
        patch.setattr(KnowledgeExOps, "submit_work", controlled)
        exops, executor, spec, run, _path, _receipt = continuation.started(tmp_path, patch)
    controller = CurationRunControl(exops, executor)
    for prepare in (lambda: None, lambda: asyncio.run(executor.manager(spec, run)),
                    lambda: asyncio.run(executor.capture_evidence(run["work_run_id"]))):
        prepare()
        before = exops.application.journal.read()
        with pytest.raises(DENIED):
            controller.open_checkpoint(run["work_run_id"], temporal_run_id=NATIVE_RUN)
        assert exops.application.journal.read() == before
    asyncio.run(executor.assess_initial(run["work_run_id"]))
    target = controller.open_checkpoint(run["work_run_id"], temporal_run_id=NATIVE_RUN)
    assert target["work_run_id"] == run["work_run_id"]


def test_read_only_owner_never_writes_a_control_rejection(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    before = exops.application.journal.read()
    exops.read_only = True
    with pytest.raises(AdmissionError) as denied:
        apply(controller, target)
    assert denied.value.code == "READ_ONLY"
    assert exops.application.journal.read() == before


def test_another_work_action_cannot_replace_an_applied_control_receipt(tmp_path, monkeypatch):
    controller, target, exops, _executor, spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    applied = apply(controller, target)
    source = saved(exops, target)["method_source_reference"]
    other_spec, other_run = curation_continuation_admission(
        exops.application, exops.teams[spec["team_id"]], source,
        work_id="work.control.other", work_run_id="workrun.control.other",
        admission_receipt_id="admission.control.other", runner_binding=controller.executor.runner_binding,
    )
    exops.director_admitted = lambda candidate: dict(candidate) in (spec, other_spec)
    exops.director_method_source_admitted = lambda candidate, selected: (
        exops.director_admitted(candidate) and dict(selected) == source)
    action_id = "curation.control." + record_digest({"command_id": COMMAND})[7:39]
    receipt = exops.submit_work(
        other_spec, other_run, actor=ACTOR, action_id=action_id,
        expires_at=exops.application.clock() + timedelta(hours=1),
        method_source_reference=source,
    )
    exops.worker_started(
        other_run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=action_id, content_digest=receipt["content_digest"],
        work_spec_digest=other_spec["spec_digest"], workflow_id="knowledgeos/" + other_run["work_run_id"],
    )
    exops.worker_unknown(other_run["work_run_id"], worker_identity=exops.worker_identity, action_id=action_id)
    assert controller.authorize_dispatch(payload(target)) == applied
    assert saved(exops, target)["run"]["state"] == "cancelled"


PAUSE = "command.curation.pause"
RESUME = "command.curation.resume"


def pause(controller, target):
    receive(controller, target, kind="pause", command_id=PAUSE)
    receipt = apply(controller, target, PAUSE)
    return receipt["next_target"], receipt


def resume(controller, target):
    receive(controller, target, kind="resume", command_id=RESUME)
    return apply(controller, target, RESUME)


def finish_successor(exops, executor, target):
    work_run_id = target["work_run_id"]
    asyncio.run(executor.manager_successor(work_run_id))
    asyncio.run(executor.normalization_proposal(work_run_id))
    result = asyncio.run(executor.assess(work_run_id))
    record = saved(exops, target)
    exops.worker_finished(
        work_run_id, worker_identity=exops.worker_identity, action_id=record["action_id"],
        next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id,
    )
    return result


def test_pause_retains_exact_initial_evidence_and_no_work_completion(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    initial = copy.deepcopy(saved(exops, target))
    source = path.read_bytes()
    next_target, receipt = pause(controller, target)
    paused = saved(exops, target)
    assert receipt["kind"] == "pause" and receipt["application_state"] == "applied"
    assert next_target != target
    assert next_target["target_control_revision"] == target["target_control_revision"] + 1
    assert next_target["checkpoint_digest"] != target["checkpoint_digest"]
    assert controller.target(run["work_run_id"], actor=ACTOR) == next_target
    assert paused["run"]["state"] == "paused" and paused["application_state"] == "started"
    assert paused["receipt"] == initial["receipt"]
    assert paused["curation_initial_assessment"] == initial["curation_initial_assessment"]
    assert paused["curation_capture_evidence"] == initial["curation_capture_evidence"]
    assert all(paused["run"][key] == initial["run"][key]
               for key in ("graph_run_ids", "segments_started", "profile_invocations", "repeated_gap_count"))
    assert "exops.work." + paused["action_id"] not in exops.application.journal.read()["receipts"]
    snapshot = exops.application.journal.read()
    for action in (executor.manager_successor, executor.normalization_proposal):
        with pytest.raises(DENIED):
            asyncio.run(action(run["work_run_id"]))
    with pytest.raises(DENIED):
        controller.authorize_successor(run["work_run_id"], temporal_run_id=NATIVE_RUN)
    assert exops.application.journal.read() == snapshot
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []
    assert path.read_bytes() == source


@pytest.mark.parametrize("phase,kind", [
    ("initial", "resume"), ("paused", "pause"), ("resumed", "pause"), ("resumed", "resume"),
])
def test_only_one_ordered_pause_resume_pair_is_admitted(tmp_path, monkeypatch, phase, kind):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    if phase != "initial":
        target, _ = pause(controller, target)
    if phase == "resumed":
        resume(controller, target)
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        receive(controller, target, kind=kind, command_id="command.invalid.order")
    assert exops.application.journal.read() == before


def test_resume_requires_fresh_paused_target_and_exact_kind(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    next_target, _ = pause(controller, target)
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        receive(controller, target, kind="resume", command_id=RESUME)
    with pytest.raises(DENIED):
        controller.authorize_dispatch(payload(target, kind="resume", command_id=PAUSE))
    assert exops.application.journal.read() == before
    receipt = receive(controller, next_target, kind="resume", command_id=RESUME)
    assert controller.authorize_dispatch(payload(next_target, kind="resume", command_id=RESUME)) == receipt


@pytest.mark.parametrize("kind", ["pause", "resume"])
def test_concurrent_control_application_and_duplicate_receipt_are_read_only(tmp_path, monkeypatch, kind):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    if kind == "resume":
        target, _ = pause(controller, target)
    command_id = PAUSE if kind == "pause" else RESUME
    expires = exops.application.clock() + timedelta(hours=1)
    receive(controller, target, kind=kind, command_id=command_id, expires_at=expires)
    before = exops.application.journal.read()
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: apply(controller, target, command_id), range(2)))
    assert receipts[0] == receipts[1]
    snapshot = exops.application.journal.read()
    assert snapshot["revision"] == before["revision"] + 1
    assert receive(controller, target, kind=kind, command_id=command_id, expires_at=expires) == receipts[0]
    assert controller.authorize_dispatch(payload(target, kind=kind, command_id=command_id)) == receipts[0]
    assert exops.application.journal.read() == snapshot
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


def test_resume_runs_real_fixture_successor_and_reconciles_historical_receipts(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    original = copy.deepcopy(saved(exops, target)["curation_initial_assessment"])
    source = path.read_bytes()
    paused_target, paused_receipt = pause(controller, target)
    resumed_receipt = resume(controller, paused_target)
    released = saved(exops, target)
    assert released["run"]["state"] == "running" and released["application_state"] == "started"
    assert released["run"]["pending_action_ids"] == []
    assert released["run"]["segments_started"] == released["run"]["profile_invocations"] == 1
    assert released["run"]["repeated_gap_count"] == 1
    assert "exops.work." + released["action_id"] not in exops.application.journal.read()["receipts"]
    assert controller.authorize_successor(run["work_run_id"], temporal_run_id=NATIVE_RUN) == resumed_receipt
    result = finish_successor(exops, executor, target)
    final = saved(exops, target)
    assert final["run"]["state"] == "completed" and final["application_state"] == "recorded"
    assert final["curation_initial_assessment"] == original
    assert final["run"]["segments_started"] == final["run"]["profile_invocations"] == 2
    assert final["run"]["repeated_gap_count"] == 1
    assert result.assessment["criterion_outcomes"][0]["outcome"] == "satisfied"
    assert result.evaluations[0] == original["evaluations"][0]
    assert len(executor.runner.requests) == len(continuation.effects(exops, run)) == 1
    assert path.read_bytes() == source
    snapshot = exops.application.journal.read()
    for old_target, command_id, receipt, kind in (
        (target, PAUSE, paused_receipt, "pause"), (paused_target, RESUME, resumed_receipt, "resume"),
    ):
        assert apply(controller, old_target, command_id) == receipt
        assert controller.authorize_dispatch(payload(old_target, kind=kind, command_id=command_id)) == receipt
        assert controller.read_command(run["work_run_id"], command_id, actor=ACTOR) == receipt
    assert exops.application.journal.read() == snapshot
    now = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: now + timedelta(days=2))
    with exops.application.journal.writer() as writer:
        writer.commit(exops.application.clock())
    snapshot = exops.application.journal.read()
    assert apply(controller, target, PAUSE) == paused_receipt
    assert apply(controller, paused_target, RESUME) == resumed_receipt
    assert exops.application.journal.read() == snapshot


def test_cancel_while_paused_finalizes_work_without_successor(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    paused_target, paused_receipt = pause(controller, target)
    receive(controller, paused_target)
    cancelled = apply(controller, paused_target)
    record = saved(exops, target)
    assert cancelled["kind"] == "cancel" and record["run"]["state"] == "cancelled"
    assert record["application_state"] == "recorded"
    assert record["run"]["segments_started"] == record["run"]["profile_invocations"] == 1
    assert exops.application.journal.read()["receipts"]["exops.work." + record["action_id"]]["receipt"]["result_digest"] == record_digest(record["run"])
    snapshot = exops.application.journal.read()
    assert apply(controller, target, PAUSE) == paused_receipt
    with pytest.raises(DENIED):
        asyncio.run(executor.manager_successor(run["work_run_id"]))
    assert exops.application.journal.read() == snapshot
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


@pytest.mark.parametrize("change", [
    "source", "evidence", "initial_run", "profile", "explicit_assignment", "budget",
    "actor", "director", "source_authority", "team", "runner", "native_run",
])
def test_resume_rechecks_retained_evidence_and_live_authority(tmp_path, monkeypatch, change):
    controller, target, exops, executor, _spec, run, path = checkpoint_ready(tmp_path, monkeypatch)
    paused_target, _ = pause(controller, target)
    receive(controller, paused_target, kind="resume", command_id=RESUME)
    if change == "source":
        path.write_text(path.read_text() + "\nChanged while paused.\n")
    elif change == "actor":
        exops.authorize_actor = lambda _actor, action: action != "control_work"
    elif change == "director":
        exops.director_admitted = lambda _spec: False
    elif change == "source_authority":
        exops.director_method_source_admitted = lambda _spec, _source: False
    elif change == "team":
        exops.current_team_binding = lambda _team: None
    elif change == "runner":
        executor.runner.binding = {**executor.runner.binding, "adapter_digest": "sha256:" + "f" * 64}
    else:
        def alter(record, _snapshot):
            if change == "evidence":
                record["curation_capture_evidence"]["reads"][0]["data"]["text"] += " forged"
            elif change == "initial_run":
                record["curation_initial_assessment"]["run"]["control_revision"] += 1
            elif change == "profile":
                record["spec"]["allowed_profiles"][1]["role_version"] = "999.0.0"
            elif change == "explicit_assignment":
                record["spec"]["explicit_assignments"] = [{"executor_id": "forged.assignment"}]
            elif change == "budget":
                record["run"]["profile_invocations"] = 0
            else:
                record["temporal_run_id"] = "native.other"
        mutate(exops, target, alter)
    before = copy.deepcopy(saved(exops, target))
    with pytest.raises(DENIED):
        apply(controller, paused_target, RESUME)
    assert saved(exops, target) == before
    assert saved(exops, target)["run"]["pending_action_ids"] == [RESUME]
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


def test_expired_resume_preserves_pause_fence_and_historical_pause_receipt(tmp_path, monkeypatch):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    paused_target, paused_receipt = pause(controller, target)
    now = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: now)
    receive(controller, paused_target, kind="resume", command_id=RESUME, expires_at=now + timedelta(seconds=5))
    exops.application = replace(exops.application, clock=lambda: now + timedelta(seconds=5))
    with pytest.raises(DENIED):
        apply(controller, paused_target, RESUME)
    before = exops.application.journal.read()
    assert saved(exops, target)["run"]["state"] == "paused"
    assert saved(exops, target)["run"]["pending_action_ids"] == [RESUME]
    assert apply(controller, target, PAUSE) == paused_receipt
    with pytest.raises(DENIED):
        controller.authorize_successor(run["work_run_id"], temporal_run_id=NATIVE_RUN)
    with pytest.raises(DENIED):
        asyncio.run(executor.manager_successor(run["work_run_id"]))
    assert exops.application.journal.read() == before
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []


@pytest.mark.parametrize("change", [
    "release_flag", "cleared_fence", "resume_snapshot", "receipt", "decision", "initial_evidence",
])
def test_successor_gate_rejects_forged_release_and_tampered_proof(tmp_path, monkeypatch, change):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    paused_target, _ = pause(controller, target)
    if change not in {"release_flag", "cleared_fence"}:
        resume(controller, paused_target)
    elif change == "cleared_fence":
        receive(controller, paused_target, kind="resume", command_id=RESUME)
    def alter(record, snapshot):
        if change in {"release_flag", "cleared_fence"}:
            record["run"]["state"] = "running"
            record["run"]["pending_action_ids"] = []
            record["control_released"] = True
        elif change == "resume_snapshot":
            snapshot["intents"]["curation.control." + RESUME]["applied_run"]["profile_invocations"] = 0
        elif change == "receipt":
            command = snapshot["intents"]["curation.control." + RESUME]
            command["receipt"]["applied_run_revision"] += 1
        elif change == "decision":
            command = snapshot["intents"]["curation.control." + RESUME]
            snapshot["decisions"][command["request"]["request_id"]]["pending_apply"] = True
        else:
            record["curation_initial_assessment"]["evaluations"][0]["evaluator_id"] = "forged.evaluator"
    mutate(exops, target, alter)
    before = exops.application.journal.read()
    with pytest.raises(DENIED):
        validate_control_release(exops.application, before, saved(exops, target))
    with pytest.raises(DENIED):
        asyncio.run(executor.manager_successor(run["work_run_id"]))
    assert exops.application.journal.read() == before
    assert executor.runner.requests == [] and continuation.effects(exops, run) == []

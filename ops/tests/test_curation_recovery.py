"""Explicit recovery reuses committed real Owner effects, never a provider retry."""

from __future__ import annotations

import asyncio
import copy

import pytest
from test_curation_work import produce, started

from vaultops.adapters.core import AdmissionError, digest
from vaultops.application.curation_work import CurationTeamExecutor
from vaultops.application.exops import OwnerActor
from vaultops.application.work import record_digest

OWNER = OwnerActor("owner.user", "cli")
RECOVERY = "recovery.curation.unit"
NATIVE_RUN = "native.run.curation.unit"
ACTIVITY = "native.activity.normalization"


class CountingRecoveryExecutor(CurationTeamExecutor):
    assessments = 0

    def _assess(self, record):
        self.assessments += 1
        return super()._assess(record)


def failed_effect(tmp_path, *, lose_model_response=False, prior_unknown=False, failure_kind=None):
    def lost_response(_response):
        raise RuntimeError("Model transport lost its response after the Owner effect")

    exops, producer, spec, run, source, receipt = started(
        tmp_path,
        alter=lost_response if lose_model_response else None,
    )
    if lose_model_response:
        with pytest.raises(RuntimeError, match="transport lost"):
            asyncio.run(produce(producer, spec, run))
    else:
        asyncio.run(produce(producer, spec, run))
    if prior_unknown:
        exops.worker_unknown(
            run["work_run_id"],
            worker_identity=exops.worker_identity,
            action_id=receipt["action_id"],
        )
    producer.mark_unknown(
        run["work_run_id"],
        action_id=receipt["action_id"],
        temporal_run_id=NATIVE_RUN,
        activity_id=ACTIVITY,
        failure_kind=failure_kind,
    )
    app = exops.application
    record = app.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    target = {
        "owner_operation_id": "knowledgeos",
        "work_run_id": run["work_run_id"],
        "action_id": receipt["action_id"],
        "content_digest": receipt["content_digest"],
        "work_spec_digest": spec["spec_digest"],
        "temporal_run_id": NATIVE_RUN,
        "activity_id": ACTIVITY,
        "run_revision": record["run"]["revision"],
        "generation": record["run"]["generation"],
        "fencing_token": record["run"]["fencing_token"],
    }
    proof = {"quiesced": True}

    def confirmed(value):
        return proof["quiesced"] and value == target

    executor = CountingRecoveryExecutor(exops, runner=producer.runner, recovery_quiesced=confirmed)
    return exops, executor, spec, run, source, target, proof


def admit(executor, run, target, *, recovery_id=RECOVERY, actor=OWNER):
    return executor.request_recovery(
        run["work_run_id"],
        actor=actor,
        recovery_id=recovery_id,
        target=target,
    )


def reconcile(
    executor, run, *, recovery_id=RECOVERY, temporal_run_id=NATIVE_RUN, activity_id=ACTIVITY
):
    return asyncio.run(
        executor.reconcile(
            run["work_run_id"],
            recovery_id,
            temporal_run_id=temporal_run_id,
            activity_id=activity_id,
        )
    )


def work_record(exops, run):
    return exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]


def test_committed_effect_response_loss_reconciles_once_with_exact_applied_replay(tmp_path):
    exops, executor, _spec, run, source, target, _proof = failed_effect(tmp_path)
    before = source.read_bytes()
    payload = admit(executor, run, target)
    assert work_record(exops, run)["application_state"] == "unknown"
    intent = exops.application.journal.read()["intents"]["curation.recovery." + RECOVERY]
    assert intent["state"] == "received" and "applied_receipt" not in intent
    assert executor.assessments == 0
    finished = reconcile(executor, run)
    snapshot = exops.application.journal.read()
    record = work_record(exops, run)
    recovery = snapshot["intents"]["curation.recovery." + RECOVERY]
    assert finished == record["receipt"] == recovery["applied_receipt"]
    assert finished["application_state"] == "recorded"
    assert record["run"]["state"] == "completed"
    assert recovery["state"] == "applied"
    assert recovery["completed_run_digest"] == record_digest(record["run"])
    assert len(record["evaluations"]) == 1 and executor.assessments == 1
    assert len(executor.runner.requests) == 1
    assert len([e for e in snapshot["intents"].values() if e.get("work_binding")]) == 1
    assert len(record["submitted_artifacts"]) == 1 and source.read_bytes() == before
    assert reconcile(executor, run) == finished
    assert admit(executor, run, target) == payload
    assert executor.assessments == 1 and len(executor.runner.requests) == 1
    assert exops.application.journal.read()["revision"] == snapshot["revision"]


@pytest.mark.parametrize(
    "stage", ["manager", "capture_evidence", "normalization_proposal", "assess"]
)
def test_unknown_does_not_reopen_original_producer_or_ordinary_assessment(tmp_path, stage):
    exops, executor, spec, run, _source, _target, _proof = failed_effect(tmp_path)
    arguments = (spec, run) if stage == "manager" else (run["work_run_id"],)
    with pytest.raises(AdmissionError):
        asyncio.run(getattr(executor, stage)(*arguments))
    assert len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


@pytest.mark.parametrize("mode", ["absent", "alive", "revoked"])
def test_quiescence_is_required_at_admission_and_again_at_application(tmp_path, mode):
    exops, executor, _spec, run, _source, target, proof = failed_effect(tmp_path)
    if mode == "revoked":
        admit(executor, run, target)
    if mode == "absent":
        executor.recovery_quiesced = None
    else:
        proof["quiesced"] = False
    with pytest.raises(AdmissionError, match="stopped producer"):
        if mode == "revoked":
            reconcile(executor, run)
        else:
            admit(executor, run, target)
    assert executor.assessments == 0 and len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


def test_missing_model_response_never_recovers_from_pending_artifact_alone(tmp_path):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(
        tmp_path,
        lose_model_response=True,
    )
    snapshot = exops.application.journal.read()
    effect = next(e for e in snapshot["intents"].values() if e.get("work_binding"))
    assert effect["state"] == "completed"
    assert snapshot["receipts"][effect["effect_id"]]["execution"]["outcome"] == "succeeded"
    assert "curation_model_execution" not in work_record(exops, run)
    with pytest.raises(AdmissionError, match="complete recorded native response"):
        admit(executor, run, target)
    assert executor.assessments == 0 and len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


@pytest.mark.parametrize("changed", ["receipt", "source", "runtime", "fence", "model", "gate"])
def test_admitted_recovery_rejects_corrupt_or_changed_pinned_evidence(tmp_path, changed):
    exops, executor, _spec, run, source, target, _proof = failed_effect(tmp_path)
    admit(executor, run, target)
    app = exops.application
    if changed == "source":
        source.write_bytes(source.read_bytes() + b"\nChanged after owner recovery admission.\n")
    elif changed == "runtime":
        executor.runner.binding = {**executor.runner.binding, "adapter_digest": digest(b"changed")}
    else:
        with app.journal.writer() as writer:
            record = writer.value["intents"]["exops.work." + run["work_run_id"]]
            effect = next(e for e in writer.value["intents"].values() if e.get("work_binding"))
            if changed == "receipt":
                writer.value["receipts"][effect["effect_id"]]["execution"]["result_digest"] = (
                    digest(b"bad")
                )
            elif changed == "fence":
                record["run"]["fencing_token"] += ".stale"
            elif changed == "model":
                evidence = record["curation_model_execution"]
                evidence["response"]["final_text"] = "Different recorded response"
                evidence["evidence_digest"] = record_digest(
                    {
                        "request": evidence["request"],
                        "response": evidence["response"],
                    }
                )
            else:
                writer.value["human_requests"][effect["human_request_id"]]["lifecycle"] = "resolved"
            writer.commit(app.clock())
    with pytest.raises(AdmissionError):
        reconcile(executor, run)
    assert len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


@pytest.mark.parametrize(
    "field",
    [
        "owner_operation_id",
        "work_run_id",
        "action_id",
        "content_digest",
        "work_spec_digest",
        "temporal_run_id",
        "activity_id",
        "run_revision",
        "generation",
        "fencing_token",
    ],
)
def test_stale_or_foreign_recovery_target_cannot_be_admitted(tmp_path, field):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    stale = copy.deepcopy(target)
    stale[field] = stale[field] + 1 if isinstance(stale[field], int) else stale[field] + ".foreign"
    with pytest.raises(AdmissionError):
        admit(executor, run, stale)
    assert len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


def test_authorized_foreign_actor_and_conflicting_duplicate_are_denied(tmp_path):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    exops.authorize_actor = lambda actor, action: True
    with pytest.raises(AdmissionError, match="original admitted actor"):
        admit(executor, run, target, actor=OwnerActor("other.owner", "web"))
    payload = admit(executor, run, target)
    assert admit(executor, run, target) == payload
    with pytest.raises(AdmissionError):
        admit(executor, run, {**target, "activity_id": "foreign.activity"})
    with pytest.raises(AdmissionError, match="different recovery"):
        admit(executor, run, target, recovery_id="recovery.second")
    assert executor.assessments == 0 and len(executor.runner.requests) == 1


@pytest.mark.parametrize("native", ["run", "activity"])
def test_reconcile_requires_exact_native_failed_activity(tmp_path, native):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    admit(executor, run, target)
    kwargs = (
        {"temporal_run_id": "foreign.run"}
        if native == "run"
        else {"activity_id": "foreign.activity"}
    )
    with pytest.raises(AdmissionError, match="native failure"):
        reconcile(executor, run, **kwargs)
    assert executor.assessments == 0 and len(executor.runner.requests) == 1
    assert work_record(exops, run)["application_state"] == "unknown"


def test_native_failure_marker_replay_cannot_retarget_unknown_work(tmp_path):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(
        tmp_path, prior_unknown=True
    )
    executor.mark_unknown(
        run["work_run_id"],
        action_id=target["action_id"],
        temporal_run_id=NATIVE_RUN,
        activity_id=ACTIVITY,
    )
    with pytest.raises(AdmissionError, match="identity changed"):
        executor.mark_unknown(
            run["work_run_id"],
            action_id=target["action_id"],
            temporal_run_id=NATIVE_RUN,
            activity_id="foreign.activity",
        )
    assert work_record(exops, run)["application_state"] == "unknown"
    assert len(executor.runner.requests) == 1


def test_effect_receipt_change_at_result_commit_keeps_application_unknown(tmp_path):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    admit(executor, run, target)
    finish = exops.worker_finished

    def changed_before_commit(*args, **kwargs):
        app = exops.application
        with app.journal.writer() as writer:
            effect = next(e for e in writer.value["intents"].values() if e.get("work_binding"))
            writer.value["receipts"][effect["effect_id"]]["execution"]["result_digest"] = digest(
                b"changed"
            )
            writer.commit(app.clock())
        return finish(*args, **kwargs)

    exops.worker_finished = changed_before_commit
    with pytest.raises(AdmissionError):
        reconcile(executor, run)
    snapshot = exops.application.journal.read()
    assert work_record(exops, run)["application_state"] == "unknown"
    assert snapshot["intents"]["curation.recovery." + RECOVERY]["state"] == "applying"
    assert executor.assessments == 1 and len(executor.runner.requests) == 1


@pytest.mark.parametrize("window", ["before_assessment", "before_commit"])
def test_recovery_rechecks_original_ingress_authority_before_application(tmp_path, window):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    admit(executor, run, target, actor=OwnerActor("owner.user", "web"))
    finish = exops.worker_finished

    def revoke():
        exops.authorize_actor = lambda actor, action: (
            actor.actor_id == "owner.user"
            and not (actor.ingress == "web" and action == "recover_work")
        )

    if window == "before_commit":
        def revoked_before_commit(*args, **kwargs):
            revoke()
            return finish(*args, **kwargs)

        exops.worker_finished = revoked_before_commit
    else:
        revoke()
    with pytest.raises(AdmissionError, match="denies this actor"):
        reconcile(executor, run)
    assert work_record(exops, run)["application_state"] == "unknown"
    assert executor.assessments == (1 if window == "before_commit" else 0)
    assert len(executor.runner.requests) == 1

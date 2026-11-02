"""Durable owner/native-shaped lifecycle records through operation recover."""

from __future__ import annotations

import asyncio
import copy

import pytest
from test_curation_control import apply, checkpoint_ready, receive
from test_curation_recovery import admit, failed_effect, reconcile
from test_curation_work import produce, started

from vaultops.adapters.core import AdmissionError
from vaultops.application.experience import KnowledgeExperience
from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity


def recover(app):
    report, code = app.recover()
    assert code == 0, report
    assert report["dispatch_performed"] is False
    assert report["canonical_target_changed"] is False
    return report["experience"]


@pytest.mark.parametrize("kind,outcome", [("timeout", "timed_out"), ("failure", "failed")])
def test_native_attempt_and_applied_recovery_are_distinct_resumable_sources(tmp_path, kind, outcome):
    exops, executor, _spec, run, source, target, _proof = failed_effect(tmp_path, failure_kind=kind)
    app = exops.application
    initial = app.journal.read()
    original_source = source.read_bytes()
    assert initial["receipts"]["exops.work.action.curation.unit"]["receipt"]["outcome"] == "outcome_unknown"
    assert recover(app) == {"observed": 1, "skipped_unavailable": 0, "deferred": 0,
                            "produced": 1, "quarantined": 0}
    after_failure = app.journal.read()
    assert after_failure["intents"] == initial["intents"]
    assert after_failure["receipts"] == initial["receipts"]
    event = after_failure["experience_events"][0]
    assert (event["category"], event["outcome_state"]) == (kind, outcome)
    assert event["evidence_references"][0]["availability"] == "unavailable"
    assert after_failure["experience"][0]["evidence_references"] == []
    admit(executor, run, target)
    assert recover(app)["observed"] == 0  # received is not recovered
    reconcile(executor, run)
    before_sensor = app.journal.read()
    reconstructed = KnowledgeApplication.from_roots(app.roots)
    assert recover(reconstructed) == {"observed": 1, "skipped_unavailable": 0, "deferred": 0,
                                      "produced": 1, "quarantined": 0}
    final = reconstructed.journal.read()
    assert final["intents"] == before_sensor["intents"]
    assert final["receipts"] == before_sensor["receipts"]
    assert [event["category"] for event in final["experience_events"]] == [kind, "crash_recovery"]
    assert final["experience_events"][0] == event
    assert len({candidate["candidate_id"] for candidate in final["experience"]}) == 2
    assert recover(KnowledgeApplication.from_roots(app.roots)) == {
        "observed": 0, "skipped_unavailable": 0, "deferred": 0,
        "produced": 0, "quarantined": 0,
    }
    assert app.journal.read() == final
    assert len(executor.runner.requests) == executor.assessments == 1
    assert source.read_bytes() == original_source


def test_identity_only_unknown_does_not_establish_failed_effect_or_native_failure(tmp_path):
    exops, executor, _spec, run, _source, target, _proof = failed_effect(tmp_path)
    app = exops.application
    before = app.journal.read()
    assert recover(app)["observed"] == 0
    assert app.journal.read() == before
    admit(executor, run, target)
    reconcile(executor, run)
    assert recover(app)["observed"] == 1
    assert [event["category"] for event in app.journal.read()["experience_events"]] == ["crash_recovery"]


def test_ordinary_evaluated_completion_is_not_unusual_success(tmp_path):
    exops, executor, spec, run, _path, receipt = started(tmp_path)
    asyncio.run(produce(executor, spec, run))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    exops.worker_finished(
        run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run,
        assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id,
    )
    before = exops.application.journal.read()
    assert recover(exops.application)["observed"] == 0
    assert exops.application.journal.read() == before


def test_actual_owner_cancel_is_one_cancellation_observation_not_failure(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    apply(controller, target)
    app = exops.application
    before = app.journal.read()
    assert recover(app)["observed"] == 1
    after = app.journal.read()
    assert after["intents"] == before["intents"] and after["receipts"] == before["receipts"]
    assert len(after["experience_events"]) == len(after["experience"]) == 1
    event = after["experience_events"][0]
    assert (event["category"], event["outcome_state"]) == ("cancellation", "aborted")
    assert event["source_run_id"] == target["work_run_id"]
    assert event["evidence_references"][0]["availability"] == "unavailable"
    assert after["experience"][0]["evidence_references"] == []
    assert recover(KnowledgeApplication.from_roots(app.roots))["observed"] == 0
    assert app.journal.read() == after
    with pytest.raises(AdmissionError, match="committed owner evidence"):
        KnowledgeExperience(app.journal, app.core).observe(
            event_id="event.cancel", category="cancellation", outcome_state="aborted")


@pytest.mark.parametrize("change", ["foreign", "private", "denied", "local_only", "wrong_target"])
def test_denied_foreign_private_or_mismatched_sources_do_not_leak(tmp_path, change):
    exops, _executor, _spec, run, source, _target, _proof = failed_effect(tmp_path, failure_kind="timeout")
    app = exops.application
    if change in {"denied", "local_only"}:
        policy = "deny" if change == "denied" else "local_only"
        source.write_text(source.read_text().replace("ai_policy: ask", "ai_policy: " + policy))
    with app.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + run["work_run_id"]]
        if change == "foreign":
            record["method_source_reference"]["owner_operation_id"] = "foreign"
        elif change == "private":
            record["method_source_reference"]["classification"] = "confidential"
        elif change == "wrong_target":
            record["curation_native_failure"]["target"]["work_spec_digest"] = "sha256:" + "0" * 64
        else:
            record["method_source_reference"] = app.reference(source.relative_to(app.roots.vault).as_posix())
        writer.commit(app.clock())
    before = app.journal.read()
    report = recover(app)
    assert report["observed"] == report["produced"] == 0
    assert report["skipped_unavailable"] == 1
    assert app.journal.read() == before
    assert not before["experience_events"] and not before["experience"] and not before["outbox"]


def test_sensor_resumes_scan_when_event_was_committed_before_process_loss(tmp_path):
    exops, _executor, _spec, _run, _source, _target, _proof = failed_effect(tmp_path, failure_kind="timeout")
    app = exops.application
    from vaultops.application.native_lifecycle import _failure_source, _work

    key, record = next((key, record) for key, record in app.journal.read()["intents"].items()
                       if key.startswith("exops.work."))
    _, _, classification = _work(app, key, record)
    source = _failure_source(app, record, classification)
    KnowledgeExperience(app.journal, app.core).observe(**source)
    before = app.journal.read()
    assert not before["experience"]
    assert recover(KnowledgeApplication.from_roots(app.roots)) == {
        "observed": 0, "skipped_unavailable": 0, "deferred": 0,
        "produced": 1, "quarantined": 0,
    }
    after = app.journal.read()
    assert after["experience_events"] == before["experience_events"]
    assert len(after["experience"]) == len(after["outbox"]) == 1


def test_source_capacity_defers_without_losing_native_marker(tmp_path):
    exops, _executor, _spec, _run, _source, _target, _proof = failed_effect(tmp_path, failure_kind="timeout")
    app = exops.application
    experience = KnowledgeExperience(app.journal, app.core)
    for index in range(100):
        experience.observe(event_id=f"event.routine.{index}", category="routine_progress",
                           outcome_state="completed")
    before = copy.deepcopy(app.journal.read()["intents"])
    report = recover(app)
    assert report == {"observed": 0, "skipped_unavailable": 0, "deferred": 1,
                      "produced": 0, "quarantined": 100}
    assert len(app.journal.read()["experience_events"]) == 100
    assert app.journal.read()["intents"] == before
    assert not app.journal.read()["experience"]


def test_sensor_cannot_bypass_operation_recover_admission(tmp_path):
    exops, _executor, _spec, _run, _source, _target, _proof = failed_effect(tmp_path, failure_kind="timeout")
    from vaultops.application.native_lifecycle import scan_native_lifecycle

    denied = KnowledgeApplication.from_roots(exops.application.roots,
                                             identity=LocalIdentity("agent", "mcp"))
    before = denied.journal.read()
    with pytest.raises(AdmissionError, match="denies this action"):
        scan_native_lifecycle(denied)
    assert denied.journal.read() == before


def test_native_source_waits_for_existing_outbox_capacity_then_resumes(tmp_path):
    exops, _executor, _spec, _run, _source, _target, _proof = failed_effect(
        tmp_path, failure_kind="timeout")
    app = exops.application
    experience = KnowledgeExperience(app.journal, app.core)
    for index in range(4):
        experience.observe(event_id=f"event.existing.{index}", category="failure",
                           outcome_state="failed")
    assert experience.scan()["produced"] == 4
    assert recover(app)["produced"] == 0
    waiting = app.journal.read()
    assert len(waiting["experience_events"]) == 5 and waiting["experience_cursor"] == 4
    assert len(waiting["outbox"]) == 4

    class Rejected:
        def admit(self, _envelope, _candidate):
            raise AdmissionError("FORBIDDEN", "Disposable receiver rejects fixture candidate")

    with pytest.raises(AdmissionError):
        experience.dispatch_one(Rejected())
    assert recover(KnowledgeApplication.from_roots(app.roots)) == {
        "observed": 0, "skipped_unavailable": 0, "deferred": 0,
        "produced": 1, "quarantined": 0,
    }
    final = app.journal.read()
    assert final["experience_events"] == waiting["experience_events"]
    assert final["experience_cursor"] == 5 and len(final["experience"]) == 5


def test_committed_event_identity_cannot_silently_change_evidence(tmp_path):
    exops, _executor, _spec, run, _source, _target, _proof = failed_effect(
        tmp_path, failure_kind="timeout")
    app = exops.application
    recover(app)
    original_events = copy.deepcopy(app.journal.read()["experience_events"])
    with app.journal.writer() as writer:
        marker = writer.value["intents"]["exops.work." + run["work_run_id"]]["curation_native_failure"]
        marker["occurred_at"] = "2026-10-31T00:00:00+00:00"
        writer.commit(app.clock())
    report, code = app.recover()
    assert code == 10 and report["errors"][0]["code"] == "EXPERIENCE_CONFLICT"
    assert app.journal.read()["experience_events"] == original_events

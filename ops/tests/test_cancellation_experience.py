"""Existing admitted owner controls are the sole cancellation learning source."""

from __future__ import annotations

import copy

import pytest
from test_curation_control import apply, checkpoint_ready, receive

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_control import _control_receipt_key
from vaultops.application.experience import KnowledgeExperience
from vaultops.application.knowledge import KnowledgeApplication


def cancelled(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, path = checkpoint_ready(tmp_path, monkeypatch)
    received = receive(controller, target)
    applied = apply(controller, target)
    app = exops.application
    command = next(value for key, value in app.journal.read()["intents"].items()
                   if key.startswith("curation.control.") and value["state"] == "applied")
    return app, target, command, path, received, applied


def test_received_cancel_is_not_applied_cancellation_learning(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    receive(controller, target)
    app = exops.application
    before = app.journal.read()
    report, code = app.recover()
    assert code == 0 and report["experience"]["observed"] == 0
    assert app.journal.read() == before


@pytest.mark.parametrize("change", ["unknown", "unresolved", "missing_receipt", "foreign", "decision",
                                    "source_denied", "source_private", "wrong_result", "wrong_revision"])
def test_unknown_unsafe_foreign_and_unavailable_cancellation_sources_do_not_leak(tmp_path, monkeypatch, change):
    app, target, command, path, _received, _applied = cancelled(tmp_path, monkeypatch)
    if change == "source_denied":
        path.write_text(path.read_text().replace("ai_policy: ask", "ai_policy: deny"))
    with app.journal.writer() as writer:
        stored = writer.value["intents"]["curation.control." + command["command_id"]]
        record = writer.value["intents"]["exops.work." + target["work_run_id"]]
        canonical = writer.value["receipts"][_control_receipt_key(command["command_id"])]
        if change == "unknown":
            stored["execution_receipt"]["outcome"] = "outcome_unknown"
            canonical["receipt"] = copy.deepcopy(stored["execution_receipt"])
            canonical["unresolved"] = True
        elif change == "unresolved":
            stored["received_run"]["unresolved_effect_ids"] = ["effect.unknown"]
        elif change == "missing_receipt":
            del writer.value["receipts"][_control_receipt_key(command["command_id"])]
        elif change == "foreign":
            stored["directive"]["owner_operation_id"] = "foreign"
        elif change == "decision":
            writer.value["decisions"][stored["request"]["request_id"]]["receipt"]["decision_state"] = "rejected"
        elif change == "source_denied":
            relative = path.relative_to(app.roots.vault).as_posix()
            record["method_source_reference"] = app.reference(relative)
        elif change == "source_private":
            record["method_source_reference"]["classification"] = "confidential"
        elif change == "wrong_result":
            stored["execution_receipt"]["result_digest"] = "sha256:" + "0" * 64
            canonical["receipt"] = copy.deepcopy(stored["execution_receipt"])
        else:
            stored["directive"]["applied_run_revision"] += 1
        writer.commit(app.clock())
    before = app.journal.read()
    report, code = app.recover()
    assert code == 0, report
    assert report["experience"]["observed"] == report["experience"]["produced"] == 0
    assert report["experience"]["skipped_unavailable"] == 1
    assert app.journal.read() == before
    assert not before["experience_events"] and not before["experience"] and not before["outbox"]


def test_owner_source_and_control_evidence_survive_reconstruction_without_new_effect(tmp_path, monkeypatch):
    app, target, command, path, _received, _applied = cancelled(tmp_path, monkeypatch)
    source_bytes = path.read_bytes()
    before = app.journal.read()
    report, code = app.recover()
    assert code == 0 and report["experience"]["observed"] == report["experience"]["produced"] == 1
    after = app.journal.read()
    for field in ("intents", "receipts", "human_requests", "decisions"):
        assert after[field] == before[field]
    event, = after["experience_events"]
    assert event["category"] == "cancellation" and event["outcome_state"] == "aborted"
    assert event["source_run_id"] == target["work_run_id"]
    assert event["source_instance_id"] == app.journal.identity["instance_id"]
    reference, = event["evidence_references"]
    assert reference["availability"] == "unavailable" and "export_capability_id" not in reference
    assert after["experience"][0]["source_record"] == event["source_record"]
    assert after["experience"][0]["evidence_references"] == []
    assert "cancellation_evidence" not in event and "cancellation_evidence" not in after["experience"][0]
    repeated, code = KnowledgeApplication.from_roots(app.roots).recover()
    assert code == 0 and repeated["experience"]["observed"] == repeated["experience"]["produced"] == 0
    assert app.journal.read() == after and path.read_bytes() == source_bytes
    assert after["intents"]["curation.control." + command["command_id"]] == command


def test_cancellation_labels_without_existing_exact_owner_evidence_are_denied(tmp_path, monkeypatch):
    app, target, _command, _path, _received, _applied = cancelled(tmp_path, monkeypatch)
    experience = KnowledgeExperience(app.journal, app.core)
    for outcome in ("completed", "failed", "timed_out", "unknown", "recovered", "denied"):
        with pytest.raises(AdmissionError, match="category and outcome"):
            experience.observe(event_id="event.invalid", category="cancellation", outcome_state=outcome)
    with pytest.raises(AdmissionError, match="committed owner evidence"):
        experience.observe(event_id="event.no_evidence", category="cancellation",
                           outcome_state="aborted", source_run_id=target["work_run_id"])

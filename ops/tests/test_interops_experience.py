"""Owner-scoped Experience extraction and uncertain InterOps delivery."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import PINS, AdmissionError, canonical, digest
from vaultops.adapters.core_cutover import core_cutover
from vaultops.application.experience import KnowledgeExperience
from vaultops.application.gateway import GatewayIdentity, KnowledgeGateway
from vaultops.application.knowledge import KnowledgeApplication

NOW = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)


def _app(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path)
    return roots, KnowledgeApplication.from_roots(roots, clock=lambda: NOW)


def _receipt(candidate):
    return {
        "receipt_id": "receipt.fixture", "candidate_id": candidate["candidate_id"],
        "owner_operation_id": "knowledgeos", "recipient_operation_id": "hermestrace",
        "idempotency_key": candidate["idempotency_key"],
        "payload_digest": digest(canonical(candidate)), "disposition": "admitted",
        "admission_level": "owner_durable", "source_revision": "revision.1",
        "received_at": NOW.isoformat(),
    }


def test_source_first_filters_private_evidence_and_does_not_infer_recovery(tmp_path):
    _, app = _app(tmp_path)
    experience = KnowledgeExperience(app.journal, app.core, lambda: NOW)
    with pytest.raises(AdmissionError, match="category and outcome"):
        experience.observe(event_id="event.invalid", category="crash_recovery",
                           outcome_state="completed")
    experience.observe(event_id="event.routine", category="routine_progress",
                       outcome_state="completed")
    reference = {
        "artifact_id": "artifact.private", "owner_operation_id": "knowledgeos",
        "source_revision": "revision.1", "content_digest": digest(b"private"),
        "classification": "confidential", "availability": "available_by_owner_export",
        "media_type": "text/plain", "size_bytes": 7,
    }
    experience.observe(event_id="event.denial", category="policy_denial",
                       outcome_state="denied", evidence_references=(reference,))
    state = app.journal.read()
    assert len(state["experience_events"]) == 2
    assert not state["experience"] and not state["outbox"]
    assert experience.scan() == {"produced": 1, "quarantined": 1}
    state = app.journal.read()
    candidate = state["experience"][0]
    assert candidate["category"] == "policy_denial"
    assert candidate["evidence_references"] == []
    assert candidate["source_record"]["content_digest"] == state["experience_events"][1]["source_record"]["content_digest"]
    assert len(state["outbox"]) == 1
    with pytest.raises(AdmissionError, match="changed meaning"):
        experience.observe(event_id="event.denial", category="policy_denial",
                           outcome_state="denied", evidence_references=())


def test_unknown_delivery_queries_owner_receipt_before_retry(tmp_path):
    _, app = _app(tmp_path)
    current = [NOW]
    app = replace(app, clock=lambda: current[0])
    experience = KnowledgeExperience(app.journal, app.core, lambda: current[0])
    experience.observe(event_id="event.denial", category="policy_denial",
                       outcome_state="denied")
    experience.scan()
    candidate = app.journal.read()["experience"][0]
    receipt = _receipt(candidate)

    class Transport:
        admits = 0
        queries = 0

        def admit(self, envelope, payload):
            self.admits += 1
            assert payload == candidate
            raise AdmissionError("UNAVAILABLE", "reply lost after potential admission")

        def query_admission(self, **identity):
            self.queries += 1
            assert identity["idempotency_key"] == candidate["idempotency_key"]
            return receipt

    transport = Transport()
    with pytest.raises(AdmissionError, match="reply lost"):
        KnowledgeGateway(app).dispatch_experience(transport)
    assert app.journal.read()["outbox"][0]["entry"]["delivery_state"] == "in_flight"
    current[0] += timedelta(seconds=61)
    assert KnowledgeGateway(app).dispatch_experience(transport) == receipt
    assert (transport.admits, transport.queries) == (1, 1)
    state = app.journal.read()
    assert state["outbox"][0]["entry"]["delivery_state"] == "admitted"
    assert state["interops_outbound"][candidate["candidate_id"]]["receipt"] == receipt
    assert KnowledgeGateway(app).dispatch_experience(transport) is None


def test_outbox_capacity_defers_sources_without_dropping_them(tmp_path):
    _, app = _app(tmp_path)
    experience = KnowledgeExperience(app.journal, app.core, lambda: NOW)
    for number in range(5):
        experience.observe(event_id=f"event.denial_{number}", category="policy_denial",
                           outcome_state="denied")
    assert experience.scan() == {"produced": 4, "quarantined": 0}
    state = app.journal.read()
    assert len(state["outbox"]) == 4 and state["experience_cursor"] == 4
    assert experience.scan() == {"produced": 0, "quarantined": 0}
    assert len(app.journal.read()["experience_events"]) == 5


def test_permanent_receiver_denial_quarantines_without_fabricating_receipt(tmp_path):
    _, app = _app(tmp_path)
    experience = KnowledgeExperience(app.journal, app.core, lambda: NOW)
    experience.observe(event_id="event.denial", category="policy_denial",
                       outcome_state="denied")
    experience.scan()

    class RejectingReceiver:
        def admit(self, _envelope, _candidate):
            raise AdmissionError("SCOPE_DENIED", "receiver policy denied the candidate")

    with pytest.raises(AdmissionError) as denied:
        KnowledgeGateway(app).dispatch_experience(RejectingReceiver())
    assert denied.value.code == "SCOPE_DENIED"
    state = app.journal.read()
    assert state["outbox"][0]["entry"]["delivery_state"] == "quarantined"
    assert state["outbox"][0]["transport_state"] == "receiver_denied:SCOPE_DENIED"
    assert state["interops_outbound"] == {}


def test_exact_v3_cutover_preserves_completed_receipt_and_rejects_drift(tmp_path):
    roots, app = _app(tmp_path)
    legacy = app.journal.empty()
    for field in ("experience_events", "experience_cursor", "interops_outbound"):
        del legacy[field]
    legacy["state_version"] = 3
    legacy["pins"].update(core_version="0.17.0", schema_version_pin="0.17.0",
                          core_digest="sha256:b69f3e389de8b8362397dec8d55a92d10a4556cc2c5f4fe646b934944db5188e")
    legacy["intents"]["index.fixture"] = {"state": "completed", "recorded_at": NOW.isoformat()}
    legacy["receipts"]["index.fixture"] = {"recorded_at": NOW.isoformat(), "outcome": "completed"}
    raw = canonical(legacy) + b"\n"
    app.journal.path.write_bytes(raw)
    app.journal.path.chmod(0o600)
    expected = hashlib.sha256(raw).hexdigest()
    assert core_cutover(roots, expected_sha256=expected)["applied"] is False
    with pytest.raises(AdmissionError, match="preimage"):
        core_cutover(roots, expected_sha256="0" * 64, apply=True)
    assert app.journal.path.read_bytes() == raw
    result = core_cutover(roots, expected_sha256=expected, apply=True)
    assert result["applied"] and result["preserved_intent_count"] == 1
    after = app.journal.read()
    assert after["pins"] == PINS and after["state_version"] == 4
    assert after["intents"] == legacy["intents"] and after["receipts"] == legacy["receipts"]
    assert (roots.state / "archives/core-cutover" / expected).read_bytes() == raw


def test_exact_v4_core_pin_cutover_preserves_completed_work_and_rejects_active_work(tmp_path):
    roots, app = _app(tmp_path)
    previous = app.journal.empty()
    previous["pins"].update(core_version="0.17.4", schema_version_pin="0.17.4",
                            core_digest="sha256:28697ac0cb49106afa32b143482cdca32e4e638d1a250173db381a2bf836e2be")
    previous["generation"] = 2
    previous["revision"] = 4
    previous["intents"]["index.fixture"] = {"state": "completed", "recorded_at": NOW.isoformat()}
    previous["receipts"]["index.fixture"] = {"recorded_at": NOW.isoformat(), "outcome": "completed"}
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    app.journal.path.chmod(0o600)
    expected = hashlib.sha256(raw).hexdigest()
    preview = core_cutover(roots, expected_sha256=expected)
    assert preview["source_version"] == 4 and preview["target_version"] == 4
    assert preview["source_generation"] == 2 and preview["target_generation"] == 3
    with pytest.raises(AdmissionError, match="preimage"):
        core_cutover(roots, expected_sha256="0" * 64, apply=True)
    assert app.journal.path.read_bytes() == raw
    applied = core_cutover(roots, expected_sha256=expected, apply=True)
    assert applied["applied"] and applied["preserved_receipt_count"] == 1
    after = app.journal.read()
    assert after["pins"] == PINS and after["generation"] == 3 and after["revision"] == 5
    assert after["intents"] == previous["intents"] and after["receipts"] == previous["receipts"]
    assert (roots.state / "archives/core-cutover" / expected).read_bytes() == raw
    with pytest.raises(AdmissionError, match="preimage"):
        core_cutover(roots, expected_sha256=expected, apply=True)

    active = copy.deepcopy(previous)
    active["intents"]["pending.fixture"] = {"state": "pending"}
    active_raw = canonical(active) + b"\n"
    app.journal.path.write_bytes(active_raw)
    with pytest.raises(AdmissionError, match="unresolved owner work"):
        core_cutover(roots, expected_sha256=hashlib.sha256(active_raw).hexdigest())

    prior_work = copy.deepcopy(previous)
    prior_work["intents"]["exops.work.fixture"] = {
        "state": "recorded", "spec": {"core_version": "0.17.4"},
    }
    work_raw = canonical(prior_work) + b"\n"
    app.journal.path.write_bytes(work_raw)
    with pytest.raises(AdmissionError, match="unresolved owner work"):
        core_cutover(roots, expected_sha256=hashlib.sha256(work_raw).hexdigest())


def test_exact_core_0180_to_0181_cutover_preserves_owner_records(tmp_path):
    roots, app = _app(tmp_path)
    previous = app.journal.empty()
    previous["pins"].update(
        core_version="0.18.0", schema_version_pin="0.18.0",
        core_digest="sha256:a1a051556d2e187c6012c562a46a9e5aed4bc842325e94e66522ccdbd9ce4c13",
    )
    previous["generation"] = 3
    previous["revision"] = 5
    previous["intents"]["index.fixture"] = {"state": "completed", "recorded_at": NOW.isoformat()}
    previous["receipts"]["index.fixture"] = {"recorded_at": NOW.isoformat(), "outcome": "completed"}
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    app.journal.path.chmod(0o600)
    expected = hashlib.sha256(raw).hexdigest()
    preview = core_cutover(roots, expected_sha256=expected)
    assert preview["source_core_version"] == "0.18.0"
    assert preview["target_core_version"] == "0.18.1"
    assert preview["source_generation"] == 3 and preview["target_generation"] == 4
    with pytest.raises(AdmissionError, match="preimage"):
        core_cutover(roots, expected_sha256="0" * 64, apply=True)
    applied = core_cutover(roots, expected_sha256=expected, apply=True)
    assert applied["applied"] and applied["preserved_receipt_count"] == 1
    after = app.journal.read()
    assert after["pins"] == PINS and after["generation"] == 4 and after["revision"] == 6
    assert (roots.state / "archives/core-cutover" / expected).read_bytes() == raw
    for key in set(previous) - {"pins", "generation", "revision", "observed_at"}:
        assert after[key] == previous[key]
    active = copy.deepcopy(previous)
    active["intents"]["pending.fixture"] = {"state": "pending"}
    active_raw = canonical(active) + b"\n"
    app.journal.path.write_bytes(active_raw)
    with pytest.raises(AdmissionError, match="unresolved owner work"):
        core_cutover(roots, expected_sha256=hashlib.sha256(active_raw).hexdigest())


def test_unbound_outbound_request_is_explicitly_unavailable(tmp_path):
    _, app = _app(tmp_path)
    gateway = KnowledgeGateway(app)
    payload = {"query": "bounded"}
    envelope = {
        "contract_version": PINS["capsule_version"], "core_version": PINS["core_version"],
        "core_digest": PINS["core_digest"], "schema_version": PINS["schema_version_pin"],
        "semantic_version": PINS["semantic_version"], "semantic_digest": PINS["semantic_digest"],
        "message_id": "message.fixture", "message_kind": "information_request",
        "request_id": "request.fixture", "idempotency_key": "request.fixture",
        "sender_operation_id": "knowledgeos", "sender_principal_id": "knowledgeos.local",
        "recipient_operation_id": "ordinary", "purpose": "status", "action": "read",
        "scope": "status", "resource_owner_operation_id": "ordinary",
        "resource_kind": "operation", "resource_id": "operation.ordinary",
        "payload_type": "information.query", "payload_reference_id": "payload.fixture",
        "payload_digest": digest(canonical(payload)), "payload_size_bytes": len(canonical(payload)),
        "classification": "internal", "semantic_term_ids": [], "issued_at": NOW.isoformat(),
        "ttl_seconds": 43200, "hop_count": 1, "revision": 1,
    }
    caller = GatewayIdentity("knowledgeos", "knowledgeos.local", ("read",))
    with pytest.raises(AdmissionError) as denied:
        gateway.register_outbound_request(envelope, payload, caller,
                                          recipient_available=lambda _recipient: False)
    assert denied.value.code == "UNAVAILABLE"
    assert not app.journal.read()["correlations"]

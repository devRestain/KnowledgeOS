"""Owner-scoped Experience extraction and uncertain InterOps delivery."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import PINS, AdmissionError, canonical, digest
from vaultops.adapters.core_cutover import core_cutover
from vaultops.application.experience import KnowledgeExperience
from vaultops.application.gateway import GatewayIdentity, KnowledgeGateway
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.interfaces.cli import main

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


def test_exact_core_0180_to_selected_pin_cutover_preserves_owner_records(tmp_path):
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
    assert preview["target_core_version"] == PINS["core_version"]
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


@pytest.fixture
def retained_core_0181(tmp_path):
    """Synthetic observed v4/generation-4/revision-7 envelope, never live State."""
    roots, app = _app(tmp_path)
    previous = app.journal.empty()
    # Frozen agentfabric-core-0.18.1 release manifest and retained readiness pins.
    previous["pins"] = {
        "core_version": "0.18.1",
        "core_digest": "sha256:a08b724d6910575a0164302b0e5ed2c81e83519bf595e930c779d5e0f6bfd676",
        "schema_version_pin": "0.18.1",
        "semantic_version": "0.7.0",
        "semantic_digest": "sha256:2998829dec11f61d635c3c2ddec1127f988e8ad517ab64b27eade9539207f127",
        "capsule_version": "2.0.0",
        "resource_binding_api": "0.3.0",
        "host_binding_api": "0.3.0",
    }
    previous.update(generation=4, revision=7, observed_at=NOW.isoformat())
    previous["intents"]["index.fixture"] = {
        "state": "completed", "recorded_at": NOW.isoformat(),
        "source_digest": digest(b"synthetic source bytes"),
    }
    # Deliberately noncanonical whitespace must survive in the original archive.
    raw = json.dumps(previous, ensure_ascii=False, indent=2).encode() + b"\n"
    app.journal.path.write_bytes(raw)
    app.journal.path.chmod(0o600)
    return roots, app, previous, raw, hashlib.sha256(raw).hexdigest()


def test_exact_core_0181_preview_is_unchanged_and_cli_consumes_transition(
    retained_core_0181, capsys,
):
    roots, app, previous, raw, expected = retained_core_0181
    before = {
        path.relative_to(roots.state): path.read_bytes()
        for path in roots.state.rglob("*") if path.is_file()
    }
    with pytest.raises(AdmissionError) as incompatible:
        KnowledgeApplication.from_roots(roots)
    assert incompatible.value.code == "STATE_PIN_MISMATCH"
    preview = core_cutover(roots, expected_sha256=expected)
    assert preview == {
        "status": "PASS", "applied": False, "source_sha256": expected,
        "source_core_version": "0.18.1", "target_core_version": "0.19.0",
        "source_version": 4, "target_version": 4,
        "source_generation": 4, "target_generation": 5,
        "preserved_intent_count": 1, "preserved_receipt_count": 0,
    }
    arguments = ["storage", "core-cutover", "--expected-sha256", expected,
                 "--root", str(roots.control)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == preview
    assert {
        path.relative_to(roots.state): path.read_bytes()
        for path in roots.state.rglob("*") if path.is_file()
    } == before
    assert not (roots.state / "archives/core-cutover").exists()

    assert main([*arguments, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied == {**preview, "applied": True, "archive_sha256": expected}
    archive = roots.state / "archives/core-cutover" / expected
    assert archive.read_bytes() == raw
    assert archive.stat().st_mode & 0o777 == 0o600
    reconstructed = KnowledgeApplication.from_roots(roots, clock=lambda: NOW)
    after = reconstructed.journal.read()
    assert after["pins"] == PINS
    assert after["generation"] == 5 and after["revision"] == 8
    assert after["observed_at"] != previous["observed_at"]
    for key in set(previous) - {"pins", "generation", "revision", "observed_at"}:
        assert after[key] == previous[key]
    applied_bytes = app.journal.path.read_bytes()
    assert main([*arguments, "--apply"]) == 10
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "STATE_PREIMAGE_CHANGED"
    assert app.journal.path.read_bytes() == applied_bytes
    assert archive.read_bytes() == raw


def test_exact_core_0181_apply_preserves_historical_receipt_and_data_bytes(retained_core_0181):
    roots, app, previous, _, _ = retained_core_0181
    historical = {
        "recorded_at": NOW.isoformat(), "outcome": "completed",
        "payload_bytes": '{ "pins": "historical", "revision": 3 }\n',
        "payload_digest": digest(b'{ "pins": "historical", "revision": 3 }\n'),
        "pins": copy.deepcopy(previous["pins"]),
        "generation": 3, "revision": 3,
    }
    previous["receipts"]["exops.control.historical"] = historical
    previous["history"] = [{"sequence": 1, "evidence": copy.deepcopy(historical)}]
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    expected = hashlib.sha256(raw).hexdigest()
    companions = [roots.state / "historical-evidence.bin",
                  roots.runtime / "reconstructable-data.bin",
                  roots.vault / "synthetic-domain.bin"]
    for path in companions:
        path.write_bytes(b"\x00synthetic retained bytes\r\n")
    before = {path: path.read_bytes() for path in companions}
    result = core_cutover(roots, expected_sha256=expected, apply=True)
    assert result["preserved_receipt_count"] == 1
    after = KnowledgeApplication.from_roots(roots).journal.read()
    for key in set(previous) - {"pins", "generation", "revision", "observed_at"}:
        assert after[key] == previous[key]
    assert {path: path.read_bytes() for path in companions} == before
    assert (roots.state / "archives/core-cutover" / expected).read_bytes() == raw


@pytest.mark.parametrize(("field", "replacement", "code"), [
    ("owner_operation_id", "foreign", "STATE_PIN_MISMATCH"),
    ("fabric_id", "foreign", "STATE_PIN_MISMATCH"),
    ("instance_id", "foreign", "STATE_PIN_MISMATCH"),
    ("state_version", 3, "STATE_VERSION_UNSUPPORTED"),
    ("intents", [], "STATE_INVALID"),
    ("history", {}, "STATE_INVALID"),
    ("generation", 2147483647, "STATE_INVALID"),
    ("revision", -1, "STATE_INVALID"),
    ("gateway", {"exchange": {}}, "STATE_ACTIVE"),
    ("correlations", {"exchange": {}}, "STATE_ACTIVE"),
    ("human_requests", {"request": {}}, "STATE_ACTIVE"),
    ("decisions", {"decision": {}}, "STATE_ACTIVE"),
    ("interops_outbound", {"exchange": {}}, "STATE_ACTIVE"),
    ("experience", [{}], "STATE_ACTIVE"),
    ("experience_events", [{}], "STATE_ACTIVE"),
    ("outbox", [{}], "STATE_ACTIVE"),
    ("experience_cursor", 1, "STATE_ACTIVE"),
    ("intents", {"active": {"state": "unknown"}}, "STATE_ACTIVE"),
    ("intents", {"work": {"state": "completed", "spec": {}}}, "STATE_ACTIVE"),
    ("intents", {"work": {"state": "completed", "run": {}}}, "STATE_ACTIVE"),
    ("receipts", {"receipt": {"unresolved": True}}, "STATE_ACTIVE"),
])
def test_exact_core_0181_rejects_foreign_active_and_invalid_state(
    retained_core_0181, field, replacement, code,
):
    roots, app, previous, _, _ = retained_core_0181
    previous[field] = replacement
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    with pytest.raises(AdmissionError) as denied:
        core_cutover(roots, expected_sha256=hashlib.sha256(raw).hexdigest(), apply=True)
    assert denied.value.code == code
    assert app.journal.path.read_bytes() == raw
    assert not (roots.state / "archives/core-cutover").exists()


@pytest.mark.parametrize("pin", [
    "core_version", "core_digest", "schema_version_pin", "semantic_version",
    "semantic_digest", "capsule_version", "resource_binding_api", "host_binding_api", "extra",
])
def test_exact_core_0181_rejects_arbitrary_pins(retained_core_0181, pin):
    roots, app, previous, _, _ = retained_core_0181
    previous["pins"][pin] = "foreign"
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    with pytest.raises(AdmissionError) as denied:
        core_cutover(roots, expected_sha256=hashlib.sha256(raw).hexdigest(), apply=True)
    assert denied.value.code == "STATE_PIN_MISMATCH"
    assert app.journal.path.read_bytes() == raw
    assert not (roots.state / "archives/core-cutover").exists()


def test_exact_core_0181_rejects_wrong_preimage_mode_uid_and_held_writer(
    retained_core_0181, monkeypatch,
):
    roots, app, _, raw, expected = retained_core_0181
    with pytest.raises(AdmissionError) as denied:
        core_cutover(roots, expected_sha256="0" * 64, apply=True)
    assert denied.value.code == "STATE_PREIMAGE_CHANGED"
    app.journal.path.chmod(0o644)
    with pytest.raises(AdmissionError) as denied:
        core_cutover(roots, expected_sha256=expected, apply=True)
    assert denied.value.code == "STATE_MODE_INVALID"
    app.journal.path.chmod(0o600)
    uid = os.getuid()
    with monkeypatch.context() as changed:
        changed.setattr(os, "getuid", lambda: uid + 1)
        with pytest.raises(AdmissionError) as denied:
            core_cutover(roots, expected_sha256=expected, apply=True)
        assert denied.value.code == "STATE_MODE_INVALID"
    descriptor = os.open(app.journal.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AdmissionError) as denied:
            core_cutover(roots, expected_sha256=expected, apply=True)
        assert denied.value.code == "STATE_ACTIVE"
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert app.journal.path.read_bytes() == raw
    assert not (roots.state / "archives/core-cutover").exists()


@pytest.mark.parametrize("apply", [False, True], ids=["preview", "apply"])
@pytest.mark.parametrize(("mismatch", "code"), [
    ("control_pins", "PIN_MISMATCH"),
    ("control_identity", "STATE_PIN_MISMATCH"),
    ("core_release", "CORE_DIGEST_MISMATCH"),
    ("core_bytes", "CORE_DIGEST_MISMATCH"),
])
def test_exact_core_0181_rejects_destination_root_mismatch_without_writes(
    retained_core_0181, capsys, mismatch, code, apply,
):
    roots, app, previous, raw, expected = retained_core_0181
    if mismatch in {"control_pins", "control_identity"}:
        binding_path = roots.control / "ops/config/core-adoption.json"
        binding = json.loads(binding_path.read_bytes())
        if mismatch == "control_pins":
            binding.update(previous["pins"])
        else:
            binding["instance_id"] = "foreign.local"
        binding_path.write_bytes(canonical(binding) + b"\n")
    elif mismatch == "core_release":
        manifest_path = roots.core / "release/manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["core_release_version"] = "0.18.1"
        manifest["release_digest"] = previous["pins"]["core_digest"]
        manifest_path.write_bytes(canonical(manifest) + b"\n")
    else:
        catalog_path = roots.core / "contracts/catalog.json"
        catalog_path.write_bytes(catalog_path.read_bytes() + b"\n")
    selected = (roots.core, roots.control, roots.vault, roots.state, roots.runtime)
    before = {
        (root, path.relative_to(root)): (path.is_dir(), path.read_bytes() if path.is_file() else None)
        for root in selected for path in root.rglob("*")
    }
    arguments = ["storage", "core-cutover", "--expected-sha256", expected,
                 "--root", str(roots.control)]
    if apply:
        arguments.append("--apply")
    assert main(arguments) == 10
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == code
    assert {
        (root, path.relative_to(root)): (path.is_dir(), path.read_bytes() if path.is_file() else None)
        for root in selected for path in root.rglob("*")
    } == before
    assert app.journal.path.read_bytes() == raw
    assert not (roots.state / "archives/core-cutover").exists()


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


def test_new_source_binding_refuses_old_live_0181_state_without_transition(tmp_path):
    roots, app = _app(tmp_path)
    previous = app.journal.empty()
    previous["pins"].update(
        core_version="0.18.1", schema_version_pin="0.18.1",
        core_digest="sha256:a08b724d6910575a0164302b0e5ed2c81e83519bf595e930c779d5e0f6bfd676",
    )
    raw = canonical(previous) + b"\n"
    app.journal.path.write_bytes(raw)
    app.journal.path.chmod(0o600)

    def snapshot():
        result = {}
        for path in [roots.state, *sorted(roots.state.rglob("*"))]:
            metadata = path.lstat()
            result[path.relative_to(roots.state).as_posix()] = (
                metadata.st_mode, metadata.st_ino, metadata.st_uid, metadata.st_gid,
                path.read_bytes() if path.is_file() else None,
            )
        return result

    def open_writer():
        with app.journal.writer():
            pytest.fail("incompatible owner State opened a writer")

    before = snapshot()
    for operation in (
        lambda: KnowledgeApplication.from_roots(roots),
        app.journal.read,
        open_writer,
    ):
        with pytest.raises(AdmissionError) as rejected:
            operation()
        assert rejected.value.code == "STATE_PIN_MISMATCH"
        assert app.journal.path.read_bytes() == raw
        assert snapshot() == before

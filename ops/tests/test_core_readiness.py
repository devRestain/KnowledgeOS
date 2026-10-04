"""C12 preparation evidence uses only disposable, independent roots."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import PINS, AdmissionError, canonical, digest
from vaultops.application.gateway import GatewayIdentity, KnowledgeGateway
from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
NOW = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def fixture(tmp_path: Path, **kwargs):
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    source = roots.vault / SOURCE
    document = parse_frontmatter(source.read_text())
    source.write_text(render_frontmatter(dict(reversed(list(document.properties.items()))), document.body))
    report, code = generate_projection(roots)
    assert code == 0, report
    app = KnowledgeApplication.from_roots(roots, clock=lambda: NOW, **kwargs)
    return roots, app


def proposal(app):
    reference = app.reference(SOURCE)
    report, code = app.create_normalize_proposal(source_reference=reference)
    assert code == 0, report
    assert report["status"] != "NO_CHANGE", report
    return report


def response(app, report):
    request = report["human_action_request"]
    return {"request_id": request["request_id"], "request_revision": 1, "owner_operation_id": "knowledgeos", "actor_id": app.identity.actor_id, "chosen_action": "approve", "idempotency_key": "response:fixture", "response_id": "response.fixture", "received_at": NOW.isoformat(), "submission_state": "submitted", "target_digest": request["target_digest"]}


def test_retrieve_normalize_review_preserves_domain_and_canonical_bytes(tmp_path):
    roots, app = fixture(tmp_path)
    before = (roots.vault / SOURCE).read_bytes()
    result, code = app.retrieve("공포")
    assert code == 0, result
    assert SOURCE in {item["path"] for item in result["candidates"]}
    created = proposal(app)
    assert created["effects"] == {"pending_artifact_created": True, "canonical_note_changed": False}
    state_before = snapshot(roots.state)
    vault_before = snapshot(roots.vault)
    inspected, code = app.inspect_proposal(created["resource_reference"])
    assert code == 0, inspected
    assert inspected["acceptance_state"] == "pass"
    assert snapshot(roots.state) == state_before
    assert snapshot(roots.vault) == vault_before
    assert (roots.vault / SOURCE).read_bytes() == before
    proposed = created["proposal"]["target"]
    assert proposed["path"] == SOURCE
    from vaultops import proposals

    document = proposals._load_proposal(roots, created["proposal_path"])
    plan = proposals._plan(roots, document)
    assert parse_frontmatter(plan.target_markdown).body == parse_frontmatter(before.decode()).body
    assert parse_frontmatter(plan.target_markdown).properties == parse_frontmatter(before.decode()).properties
    replay, code = app.create_normalize_proposal(source_reference=app.reference(SOURCE))
    assert code == 0 and replay["replayed"], replay
    assert replay["proposal_sha256"] == created["proposal_sha256"]
    assert len(list((roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1


@pytest.mark.parametrize("field", ["core_version", "core_digest", "semantic_version", "semantic_digest", "operation_id"])
def test_bad_adoption_pins_and_foreign_identity_fail_before_state_write(tmp_path, field):
    roots, _ = fixture(tmp_path)
    before = snapshot(roots.state)
    path = roots.control / "ops/config/core-adoption.json"
    value = json.loads(path.read_bytes())
    value[field] = "foreign" if field == "operation_id" else "bad"
    path.write_bytes(canonical(value))
    with pytest.raises(AdmissionError):
        KnowledgeApplication.from_roots(roots)
    assert snapshot(roots.state) == before


def test_mcp_identity_foreign_resource_and_untrusted_fields_are_denied(tmp_path):
    roots, app = fixture(tmp_path, identity=LocalIdentity("local.mcp", "mcp"))
    before = snapshot(roots.state)
    denied, code = app.human_decision({"chosen_action": "approve"})
    assert code != 0 and denied["errors"][0]["code"] == "ACTION_DENIED"
    foreign = {**app.reference(SOURCE), "owner_operation_id": "foreign"}
    denied, code = app.create_normalize_proposal(source_reference=foreign)
    assert code != 0 and denied["errors"][0]["code"] == "OWNER_DENIED"
    denied, code = app.create_normalize_proposal(source_reference={**app.reference(SOURCE), "actor_id": "owner"})
    assert code != 0
    assert snapshot(roots.state) == before


@pytest.mark.parametrize("change", ["source", "proposal", "policy", "schema", "proposal_schema", "semantic"])
def test_human_target_is_bound_to_all_interpretation_and_effect_inputs(tmp_path, change):
    roots, app = fixture(tmp_path)
    created = proposal(app)
    submitted = response(app, created)
    if change == "source":
        path = roots.vault / SOURCE
    elif change == "proposal":
        path = roots.vault / created["proposal_path"]
    elif change == "policy":
        path = roots.control / "ops/policies/retrieval.yaml"
    elif change == "schema":
        path = roots.control / "blueprint/blueprint.schema.json"
    elif change == "proposal_schema":
        path = roots.control / "ops/schemas/proposal.schema.json"
    else:
        path = roots.core / "contracts/catalog.json"
    path.write_bytes(path.read_bytes() + b"\n")
    before = snapshot(roots.state)
    denied, code = app.human_decision(submitted)
    assert code != 0, denied
    assert snapshot(roots.state) == before


def test_valid_human_response_is_accepted_once_without_canonical_apply(tmp_path):
    roots, app = fixture(tmp_path)
    before = (roots.vault / SOURCE).read_bytes()
    created = proposal(app)
    submitted = response(app, created)
    accepted, code = app.human_decision(submitted)
    assert code == 0 and accepted["decision"]["pending_apply"], accepted
    state_before = snapshot(roots.state)
    replay, code = app.human_decision(submitted)
    assert code == 0 and replay["replayed"], replay
    assert snapshot(roots.state) == state_before
    assert (roots.vault / SOURCE).read_bytes() == before
    conflict, code = app.human_decision({**submitted, "chosen_action": "reject"})
    assert code != 0, conflict


def test_explicit_apply_requires_owner_approval_and_separate_dispatch(tmp_path):
    roots, app = fixture(tmp_path)
    created = proposal(app)
    before = (roots.vault / SOURCE).read_bytes()
    assert app.apply_proposal(created["proposal_path"])[1] != 0
    assert (roots.vault / SOURCE).read_bytes() == before
    assert app.human_decision(response(app, created))[1] == 0
    applied, code = app.apply_proposal(created["proposal_path"])
    assert code == 0 and applied["canonical_target_changed"], applied
    assert (roots.vault / SOURCE).read_bytes() != before
    observed, code = app.status()
    assert code == 0, observed
    assert observed["canonical_apply"] == "completed"
    assert observed["human_approval"] == "approved"
    assert observed["snapshot"]["acceptance_state"] == "not_evaluated"


def test_cli_decision_replay_uses_the_first_trusted_response(tmp_path):
    roots, app = fixture(tmp_path)
    created = proposal(app)
    selected = created["proposal_path"]
    assert app.decide_proposal(selected, created["proposal_sha256"], "approve")[1] == 0
    state_before = snapshot(roots.state)
    from dataclasses import replace

    later = replace(app, clock=lambda: NOW + timedelta(seconds=1))
    replay, code = later.decide_proposal(selected, created["proposal_sha256"], "approve")
    assert code == 0 and replay["replayed"], replay
    assert snapshot(roots.state) == state_before


def test_source_drift_after_artifact_creation_preserves_unknown_effect(tmp_path):
    roots, app = fixture(tmp_path)
    def drift(phase):
        if phase == "artifact_created":
            source = roots.vault / SOURCE
            source.write_bytes(source.read_bytes() + b"\nexternal revision\n")
    from dataclasses import replace

    app = replace(app, fault_hook=drift)
    result, code = app.create_normalize_proposal(source_reference=app.reference(SOURCE))
    assert code != 0 and result["execution_outcome"] == "unknown", result
    value = app.journal.read()
    assert {item["state"] for item in value["intents"].values()} == {"unknown"}
    assert value["human_requests"] == {}
    assert len(list((roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    recovered, code = app.recover()
    assert code == 0 and not recovered["dispatch_performed"], recovered
    value = app.journal.read()
    assert value["human_requests"] == {}


@pytest.mark.parametrize("change", ["run", "owner", "checkpoint", "partition"])
def test_execution_success_cannot_substitute_a_mismatched_evaluation(tmp_path, change):
    from vaultops.application.knowledge import evaluate_proposal

    def invalid(report, effect_id, checkpoint):
        result = evaluate_proposal(report, effect_id, checkpoint)
        if change == "run":
            result["run_id"] = "foreign.run"
        elif change == "owner":
            result["owner_operation_id"] = "foreign"
        elif change == "checkpoint":
            result["checkpoint_digest"] = digest(b"foreign")
        else:
            result["failed_criterion_ids"] = ["proposal.valid"]
        return result
    _, app = fixture(tmp_path, evaluator=invalid)
    result, code = app.create_normalize_proposal(source_reference=app.reference(SOURCE))
    assert code != 0 and result["execution_outcome"] == "unknown", result
    assert app.journal.read()["human_requests"] == {}


def test_not_run_domain_criteria_remain_partial_and_cannot_be_approved(tmp_path):
    from vaultops.application.knowledge import evaluate_proposal

    def incomplete(report, effect_id, checkpoint):
        result = evaluate_proposal(report, effect_id, checkpoint)
        result["passed_criterion_ids"] = []
        result["not_run_criterion_ids"] = ["proposal.valid"]
        return result
    _, app = fixture(tmp_path, evaluator=incomplete)
    created = proposal(app)
    assert created["execution_outcome"] == "completed"
    assert created["acceptance_state"] == "partial"
    assert app.human_decision(response(app, created))[1] != 0
    observed, code = app.status()
    assert code == 0 and observed["snapshot"]["acceptance_state"] == "partial", observed


@pytest.mark.parametrize("version", [1, 2, 4])
def test_unknown_state_version_is_refused_before_even_a_lock_write(tmp_path, version):
    roots, app = fixture(tmp_path)
    value = app.journal.read()
    value["state_version"] = version
    app.journal.path.write_bytes(canonical(value))
    before = snapshot(roots.state)
    with pytest.raises(AdmissionError):
        KnowledgeApplication.from_roots(roots)
    assert snapshot(roots.state) == before


def test_traversal_symlink_and_system_scope_fail_before_domain_dispatch(tmp_path):
    roots, app = fixture(tmp_path)
    canary = tmp_path / "outside.md"
    canary.write_bytes(b"private canary")
    (roots.vault / "escape.md").symlink_to(canary)
    before = snapshot(roots.state)
    for source in ("../outside.md", "escape.md", "99_System/private.md"):
        result, code = app.create_normalize_proposal(source_path=source, expected_sha256="a" * 64)
        assert code != 0, result
    assert snapshot(roots.state) == before
    assert canary.read_bytes() == b"private canary"


@pytest.mark.parametrize("phase", ["intent_committed", "artifact_created"])
def test_interrupted_dispatch_reconciles_observed_bytes_without_retry(tmp_path, phase):
    def stop(observed):
        if observed == phase:
            raise RuntimeError("simulated interruption")
    roots, app = fixture(tmp_path, fault_hook=stop)
    before = (roots.vault / SOURCE).read_bytes()
    with pytest.raises(RuntimeError):
        app.create_normalize_proposal(source_reference=app.reference(SOURCE))
    restarted = KnowledgeApplication.from_roots(roots, clock=lambda: NOW)
    denied, code = restarted.create_normalize_proposal(source_reference=restarted.reference(SOURCE))
    assert code != 0 and denied["errors"][0]["code"] == "OUTCOME_UNKNOWN", denied
    result, code = restarted.recover()
    assert code == 0 and not result["dispatch_performed"], result
    assert result["observations"][0]["state"] == ("cancelled" if phase == "intent_committed" else "completed")
    assert len(list((roots.vault / "01_AI_Review/Pending").glob("*.md"))) == (0 if phase == "intent_committed" else 1)
    assert (roots.vault / SOURCE).read_bytes() == before
    assert restarted.journal.read()["outbox"][0]["transport_state"] == "unconfigured"


def test_runtime_loss_preserves_approval_receipt_and_unknown_marker(tmp_path):
    roots, app = fixture(tmp_path)
    created = proposal(app)
    assert app.human_decision(response(app, created))[1] == 0
    with app.journal.writer() as session:
        session.value["intents"]["uncertain.fixture"] = {"state": "unknown", "generation": 1, "content_digest": digest(b"unknown"), "fencing_token": "generation.g1"}
        session.commit(NOW)
    before = snapshot(roots.state)
    shutil.rmtree(roots.runtime)
    restarted = KnowledgeApplication.from_roots(roots, clock=lambda: NOW)
    observed, code = restarted.status()
    assert code == 0, observed
    assert observed["snapshot"]["execution_outcome"] == "unknown"
    assert observed["snapshot"]["acceptance_state"] == "unknown"
    assert snapshot(roots.state) == before


def test_stale_writer_cannot_commit_after_generation_or_previous_bytes_change(tmp_path):
    _roots, app = fixture(tmp_path)
    with app.journal.writer() as session:
        external = copy.deepcopy(session.value)
        external["generation"] += 1
        app.journal.path.write_bytes(canonical(external) + b"\n")
        with pytest.raises(AdmissionError, match="generation|bytes"):
            session.commit(NOW)
    assert app.journal.read()["generation"] == 2


def envelope():
    payload = {"query": "bounded status query"}
    value = {"contract_version": "2.0.0", "core_version": "0.16.1", "core_digest": PINS["core_digest"], "schema_version": "0.16.1", "semantic_version": "0.6.0", "semantic_digest": PINS["semantic_digest"], "action": "read", "classification": "internal", "hop_count": 0, "idempotency_key": "request.fixture", "issued_at": NOW.isoformat(), "message_id": "message.fixture", "message_kind": "information_request", "payload_digest": digest(canonical(payload)), "payload_reference_id": "payload.fixture", "payload_size_bytes": len(canonical(payload)), "payload_type": "information.query", "purpose": "status", "recipient_operation_id": "knowledgeos", "request_id": "request.fixture", "resource_id": "operation.knowledgeos", "resource_kind": "operation", "resource_owner_operation_id": "knowledgeos", "revision": 1, "scope": "status", "semantic_term_ids": [], "sender_operation_id": "hermestrace", "sender_principal_id": "hermestrace.local", "ttl_seconds": 43200}
    return value, payload


def test_gateway_durable_ack_replay_conflict_eligibility_and_expiry(tmp_path):
    _, app = fixture(tmp_path)
    gateway = KnowledgeGateway(app)
    caller = GatewayIdentity("hermestrace", "hermestrace.local", ("read",))
    request, payload = envelope()
    result = gateway.admit(request, payload, caller)
    assert result["admission_state"] == "admitted"
    assert result["execution_outcome"] == "not_started"
    assert result["acceptance_state"] == "not_evaluated"
    assert gateway.admit(request, payload, caller)["replayed"]
    before = app.journal.path.read_bytes()
    with pytest.raises(AdmissionError):
        gateway.admit({**request, "scope": "changed"}, payload, caller)
    ordinary = GatewayIdentity("ordinary", "ordinary.local", ("read",))
    with pytest.raises(AdmissionError):
        gateway.admit({**request, "sender_operation_id": ordinary.operation_id, "sender_principal_id": ordinary.principal_id}, payload, ordinary)
    with pytest.raises(AdmissionError):
        gateway.admit({**request, "issued_at": (NOW - timedelta(hours=13)).isoformat()}, payload, caller)
    assert app.journal.path.read_bytes() == before


@pytest.mark.parametrize("change", ["none", "stub", "pin", "payload", "expired", "semantic"])
def test_gateway_revalidates_the_full_prior_correlation_before_admission(tmp_path, change):
    _, app = fixture(tmp_path)
    request, query = envelope()
    prior = {**request, "sender_operation_id": "knowledgeos", "sender_principal_id": app.identity.actor_id,
             "recipient_operation_id": "hermestrace", "resource_owner_operation_id": "hermestrace", "resource_id": "operation.hermestrace"}
    record = {"envelope": prior, "payload": query, "caller": {"operation_id": "knowledgeos", "principal_id": app.identity.actor_id, "allowed_actions": ["read"]}}
    if change == "stub":
        record = {"request_id": prior["request_id"]}
    elif change == "pin":
        prior["core_digest"] = digest(b"foreign")
    elif change == "payload":
        record["payload"] = {"query": "changed"}
    elif change == "expired":
        prior["issued_at"] = (NOW - timedelta(hours=13)).isoformat()
    elif change == "semantic":
        prior["semantic_term_ids"] = ["foreign.unknown"]
    with app.journal.writer() as session:
        session.value["correlations"]["correlation.fixture"] = record
        session.commit(NOW)
    payload = {"resources": []}
    incoming = {**request, "message_kind": "correlated_response", "action": "export",
                "payload_type": "information.result", "payload_digest": digest(canonical(payload)),
                "payload_size_bytes": len(canonical(payload)), "correlation_id": "correlation.fixture"}
    gateway = KnowledgeGateway(app)
    caller = GatewayIdentity("hermestrace", "hermestrace.local", ("export",))
    before = snapshot(app.roots.state)
    if change == "none":
        result = gateway.admit(incoming, payload, caller)
        assert result["execution_outcome"] == "not_started"
    else:
        with pytest.raises(AdmissionError):
            gateway.admit(incoming, payload, caller)
        assert snapshot(app.roots.state) == before


def test_two_process_cli_and_mcp_ingress_share_one_artifact_and_receipt(tmp_path):
    roots, app = fixture(tmp_path)
    source_digest = app.reference(SOURCE)["content_digest"][7:]
    script = "from vaultops.cli import main; import sys; raise SystemExit(main(sys.argv[1:]))"
    cli = [sys.executable, "-c", script, "ai", "normalize", "--root", str(roots.control), "--source", SOURCE, "--expected-sha256", source_digest]
    mcp_script = "from pathlib import Path; from vaultops.mcp_server import _load_context; import json,sys; c=_load_context(Path(sys.argv[1])); r,n=c.services.create_normalize_proposal(source_reference=c.services.reference(sys.argv[2])); print(json.dumps(r)); raise SystemExit(n)"
    mcp = [sys.executable, "-c", mcp_script, str(roots.control), SOURCE]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda command: subprocess.run(command, capture_output=True, text=True, check=False, cwd=tmp_path), (cli, mcp)))
    for result in results:
        assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    assert len(app.journal.read()["receipts"]) == 1


def test_core_retention_keeps_dedupe_and_unresolved_after_detail_eviction(tmp_path):
    _, app = fixture(tmp_path)
    value = app.journal.empty()
    value["history"] = [{"sequence": i} for i in range(150)]
    value["gateway"]["receipt"] = {"recorded_at": NOW.isoformat(), "content_digest": digest(b"receipt")}
    value["gateway"]["old_detail"] = {"recorded_at": NOW.isoformat(), "content_digest": digest(b"old"), "detail_sequence": 1, "envelope": {"private": "detail"}}
    value["receipts"]["unknown"] = {"recorded_at": (NOW - timedelta(days=5)).isoformat(), "unresolved": True}
    app.journal.prune(value, NOW)
    assert len(value["history"]) == 100
    assert "receipt" in value["gateway"] and "unknown" in value["receipts"]
    assert "envelope" not in value["gateway"]["old_detail"]
    app.journal.prune(value, NOW + timedelta(hours=12))
    assert "receipt" not in value["gateway"] and "unknown" in value["receipts"]

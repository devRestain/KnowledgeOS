from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vaultops.mobile_offline import (
    CLEANUP_MINIMUM_AGE_DAYS,
    MAX_MARKDOWN_BYTES,
    MAX_SELECTED_TEXT_BYTES,
    OutboxConflictError,
    OutboxStore,
    build_outbox_payload,
    evaluate_git_transaction,
    load_shortcut_contract,
    scan_secret_patterns,
    stale_warning,
    validate_asset,
    validate_defer_source,
    validate_outbox_payload,
    validate_storage_policy,
    validate_text_input,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/c11_mobile_offline"
JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
CREATED_AT = "2026-09-01T15:42:12+09:00"
TARGET = "00_Inbox/Captures/2026/09/20260901-154212-iphone-550e8400.md"


def _payload(*, content: str = "원문\n", storage_policy: str = "github_allowed") -> dict[str, object]:
    return build_outbox_payload(
        job_id=JOB_ID,
        target_path=TARGET,
        created_at=CREATED_AT,
        input_kind="thought",
        storage_policy=storage_policy,
        content=content,
    )


def test_exportable_catalog_has_exactly_the_five_blueprint_shortcuts() -> None:
    catalog = load_shortcut_contract(CONTROL_ROOT)
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    observed = catalog["shortcuts"]
    expected = blueprint["shortcut_contracts"]
    assert [item["id"] for item in observed] == [item["id"] for item in expected]
    assert len(observed) == 5
    assert catalog["external_effects"] == "none"


def test_text_gate_enforces_utf8_controls_and_two_byte_limits() -> None:
    assert validate_text_input("a\tb\n")[1] == 4
    with pytest.raises(ValueError, match="valid UTF-8"):
        validate_text_input(b"\xff")
    with pytest.raises(ValueError, match="control character"):
        validate_text_input("safe\x01")
    with pytest.raises(ValueError, match="Markdown content"):
        validate_text_input("a" * (MAX_MARKDOWN_BYTES + 1))
    with pytest.raises(ValueError, match="selected text"):
        validate_text_input("a" * (MAX_SELECTED_TEXT_BYTES + 1), selected_text=True)


def test_storage_gate_blocks_confidential_and_secrets_but_allows_local_only() -> None:
    assert scan_secret_patterns("token: ghp_123456789012345678901234567890") == ("github_token", "credential_assignment")
    blocked = validate_storage_policy(
        "github_allowed",
        sensitivity="confidential",
        content="ordinary text",
    )
    assert blocked.code == "MOBILE_CONFIDENTIAL_GITHUB_FORBIDDEN"
    secret = validate_storage_policy(
        "github_allowed",
        content="token: ghp_123456789012345678901234567890",
    )
    assert secret.code == "MOBILE_SECRET_PATTERN_FOUND"
    local_only = validate_storage_policy(
        "local_only",
        sensitivity="confidential",
        content="token: ghp_123456789012345678901234567890",
    )
    assert local_only.passed


def test_asset_gate_handles_allowlist_budget_heic_and_forbidden_mime() -> None:
    assert validate_asset("image/jpeg", 1024).passed
    assert validate_asset("application/pdf", 10 * 1024 * 1024 + 1).code == "MOBILE_ASSET_PLACEHOLDER"
    assert validate_asset("image/heic", 1024).code == "MOBILE_HEIC_REQUIRES_REVALIDATION"
    converted = validate_asset(
        "image/heic",
        1024,
        converted_mime_type="image/jpeg",
        converted_size_bytes=2048,
    )
    assert converted.passed
    assert validate_asset("image/heic", 1024, placeholder=True).code == "MOBILE_ASSET_PLACEHOLDER"
    assert validate_asset("audio/mpeg", 10).code == "MOBILE_ASSET_FORBIDDEN_MIME"


def test_outbox_persists_canonical_payload_and_append_only_events(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "On My iPhone/KnowledgeHub-Recovery/Outbox")
    record = store.persist(_payload(), event_at=CREATED_AT)
    payload_path = store.root / JOB_ID / "input.json"
    original_payload_bytes = payload_path.read_bytes()
    assert record.payload_sha256
    assert payload_path.stat().st_mode & 0o777 == 0o600
    assert (store.root / JOB_ID / "events/0001-captured.json").is_file()

    store.append_event(JOB_ID, "vault_written", event_at=CREATED_AT)
    store.append_event(JOB_ID, "committed", event_at=CREATED_AT, reason="offline")
    assert payload_path.read_bytes() == original_payload_bytes
    assert [event["state"] for event in store.read(JOB_ID).events] == ["captured", "vault_written", "committed"]
    with pytest.raises(OutboxConflictError, match="already exists"):
        store.persist(_payload(), event_at=CREATED_AT)


def test_outbox_recovery_same_digest_is_noop_and_changed_digest_is_conflict(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox")
    payload = _payload()
    status, _ = store.recover(payload, event_at=CREATED_AT)
    assert status == "CREATED"
    status, record = store.recover(payload, event_at=CREATED_AT)
    assert status == "NO_OP"
    assert len(record.events) == 1
    changed = _payload(content="다른 원문\n")
    with pytest.raises(OutboxConflictError, match="different payload digest"):
        store.recover(changed, event_at=CREATED_AT)
    tampered = json.loads((store.root / JOB_ID / "input.json").read_text(encoding="utf-8"))
    tampered["content"] = "변조\n"
    with pytest.raises(ValueError, match="does not match"):
        validate_outbox_payload(tampered)
    secret_payload = _payload(content="token: ghp_123456789012345678901234567890", storage_policy="local_only")
    secret_payload["sensitivity_choice"] = "github_allowed"
    with pytest.raises(ValueError, match="bounded secret scan"):
        validate_outbox_payload(secret_payload)


def test_synthetic_failure_cases_survive_and_remain_pending(tmp_path: Path) -> None:
    cases = load_yaml_file(FIXTURE_ROOT / "recovery-cases.yaml")["cases"]
    for index, case in enumerate(cases):
        # Keep the fixture's failure model independent of the generated UUID.
        job_id = f"550e8400-e29b-41d4-a716-44665544{index:04d}"
        target = f"00_Inbox/Captures/2026/09/20260901-154212-iphone-{job_id[:8]}.md"
        payload = build_outbox_payload(
            job_id=job_id,
            target_path=target,
            created_at=CREATED_AT,
            input_kind="thought",
            storage_policy="github_allowed",
            content=f"fixture {case['id']}\n",
        )
        store = OutboxStore(tmp_path / str(index))
        store.persist(payload, event_at=CREATED_AT)
        for state in case["initial_events"][1:]:
            store.append_event(job_id, state, event_at=CREATED_AT, reason=case["failure_reason"])
        if case["recovery"] == "same_id_same_digest_no_op":
            status, _ = store.recover(payload, event_at=CREATED_AT)
            assert status == "NO_OP"
        elif case["recovery"] in {"payload_and_events_survive", "local_commit_and_outbox_survive", "payload_and_event_survive"}:
            assert store.read(job_id).payload["content"].startswith("fixture")
        else:
            changed = dict(payload)
            changed["content"] = "changed retry\n"
            changed["content_bytes"] = len(b"changed retry\n")
            changed["content_sha256"] = hashlib.sha256(b"changed retry\n").hexdigest()
            with pytest.raises(OutboxConflictError):
                store.recover(changed, event_at=CREATED_AT)
        assert store.pending()[0].job_id == job_id


def test_cleanup_requires_ack_age_and_interactive_confirmation(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox")
    store.persist(_payload(), event_at=CREATED_AT)
    now = datetime.fromisoformat(CREATED_AT) + timedelta(days=CLEANUP_MINIMUM_AGE_DAYS + 1)
    assert store.cleanup(JOB_ID, now=now, interactive_confirmation=True).code == "OUTBOX_ACK_REQUIRED"
    store.append_event(JOB_ID, "vault_written", event_at=CREATED_AT)
    store.append_event(JOB_ID, "committed", event_at=CREATED_AT)
    store.append_event(JOB_ID, "pushed", event_at=CREATED_AT)
    store.append_event(JOB_ID, "remote_observed", event_at=CREATED_AT)
    young = datetime.fromisoformat(CREATED_AT) + timedelta(days=CLEANUP_MINIMUM_AGE_DAYS - 1)
    assert store.cleanup(JOB_ID, now=young, interactive_confirmation=True).code == "OUTBOX_MINIMUM_AGE_REQUIRED"
    assert store.cleanup(JOB_ID, now=now, interactive_confirmation=False).code == "OUTBOX_INTERACTIVE_CONFIRMATION_REQUIRED"
    assert store.cleanup(JOB_ID, now=now, interactive_confirmation=True).code == "OUTBOX_CLEANED"
    assert not (store.root / JOB_ID).exists()


def test_stale_warning_is_separate_from_cleanup_authorization(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox")
    record = store.persist(_payload(), event_at=CREATED_AT)
    now = datetime.fromisoformat(CREATED_AT) + timedelta(days=30)
    assert stale_warning(record, now=now).code == "OUTBOX_STALE_WARNING"


def test_git_gate_disables_automatic_actions_for_uninspectable_or_dirty_state() -> None:
    blocked = evaluate_git_transaction(
        repository_ok=True,
        remote_ok=True,
        branch_ok=True,
        root_sentinel_ok=True,
        repository_state="dirty",
        preexisting_staged_paths=(),
        staged_paths=(TARGET,),
        expected_new_paths=(TARGET,),
        action_exposes_staged_set=True,
    )
    assert not blocked.passed
    assert blocked.details["pull_allowed"] is False
    unavailable = evaluate_git_transaction(
        repository_ok=True,
        remote_ok=True,
        branch_ok=True,
        root_sentinel_ok=True,
        repository_state="clean",
        preexisting_staged_paths=(),
        staged_paths=None,
        expected_new_paths=(TARGET,),
        action_exposes_staged_set=False,
    )
    assert unavailable.code == "MOBILE_AUTO_COMMIT_DISABLED"
    allowed = evaluate_git_transaction(
        repository_ok=True,
        remote_ok=True,
        branch_ok=True,
        root_sentinel_ok=True,
        repository_state="clean",
        preexisting_staged_paths=(),
        staged_paths=(TARGET,),
        expected_new_paths=(TARGET,),
        action_exposes_staged_set=True,
    )
    assert allowed.passed
    assert allowed.details["pull_allowed"] is False


def test_defer_requires_a_readable_committed_blob_and_matching_digest() -> None:
    blob = b"---\ntype: capture\n---\n"
    digest = hashlib.sha256(blob).hexdigest()
    assert validate_defer_source(
        tracked=True,
        expected_head=True,
        index_clean=True,
        worktree_clean=True,
        committed_blob=blob,
        expected_blob_sha256=digest,
    ).passed
    assert validate_defer_source(
        tracked=True,
        expected_head=True,
        index_clean=False,
        worktree_clean=True,
        committed_blob=blob,
        expected_blob_sha256=digest,
    ).code == "DEFER_SOURCE_DIRTY"
    assert validate_defer_source(
        tracked=True,
        expected_head=True,
        index_clean=True,
        worktree_clean=True,
        committed_blob=None,
        expected_blob_sha256=digest,
    ).code == "DEFER_COMMITTED_BLOB_UNREADABLE"

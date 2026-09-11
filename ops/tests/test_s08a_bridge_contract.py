from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vaultops.bridge_contract import (
    ALLOWED_TRANSITIONS,
    BRIDGE_STATES,
    RESPONSE_STATUSES,
    build_bridge_request_schema,
    build_bridge_response_schema,
    build_root_sentinel_schema,
    canonicalize_github_remote,
    remote_identity_sha256,
    render_fixture_response,
    validate_append_only_history,
    validate_bridge_request,
    validate_bridge_response,
    validate_remote_identity,
    validate_root_sentinel,
    validate_state_mapping,
    validate_transition,
    write_fixture_response,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/s08a_bridge_contract"


def _blueprint() -> dict[str, object]:
    value = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    assert isinstance(value, dict)
    return value


def _request(pipeline_kind: str = "triage") -> dict[str, object]:
    return {
        "schema_version": 1,
        "job_id": "8b18f63f-6ca2-4b84-b5d8-8c3d85cfa2b1",
        "created_at": "2026-09-07T15:42:12+09:00",
        "device_id": "wc-phone-01",
        "pipeline_kind": pipeline_kind,
        "source": {
            "path": "00_Inbox/Captures/2026/09/example.md",
            "blob_sha256": "a" * 64,
            "locator": "#^raw-1",
            "fragment_sha256": "c" * 64,
        },
        "target": None,
        "parameters": {},
        "route_id": "codex_chatgpt_login",
        "mode": "proposal",
    }


def _response(status: str = "needs_review") -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "job_id": "8b18f63f-6ca2-4b84-b5d8-8c3d85cfa2b1",
        "sequence": 1,
        "status": status,
        "event_at": "2026-09-07T16:02:10+09:00",
        "completed_at": "2026-09-07T16:02:10+09:00",
        "request_sha256": "d" * 64,
        "request_commit": "0123456789abcdef0123456789abcdef01234567",
        "job_receipt_sha256": "e" * 64,
        "output_schema_sha256": "f" * 64,
        "warnings": [],
    }
    if status == "needs_review":
        result.update(
            {
                "proposal_id": "e2a1d660-3e18-4a92-bf2b-f8c2480a8861",
                "proposal_path": "01_AI_Review/Pending/2026/09/AI proposal.md",
                "proposal_sha256": "b" * 64,
            }
        )
    return result


def test_bridge_shape_has_the_blueprint_state_machine_and_mapping() -> None:
    assert len(BRIDGE_STATES) == 17
    assert set(ALLOWED_TRANSITIONS) == set(BRIDGE_STATES) - {"mobile_visible"}
    assert len(RESPONSE_STATUSES) == 11
    assert validate_transition("committed_request", "ingested").passed
    invalid = validate_transition("failed", "running")
    assert not invalid.passed
    assert invalid.issues[0].code == "BRIDGE_TRANSITION_NOT_ALLOWED"
    unknown = validate_transition("unknown", "running")
    assert {issue.code for issue in unknown.issues} == {"BRIDGE_UNKNOWN_STATE"}
    assert validate_state_mapping().passed
    incomplete = validate_state_mapping({"queued": {"runtime": "queue", "public_status": "queued"}})
    assert "BRIDGE_MAPPING_MISSING" in {issue.code for issue in incomplete.issues}
    broken_mapping = {"queued": {"runtime": "queue", "public_status": "unknown"}}
    broken = validate_state_mapping(broken_mapping)
    assert not broken.passed
    assert "BRIDGE_UNKNOWN_PUBLIC_STATUS" in {issue.code for issue in broken.issues}
    assert validate_append_only_history([{"path": "requests/one.json", "action": "add"}]).passed
    changed = validate_append_only_history(
        [
            {"path": "requests/one.json", "action": "add"},
            {"path": "requests/one.json", "action": "update"},
        ]
    )
    assert {issue.code for issue in changed.issues} == {"BRIDGE_CREATE_ONLY_VIOLATION"}


def test_remote_identity_accepts_ssh_and_https_but_hashes_one_canonical_form() -> None:
    expected = "github.com/example-owner/example-repo.git\n"
    for remote in (
        "git@example-owner.invalid:ignored/repo.git",
        "git@github.com:Example-Owner/Example-Repo.git",
        "ssh://git@github.com/Example-Owner/Example-Repo",
        "https://github.com/Example-Owner/Example-Repo.git",
        "github.com/Example-Owner/Example-Repo",
    ):
        if "invalid" not in remote:
            assert canonicalize_github_remote(remote) == expected
    assert remote_identity_sha256("https://github.com/Example-Owner/Example-Repo") == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()
    digest = remote_identity_sha256("https://github.com/Example-Owner/Example-Repo")
    assert validate_remote_identity("git@github.com:Example-Owner/Example-Repo.git", digest).passed
    assert not validate_remote_identity("https://github.com/other/repo.git", digest).passed

    for remote in (
        "https://token@github.com/owner/repo.git",
        "https://github.com/owner/repo.git?token=secret",
        "https://github.com/owner/repo.git#fragment",
        "https://gitlab.com/owner/repo.git",
        "https://github.com/owner/repo/extra.git",
    ):
        with pytest.raises(ValueError):
            canonicalize_github_remote(remote)


def test_request_and_response_schemas_are_strict_and_action_aware() -> None:
    blueprint = _blueprint()
    assert build_bridge_request_schema(blueprint)["additionalProperties"] is False
    assert build_bridge_response_schema(blueprint)["oneOf"]
    assert validate_bridge_request(_request(), blueprint).passed

    extra = _request()
    extra["content"] = "must never cross the bridge"
    rejected = validate_bridge_request(extra, blueprint)
    assert not rejected.passed

    response = _response()
    assert validate_bridge_response(response, blueprint).passed
    missing_terminal = dict(response)
    del missing_terminal["output_schema_sha256"]
    assert not validate_bridge_response(missing_terminal, blueprint).passed


def test_checked_in_synthetic_fixtures_pass_the_trusted_contracts() -> None:
    blueprint = _blueprint()
    request = json.loads((FIXTURE_ROOT / "request-triage.json").read_text(encoding="utf-8"))
    response = json.loads((FIXTURE_ROOT / "response-needs-review.json").read_text(encoding="utf-8"))
    sentinel = json.loads((FIXTURE_ROOT / "sentinel-valid.json").read_text(encoding="utf-8"))
    assert validate_bridge_request(request, blueprint).passed
    assert validate_bridge_response(response, blueprint).passed
    assert validate_root_sentinel(sentinel, blueprint).passed


def test_root_sentinel_schema_is_allowlisted_and_does_not_create_a_sentinel() -> None:
    blueprint = _blueprint()
    sentinel = {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-root-v1",
        "vault_uuid": "8b18f63f-6ca2-4b84-b5d8-8c3d85cfa2b1",
        "canonical_vault_name": "KnowledgeHub",
        "remote_identity_sha256": "a" * 64,
        "expected_branch": "main",
    }
    assert validate_root_sentinel(sentinel, blueprint).passed
    assert validate_root_sentinel(
        sentinel,
        blueprint,
        expected={"expected_branch": "release"},
    ).issues[0].code == "BRIDGE_SENTINEL_BINDING_MISMATCH"
    extra = dict(sentinel)
    extra["remote"] = "https://github.com/owner/repo.git"
    assert not validate_root_sentinel(extra, blueprint).passed
    assert build_root_sentinel_schema(blueprint)["additionalProperties"] is False


def test_fixture_response_is_byte_stable_create_only_and_root_restricted(tmp_path: Path) -> None:
    blueprint = _blueprint()
    response = _response()
    first = render_fixture_response(response, blueprint)
    second = render_fixture_response(response, blueprint)
    assert first == second
    destination = tmp_path / "fixture" / "responses/2026/09/job/0001-needs_review.json"
    write_fixture_response(response, blueprint, destination, tmp_path / "fixture")
    assert destination.read_bytes() == first
    with pytest.raises(FileExistsError):
        write_fixture_response(response, blueprint, destination, tmp_path / "fixture")
    with pytest.raises(ValueError):
        write_fixture_response(response, blueprint, tmp_path / "outside.json", tmp_path / "fixture")

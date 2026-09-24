from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vaultops.cli import main
from vaultops.thin_client import (
    ThinClientError,
    build_citation_open_action,
    build_diff_preview,
    build_review_decision,
    build_thin_client_request,
    dispatch_thin_client,
    render_markdown_fallback,
    validate_thin_client_request,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]

JOB_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"
TOKEN = "c41-test-token"
HASH_1 = "1" * 64
HASH_2 = "2" * 64


def _request() -> dict[str, object]:
    return build_thin_client_request(
        job_id=JOB_ID,
        request_id=REQUEST_ID,
        question="What changed in the bounded review fixture?",
        scope={
            "kind": "selection",
            "path": "40_Knowledge/Notes/C41 Fixture.md",
            "locator": "# Evidence",
            "content_sha256": HASH_1,
            "selection_sha256": HASH_2,
            "selection_byte_length": 18,
        },
        broker_endpoint="http://127.0.0.1:17900/broker",
        authorization_token=TOKEN,
        policy_sha256=HASH_1,
        privacy_policy_sha256=HASH_2,
        index_generation_id="c41-fixture",
        created_at="2026-09-24T00:00:00+00:00",
    )


def _candidate() -> dict[str, object]:
    excerpt = "The bounded review fixture remains proposal-only."
    return {
        "note_id": "note-c41-001",
        "path": "40_Knowledge/Notes/C41 Fixture.md",
        "content_hash": HASH_1,
        "chunk_id": "chunk-c41-001",
        "chunk_hash": HASH_2,
        "chunk_locator": "# Evidence",
        "index_generation_id": "c41-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
        "excerpt_byte_length": len(excerpt.encode()),
        "eligibility_class": "local_eligible",
    }


def test_c41_request_is_exactly_bound_to_loopback_broker_and_scope() -> None:
    request = _request()

    validated = validate_thin_client_request(request)

    assert validated["broker"]["transport"] == "loopback"
    assert validated["broker"]["endpoint"].startswith("http://127.0.0.1:")
    assert validated["scope"]["kind"] == "selection"
    assert validated["proposal_only"] is True
    assert validated["canonical_apply_allowed"] is False
    assert "c41-test-token" not in json.dumps(validated)


def test_c41_dispatch_calls_only_authenticated_broker_and_stays_proposal_only() -> None:
    request = _request()
    calls: list[dict[str, object]] = []

    def broker(value: dict[str, object]) -> dict[str, object]:
        calls.append(value)
        return {
            "schema_version": 1,
            "capability": "C41",
            "request_sha256": value["request_sha256"],
            "brokered": True,
            "direct_provider_called": False,
            "content_digest_rechecked": True,
            "policy_digest_rechecked": True,
            "index_digest_rechecked": True,
            "mutation_performed": False,
            "canonical_apply_allowed": False,
            "proposal_only": True,
            "status": "completed",
            "result": {"summary": "review this proposal"},
        }

    report = dispatch_thin_client(request, broker, authorization_token=TOKEN)

    assert len(calls) == 1
    assert report["status"] == "PASS"
    assert report["broker_called"] is True
    assert report["direct_provider_called"] is False
    assert report["vault_mutation_performed"] is False
    assert report["canonical_apply_allowed"] is False
    assert report["result"]["summary"] == "review this proposal"


def test_c41_rejects_wrong_token_direct_provider_and_non_loopback_endpoint() -> None:
    request = _request()

    with pytest.raises(ThinClientError, match="authorization token"):
        dispatch_thin_client(request, lambda _: {}, authorization_token="wrong-token")

    def unsafe_broker(value: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "capability": "C41",
            "request_sha256": value["request_sha256"],
            "brokered": True,
            "direct_provider_called": True,
            "content_digest_rechecked": True,
            "policy_digest_rechecked": True,
            "index_digest_rechecked": True,
            "mutation_performed": False,
            "canonical_apply_allowed": False,
            "proposal_only": True,
            "status": "completed",
            "result": {},
        }

    with pytest.raises(ThinClientError, match="broker boundary"):
        dispatch_thin_client(request, unsafe_broker, authorization_token=TOKEN)

    with pytest.raises(ThinClientError, match="loopback"):
        build_thin_client_request(
            job_id=JOB_ID,
            request_id=REQUEST_ID,
            question="question",
            scope={
                "kind": "current_note",
                "path": "40_Knowledge/Notes/C41 Fixture.md",
                "content_sha256": HASH_1,
            },
            broker_endpoint="http://192.168.1.10:17900/broker",
            authorization_token=TOKEN,
            policy_sha256=HASH_1,
            privacy_policy_sha256=HASH_2,
            index_generation_id="c41-fixture",
        )


def test_c41_citation_diff_review_and_markdown_fallback_are_bounded() -> None:
    request = _request()
    candidate = _candidate()
    citation = {
        key: candidate[key]
        for key in (
            "note_id",
            "path",
            "content_hash",
            "chunk_id",
            "chunk_hash",
            "chunk_locator",
            "index_generation_id",
            "excerpt",
            "excerpt_sha256",
            "excerpt_byte_length",
        )
    }

    citation_action = build_citation_open_action(citation, [candidate])
    diff = build_diff_preview(citation["path"], "status: draft\n", "status: review\n")
    decision = build_review_decision(HASH_2, "approve")
    markdown = render_markdown_fallback(
        request,
        result_summary="A human must review the proposal.",
        citation_actions=[citation_action],
        diff_preview=diff,
    )

    assert citation_action["exact_source"] is True
    assert citation_action["canonical_apply_allowed"] is False
    assert diff["bounded"] is True
    assert diff["apply_authority"] == "c19_only"
    assert decision["decision"] == "approve"
    assert decision["canonical_apply_requested"] is False
    assert "Presentation fallback only" in markdown
    assert "Approve or reject explicitly" in markdown


def test_c41_rejects_citation_drift_before_open_action() -> None:
    candidate = _candidate()
    citation = {
        key: candidate[key]
        for key in (
            "note_id",
            "path",
            "content_hash",
            "chunk_id",
            "chunk_hash",
            "chunk_locator",
            "index_generation_id",
            "excerpt",
            "excerpt_sha256",
            "excerpt_byte_length",
        )
    }
    citation["excerpt"] = "stale bytes"

    with pytest.raises(ThinClientError, match="digest"):
        build_citation_open_action(citation, [candidate])


def test_c41_static_fixture_is_executable_through_plugin_free_cli(tmp_path: Path, capsys) -> None:
    request = json.loads(
        (CONTROL_ROOT / "ops/tests/fixtures/c41_thin_client/request.json").read_text(encoding="utf-8")
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    exit_code = main(
        [
            "ai",
            "client",
            "--request-file",
            str(request_path),
            "--markdown",
            "--root",
            str(tmp_path),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["markdown_fallback_available"] is True
    assert "Presentation fallback only" in report["markdown"]

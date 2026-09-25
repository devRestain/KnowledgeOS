from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

from vaultops.frozen_proposals import promote_frozen_triage_proposal
from vaultops.proposals import apply_proposal, approve_proposal, review_proposals
from vaultops.provider_broker import _triage_output
from vaultops.provider_contract import (
    build_frozen_context,
    build_identity_receipt,
    build_provider_request,
    build_provider_response,
    canonical_json_bytes,
    write_context_envelope,
    write_provider_request,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "KnowledgeHub",
            "runtime",
        ),
    )
    fixture = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
    shutil.copytree(fixture, root / "KnowledgeHub")
    for relative in (
        "01_AI_Review/Pending",
        "01_AI_Review/Resolved",
        "01_AI_Review/Rejected",
    ):
        (root / "KnowledgeHub" / relative).mkdir(parents=True, exist_ok=True)
    return root


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(0o600)


def _job(root: Path) -> tuple[str, str, dict[str, Any]]:
    job_id = str(uuid.uuid4())
    source_path = "runtime/provider-fixtures/e05-unit.md"
    source_hash = "e" * 64
    excerpt = "Frozen evidence remains proposal-only in the unit test."
    candidate: dict[str, Any] = {
        "note_id": "e05-unit-note",
        "path": source_path,
        "content_hash": source_hash,
        "chunk_hash": source_hash,
        "chunk_id": "e05-unit-chunk",
        "chunk_locator": "# Evidence",
        "index_generation_id": "e05-unit-generation",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": "local_eligible",
        "channel": "exact",
        "rank": 1,
        "retrieval_reason": "E05 frozen promotion test",
    }
    candidate["candidate_sha256"] = _digest(canonical_json_bytes(candidate))
    source = {
        "note_id": candidate["note_id"],
        "path": source_path,
        "content_hash": source_hash,
        "chunk_hash": source_hash,
        "locator": "# Evidence",
    }
    now = "2026-09-25T00:00:00+00:00"
    schema_path = "ops/schemas/triage-result.schema.json"
    context = build_frozen_context(
        job_id=job_id,
        action="triage",
        prompt="Return one bounded triage proposal.",
        output_schema={"path": schema_path, "sha256": _digest((root / schema_path).read_bytes())},
        policy_decision={
            "decision": "allow_local",
            "policy_sha256": "1" * 64,
            "privacy_policy_sha256": "2" * 64,
            "scope": "e05-unit",
            "authorization_sha256": None,
        },
        frozen_candidates=[candidate],
        index_generation_id="e05-unit-generation",
        source_hashes=[source],
        provider={
            "route": "local:gemma4",
            "model_tag": "gemma4:12b",
            "model_digest": "a" * 64,
            "ollama_version": "0.33.3",
        },
        created_at=now,
    )
    context_report, context_code = write_context_envelope(root, context)
    assert context_code == 0, context_report
    request = build_provider_request(context, created_at=now)
    request_report, request_code = write_provider_request(root, request)
    assert request_code == 0, request_report
    output = _triage_output(context)
    response = build_provider_response(
        request,
        status="completed",
        output=output,
        received_at=now,
        completed_at=now,
    )
    response_path = root / "runtime/runs" / job_id / "response.json"
    _write_private(response_path, response)
    receipt = build_identity_receipt(
        request,
        response,
        provider_called=True,
        created_at=now,
    )
    _write_private(root / "runtime/runs" / job_id / "receipts/provider-receipt.json", receipt)
    candidate_id = output["proposal"]["candidates"][0]["candidate_id"]
    return job_id, candidate_id, response


def test_frozen_triage_promotes_through_c19_and_replays_safely(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, candidate_id, _response = _job(root)

    promoted, promote_code = promote_frozen_triage_proposal(
        root,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    assert promote_code == 0, promoted
    assert promoted["status"] == "PASS"
    proposal_path = promoted["proposal_path"]
    proposal_hash = promoted["proposal_sha256"]
    target_path = promoted["target_path"]
    assert target_path == "40_Knowledge/Ideas/Frozen evidence remains proposal-only in the unit test..md"
    assert not (root / "KnowledgeHub" / target_path).exists()

    replay, replay_code = promote_frozen_triage_proposal(
        root,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    assert replay_code == 0, replay
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True

    reviewed, review_code = review_proposals(root, proposal_path=proposal_path)
    assert review_code == 0, reviewed
    assert reviewed["status"] == "PASS"
    approved, approve_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved
    applied, apply_code = apply_proposal(root, proposal_path=proposal_path)
    assert apply_code == 0, applied
    assert applied["status"] == "PASS"
    assert (root / "KnowledgeHub" / target_path).is_file()
    assert (root / "KnowledgeHub" / applied["closed_path"]).is_file()


def test_frozen_runtime_drift_blocks_review_without_target_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, candidate_id, response = _job(root)
    promoted, promote_code = promote_frozen_triage_proposal(
        root,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    assert promote_code == 0, promoted
    response["warnings"] = ["changed after promotion"]
    response_path = root / "runtime/runs" / job_id / "response.json"
    _write_private(response_path, response)

    reviewed, review_code = review_proposals(root, proposal_path=promoted["proposal_path"])
    assert review_code == 30
    assert reviewed["status"] == "FAIL"
    assert reviewed["errors"][0]["code"] == "E05_RUNTIME_SOURCE_DRIFT"
    assert not (root / "KnowledgeHub" / promoted["target_path"]).exists()

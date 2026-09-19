from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultops.provider_contract import (
    FROZEN_CONTEXT_SCHEMA_PATH,
    LIMITS,
    PROVIDER_FAILURE_SCHEMA_PATH,
    PROVIDER_RECEIPT_SCHEMA_PATH,
    PROVIDER_REQUEST_SCHEMA_PATH,
    PROVIDER_RESPONSE_SCHEMA_PATH,
    REMOTE_AUTHORIZATION_SCHEMA_PATH,
    ProviderContractError,
    build_frozen_context,
    build_identity_receipt,
    build_provider_failure,
    build_provider_request,
    build_provider_response,
    build_remote_authorization,
    canonical_json_bytes,
    provider_schema,
    read_context_envelope,
    schema_bytes,
    write_context_envelope,
    write_provider_request,
)
from vaultops.schema_export import export_schema_artifacts

CONTROL_ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "123e4567-e89b-42d3-a456-426614174000"
REQUEST_ID = "223e4567-e89b-42d3-a456-426614174000"
CREATED_AT = "2026-09-18T10:00:00+09:00"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(*, eligibility: str = "local_eligible", rank: int = 1) -> dict[str, object]:
    excerpt = "A frozen, byte-addressable excerpt."
    candidate: dict[str, object] = {
        "note_id": "note-c31-001",
        "path": "40_Knowledge/Notes/C31 Fixture.md",
        "content_hash": "1" * 64,
        "chunk_id": "chunk-c31-001",
        "chunk_hash": "2" * 64,
        "chunk_locator": "# Fixture",
        "index_generation_id": "generation-c31-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": eligibility,
        "channel": "lexical",
        "rank": rank,
        "retrieval_reason": "Exact fixture match",
    }
    candidate["candidate_sha256"] = _digest(
        canonical_json_bytes(candidate).decode("utf-8")
    )
    return candidate


def _provider(route: str = "local:baseline") -> dict[str, object]:
    return {
        "route": route,
        "model_tag": "gemma4:12b",
        "model_digest": "6" * 64,
        "ollama_version": "0.11.0",
    }


def _context(
    *,
    prompt: str = "Prepare a reviewed proposal from the frozen evidence.",
    provider: dict[str, object] | None = None,
    eligibility: str = "local_eligible",
    job_id: str = JOB_ID,
) -> dict[str, object]:
    return build_frozen_context(
        job_id=job_id,
        action="answer",
        prompt=prompt,
        output_schema={
            "path": "ops/schemas/answer.schema.json",
            "sha256": "5" * 64,
        },
        policy_decision={
            "decision": "allow_local" if (provider or _provider())["route"].startswith("local:") else "allow_remote",
            "policy_sha256": "3" * 64,
            "privacy_policy_sha256": "4" * 64,
            "scope": "c31-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[_candidate(eligibility=eligibility)],
        index_generation_id="generation-c31-fixture",
        source_hashes=[
            {
                "note_id": "note-c31-001",
                "path": "40_Knowledge/Notes/C31 Fixture.md",
                "content_hash": "1" * 64,
                "chunk_hash": "2" * 64,
                "locator": "# Fixture",
            }
        ],
        provider=provider or _provider(),
        created_at=CREATED_AT,
    )


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("request", PROVIDER_REQUEST_SCHEMA_PATH),
        ("response", PROVIDER_RESPONSE_SCHEMA_PATH),
        ("receipt", PROVIDER_RECEIPT_SCHEMA_PATH),
        ("failure", PROVIDER_FAILURE_SCHEMA_PATH),
        ("authorization", REMOTE_AUTHORIZATION_SCHEMA_PATH),
        ("context", FROZEN_CONTEXT_SCHEMA_PATH),
    ],
)
def test_c31_schemas_are_strict_and_zero_diff_generated(kind: str, path: str) -> None:
    schema = provider_schema(kind)

    assert schema["additionalProperties"] is False
    assert list(Draft202012Validator.check_schema(schema) or []) == []
    assert (CONTROL_ROOT / path).read_bytes() == schema_bytes(schema)


def test_c31_schema_export_binds_the_blueprint_contract() -> None:
    result = export_schema_artifacts(CONTROL_ROOT, check=True)

    assert result.passed, result.report
    c31 = [item for item in result.report["artifacts"] if item["first_capability"] == "C31"]
    assert len(c31) == 6
    assert {item["status"] for item in c31} == {"PASS"}


def test_c31_context_is_private_immutable_and_replay_safe(tmp_path: Path) -> None:
    context = _context()

    report, exit_code = write_context_envelope(tmp_path, context)

    assert exit_code == 0
    assert report["write"] == "CREATED"
    context_path = tmp_path / "runtime/runs" / JOB_ID / "context.json"
    assert stat.S_IMODE(context_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(context_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(context_path.parent.parent.stat().st_mode) == 0o700
    assert read_context_envelope(tmp_path, job_id=JOB_ID) == context

    replay_report, replay_exit_code = write_context_envelope(tmp_path, context)
    assert replay_exit_code == 0
    assert replay_report["write"] == "NO_OP"

    changed = _context(prompt="Prepare a different reviewed proposal.")
    conflict_report, conflict_exit_code = write_context_envelope(tmp_path, changed)
    assert conflict_exit_code == 30
    assert conflict_report["status"] == "CONFLICT"
    assert context_path.read_bytes() == canonical_json_bytes(context)


def test_c31_request_response_receipt_and_failure_keep_raw_payload_private(tmp_path: Path) -> None:
    context = _context()
    context_report, context_exit_code = write_context_envelope(tmp_path, context)
    assert context_exit_code == 0, context_report

    request = build_provider_request(
        context,
        request_id=REQUEST_ID,
        created_at=CREATED_AT,
    )
    request_report, request_exit_code = write_provider_request(tmp_path, request)
    assert request_exit_code == 0, request_report
    request_bytes = canonical_json_bytes(request)
    assert b"Prepare a reviewed proposal" not in request_bytes
    assert b"frozen_candidates" not in request_bytes

    response = build_provider_response(
        request,
        status="completed",
        output={"proposal": "Untrusted provider output"},
        received_at=CREATED_AT,
        completed_at="2026-09-18T10:00:03+09:00",
    )
    receipt = build_identity_receipt(
        request,
        response,
        provider_called=True,
        created_at="2026-09-18T10:00:04+09:00",
    )
    receipt_bytes = canonical_json_bytes(receipt)
    assert b"Untrusted provider output" not in receipt_bytes
    assert b"Prepare a reviewed proposal" not in receipt_bytes
    assert b'"text"' not in receipt_bytes
    assert receipt["vault_mutation_performed"] is False
    assert receipt["canonical_apply_allowed"] is False

    failure = build_provider_failure(
        job_id=JOB_ID,
        phase="output",
        code="OUTPUT_SCHEMA_INVALID",
        message="Provider output is untrusted and did not satisfy the output schema",
        retryable=False,
        request_sha256=request["request_sha256"],
        context_sha256=context["context_sha256"],
        provider=context["provider"],
        provider_called=True,
    )
    assert failure["raw_payload_persisted"] is False
    assert b"Untrusted provider output" not in canonical_json_bytes(failure)


def test_c31_bindings_reject_privacy_drift_and_oversized_input() -> None:
    with pytest.raises(ProviderContractError) as privacy_error:
        _context(provider=_provider("openai_api_relay"), eligibility="local_eligible")
    assert privacy_error.value.code == "C31_PRIVACY_DENIED"

    with pytest.raises(ProviderContractError) as prompt_error:
        _context(prompt="p" * (LIMITS["max_prompt_bytes"] + 1))
    assert prompt_error.value.code == "C31_PROMPT_TOO_LARGE"

    remote_context = _context(
        provider=_provider("openai_api_relay"),
        eligibility="remote_eligible",
    )
    assert remote_context["provider"]["route"] == "openai_api_relay"


def test_c31_remote_authorization_is_one_job_and_digest_bound() -> None:
    context = _context(
        provider=_provider("openai_api_relay"),
        eligibility="remote_eligible",
    )
    request = build_provider_request(
        context,
        request_id=REQUEST_ID,
        created_at=CREATED_AT,
    )

    authorization = build_remote_authorization(
        request,
        actor="test-operator",
        authorized_at=CREATED_AT,
        expires_at="2026-09-18T10:01:00+09:00",
    )

    assert authorization["job_id"] == JOB_ID
    assert authorization["scope"] == "one_job"
    assert authorization["tty_confirmed"] is True
    assert authorization["binds"]["context_sha256"] == context["context_sha256"]
    assert authorization["binds"]["request_sha256"] == request["request_sha256"]


def test_c31_provider_output_is_json_serializable() -> None:
    context = _context()
    request = build_provider_request(context, request_id=REQUEST_ID, created_at=CREATED_AT)
    response = build_provider_response(
        request,
        status="completed",
        output={"proposal": "untrusted"},
        received_at=CREATED_AT,
        completed_at=CREATED_AT,
    )

    json.dumps(response, ensure_ascii=False, sort_keys=True)

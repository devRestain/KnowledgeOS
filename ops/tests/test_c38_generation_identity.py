from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultops.e02 import generation_profile
from vaultops.generation_identity import (
    CONTRACT_ID,
    DEFAULT_STATES,
    GENERATION_MODEL_TAG,
    GenerationIdentityError,
    build_generation_identity,
    build_generation_identity_from_c31,
    canonical_generation_profile,
    generation_identity_schema,
    generation_inference_options,
    read_generation_identity,
    validate_generation_identity,
    write_generation_identity,
)
from vaultops.local_models import LOCAL_MODEL_DECLARATION
from vaultops.provider_contract import (
    DEFAULT_INFERENCE_OPTIONS,
    build_frozen_context,
    build_provider_request,
    canonical_json_bytes,
    write_context_envelope,
    write_provider_request,
)
from vaultops.schema_export import export_schema_artifacts

CONTROL_ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "123e4567-e89b-42d3-a456-426614174000"
CREATED_AT = "2026-09-20T10:00:00+09:00"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _policy() -> dict[str, object]:
    return {
        "decision": "allow_local",
        "policy_sha256": "1" * 64,
        "privacy_policy_sha256": "2" * 64,
        "scope": "c38-test",
        "authorization_sha256": None,
    }


def _frozen_evidence() -> dict[str, object]:
    value: dict[str, object] = {
        "context_sha256": "3" * 64,
        "source_hashes_sha256": "4" * 64,
        "candidate_set_sha256": "5" * 64,
        "index_generation_id": "generation-c38-fixture",
        "projection_generation_id": "projection-c38-fixture",
    }
    value["frozen_evidence_sha256"] = _digest(canonical_json_bytes(value).decode("utf-8"))
    return value


def _artifacts(job_id: str = JOB_ID) -> dict[str, dict[str, object] | None]:
    return {
        "context": {
            "path": f"runtime/runs/{job_id}/context.json",
            "sha256": "6" * 64,
            "byte_length": 100,
            "mode": "0600",
        },
        "request": {
            "path": f"runtime/runs/{job_id}/request.json",
            "sha256": "7" * 64,
            "byte_length": 100,
            "mode": "0600",
        },
        "response": None,
        "receipt": None,
    }


def _identity(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "job_id": JOB_ID,
        "action": "answer",
        "prompt_sha256": "8" * 64,
        "output_schema_sha256": "9" * 64,
        "policy_decision": _policy(),
        "frozen_evidence": _frozen_evidence(),
        "resolved_model_name": GENERATION_MODEL_TAG,
        "model_digest": "a" * 64,
        "ollama_version": "0.11.0",
        "states": {**DEFAULT_STATES, "reachable": "reachable", "verified": "verified"},
        "outcome_status": "not_run",
        "provider_called": False,
        "artifacts": _artifacts(),
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return values


def test_c38_canonical_profile_is_shared_by_c30_c31_and_e02() -> None:
    profile = canonical_generation_profile()

    assert profile["requested_model_tag"] == GENERATION_MODEL_TAG == "gemma4:12b"
    assert profile["options"] == LOCAL_MODEL_DECLARATION["profiles"]["generation"]["options"]
    assert profile["options"] == {
        key: value for key, value in generation_profile()["options"].items() if key != "max_queue"
    }
    assert DEFAULT_INFERENCE_OPTIONS == generation_inference_options()
    assert profile["enabled_by_default"] is False
    assert profile["safety"] == {
        "automatic_pull": False,
        "fallback": False,
        "tools": False,
        "reasoning_retention": False,
        "loopback_only": True,
        "cloud_disabled": True,
    }


def test_c38_schema_is_strict_and_generated() -> None:
    schema = generation_identity_schema()

    assert schema["additionalProperties"] is False
    Draft202012Validator.check_schema(schema)
    result = export_schema_artifacts(CONTROL_ROOT, check=True)
    assert result.passed, result.report
    artifact = next(
        item for item in result.report["artifacts"] if item["path"] == "ops/schemas/c38-generation-identity.schema.json"
    )
    assert artifact["owner"] == "C38"
    assert artifact["status"] == "PASS"


def test_c38_identity_binds_full_model_identity_and_keeps_profile_disabled() -> None:
    envelope = build_generation_identity(**_identity())

    assert envelope["contract_id"] == CONTRACT_ID
    assert envelope["identity"] == {
        "provider_route": "local:ollama",
        "requested_model_tag": "gemma4:12b",
        "resolved_model_name": "gemma4:12b",
        "model_digest": "a" * 64,
        "ollama_version": "0.11.0",
    }
    assert envelope["binding"]["frozen_evidence"]["projection_generation_id"] == "projection-c38-fixture"
    assert envelope["states"]["enabled"] == "disabled"
    assert envelope["privacy"]["canonical_apply_allowed"] is False
    assert envelope["privacy"]["model_output_untrusted"] is True


def test_c38_identity_write_is_private_immutable_and_replay_safe(tmp_path: Path) -> None:
    envelope = build_generation_identity(**_identity())

    report, code = write_generation_identity(tmp_path, envelope)

    assert code == 0
    assert report["write"] == "CREATED"
    path = tmp_path / "runtime/runs" / JOB_ID / "receipts/generation-identity.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert read_generation_identity(tmp_path, job_id=JOB_ID) == envelope
    raw = path.read_bytes()
    assert b"C38 private prompt" not in raw
    assert b"model output" not in raw

    replay, replay_code = write_generation_identity(tmp_path, envelope)
    assert replay_code == 0
    assert replay["write"] == "NO_OP"

    changed = build_generation_identity(**_identity(prompt_sha256="b" * 64))
    conflict, conflict_code = write_generation_identity(tmp_path, changed)
    assert conflict_code == 30
    assert conflict["status"] == "CONFLICT"
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "status",
    ["unavailable", "timeout", "refused", "failed", "overloaded", "conflict", "cancelled"],
)
def test_c38_records_failure_boundaries_without_claiming_provider_success(status: str) -> None:
    envelope = build_generation_identity(**_identity(outcome_status=status, provider_called=False))

    assert envelope["outcome"] == {"status": status, "provider_called": False}
    assert envelope["states"]["enabled"] == "disabled"


def test_c38_rejects_identity_drift_and_oversized_options() -> None:
    envelope = build_generation_identity(**_identity())

    drifted = dict(envelope)
    drifted["identity"] = dict(envelope["identity"])
    drifted["identity"]["resolved_model_name"] = "gemma4:12b-q4_K_M"
    with pytest.raises(GenerationIdentityError, match="resolved model name"):
        validate_generation_identity(drifted)

    with pytest.raises(GenerationIdentityError, match="canonical C38 bound"):
        build_generation_identity(**_identity(inference_options={**generation_inference_options(), "max_output_tokens": 1025}))

    with pytest.raises(GenerationIdentityError, match="completed outcome"):
        build_generation_identity(**_identity(outcome_status="completed", provider_called=False))


def test_c38_rejects_artifacts_outside_the_private_runtime_job() -> None:
    artifacts = _artifacts()
    artifacts["request"] = {
        "path": "KnowledgeHub/request.json",
        "sha256": "7" * 64,
        "byte_length": 100,
        "mode": "0600",
    }

    with pytest.raises(GenerationIdentityError):
        build_generation_identity(**_identity(artifacts=artifacts))


def test_c38_binds_an_existing_c31_request_without_raw_prompt_or_candidates(tmp_path: Path) -> None:
    candidate_text = "C38 frozen evidence"
    candidate: dict[str, object] = {
        "note_id": "c38-note",
        "path": "40_Knowledge/Notes/C38 Fixture.md",
        "content_hash": "b" * 64,
        "chunk_id": "c38-chunk",
        "chunk_hash": "c" * 64,
        "chunk_locator": "# Fixture",
        "index_generation_id": "generation-c38-fixture",
        "excerpt": candidate_text,
        "excerpt_sha256": _digest(candidate_text),
        "excerpt_byte_length": len(candidate_text.encode()),
        "eligibility_class": "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "C38 fixture",
    }
    candidate["candidate_sha256"] = _digest(canonical_json_bytes(candidate).decode("utf-8"))
    context = build_frozen_context(
        job_id=JOB_ID,
        action="answer",
        prompt="C38 private prompt that must not be copied into the identity receipt.",
        output_schema={"path": "ops/schemas/answer.schema.json", "sha256": "d" * 64},
        policy_decision=_policy(),
        frozen_candidates=[candidate],
        index_generation_id="generation-c38-fixture",
        source_hashes=[
            {
                "note_id": candidate["note_id"],
                "path": candidate["path"],
                "content_hash": candidate["content_hash"],
                "chunk_hash": candidate["chunk_hash"],
                "locator": candidate["chunk_locator"],
            }
        ],
        provider={
            "route": "local:ollama",
            "model_tag": GENERATION_MODEL_TAG,
            "model_digest": "e" * 64,
            "ollama_version": "0.11.0",
        },
        inference_options={**DEFAULT_INFERENCE_OPTIONS, "max_output_tokens": 32},
        created_at=CREATED_AT,
    )
    context_report, context_code = write_context_envelope(tmp_path, context)
    assert context_code == 0, context_report
    request = build_provider_request(context, request_id=JOB_ID, created_at=CREATED_AT)
    request_report, request_code = write_provider_request(tmp_path, request)
    assert request_code == 0, request_report

    envelope = build_generation_identity_from_c31(request, created_at=CREATED_AT)

    assert envelope["binding"]["action"] == "answer"
    assert envelope["binding"]["frozen_evidence"]["context_sha256"] == request["context_sha256"]
    assert envelope["inference_options"]["max_output_tokens"] == 32
    assert b"C38 private prompt" not in canonical_json_bytes(envelope)


def test_c38_c31_request_identity_drift_fails_closed() -> None:
    request = {
        "job_id": JOB_ID,
        "action": "answer",
        "prompt_sha256": "1" * 64,
        "output_schema": {"sha256": "2" * 64},
        "policy_decision": _policy(),
        "index_generation_id": "index",
        "candidate_set_sha256": "3" * 64,
        "source_hashes_sha256": "4" * 64,
        "context_sha256": "5" * 64,
        "request_sha256": "6" * 64,
        "provider": {
            "route": "local:baseline",
            "model_tag": GENERATION_MODEL_TAG,
            "model_digest": "7" * 64,
            "ollama_version": "0.11.0",
        },
        "inference_options": generation_inference_options(),
    }

    with pytest.raises(GenerationIdentityError, match="canonical local generation identity"):
        build_generation_identity_from_c31(request)

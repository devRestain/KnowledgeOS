from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from vaultops.e02 import (
    DEFAULT_GENERATION_TAG,
    E02Error,
    benchmark_embeddings,
    benchmark_generation,
    e02_contract,
    e02_verification_report_schema,
    evaluate_e02_rankings,
    inspect_e02_service,
    run_e02_host_job,
    validate_e02_report,
    write_e02_report,
)
from vaultops.provider_contract import (
    DEFAULT_INFERENCE_OPTIONS,
    build_frozen_context,
    build_provider_request,
    write_context_envelope,
    write_provider_request,
)

MODEL_DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def _explicit_local_ollama_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_NO_CLOUD", "1")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


class _FakeE02Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.chat_content = json.dumps({"summary": "bounded fake output"})

    def version(self) -> str:
        self.calls.append(("version", "", None))
        return "0.9.0"

    def tags(self) -> dict[str, Any]:
        self.calls.append(("tags", "", None))
        return {
            "models": [
                {
                    "model": DEFAULT_GENERATION_TAG,
                    "name": "gemma4:12b-it-q4_K_M",
                    "digest": f"sha256:{MODEL_DIGEST}",
                    "size": 123,
                }
            ]
        }

    def show(self, model: str) -> dict[str, Any]:
        self.calls.append(("show", model, None))
        return {
            "details": {"quantization_level": "Q4_K_M", "family": "gemma"},
            "model_info": {"context_length": 32768},
        }

    def ps(self) -> dict[str, Any]:
        self.calls.append(("ps", "", None))
        return {
            "models": [
                {
                    "name": "gemma4:12b-it-q4_K_M",
                    "backend": "metal",
                    "device": "gpu",
                    "processor": "100% GPU",
                    "context_length": 8192,
                }
            ]
        }

    def verify_identity(self, model: str, expected_digest: str, *, expected_version: str | None = None) -> dict[str, Any]:
        self.calls.append(("verify_identity", model, expected_version))
        assert expected_digest == MODEL_DIGEST
        assert expected_version == "0.9.0"
        return {"model": model, "model_digest": expected_digest}

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = "json",
        keep_alive: int = 0,
    ) -> dict[str, Any]:
        self.calls.append(("chat", model, {"messages": messages, "options": options, "format": format, "keep_alive": keep_alive}))
        return {"model": model, "message": {"role": "assistant", "content": self.chat_content}, "done": True}

    def embed(
        self,
        model: str,
        inputs: str | list[str],
        *,
        options: dict[str, Any] | None = None,
        truncate: bool = False,
        keep_alive: int = 0,
    ) -> dict[str, Any]:
        self.calls.append(("embed", model, {"inputs": inputs, "options": options, "truncate": truncate, "keep_alive": keep_alive}))
        values = [inputs] if isinstance(inputs, str) else inputs
        dimension = 4 if model.startswith("qwen3-embedding") else 3
        return {"embeddings": [[0.1] * dimension for _ in values], "truncated": False}


def _write_job(root: Path) -> tuple[Path, str]:
    root.mkdir()
    (root / "KnowledgeHub").mkdir()
    job_id = str(uuid.uuid4())
    now = "2026-09-18T20:00:00+09:00"
    candidate_text = "The bounded E02 fake fixture is local-only evidence."
    candidate = {
        "note_id": "e02-note-001",
        "path": "40_Knowledge/Notes/E02 Fixture.md",
        "content_hash": hashlib.sha256(candidate_text.encode()).hexdigest(),
        "chunk_id": "e02-chunk-001",
        "chunk_hash": hashlib.sha256(candidate_text.encode()).hexdigest(),
        "chunk_locator": "# Evidence",
        "index_generation_id": "e02-fixture",
        "excerpt": candidate_text,
        "excerpt_sha256": hashlib.sha256(candidate_text.encode()).hexdigest(),
        "excerpt_byte_length": len(candidate_text.encode()),
        "eligibility_class": "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "E02 fake fixture",
    }
    candidate["candidate_sha256"] = hashlib.sha256(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    context = build_frozen_context(
        job_id=job_id,
        action="summarize",
        prompt="Return one bounded JSON summary from the frozen E02 fixture.",
        output_schema={"path": "ops/schemas/answer.schema.json#summary", "sha256": "b" * 64},
        policy_decision={
            "decision": "allow_local",
            "policy_sha256": "c" * 64,
            "privacy_policy_sha256": "d" * 64,
            "scope": "e02-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[candidate],
        index_generation_id="e02-fixture",
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
            "model_tag": DEFAULT_GENERATION_TAG,
            "model_digest": MODEL_DIGEST,
            "ollama_version": "0.9.0",
        },
        inference_options={**DEFAULT_INFERENCE_OPTIONS, "max_output_tokens": 32},
        created_at=now,
    )
    context_report, context_code = write_context_envelope(root, context)
    assert context_code == 0, context_report
    request = build_provider_request(context, created_at=now, request_id=job_id)
    request_report, request_code = write_provider_request(root, request)
    assert request_code == 0, request_report
    return root / "runtime" / "runs" / job_id, job_id


def test_e02_contract_keeps_live_and_c34_boundaries_explicit() -> None:
    contract = e02_contract()

    assert contract["enabled_by_default"] is False
    assert contract["endpoint"]["auto_pull"] is False
    assert contract["endpoint"]["loopback_only"] is True
    assert contract["generation"]["requested_model_tag"] == DEFAULT_GENERATION_TAG
    assert contract["embeddings"][0]["requested_model_tag"] == "qwen3-embedding:8b-q4_K_M"
    assert contract["canonical_c34_mutation_allowed"] is False
    assert e02_verification_report_schema()["properties"]["capability"]["const"] == "E02"


def test_e02_deferred_inspection_never_calls_client_or_network(tmp_path: Path) -> None:
    storage = tmp_path / "internal"
    storage.mkdir()
    client = _FakeE02Client()

    report, code = inspect_e02_service(
        client,
        storage_path=storage,
        storage_root=tmp_path,
        authorized=False,
    )

    assert code == 30
    assert report["status"] == "DEFERRED"
    assert report["provider_called"] is False
    assert client.calls == []
    validate_e02_report(report)


def test_e02_live_inspection_requires_explicit_cloud_off_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = tmp_path / "internal"
    storage.mkdir()
    monkeypatch.delenv("OLLAMA_NO_CLOUD")

    report, code = inspect_e02_service(
        _FakeE02Client(),
        storage_path=storage,
        storage_root=tmp_path,
        authorized=True,
    )

    assert code == 10
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "E02_CLOUD_NOT_DISABLED"
    assert report["provider_called"] is False


def test_e02_inspection_records_requested_and_resolved_identity_without_promotion(tmp_path: Path) -> None:
    storage = tmp_path / "internal"
    storage.mkdir()
    report, code = inspect_e02_service(
        _FakeE02Client(),
        storage_path=storage,
        storage_root=tmp_path,
        authorized=True,
    )

    assert code == 0
    assert report["status"] == "PASS"
    assert report["service"]["requested_model_tag"] == DEFAULT_GENERATION_TAG
    assert report["service"]["resolved_model_name"] == "gemma4:12b-it-q4_K_M"
    assert report["service"]["model_digest"] == MODEL_DIGEST
    assert report["service"]["storage_scope"] == "internal_ssd"
    assert report["promotion"]["gate_passed"] is False


def test_e02_job_runner_uses_only_one_private_spool_and_replays_without_call(tmp_path: Path) -> None:
    root = tmp_path / "internal"
    job_dir, job_id = _write_job(root)
    client = _FakeE02Client()

    report, code = run_e02_host_job(
        job_dir,
        client,
        storage_root=root,
        authorized=True,
    )

    assert code == 0, report
    assert report["status"] == "PASS"
    assert report["job"]["job_id"] == job_id
    assert report["mutation_performed"] is False
    assert (job_dir / "response.json").stat().st_mode & 0o777 == 0o600
    assert (job_dir / "receipts/provider-receipt.json").stat().st_mode & 0o777 == 0o600
    first_call_count = len(client.calls)

    replay, replay_code = run_e02_host_job(
        job_dir,
        client,
        storage_root=root,
        authorized=True,
    )

    assert replay_code == 0, replay
    assert replay["status"] == "NO_OP"
    assert len(client.calls) == first_call_count

    report_artifact = write_e02_report(job_dir, report, storage_root=root)
    assert report_artifact["state"] == "CREATED"
    report_path = job_dir / "verification-report.json"
    assert report_path.stat().st_mode & 0o777 == 0o600
    same_artifact = write_e02_report(job_dir, report, storage_root=root)
    assert same_artifact["state"] == "NO_OP"


def test_e02_job_runner_rejects_external_storage_and_invalid_job_paths(tmp_path: Path) -> None:
    root = tmp_path / "internal"
    job_dir, _job_id = _write_job(root)
    external = tmp_path / "external"
    external.mkdir()

    with pytest.raises(E02Error, match="outside the declared internal SSD root"):
        run_e02_host_job(job_dir, _FakeE02Client(), storage_root=external, authorized=True)

    bad_job_dir = root / "bad-job"
    bad_job_dir.mkdir(mode=0o700)
    with pytest.raises(E02Error, match="lowercase UUIDv4"):
        run_e02_host_job(bad_job_dir, _FakeE02Client(), storage_root=root, authorized=False)


def test_e02_embedding_benchmark_uses_distinct_query_and_document_roles(tmp_path: Path) -> None:
    storage = tmp_path / "internal"
    storage.mkdir()
    client = _FakeE02Client()

    report, code = benchmark_embeddings(
        client,
        ["한국어 검색 질의", "stable note passage"],
        model_tags=["qwen3-embedding:4b-q8_0"],
        authorized=True,
        storage_path=storage,
        storage_root=tmp_path,
    )

    assert code == 0, report
    embed_call = next(call for call in client.calls if call[0] == "embed")
    inputs = embed_call[2]["inputs"]
    assert inputs[0].startswith("Given a KnowledgeOS search query")
    assert inputs[1].startswith("title: none | text:")
    assert report["measurements"][0]["request_count"] == 2
    assert report["measurements"][0]["success_count"] == 2
    assert report["measurements"][0]["observed_dimension"] == 4


def test_e02_generation_benchmark_requires_exact_model_and_internal_storage(tmp_path: Path) -> None:
    storage = tmp_path / "internal"
    storage.mkdir()
    client = _FakeE02Client()

    with pytest.raises(E02Error, match="internal SSD path and root"):
        benchmark_generation(
            client,
            ["bounded prompt"],
            authorized=True,
            storage_path=storage,
            storage_root=None,
        )

    with pytest.raises(E02Error, match="require model tag gemma4:12b"):
        benchmark_generation(
            client,
            ["bounded prompt"],
            model_tag="gemma4:12b-it-q4_K_M",
            authorized=True,
            storage_path=storage,
            storage_root=tmp_path,
        )

    report, code = benchmark_generation(
        client,
        ["bounded prompt"],
        authorized=True,
        storage_path=storage,
        storage_root=tmp_path,
    )
    assert code == 0
    assert report["status"] == "PASS"
    assert [item["phase"] for item in report["measurements"]] == ["cold", "warm", "unload"]


def test_e02_promotion_requires_live_evidence_and_never_mutates_c34() -> None:
    cases = [
        {
            "id": "ko-1",
            "expected_paths": ["note-a"],
            "rankings": {
                "c34_learned": ["note-a", "note-b"],
                "qwen3-embedding:4b-q8_0": ["note-a", "note-b"],
            },
        }
    ]

    report, code = evaluate_e02_rankings(
        cases,
        candidate_id="qwen3-embedding:4b-q8_0",
        live_model_evidence=False,
        privacy_gate_passed=True,
        citation_gate_passed=True,
        staleness_gate_passed=True,
        resource_gate_passed=True,
    )

    assert code == 0
    assert report["promotion"]["decision"] == "retain_c34"
    assert report["promotion"]["gate_passed"] is False
    assert report["evaluation"]["canonical_index_mutated"] is False
    assert report["mutation_performed"] is False


def test_e02_report_writer_rejects_a_report_bound_to_another_job(tmp_path: Path) -> None:
    root = tmp_path / "internal"
    job_dir, _job_id = _write_job(root)
    report, _code = run_e02_host_job(
        job_dir,
        _FakeE02Client(),
        storage_root=root,
        authorized=True,
    )
    report["job"]["job_id"] = str(uuid.uuid4())

    with pytest.raises(E02Error, match="report job_id"):
        write_e02_report(job_dir, report, storage_root=root)

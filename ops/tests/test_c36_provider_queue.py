from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_c17_bridge_publish import SOURCE_PATH, _fresh_control_copy

from vaultops.bridge_contract import canonical_json_bytes
from vaultops.bridge_publish import ingest_bridge_request
from vaultops.provider_broker import SYNTHETIC_MODEL_DIGEST, SYNTHETIC_MODEL_TAG
from vaultops.provider_contract import (
    build_frozen_context,
    build_provider_request,
    write_context_envelope,
    write_provider_request,
)
from vaultops.provider_contract import canonical_json_bytes as provider_canonical_json_bytes
from vaultops.provider_queue import DEFAULT_LEASE_SECONDS, consume_provider_queue

JOB_ID = "550e8400-e29b-41d4-a716-446655440000"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate(source_hash: str, *, remote: bool = False) -> dict[str, object]:
    excerpt = "The C36 queue fixture preserves one byte-addressable evidence excerpt."
    result: dict[str, object] = {
        "note_id": "note-c36-001",
        "path": SOURCE_PATH,
        "content_hash": source_hash,
        "chunk_id": "chunk-c36-001",
        "chunk_hash": "2" * 64,
        "chunk_locator": "# Evidence",
        "index_generation_id": "generation-c36-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": "remote_eligible" if remote else "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "C36 deterministic fixture",
    }
    result["candidate_sha256"] = _digest(provider_canonical_json_bytes(result))
    return result


def _schema_binding(root: Path, relative: str, fragment: str | None = None) -> dict[str, str]:
    raw = (root / relative).read_bytes()
    return {
        "path": f"{relative}#{fragment}" if fragment else relative,
        "sha256": _digest(raw),
    }


def _prepare_answer_job(root: Path, *, route: str = "local:c36-synthetic") -> None:
    vault = root / "KnowledgeHub"
    source_hash = _digest((vault / SOURCE_PATH).read_bytes())
    request = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "created_at": "2026-09-19T12:00:00+09:00",
        "device_id": "test-phone-01",
        "pipeline_kind": "answer",
        "source": {"path": SOURCE_PATH, "blob_sha256": source_hash},
        "target": None,
        "parameters": {"scope_id": "c36-test"},
        "route_id": route,
        "mode": "local_only",
    }
    request_path = vault / f".vault-bridge/requests/2026/09/{JOB_ID}.json"
    request_path.write_bytes(canonical_json_bytes(request))
    import subprocess

    subprocess.run(
        ["git", "-c", f"safe.directory={vault}", "-C", str(vault), "add", "--", request_path.relative_to(vault).as_posix()],
        check=True,
    )
    subprocess.run(
        ["git", "-c", f"safe.directory={vault}", "-C", str(vault), "commit", "-m", "add C36 answer request"],
        check=True,
        capture_output=True,
    )
    now = datetime.now(UTC).replace(microsecond=0)
    context = build_frozen_context(
        job_id=JOB_ID,
        action="answer",
        prompt="Run the bounded C36 answer pipeline over frozen evidence.",
        output_schema=_schema_binding(root, "ops/schemas/answer.schema.json", "answer"),
        policy_decision={
            "decision": "allow_local",
            "policy_sha256": _digest((root / "ops/policies/retrieval.yaml").read_bytes()),
            "privacy_policy_sha256": _digest((root / "ops/policies/privacy.yaml").read_bytes()),
            "scope": "c36-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[_candidate(source_hash, remote=not route.startswith("local:"))],
        index_generation_id="generation-c36-fixture",
        source_hashes=[
            {
                "note_id": "note-c36-001",
                "path": SOURCE_PATH,
                "content_hash": source_hash,
                "chunk_hash": "2" * 64,
                "locator": "# Evidence",
            }
        ],
        provider={
            "route": route,
            "model_tag": SYNTHETIC_MODEL_TAG,
            "model_digest": SYNTHETIC_MODEL_DIGEST,
            "ollama_version": None,
        },
        created_at=now.isoformat(),
    )
    context_report, context_code = write_context_envelope(root, context)
    assert context_code == 0, context_report
    request_envelope = build_provider_request(context, created_at=now.isoformat())
    request_report, request_code = write_provider_request(root, request_envelope)
    assert request_code == 0, request_report


def test_c36_consumes_local_answer_and_publishes_exact_bridge_response(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _prepare_answer_job(root)
    ingest, ingest_code = ingest_bridge_request(root, job_id=JOB_ID)
    assert ingest_code == 0, ingest

    now = datetime.now(UTC).replace(microsecond=0)
    report, exit_code = consume_provider_queue(root, owner_id="c36-test", now=now)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["capability"] == "C36"
    assert report["claimed"] == 1
    assert report["processed"] == 1
    assert report["synthetic_provider_called"] is True
    assert report["live_provider_called"] is False
    assert report["canonical_apply_allowed"] is False
    assert report["vault_mutation_performed"] is True
    assert list((root / "runtime/queue").glob("*.json")) == []
    assert (root / "runtime/review" / f"{JOB_ID}.json").is_file()
    response_files = list((root / "KnowledgeHub/.vault-bridge/responses/2026/09" / JOB_ID).glob("*.json"))
    assert len(response_files) == 1
    response = json.loads(response_files[0].read_text(encoding="utf-8"))
    assert response["status"] == "answer_ready"
    assert response["request_sha256"] == ingest["request_sha256"]
    assert response["answer_id"].startswith("answer-")
    assert (root / "runtime/runs" / JOB_ID / "response.json").is_file()
    assert (root / "runtime/runs" / JOB_ID / "receipts/provider-receipt.json").is_file()

    replay, replay_code = consume_provider_queue(root, owner_id="c36-test-replay", now=now + timedelta(seconds=1))
    assert replay_code == 0, replay
    assert replay["status"] == "PASS"
    assert replay["claimed"] == 0
    assert len(list((root / "KnowledgeHub/.vault-bridge/responses/2026/09" / JOB_ID).glob("*.json"))) == 1


def test_c36_requeues_expired_lease_and_replays_private_provider_artifacts(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _prepare_answer_job(root)
    ingest_bridge_request(root, job_id=JOB_ID)
    now = datetime.now(UTC).replace(microsecond=0)

    crashed, crashed_code = consume_provider_queue(
        root,
        owner_id="c36-crashed",
        now=now,
        fault_after="provider",
    )
    assert crashed_code == 30
    assert crashed["status"] == "FAIL"
    assert crashed["results"][0]["crashed"] is True
    assert (root / "runtime/running" / f"{JOB_ID}.lease.json").is_file()
    assert not list((root / "runtime/queue").glob("*.json"))

    recovered, recovered_code = consume_provider_queue(
        root,
        owner_id="c36-recovered",
        now=now + timedelta(seconds=DEFAULT_LEASE_SECONDS + 1),
    )
    assert recovered_code == 0, recovered
    assert recovered["status"] == "PASS"
    assert recovered["recovered"] == 1
    assert recovered["results"][0]["replayed"] is True


def test_c36_quarantines_tampered_queue_bytes_and_defers_inactive_routes(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _prepare_answer_job(root)
    ingest_bridge_request(root, job_id=JOB_ID)
    queue_path = root / "runtime/queue" / f"{JOB_ID}.json"
    queue_path.write_bytes(queue_path.read_bytes() + b"tamper")

    conflict, conflict_code = consume_provider_queue(root, owner_id="c36-tamper")
    assert conflict_code == 30
    assert conflict["status"] == "CONFLICT"
    assert list((root / "runtime/quarantine/provider").glob(f"{JOB_ID}-*/{JOB_ID}.json"))

    root = _fresh_control_copy(tmp_path / "inactive")
    _prepare_answer_job(root, route="codex_chatgpt_login")
    ingest_bridge_request(root, job_id=JOB_ID)
    deferred, deferred_code = consume_provider_queue(root, owner_id="c36-inactive")
    assert deferred_code == 0
    assert deferred["status"] == "DEFERRED"
    assert deferred["deferred"] == 1
    assert (root / "runtime/queue" / f"{JOB_ID}.json").is_file()

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vaultops.cli import main
from vaultops.provider_broker import (
    SYNTHETIC_MODEL_DIGEST,
    SYNTHETIC_MODEL_TAG,
    run_synthetic_job,
)
from vaultops.provider_contract import (
    build_frozen_context,
    build_provider_request,
    canonical_json_bytes,
    write_context_envelope,
    write_provider_request,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _copy_broker_contract(root: Path) -> None:
    relative_paths = [
        "ops/schemas/triage-result.schema.json",
        "ops/schemas/proposal.schema.json",
        "ops/schemas/answer.schema.json",
        "ops/policies/privacy.yaml",
        "ops/policies/retrieval.yaml",
        "ops/actions/triage.json",
        "ops/actions/draft-note.json",
        "ops/actions/link-suggestions.json",
        "ops/actions/normalize.json",
        "ops/actions/summarize.json",
        "ops/actions/answer.json",
        "ops/prompts/triage.md",
        "ops/prompts/draft-note.md",
        "ops/prompts/link-suggestions.md",
        "ops/prompts/normalize.md",
        "ops/prompts/summarize.md",
        "ops/prompts/answer.md",
    ]
    for relative in relative_paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONTROL_ROOT / relative, target)


def _candidate() -> dict[str, object]:
    excerpt = "The frozen C32 fixture preserves one byte-addressable evidence excerpt."
    value: dict[str, object] = {
        "note_id": "note-c32-001",
        "path": "40_Knowledge/Notes/C32 Fixture.md",
        "content_hash": "1" * 64,
        "chunk_id": "chunk-c32-001",
        "chunk_hash": "2" * 64,
        "chunk_locator": "# Evidence",
        "index_generation_id": "generation-c32-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "C32 deterministic fixture",
    }
    value["candidate_sha256"] = _digest(canonical_json_bytes(value))
    return value


def _schema_binding(relative: str, fragment: str | None = None) -> dict[str, str]:
    raw = (CONTROL_ROOT / relative).read_bytes()
    path = f"{relative}#{fragment}" if fragment else relative
    return {"path": path, "sha256": _digest(raw)}


def _context(
    root: Path,
    *,
    job_id: str,
    action: str,
    policy_decision: str = "allow_local",
) -> dict[str, object]:
    schema = {
        "triage": _schema_binding("ops/schemas/triage-result.schema.json"),
        "draft_note": _schema_binding("ops/schemas/proposal.schema.json"),
        "link_suggestions": _schema_binding("ops/schemas/proposal.schema.json"),
        "normalize": _schema_binding("ops/schemas/proposal.schema.json"),
        "summarize": _schema_binding("ops/schemas/answer.schema.json", "summary"),
        "answer": _schema_binding("ops/schemas/answer.schema.json", "answer"),
    }[action]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    source_hash = "1" * 64
    context = build_frozen_context(
        job_id=job_id,
        action=action,
        prompt=f"Run the bounded C32 {action} pipeline over frozen evidence.",
        output_schema=schema,
        policy_decision={
            "decision": policy_decision,
            "policy_sha256": _digest((root / "ops/policies/retrieval.yaml").read_bytes()),
            "privacy_policy_sha256": _digest((root / "ops/policies/privacy.yaml").read_bytes()),
            "scope": "c32-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[_candidate()],
        index_generation_id="generation-c32-fixture",
        source_hashes=[
            {
                "note_id": "note-c32-001",
                "path": "40_Knowledge/Notes/C32 Fixture.md",
                "content_hash": source_hash,
                "chunk_hash": "2" * 64,
                "locator": "# Evidence",
            }
        ],
        provider={
            "route": "local:c32-synthetic",
            "model_tag": SYNTHETIC_MODEL_TAG,
            "model_digest": SYNTHETIC_MODEL_DIGEST,
            "ollama_version": None,
        },
        created_at=now,
    )
    context_report, context_code = write_context_envelope(root, context)
    assert context_code == 0, context_report
    request = build_provider_request(context, created_at=now)
    request_report, request_code = write_provider_request(root, request)
    assert request_code == 0, request_report
    return context


def _job_root(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    root.mkdir()
    _copy_broker_contract(root)
    return root


@pytest.mark.parametrize(
    "action",
    ["triage", "draft_note", "link_suggestions", "normalize", "summarize", "answer"],
)
def test_c32_runs_each_pipeline_through_strict_output_validation(tmp_path: Path, action: str) -> None:
    root = _job_root(tmp_path)
    job_id = str(uuid.uuid4())
    _context(root, job_id=job_id, action=action)

    report, exit_code = run_synthetic_job(root, job_id=job_id)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["capability"] == "C32"
    assert report["provider_called"] is False
    assert report["synthetic_provider_called"] is True
    assert report["live_provider_called"] is False
    assert report["mutation_performed"] is False
    assert report["vault_mutation_performed"] is False
    assert report["canonical_apply_allowed"] is False
    assert report["artifact_kind"] in {"proposal", "answer"}

    response_path = root / report["response_path"]
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["status"] == "completed"
    assert response["model_output_untrusted"] is True
    if action not in {"summarize", "answer"}:
        assert response["output"]["provider_called"] is False
        assert response["output"]["mutation_performed"] is False
    assert (root / report["receipt_path"]).is_file()
    assert "Run the bounded C32" not in response_path.read_text(encoding="utf-8")


def test_c32_replays_identical_private_artifacts_without_reinvoking_adapter(tmp_path: Path) -> None:
    root = _job_root(tmp_path)
    job_id = str(uuid.uuid4())
    _context(root, job_id=job_id, action="answer")

    first, first_code = run_synthetic_job(root, job_id=job_id)
    response_before = (root / first["response_path"]).read_bytes()
    second, second_code = run_synthetic_job(root, job_id=job_id, scenario="replay")

    assert first_code == 0
    assert second_code == 0
    assert second["status"] == "NO_OP"
    assert second["replayed"] is True
    assert second["synthetic_provider_called"] is False
    assert (root / second["response_path"]).read_bytes() == response_before


@pytest.mark.parametrize(
    ("scenario", "expected_code", "expected_exit"),
    [
        ("refusal", "PROVIDER_REFUSED", 30),
        ("malformed_json", "OUTPUT_INVALID", 10),
        ("schema_violation", "C32_OUTPUT_SCHEMA_INVALID", 10),
        ("prompt_injection", "C32_PROMPT_INJECTION", 10),
        ("oversized_output", "OUTPUT_TOO_LARGE", 10),
        ("timeout", "PROVIDER_TIMEOUT", 30),
        ("cancelled", "CANCELLED", 30),
        ("overloaded", "PROVIDER_OVERLOADED", 30),
        ("adapter_crash", "PROVIDER_UNAVAILABLE", 30),
        ("digest_conflict", "C32_DIGEST_CONFLICT", 30),
    ],
)
def test_c32_failure_scenarios_persist_bounded_failure_and_receipt(
    tmp_path: Path,
    scenario: str,
    expected_code: str,
    expected_exit: int,
) -> None:
    root = _job_root(tmp_path)
    job_id = str(uuid.uuid4())
    _context(root, job_id=job_id, action="answer")

    report, exit_code = run_synthetic_job(root, job_id=job_id, scenario=scenario)

    assert exit_code == expected_exit, report
    assert report["status"] in {"FAIL", "CONFLICT"}
    assert report["errors"][0]["code"] == expected_code
    assert (root / report["response_path"]).is_file()
    assert (root / report["failure_path"]).is_file()
    assert (root / report["receipt_path"]).is_file()
    response = json.loads((root / report["response_path"]).read_text(encoding="utf-8"))
    failure = json.loads((root / report["failure_path"]).read_text(encoding="utf-8"))
    assert response["output"] is None
    assert failure["raw_payload_persisted"] is False
    assert "Run the bounded C32" not in (root / report["failure_path"]).read_text(encoding="utf-8")


def test_c32_policy_denial_prevents_synthetic_provider_execution(tmp_path: Path) -> None:
    root = _job_root(tmp_path)
    job_id = str(uuid.uuid4())
    _context(root, job_id=job_id, action="answer", policy_decision="deny")

    report, exit_code = run_synthetic_job(root, job_id=job_id)

    assert exit_code == 10
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "POLICY_DENIED"
    assert report["synthetic_provider_called"] is False
    assert not (root / "runtime/runs" / job_id / "response.json").exists()


def test_c32_cli_exposes_explicit_broker_without_changing_provider_free_routes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _job_root(tmp_path)
    job_id = str(uuid.uuid4())
    _context(root, job_id=job_id, action="answer")

    exit_code = main(["ai", "broker", "--job-id", job_id, "--root", str(root)])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["capability"] == "C32"
    assert report["status"] == "PASS"

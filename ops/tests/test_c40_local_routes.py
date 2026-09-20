from __future__ import annotations

import hashlib
import json
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from vaultops.gemma_routes import GEMMA_MODEL_TAG
from vaultops.ollama import OllamaClient, OllamaProfile
from vaultops.provider_broker import _answer_output, _proposal_output, _triage_output
from vaultops.provider_contract import (
    build_frozen_context,
    build_provider_request,
    canonical_json_bytes,
    write_context_envelope,
    write_provider_request,
)
from vaultops.provider_routes import C40_LOCAL_PIPELINES, run_local_route

CONTROL_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIGEST = "a" * 64


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
    return root


def _job(root: Path, action: str) -> tuple[str, dict[str, Any]]:
    job_id = str(uuid.uuid4())
    excerpt = "Frozen C40 evidence remains bounded and reviewable."
    candidate: dict[str, Any] = {
        "note_id": "note-c40-001",
        "path": "40_Knowledge/Notes/C40 Fixture.md",
        "content_hash": "1" * 64,
        "chunk_id": "chunk-c40-001",
        "chunk_hash": "2" * 64,
        "chunk_locator": "# Evidence",
        "index_generation_id": "generation-c40-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "C40 bounded local-route fixture",
    }
    candidate["candidate_sha256"] = _digest(canonical_json_bytes(candidate))
    source = {
        "note_id": candidate["note_id"],
        "path": candidate["path"],
        "content_hash": candidate["content_hash"],
        "chunk_hash": candidate["chunk_hash"],
        "locator": candidate["chunk_locator"],
    }
    schema_path = {
        "answer": "ops/schemas/answer.schema.json#answer",
        "triage": "ops/schemas/triage-result.schema.json",
        "draft_note": "ops/schemas/proposal.schema.json",
        "link_suggestions": "ops/schemas/proposal.schema.json",
        "normalize": "ops/schemas/proposal.schema.json",
    }[action]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    context = build_frozen_context(
        job_id=job_id,
        action=action,
        prompt=f"Return one independently validated C40 {action} JSON result.",
        output_schema={
            "path": schema_path,
            "sha256": _digest((root / schema_path.split("#", 1)[0]).read_bytes()),
        },
        policy_decision={
            "decision": "allow_local",
            "policy_sha256": "3" * 64,
            "privacy_policy_sha256": "4" * 64,
            "scope": "c40-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[candidate],
        index_generation_id="generation-c40-fixture",
        source_hashes=[source],
        provider={
            "route": "local:gemma4",
            "model_tag": GEMMA_MODEL_TAG,
            "model_digest": MODEL_DIGEST,
            "ollama_version": "0.9.0",
        },
        created_at=now,
    )
    context_report, context_code = write_context_envelope(root, context)
    assert context_code == 0, context_report
    request = build_provider_request(context, created_at=now)
    request_report, request_code = write_provider_request(root, request)
    assert request_code == 0, request_report
    return job_id, context


def _output(root: Path, context: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "answer":
        return _answer_output(context, summary=False)
    if action == "triage":
        return _triage_output(context)
    return _proposal_output(root, context, action)


class _FakeOllama:
    def __init__(self) -> None:
        self.chat_content = "{}"
        self.response_model = GEMMA_MODEL_TAG
        self.message_extra: dict[str, Any] = {}
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def _payload(self) -> dict[str, Any] | None:
                length = int(self.headers.get("Content-Length", "0"))
                if length == 0:
                    return None
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def _respond(self, value: object, *, status: int = 200) -> None:
                raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, payload: dict[str, Any] | None) -> None:
                owner.requests.append((self.command, self.path, payload))

            def do_GET(self) -> None:
                self._record(None)
                if self.path == "/api/version":
                    self._respond({"version": "0.9.0"})
                elif self.path == "/api/tags":
                    self._respond({"models": [{"name": GEMMA_MODEL_TAG, "digest": f"sha256:{MODEL_DIGEST}"}]})
                elif self.path == "/api/ps":
                    self._respond({"models": [{"name": GEMMA_MODEL_TAG}]})
                else:
                    self._respond({"error": "not found"}, status=404)

            def do_POST(self) -> None:
                payload = self._payload()
                self._record(payload)
                if self.path == "/api/show":
                    self._respond({"details": {"family": "fake"}, "model_info": {}})
                elif self.path == "/api/chat":
                    message = {"role": "assistant", "content": owner.chat_content}
                    message.update(owner.message_extra)
                    self._respond(
                        {
                            "model": owner.response_model,
                            "message": message,
                            "done": True,
                        }
                    )
                else:
                    self._respond({"error": "not found"}, status=404)

        return Handler

    @contextmanager
    def running(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


@pytest.mark.parametrize("action", C40_LOCAL_PIPELINES)
def test_c40_enabled_local_routes_validate_c35_output_and_remain_proposal_only(
    tmp_path: Path,
    action: str,
) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, context = _job(root, action)
    fake = _FakeOllama()
    fake.chat_content = json.dumps(_output(root, context, action), ensure_ascii=False)

    with fake.running() as base_url:
        report, exit_code = run_local_route(
            root,
            job_id=job_id,
            client=OllamaClient(OllamaProfile(base_url=base_url)),
            pipeline=action,
            enabled=True,
            authorized=True,
        )

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["capability"] == "C40"
    assert report["pipeline"] == action
    assert report["proposal_only"] is True
    assert report["provider_called"] is True
    assert report["live_provider_called"] is True
    assert report["mutation_performed"] is False
    assert report["vault_mutation_performed"] is False
    assert report["canonical_apply_allowed"] is False
    response_path = root / report["response_path"]
    receipt_path = root / report["receipt_path"]
    assert stat.S_IMODE(response_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert "message" not in response
    assert not (root / "KnowledgeHub").exists()


def test_c40_default_and_unauthorized_paths_defer_without_provider_access(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, _ = _job(root, "answer")

    disabled, disabled_code = run_local_route(
        root,
        job_id=job_id,
        client=None,
        enabled=False,
        authorized=False,
    )
    assert disabled_code == 30
    assert disabled["status"] == "DEFERRED"
    assert disabled["errors"][0]["code"] == "C40_ROUTE_DISABLED"
    assert disabled["provider_called"] is False

    unauthorized, unauthorized_code = run_local_route(
        root,
        job_id=job_id,
        client=None,
        enabled=True,
        authorized=False,
    )
    assert unauthorized_code == 30
    assert unauthorized["status"] == "DEFERRED"
    assert unauthorized["errors"][0]["code"] == "C40_AUTHORIZATION_REQUIRED"
    assert unauthorized["provider_called"] is False
    assert not (root / "runtime/runs" / job_id / "response.json").exists()


@pytest.mark.parametrize(
    ("message_extra", "response_model", "content", "expected_code"),
    [
        ({"tool_calls": []}, GEMMA_MODEL_TAG, "{}", "OUTPUT_INVALID"),
        ({"thinking": "private reasoning"}, GEMMA_MODEL_TAG, "{}", "OUTPUT_INVALID"),
        ({}, "gemma4:other", "{}", "DIGEST_CONFLICT"),
        ({}, GEMMA_MODEL_TAG, "not-json", "OUTPUT_INVALID"),
    ],
)
def test_c40_rejects_unsafe_or_invalid_transport_output_before_completed_persistence(
    tmp_path: Path,
    message_extra: dict[str, Any],
    response_model: str,
    content: str,
    expected_code: str,
) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, _ = _job(root, "answer")
    fake = _FakeOllama()
    fake.message_extra = message_extra
    fake.response_model = response_model
    fake.chat_content = content

    with fake.running() as base_url:
        report, exit_code = run_local_route(
            root,
            job_id=job_id,
            client=OllamaClient(OllamaProfile(base_url=base_url)),
            pipeline="answer",
            enabled=True,
            authorized=True,
        )

    assert exit_code in {10, 30}, report
    assert report["status"] in {"FAIL", "CONFLICT"}
    assert report["errors"][0]["code"] == expected_code
    assert report["provider_called"] is True
    response = json.loads((root / "runtime/runs" / job_id / "response.json").read_text(encoding="utf-8"))
    assert response["status"] == ("conflict" if expected_code == "DIGEST_CONFLICT" else "failed")
    assert response["output"] is None


def test_c40_rejects_stale_citation_and_replays_only_after_route_revalidation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    job_id, context = _job(root, "answer")
    output = _answer_output(context, summary=False)
    output["citations"][0]["content_hash"] = "b" * 64
    output["answer_sha256"] = _digest(
        canonical_json_bytes({key: value for key, value in output.items() if key != "answer_sha256"})
    )
    fake = _FakeOllama()
    fake.chat_content = json.dumps(output, ensure_ascii=False)

    with fake.running() as base_url:
        client = OllamaClient(OllamaProfile(base_url=base_url))
        stale, stale_code = run_local_route(
            root,
            job_id=job_id,
            client=client,
            pipeline="answer",
            enabled=True,
            authorized=True,
        )

    assert stale_code == 30
    assert stale["status"] == "CONFLICT"
    assert stale["errors"][0]["code"] == "C35_CITATION_DRIFT"
    stale_response = json.loads(
        (root / "runtime/runs" / job_id / "response.json").read_text(encoding="utf-8")
    )
    assert stale_response["status"] == "failed"
    assert stale_response["output"] is None

    root = _fresh_control_copy(tmp_path / "replay")
    job_id, context = _job(root, "answer")
    fake = _FakeOllama()
    fake.chat_content = json.dumps(_answer_output(context, summary=False), ensure_ascii=False)
    with fake.running() as base_url:
        client = OllamaClient(OllamaProfile(base_url=base_url))
        first, first_code = run_local_route(
            root,
            job_id=job_id,
            client=client,
            pipeline="answer",
            enabled=True,
            authorized=True,
        )
        requests_after_first = len(fake.requests)
        replay, replay_code = run_local_route(
            root,
            job_id=job_id,
            client=client,
            pipeline="answer",
            enabled=True,
            authorized=True,
        )

    assert first_code == 0, first
    assert replay_code == 0, replay
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True
    assert len(fake.requests) == requests_after_first
    assert replay["canonical_apply_allowed"] is False

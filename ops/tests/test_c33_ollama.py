from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from vaultops.ollama import OllamaClient, OllamaError, OllamaProfile, run_ollama_job
from vaultops.provider_broker import _answer_output
from vaultops.provider_contract import (
    build_frozen_context,
    build_provider_request,
    canonical_json_bytes,
    write_context_envelope,
    write_provider_request,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]
MODEL = "fake:1b"
MODEL_DIGEST = "a" * 64


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _FakeOllama:
    def __init__(
        self,
        *,
        redirect: bool = False,
        oversized: bool = False,
        version: object = "0.9.0",
    ) -> None:
        self.redirect = redirect
        self.oversized = oversized
        self.version = version
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.chat_content = json.dumps({"ok": True}, separators=(",", ":"))

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
                if owner.redirect and self.path == "/api/version":
                    self.send_response(302)
                    self.send_header("Location", "/api/version-redirected")
                    self.end_headers()
                    return
                if owner.oversized and self.path == "/api/version":
                    self._respond({"version": "x" * 3000})
                    return
                if self.path == "/api/version":
                    self._respond({"version": owner.version})
                elif self.path == "/api/tags":
                    self._respond({"models": [{"name": MODEL, "digest": f"sha256:{MODEL_DIGEST}"}]})
                elif self.path == "/api/ps":
                    self._respond({"models": [{"name": MODEL}]})
                else:
                    self._respond({"error": "not found"}, status=404)

            def do_POST(self) -> None:
                payload = self._payload()
                self._record(payload)
                if self.path == "/api/show":
                    self._respond({"details": {"family": "fake"}, "model_info": {}})
                elif self.path == "/api/chat":
                    self._respond(
                        {
                            "model": MODEL,
                            "message": {"role": "assistant", "content": owner.chat_content},
                            "done": True,
                        }
                    )
                elif self.path == "/api/embed":
                    inputs = payload["input"] if isinstance(payload, dict) else ""
                    count = len(inputs) if isinstance(inputs, list) else 1
                    self._respond({"embeddings": [[0.1, 0.2] for _ in range(count)]})
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


def test_c33_loopback_client_calls_only_bounded_allowlisted_endpoints() -> None:
    fake = _FakeOllama()
    with fake.running() as base_url:
        client = OllamaClient(OllamaProfile(base_url=base_url))

        assert client.version() == "0.9.0"
        assert client.get_version()["version"] == "0.9.0"
        assert client.check_version("0.9.0") == "0.9.0"
        assert client.tags()["models"][0]["name"] == MODEL
        assert client.show(MODEL)["details"]["family"] == "fake"
        assert client.ps()["models"][0]["name"] == MODEL
        assert client.model_digest(MODEL) == MODEL_DIGEST
        identity = client.verify_identity(MODEL, MODEL_DIGEST, expected_version="0.9.0")
        assert identity["auto_pull"] is False
        assert client.chat(MODEL, [{"role": "user", "content": "hello"}])["done"] is True
        assert len(client.embed(MODEL, ["one", "two"])["embeddings"]) == 2

    paths = [path for _, path, _ in fake.requests]
    assert set(paths) == {
        "/api/version",
        "/api/tags",
        "/api/show",
        "/api/ps",
        "/api/chat",
        "/api/embed",
    }
    chat_payload = next(payload for method, path, payload in fake.requests if path == "/api/chat")
    assert chat_payload == {
        "format": "json",
        "keep_alive": 0,
        "messages": [{"content": "hello", "role": "user"}],
        "model": MODEL,
        "options": {},
        "stream": False,
        "think": False,
    }
    embed_payload = next(payload for method, path, payload in fake.requests if path == "/api/embed")
    assert embed_payload["truncate"] is False
    assert embed_payload["keep_alive"] == 0


@pytest.mark.parametrize(
    "profile",
    [
        OllamaProfile(base_url="http://localhost:11434"),
        OllamaProfile(base_url="https://127.0.0.1:11434"),
        OllamaProfile(base_url="http://0.0.0.0:11434"),
        OllamaProfile(base_url="http://127.0.0.1:11434/api"),
        OllamaProfile(cloud_disabled=False),
        OllamaProfile(allow_redirects=True),
        OllamaProfile(allow_proxy=True),
        OllamaProfile(allow_tunnel=True),
        OllamaProfile(auto_pull=True),
    ],
)
def test_c33_rejects_non_loopback_or_unsafe_profiles(profile: OllamaProfile) -> None:
    with pytest.raises(OllamaError):
        OllamaClient(profile)


def test_c33_rejects_redirects_and_oversized_responses_without_following_or_fallback() -> None:
    redirecting = _FakeOllama(redirect=True)
    with redirecting.running() as base_url:
        with pytest.raises(OllamaError, match="redirect") as error:
            OllamaClient(base_url).version()
        assert error.value.code == "OLLAMA_REDIRECT_REJECTED"
        assert len(redirecting.requests) == 1

    oversized = _FakeOllama(oversized=True)
    with oversized.running() as base_url:
        profile = OllamaProfile(base_url=base_url, max_response_bytes=1024)
        with pytest.raises(OllamaError) as error:
            OllamaClient(profile).version()
        assert error.value.code == "OLLAMA_RESPONSE_TOO_LARGE"
        assert len(oversized.requests) == 1


def test_c33_rejects_unpinned_or_unsafe_requests() -> None:
    fake = _FakeOllama(version="not a version")
    with fake.running() as base_url:
        client = OllamaClient(base_url)
        with pytest.raises(OllamaError) as error:
            client.version()
        assert error.value.code == "OLLAMA_VERSION_INVALID"
        with pytest.raises(OllamaError) as error:
            client.show("latest")
        assert error.value.code == "OLLAMA_MODEL_TAG_UNPINNED"
        with pytest.raises(OllamaError) as error:
            client.embed(MODEL, "text", truncate=True)
        assert error.value.code == "OLLAMA_TRUNCATION_FORBIDDEN"


def _copy_adapter_contract(root: Path) -> None:
    target = root / "ops/schemas/answer.schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CONTROL_ROOT / "ops/schemas/answer.schema.json", target)


def _write_answer_job(root: Path) -> tuple[str, dict[str, Any]]:
    _copy_adapter_contract(root)
    job_id = str(uuid.uuid4())
    excerpt = "The fake C33 service returns a bounded answer payload."
    candidate: dict[str, Any] = {
        "note_id": "note-c33-001",
        "path": "40_Knowledge/Notes/C33 Fixture.md",
        "content_hash": "1" * 64,
        "chunk_id": "chunk-c33-001",
        "chunk_hash": "2" * 64,
        "chunk_locator": "# Evidence",
        "index_generation_id": "generation-c33-fixture",
        "excerpt": excerpt,
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "eligibility_class": "local_eligible",
        "channel": "lexical",
        "rank": 1,
        "retrieval_reason": "C33 fake service fixture",
    }
    candidate["candidate_sha256"] = _digest(canonical_json_bytes(candidate))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    schema_bytes = (CONTROL_ROOT / "ops/schemas/answer.schema.json").read_bytes()
    context = build_frozen_context(
        job_id=job_id,
        action="answer",
        prompt="Return one cited JSON answer from the frozen C33 evidence.",
        output_schema={"path": "ops/schemas/answer.schema.json#answer", "sha256": _digest(schema_bytes)},
        policy_decision={
            "decision": "allow_local",
            "policy_sha256": "3" * 64,
            "privacy_policy_sha256": "4" * 64,
            "scope": "c33-test",
            "authorization_sha256": None,
        },
        frozen_candidates=[candidate],
        index_generation_id="generation-c33-fixture",
        source_hashes=[
            {
                "note_id": "note-c33-001",
                "path": "40_Knowledge/Notes/C33 Fixture.md",
                "content_hash": "1" * 64,
                "chunk_hash": "2" * 64,
                "locator": "# Evidence",
            }
        ],
        provider={
            "route": "local:ollama",
            "model_tag": MODEL,
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


def test_c33_adapter_runs_one_c31_job_through_c32_without_canonical_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    job_id, context = _write_answer_job(root)
    fake = _FakeOllama()
    fake.chat_content = json.dumps(_answer_output(context, summary=False), ensure_ascii=False)

    with fake.running() as base_url:
        client = OllamaClient(base_url)
        report, exit_code = run_ollama_job(root, job_id=job_id, client=client)
        replay, replay_code = run_ollama_job(root, job_id=job_id, client=client)

    assert exit_code == 0, report
    assert replay_code == 0, replay
    assert report["status"] == "PASS"
    assert report["provider_called"] is True
    assert report["live_provider_called"] is True
    assert report["synthetic_provider_called"] is False
    assert report["mutation_performed"] is False
    assert report["vault_mutation_performed"] is False
    assert report["canonical_apply_allowed"] is False
    assert report["artifact_kind"] == "answer"
    assert replay["status"] == "NO_OP"
    assert replay["live_provider_called"] is False

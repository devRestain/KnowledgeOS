from __future__ import annotations

import hashlib
import http.client
import io
import json
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from vaultops.cli import _read_broker_token, build_parser
from vaultops.provider_contract import canonical_json_bytes
from vaultops.thin_client import ThinClientError, build_thin_client_request
from vaultops.thin_client_http import (
    ThinClientHttpService,
    VaultThinClientBroker,
    create_thin_client_server,
)

TOKEN = "e05-broker-test-token"
JOB_ID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
NOTE_PATH = "40_Knowledge/Notes/Broker fixture.md"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture_root(tmp_path: Path) -> tuple[Path, bytes, str, str]:
    root = tmp_path / "control"
    vault_note = root / "KnowledgeHub" / NOTE_PATH
    vault_note.parent.mkdir(parents=True)
    content = b"---\nschema_version: 1\ntype: knowledge\ntitle: Broker fixture\n---\n\nThe broker remains proposal-only.\n"
    vault_note.write_bytes(content)
    (root / "ops" / "policies").mkdir(parents=True)
    retrieval = root / "ops" / "policies" / "retrieval.yaml"
    privacy = root / "ops" / "policies" / "privacy.yaml"
    retrieval.write_text("retrieval: fixture\n", encoding="utf-8")
    privacy.write_text("privacy: fixture\n", encoding="utf-8")
    return root, content, _sha256(retrieval.read_bytes()), _sha256(privacy.read_bytes())


def _request(root: Path, endpoint: str, content: bytes, policy_sha256: str, privacy_sha256: str) -> dict[str, Any]:
    return build_thin_client_request(
        job_id=JOB_ID,
        request_id=REQUEST_ID,
        question="What does the broker fixture preserve?",
        scope={
            "kind": "current_note",
            "path": NOTE_PATH,
            "content_sha256": _sha256(content),
        },
        broker_endpoint=endpoint,
        authorization_token=TOKEN,
        policy_sha256=policy_sha256,
        privacy_policy_sha256=privacy_sha256,
        index_generation_id="fixture-generation-1",
        created_at="2026-09-24T00:00:00+00:00",
    )


def _answer_runner(request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    excerpt = "The broker remains proposal-only."
    citation = {
        "note_id": "broker-fixture",
        "path": NOTE_PATH,
        "content_hash": request["scope"]["content_sha256"],
        "chunk_id": "chunk-broker-fixture",
        "chunk_hash": _sha256(excerpt.encode("utf-8")),
        "locator": "# Evidence",
        "index_generation_id": request["policy"]["index_generation_id"],
        "excerpt": excerpt,
        "excerpt_sha256": _sha256(excerpt.encode("utf-8")),
    }
    return {
        "status": "PASS",
        "provider_called": False,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "answer_plaintext": "The broker returned a provider-free proposal-only answer.",
        "citations": [citation],
        "answer_sha256": "5" * 64,
        "uncertainty": "fixture",
    }, 0


def _post(endpoint: str, request: dict[str, Any], authorization: str = f"Bearer {TOKEN}") -> tuple[int, dict[str, Any]]:
    parsed = endpoint.removeprefix("http://127.0.0.1:")
    port_text, path = parsed.split("/", 1)
    connection = http.client.HTTPConnection("127.0.0.1", int(port_text), timeout=5)
    try:
        connection.request(
            "POST",
            f"/{path}",
            body=canonical_json_bytes(request),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": authorization,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()


def test_e05_loopback_broker_rechecks_bindings_and_returns_exact_c41_result(tmp_path: Path) -> None:
    root, content, policy_sha256, privacy_sha256 = _fixture_root(tmp_path)
    broker = VaultThinClientBroker(
        root,
        index_generation_reader=lambda: "fixture-generation-1",
        answer_runner=_answer_runner,
    )
    service = ThinClientHttpService(broker, authorization_token=TOKEN)
    server = create_thin_client_server(service, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        endpoint = f"http://127.0.0.1:{port}/broker"
        request = _request(root, endpoint, content, policy_sha256, privacy_sha256)

        status, response = _post(endpoint, request)

        assert status == 200, response
        assert set(response) == {
            "schema_version",
            "capability",
            "request_sha256",
            "brokered",
            "direct_provider_called",
            "content_digest_rechecked",
            "policy_digest_rechecked",
            "index_digest_rechecked",
            "mutation_performed",
            "canonical_apply_allowed",
            "proposal_only",
            "status",
            "result",
        }
        assert response["status"] == "completed"
        assert response["brokered"] is True
        assert response["direct_provider_called"] is False
        assert response["content_digest_rechecked"] is True
        assert response["policy_digest_rechecked"] is True
        assert response["index_digest_rechecked"] is True
        assert response["mutation_performed"] is False
        assert response["canonical_apply_allowed"] is False
        assert response["result"]["proposal_sha256"] == "5" * 64
        assert response["result"]["citation_actions"][0]["exact_source"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_e05_loopback_broker_rejects_wrong_bearer_and_content_drift(tmp_path: Path) -> None:
    root, content, policy_sha256, privacy_sha256 = _fixture_root(tmp_path)
    broker = VaultThinClientBroker(
        root,
        index_generation_reader=lambda: "fixture-generation-1",
        answer_runner=_answer_runner,
    )
    service = ThinClientHttpService(broker, authorization_token=TOKEN)
    server = create_thin_client_server(service, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        endpoint = f"http://127.0.0.1:{port}/broker"
        request = _request(root, endpoint, content, policy_sha256, privacy_sha256)

        auth_status, auth_payload = _post(endpoint, request, authorization="Bearer wrong-token")

        assert auth_status == 401
        assert auth_payload["errors"][0]["code"] == "C41_AUTH_INVALID"
        (root / "KnowledgeHub" / NOTE_PATH).write_bytes(content + b"drift\n")

        drift_status, drift_payload = _post(endpoint, request)

        assert drift_status == 200
        assert drift_payload["status"] == "conflict"
        assert drift_payload["content_digest_rechecked"] is True
        assert drift_payload["result"]["errors"][0]["code"] == "C41_CONTENT_DRIFT"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_e05_loopback_server_rejects_non_loopback_binding(tmp_path: Path) -> None:
    root, _content, _policy_sha256, _privacy_sha256 = _fixture_root(tmp_path)
    service = ThinClientHttpService(
        VaultThinClientBroker(
            root,
            index_generation_reader=lambda: "fixture-generation-1",
            answer_runner=_answer_runner,
        ),
        authorization_token=TOKEN,
    )

    with pytest.raises(ThinClientError, match="127.0.0.1"):
        create_thin_client_server(service, host="0.0.0.0", port=0)


def test_e05_provider_free_broker_calls_the_existing_vaultctl_answer_seam(tmp_path: Path, monkeypatch) -> None:
    root, content, policy_sha256, privacy_sha256 = _fixture_root(tmp_path)
    calls: list[tuple[Path, str, dict[str, Any]]] = []

    def fake_ask(root_arg: Path, query: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append((root_arg, query, kwargs))
        return {
            "status": "PASS",
            "provider_called": False,
            "mutation_performed": False,
            "answer_plaintext": "provider-free answer seam",
            "citations": [],
        }, 0

    monkeypatch.setattr("vaultops.thin_client_http.ask", fake_ask)
    broker = VaultThinClientBroker(
        root,
        index_generation_reader=lambda: "fixture-generation-1",
    )
    request = _request(
        root,
        "http://127.0.0.1:17900/broker",
        content,
        policy_sha256,
        privacy_sha256,
    )

    response = broker.dispatch(request)

    assert calls == [
        (
            root.resolve(),
            request["question"]["text"],
            {
                "path_prefix": NOTE_PATH,
                "expected_generation_id": "fixture-generation-1",
            },
        )
    ]
    assert response["status"] == "completed"
    assert response["direct_provider_called"] is False
    assert response["result"]["summary"] == "provider-free answer seam"


def test_e05_provider_free_broker_reports_policy_and_index_drift_as_conflict(tmp_path: Path) -> None:
    root, content, policy_sha256, privacy_sha256 = _fixture_root(tmp_path)
    request = _request(
        root,
        "http://127.0.0.1:17900/broker",
        content,
        policy_sha256,
        privacy_sha256,
    )
    (root / "ops" / "policies" / "privacy.yaml").write_text("privacy: drift\n", encoding="utf-8")
    broker = VaultThinClientBroker(
        root,
        index_generation_reader=lambda: "different-generation",
        answer_runner=_answer_runner,
    )

    response = broker.dispatch(request)

    assert response["status"] == "conflict"
    assert {item["code"] for item in response["result"]["errors"]} == {
        "C41_POLICY_DRIFT",
        "C41_INDEX_DRIFT",
    }


def test_e05_vaultctl_parser_exposes_a_separate_broker_entrypoint() -> None:
    serve = build_parser().parse_args(
        [
            "ai",
            "client",
            "--serve",
            "--token-stdin",
            "--port",
            "17900",
            "--root",
            "/tmp/control",
        ]
    )
    legacy = build_parser().parse_args(["ai", "client", "--request-file", "fixture.json"])

    assert serve.serve is True
    assert serve.request_file is None
    assert serve.token_stdin is True
    assert serve.token_file is None
    assert serve.port == 17900
    assert legacy.serve is False
    assert legacy.request_file == Path("fixture.json")


def test_e05_broker_token_reader_is_bounded_private_and_non_whitespace(tmp_path: Path, monkeypatch) -> None:
    token_path = tmp_path / "broker.token"
    token_path.write_text("fixture-token\n", encoding="utf-8")
    token_path.chmod(0o600)

    assert _read_broker_token(token_path, from_stdin=False) == "fixture-token"

    monkeypatch.setattr("sys.stdin", io.StringIO("stdin-token\n"))
    assert _read_broker_token(None, from_stdin=True) == "stdin-token"

    token_path.chmod(0o644)
    with pytest.raises(ThinClientError, match="0600"):
        _read_broker_token(token_path, from_stdin=False)

    token_path.chmod(0o600)
    token_path.write_text("bad token\n", encoding="utf-8")
    with pytest.raises(ThinClientError, match="non-whitespace"):
        _read_broker_token(token_path, from_stdin=False)

    token_path.write_bytes(b"x" * 257)
    with pytest.raises(ThinClientError, match="too large"):
        _read_broker_token(token_path, from_stdin=False)

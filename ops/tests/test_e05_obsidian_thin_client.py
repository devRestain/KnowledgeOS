from __future__ import annotations

import json
import re
from pathlib import Path

CONTROL_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = CONTROL_ROOT / "ops" / "clients" / "obsidian-thin-client"


def _source() -> str:
    return (CLIENT_ROOT / "main.js").read_text(encoding="utf-8")


def test_e05_package_is_desktop_removable_and_records_separate_live_gates() -> None:
    manifest = json.loads((CLIENT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    contract = json.loads((CLIENT_ROOT / "contract.json").read_text(encoding="utf-8"))

    assert manifest == {
        "id": "knowledgeos-thin-client",
        "name": "KnowledgeOS Thin Client",
        "version": "0.1.0",
        "minAppVersion": "1.5.0",
        "description": "Removable proposal-only Obsidian presentation client for the KnowledgeOS loopback broker.",
        "author": "KnowledgeOS",
        "isDesktopOnly": True,
    }
    assert contract["capability"] == "C41"
    assert contract["commands"] == ["open-review", "ask-current-note"]
    assert contract["broker"] == {
        "transport": "authenticated_http_loopback",
        "host": "127.0.0.1",
        "path": "/broker",
        "control_adapter": "vaultops.thin_client_http",
        "answer_route": "vaultctl ai ask",
        "server_entrypoint": "vaultctl ai client --serve",
        "token_sources": ["--token-file", "--token-stdin"],
        "endpoint_is_configurable": True,
        "token_persistence": "none",
    }
    assert contract["evidence_boundary"] == {
        "artifact": "implemented",
        "installation": "not_run",
        "device": "not_run",
        "broker_deployment": "not_run",
        "provider_activation": "not_run",
    }
    assert not (CLIENT_ROOT / "data.json").exists()


def test_e05_source_implements_exact_c41_request_and_loopback_boundary() -> None:
    source = _source()

    for required in (
        'const CAPABILITY = "C41"',
        'const OPERATION = "ai thin client"',
        'action: "answer"',
        "proposal_only: true",
        "canonical_apply_allowed: false",
        "request.broker.auth_sha256 = await authorizationDigest(token, request)",
        "request.request_sha256 = await requestDigest(request)",
        'parsed.hostname !== "127.0.0.1"',
        'Authorization: `Bearer ${token}`',
        'requestUrl({',
        'scopeKind === "selection"',
        "sha256Hex(contentText)",
        "sha256Hex(selected)",
    ):
        assert required in source

    assert "localhost" not in source
    assert "0.0.0.0" not in source
    assert 'brokerEndpoint: "http://127.0.0.1:17900/broker"' in source
    assert "authToken" not in source
    assert "authorizationToken" not in source


def test_e05_source_is_presentation_only_and_has_no_shell_or_writer_path() -> None:
    source = _source()

    assert not re.search(r'require\(["\'](?:child_process|fs|net|http)["\']\)', source)
    for forbidden in (
        "app.vault.modify(",
        "app.vault.create(",
        "app.vault.delete(",
        "child_process",
        "spawn(",
        "execFile(",
        "ollama",
        "canonical_apply_allowed: true",
    ):
        assert forbidden not in source
    assert "Approve for C19 review" in source
    assert "Bounded diff preview" in source
    assert "Exact citations" in source
    assert "session-only handoff to C19 review" in source


def test_e05_source_does_not_persist_the_broker_token() -> None:
    source = _source()
    settings_region = source[source.index("const DEFAULT_SETTINGS"): source.index("const MAX_QUESTION_BYTES")]
    save_region = source[source.index("async saveSettings()"): source.index("module.exports")]

    assert "token" not in settings_region.lower()
    assert "token" not in save_region.lower()
    assert 'this.tokenInput.value = ""' in source

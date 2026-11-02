"""Failure-boundary tests only; no Docker, Ollama, model or private-root access."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from support import ollama_embedding_smoke as smoke


def isolated_host(monkeypatch):
    snapshot = {"domain": {"synthetic-note": "digest"}, "profiles": {"config": "digest"}}
    identity = [{"name": smoke.MODEL, "digest": smoke.DIGEST, "size": 1}]
    observations = []

    def private_snapshot():
        observations.append("preservation")
        return snapshot

    monkeypatch.setattr(smoke, "private_snapshot", private_snapshot)
    monkeypatch.setattr(smoke, "selected_identity", lambda: identity)
    monkeypatch.setattr(smoke, "loaded", list)
    monkeypatch.setattr(smoke, "runner_resident_memory", dict)
    return observations, identity


def test_unexpected_host_type_error_propagates_after_preservation_and_failure_evidence(tmp_path, monkeypatch):
    observations, identity = isolated_host(monkeypatch)
    attempts = 0

    def selected_identity():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TypeError("private diagnostic must not be exported")
        return identity

    monkeypatch.setattr(smoke, "selected_identity", selected_identity)
    evidence = tmp_path / "failure.json"
    with pytest.raises(TypeError):
        smoke.host(evidence)
    report = json.loads(evidence.read_text())
    assert report["status"] == "FAIL" and observations == ["preservation", "preservation"]
    assert report["cleanup"]["complete"] is True
    assert report["preservation"]["domain_unchanged"] is True
    assert "private diagnostic" not in evidence.read_text()


def test_expected_host_transport_failure_is_redacted_and_preserved(tmp_path, monkeypatch):
    observations, identity = isolated_host(monkeypatch)
    attempts = 0

    def selected_identity():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("private transport diagnostic")
        return identity

    monkeypatch.setattr(smoke, "selected_identity", selected_identity)
    evidence = tmp_path / "failure.json"
    assert smoke.host(evidence) == 1
    report = json.loads(evidence.read_text())
    assert report["error"] == "OSError" and observations == ["preservation", "preservation"]
    assert report["preservation"]["profiles_unchanged"] is True
    assert "private transport diagnostic" not in evidence.read_text()


def test_unexpected_cleanup_error_cannot_skip_preservation_or_publish_pass(tmp_path, monkeypatch):
    observations, _ = isolated_host(monkeypatch)
    process = SimpleNamespace(
        stdin=io.StringIO(),
        stdout=io.StringIO(json.dumps({"kind": "result", "result": {"status": "PASS"}}) + "\n"),
        wait=lambda **kwargs: 0,
    )
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *args, **kwargs: process)

    def failed_cleanup(*args):
        raise TypeError("unexpected cleanup implementation error")

    monkeypatch.setattr(smoke, "cleanup_container", failed_cleanup)
    evidence = tmp_path / "failure.json"
    with pytest.raises(TypeError):
        smoke.host(evidence)
    report = json.loads(evidence.read_text())
    assert report["container_result"]["status"] == "PASS"
    assert report["status"] == "FAIL" and observations == ["preservation", "preservation"]
    assert report["preservation"]["domain_unchanged"] is True
    assert report["preservation"]["profiles_unchanged"] is True

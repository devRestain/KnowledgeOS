from __future__ import annotations

import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

CONTROL_ROOT = Path(__file__).resolve().parents[2]
if str(CONTROL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(CONTROL_ROOT / "scripts"))

from e02_runner_support import job_directory

from vaultops.ollama import OllamaClient, OllamaProfile


def test_e02_host_launcher_accepts_only_the_project_job_spool(tmp_path: Path) -> None:
    del tmp_path
    job_id = "123e4567-e89b-42d3-a456-426614174000"
    expected = CONTROL_ROOT / "runtime" / "runs" / job_id

    with pytest.raises(ValueError, match="exactly runtime/runs"):
        job_directory("/tmp/other/runtime/runs/" + job_id)
    with pytest.raises(ValueError, match="existing private directory"):
        job_directory(str(expected))


def test_e02_fallback_is_profile_gated_and_keeps_the_canonical_service_networkless() -> None:
    compose = (CONTROL_ROOT / "ops" / "compose.yaml").read_text(encoding="utf-8")
    service = compose.split("  e02-live:\n", 1)[1].split("\nvolumes:", 1)[0]

    assert 'profiles: ["e02-live"]' in service
    assert "target: e02-live" in service
    assert "read_only: true" in service
    assert "network_mode: none" in service
    assert 'cap_drop: ["ALL"]' in service
    assert "no-new-privileges:true" in service
    assert "cpus: \"2.0\"" in service
    assert "mem_limit: 4g" in service
    assert "pids_limit: 64" in service
    assert "../KnowledgeHub" not in service
    assert ".git" not in service
    assert "host.docker.internal" not in service
    assert "network_mode: host" not in service

    launcher = (CONTROL_ROOT / "scripts" / "e02-host-runner").read_text(encoding="utf-8")
    fallback = (CONTROL_ROOT / "scripts" / "e02-live-fallback").read_text(encoding="utf-8")
    assert "mise exec -- uv run" in launcher
    assert "--frozen --no-sync --no-dev" in launcher
    assert "--profile" in fallback or "e02_live_fallback.py" in fallback


def test_e02_unix_relay_transport_never_needs_a_tcp_loopback_connection(tmp_path: Path) -> None:
    socket_path = tmp_path / "ollama.sock"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/api/version"
            payload = b'{"version":"0.9.0"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    socket_path.chmod(0o600)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OllamaClient(
            OllamaProfile(base_url="http://127.0.0.1:11434", relay_socket=socket_path)
        )
        assert client.version() == "0.9.0"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

#!/usr/bin/env python3
"""Relay one isolated e02-live job from a Unix socket to loopback Ollama.

The relay is intentionally a short-lived, per-job HTTP forwarder. It accepts
only the bounded Ollama endpoints, never listens on TCP, and is started only
by the optional container fallback after explicit live-service authorization.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socketserver
import stat
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from e02_runner_support import job_directory, private_relay_socket

_ALLOWED_PATHS = frozenset(
    {
        "/api/version",
        "/api/tags",
        "/api/show",
        "/api/ps",
        "/api/chat",
        "/api/embed",
    }
)
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 256 * 1024
_HOST = "127.0.0.1"
_PORT = 11434


class _RelayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.request_count = 0
        super().__init__(str(socket_path), _RelayHandler)


class _RelayHandler(BaseHTTPRequestHandler):
    server: _RelayServer
    protocol_version = "HTTP/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _error(self, status: int, code: str) -> None:
        payload = json.dumps({"error": code}, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _request_body(self) -> bytes | None:
        if self.command == "GET":
            if self.headers.get("Content-Length") not in {None, "0"}:
                raise ValueError("GET body is not allowed")
            return None
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("chunked relay requests are not allowed")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("POST content length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("POST content length is invalid") from error
        if length < 0 or length > _MAX_REQUEST_BYTES:
            raise ValueError("POST body exceeds the relay limit")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("POST body is truncated")
        return body

    def _forward(self) -> None:
        if self.path not in _ALLOWED_PATHS or self.command not in {"GET", "POST"}:
            self._error(404, "E02_RELAY_PATH_FORBIDDEN")
            return
        if self.server.request_count >= 32:
            self._error(429, "E02_RELAY_REQUEST_LIMIT")
            return
        try:
            body = self._request_body()
        except ValueError:
            self._error(400, "E02_RELAY_REQUEST_INVALID")
            return
        self.server.request_count += 1
        headers = {"Accept": "application/json", "Connection": "close"}
        if body is not None:
            if self.headers.get("Content-Type") != "application/json":
                self._error(415, "E02_RELAY_CONTENT_TYPE_INVALID")
                return
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(_HOST, _PORT, timeout=30.0)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                self._error(502, "E02_RELAY_RESPONSE_TOO_LARGE")
                return
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (http.client.HTTPException, OSError, TimeoutError):
            self._error(502, "E02_RELAY_UPSTREAM_UNAVAILABLE")
        finally:
            connection.close()


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Run one private E02 host relay")
    parser.add_argument("--socket", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    socket_path = args.socket.expanduser()
    if not socket_path.is_absolute() or socket_path.name != ".ollama-relay.sock":
        print("E02 relay socket must be an absolute private job-spool path", file=sys.stderr)
        return 10
    try:
        job_dir = job_directory(str(socket_path.parent))
    except ValueError as error:
        print(f"E02 relay job rejected: {error}", file=sys.stderr)
        return 10
    if socket_path != private_relay_socket(job_dir):
        print("E02 relay socket is outside the selected job spool", file=sys.stderr)
        return 10
    if os.environ.get("OLLAMA_HOST") != f"{_HOST}:{_PORT}" or os.environ.get("OLLAMA_NO_CLOUD") != "1":
        print("E02 relay requires explicit loopback and cloud-off environment", file=sys.stderr)
        return 10
    if socket_path.exists() or socket_path.is_symlink():
        print("E02 relay socket already exists", file=sys.stderr)
        return 10

    old_umask = os.umask(0o177)
    server: _RelayServer | None = None
    try:
        server = _RelayServer(socket_path)
        socket_path.chmod(0o600)

        def stop(_signum: int, _frame: Any) -> None:
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.2)
    except (OSError, ValueError) as error:
        print(f"E02 relay failed: {error}", file=sys.stderr)
        return 10
    finally:
        os.umask(old_umask)
        if server is not None:
            server.server_close()
        if not socket_path.is_symlink() and socket_path.exists():
            try:
                if stat.S_ISSOCK(socket_path.stat().st_mode):
                    socket_path.unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

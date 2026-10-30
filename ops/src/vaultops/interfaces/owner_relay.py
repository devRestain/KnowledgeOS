"""No-state HTTP relay from host loopback publication to an internal owner.

The relay container has no Vault, State, Runtime, or credential mounts. Compose
publishes only its port on host 127.0.0.1; the upstream is fixed to the owner
service on a separate internal network. Importing this module starts nothing.
"""

from __future__ import annotations

import json
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 1048576


def _handler(upstream_host: str, upstream_port: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            # Never log a note selector, request body, or memory-only token.
            return

        def do_POST(self) -> None:
            if (not self.path.startswith("/owner/") or len(self.path) > 128
                    or "?" in self.path or "#" in self.path
                    or self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json"):
                self._reply(404, {"status": "FAIL", "message": "Unknown owner route"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_REQUEST_BYTES:
                self._reply(413, {"status": "FAIL", "message": "Owner body exceeds its bound"})
                return
            try:
                self.connection.settimeout(120)
                body = self.rfile.read(size)
            except OSError:
                self._reply(400, {"status": "FAIL", "message": "Incomplete owner body"})
                return
            if len(body) != size:
                self._reply(400, {"status": "FAIL", "message": "Incomplete owner body"})
                return
            try:
                connection = HTTPConnection(upstream_host, upstream_port, timeout=120)
                try:
                    connection.request("POST", self.path, body=body, headers={
                        "Content-Type": "application/json",
                        "X-KnowledgeOS-CSRF": self.headers.get("X-KnowledgeOS-CSRF", ""),
                    })
                    response = connection.getresponse()
                    result = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(result) > MAX_RESPONSE_BYTES:
                        raise ValueError("Owner response exceeds relay bound")
                    status = response.status
                finally:
                    connection.close()
            except (OSError, HTTPException, ValueError):
                self._reply(502, {"status": "FAIL", "message": "Owner service unavailable"})
                return
            self._reply_bytes(status, result)

        def _reply(self, status: int, value: dict[str, Any]) -> None:
            self._reply_bytes(status, json.dumps(value, separators=(",", ":")).encode("utf-8"))

        def _reply_bytes(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def create_owner_relay(*, upstream_host: str = "owner", upstream_port: int = 17900,
                       listen_port: int = 17900) -> ThreadingHTTPServer:
    """Build an unstarted relay; only the trusted container may choose ports."""
    if upstream_host not in {"owner", "127.0.0.1"} or not 0 < upstream_port <= 65535 or not 0 <= listen_port <= 65535:
        raise ValueError("Owner relay requires a local trusted upstream and ports")
    return ThreadingHTTPServer(("0.0.0.0", listen_port), _handler(upstream_host, upstream_port))


def main() -> None:
    with create_owner_relay() as server:
        server.serve_forever()


if __name__ == "__main__":
    main()

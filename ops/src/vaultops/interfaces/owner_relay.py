"""No-state HTTP relay from host loopback publication to an internal owner.

The relay container has no Vault, State, Runtime, or credential mounts. Compose
publishes only its port on host 127.0.0.1; the upstream is fixed to the owner
service on a separate internal network. Importing this module starts nothing.
"""

from __future__ import annotations

import argparse
import json
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 1048576


def _handler(upstream_host: str, upstream_port: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            # Never log a note selector or request body.
            return

        def _admit_browser(self) -> bool:
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                return False
            try:
                selected = urlsplit("http://" + hosts[0])
                port = selected.port
            except ValueError:
                return False
            if (selected.hostname not in {"127.0.0.1", "localhost"}
                    or selected.username or selected.password or selected.path
                    or selected.query or selected.fragment
                    or port is not None and not 0 < port <= 65535):
                return False
            origin = self.headers.get("Origin")
            return (self.headers.get("Sec-Fetch-Site") != "cross-site"
                    and (origin is None or origin == "http://" + hosts[0]))

        def do_GET(self) -> None:
            if not self._admit_browser():
                self._reply(403, {"status": "FAIL", "message": "Foreign browser ingress is denied"})
                return
            if self.path not in {
                "/", "/console", "/console/", "/console/app.js", "/console/style.css",
            }:
                self._reply(404, {"status": "FAIL", "message": "Unknown console asset"})
                return
            self._forward("GET")

        def do_POST(self) -> None:
            if not self._admit_browser():
                self._reply(403, {"status": "FAIL", "message": "Foreign browser ingress is denied"})
                return
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
            self._forward("POST", body)

        def _forward(self, method: str, body: bytes | None = None) -> None:
            try:
                connection = HTTPConnection(upstream_host, upstream_port, timeout=120)
                try:
                    headers = {}
                    if method == "POST":
                        headers = {
                            "Content-Type": "application/json",
                        }
                        # Validate the public browser origin above, then bind the
                        # internal request to this fixed upstream, never a header target.
                        if self.headers.get("Origin") is not None:
                            headers["Origin"] = f"http://{upstream_host}:{upstream_port}"
                    connection.request(method, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    result = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(result) > MAX_RESPONSE_BYTES:
                        raise ValueError("Owner response exceeds relay bound")
                    status = response.status
                    response_headers = {
                        key: value for key, value in response.getheaders()
                        if key.lower() in {
                            "content-type", "content-security-policy", "referrer-policy",
                            "cross-origin-resource-policy", "x-content-type-options",
                        }
                    }
                finally:
                    connection.close()
            except (OSError, HTTPException, ValueError):
                self._reply(502, {"status": "FAIL", "message": "Owner service unavailable"})
                return
            self._reply_bytes(status, result, response_headers)

        def _reply(self, status: int, value: dict[str, Any]) -> None:
            self._reply_bytes(status, json.dumps(value, separators=(",", ":")).encode("utf-8"))

        def _reply_bytes(self, status: int, body: bytes,
                         headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for key, value in (headers or {"Content-Type": "application/json; charset=utf-8"}).items():
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def create_owner_relay(*, upstream_host: str = "owner", upstream_port: int = 17900,
                       listen_port: int = 17900) -> ThreadingHTTPServer:
    """Build an unstarted relay; only the trusted container may choose ports."""
    if upstream_host not in {"owner", "owner-http", "127.0.0.1"} or not 0 < upstream_port <= 65535 or not 0 <= listen_port <= 65535:
        raise ValueError("Owner relay requires a local trusted upstream and ports")
    return ThreadingHTTPServer(("0.0.0.0", listen_port), _handler(upstream_host, upstream_port))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-host", choices=("owner", "owner-http"), default="owner")
    parser.add_argument("--upstream-port", type=int, default=17900)
    parser.add_argument("--listen-port", type=int, default=17900)
    arguments = parser.parse_args()
    with create_owner_relay(upstream_host=arguments.upstream_host, upstream_port=arguments.upstream_port,
                            listen_port=arguments.listen_port) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()

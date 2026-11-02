"""Explicitly started, local-only HTTP adapter for owner Web calls.

Composition of Director, Teams, Temporal and models remains the caller's
responsibility. Importing this module never starts a listener or a service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any

from ..adapters.core import AdmissionError
from ..application.exops import KnowledgeExOps, OwnerActor
from .exops_web import ExOpsWebBackend


@dataclass(frozen=True)
class LoopbackIngress:
    owner_operation_id: str
    method: str
    resource_path: str
    body: bytes


def create_loopback_owner(exops: KnowledgeExOps, *, actor_id: str,
                          admit_request: Any = None, admit_template: Any = None,
                          port: int = 17900, bind_host: str = "127.0.0.1",
                          run_control: Any = None, selected_methods: Any = None,
                          admit_selected_request: Any = None) -> ThreadingHTTPServer:
    """Return an unstarted local owner server, without login or session state.

    The ``owner`` hostname is reserved for a single private owner network.
    Dual-network writable Owners use ``owner-http``, aliased only on the HTTP
    network. Neither publishes a port; the relay publishes host loopback.
    """
    if not actor_id or not 0 <= port <= 65535 or bind_host not in {"127.0.0.1", "owner", "owner-http"}:
        raise AdmissionError("BINDING_INVALID", "Owner needs an exact actor, bind host and port")
    backend = ExOpsWebBackend(
        exops, OwnerActor(actor_id, "web"), admit_request=admit_request,
        admit_template=admit_template, run_control=run_control, selected_methods=selected_methods,
        admit_selected_request=admit_selected_request)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            # Note selectors and request contents are never sent to logs.
            return

        def _valid_host(self) -> bool:
            hosts = self.headers.get_all("Host", [])
            names = {"127.0.0.1", "localhost", bind_host}
            allowed = {f"{name}:{self.server.server_port}" for name in names}
            if self.server.server_port == 80:
                allowed.update(names)
            return len(hosts) == 1 and hosts[0] in allowed

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; form-action 'none'; "
                             "frame-ancestors 'none'")

        def do_GET(self) -> None:
            if not self._valid_host():
                self._reply(403, {"status": "FAIL", "message": "Foreign host is denied"})
                return
            # Public shell only: exact names, never a caller-selected filesystem path.
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/console": ("index.html", "text/html; charset=utf-8"),
                "/console/": ("index.html", "text/html; charset=utf-8"),
                "/console/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/console/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            selected = assets.get(self.path)
            if selected is None:
                self._reply(404, {"status": "FAIL", "message": "Unknown console asset"})
                return
            body = files(__package__).joinpath("web_console", selected[0]).read_bytes()
            if len(body) > 262144:
                self._reply(503, {"status": "FAIL", "message": "Console asset exceeds its bound"})
                return
            self.send_response(200)
            self.send_header("Content-Type", selected[1])
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


        def do_POST(self) -> None:
            # Browser requests must come from this local origin.
            origin = self.headers.get("Origin")
            expected_origin = "http://" + str(self.headers.get("Host", ""))
            if (not self._valid_host() or self.headers.get("Sec-Fetch-Site") == "cross-site"
                    or origin is not None and origin != expected_origin):
                self._reply(403, {"status": "FAIL", "message": "Foreign origin is denied"})
                return
            route = self.path.removeprefix("/owner/")
            if (not self.path.startswith("/owner/") or not route
                    or "?" in route or "#" in route
                    or self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json"):
                self._reply(404, {"status": "FAIL", "message": "Unknown owner route"})
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= 16384:
                self._reply(413, {"status": "FAIL", "message": "Owner body exceeds its bound"})
                return
            ingress = LoopbackIngress("knowledgeos", "POST", route, self.rfile.read(length))
            try:
                result = backend(ingress)
            except AdmissionError as error:
                self._reply(409 if error.code in {"RESOURCE_STALE", "INDEX_STALE", "OUTCOME_UNKNOWN"}
                            else 403, {"status": "FAIL", "errors": [{"code": error.code,
                                                                         "message": str(error)}]})
                return
            self._reply(200, result)

        def _reply(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((bind_host, port), Handler)

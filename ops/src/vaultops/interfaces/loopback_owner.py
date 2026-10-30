"""Explicitly started, memory-token, loopback HTTP adapter for owner Web calls.

Composition of Director, Teams, Temporal and models remains the caller's
responsibility. Importing this module never starts a listener or a service.
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..adapters.core import AdmissionError
from ..application.exops import KnowledgeExOps, OwnerActor
from .exops_web import ExOpsWebBackend


@dataclass(frozen=True)
class LoopbackIngress:
    owner_operation_id: str
    actor_login: str
    method: str
    resource_path: str
    body: bytes
    csrf_token: str | None


def create_loopback_owner(exops: KnowledgeExOps, *, actor_id: str,
                          admit_request: Any = None, admit_template: Any = None,
                          port: int = 17900, bind_host: str = "127.0.0.1",
                          session_token: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """Return an unstarted owner server and a session token retained only in memory.

    The ``owner`` hostname is reserved for a service attached only to the
    private owner network, with no published port. The separate relay publishes
    the host loopback port. The default stays process-local for direct callers.
    """
    if not actor_id or not 0 <= port <= 65535 or bind_host not in {"127.0.0.1", "owner"}:
        raise AdmissionError("BINDING_INVALID", "Owner needs an exact actor, bind host and port")
    token = session_token if session_token is not None else secrets.token_urlsafe(32)
    if (not isinstance(token, str) or not 43 <= len(token) <= 128
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                   for character in token)):
        raise AdmissionError("BINDING_INVALID", "Owner session requires a private URL-safe token")
    actor = OwnerActor(actor_id, "web")
    backend = ExOpsWebBackend(
        exops, lambda login: actor if login == actor_id else None,
        lambda selected, supplied: selected == actor and supplied.isascii()
        and hmac.compare_digest(supplied, token),
        admit_request=admit_request, admit_template=admit_template)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            # The session token and note selectors are never sent to logs.
            return

        def do_POST(self) -> None:
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
            ingress = LoopbackIngress("knowledgeos", actor_id, "POST", route,
                                      self.rfile.read(length), self.headers.get("X-KnowledgeOS-CSRF"))
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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((bind_host, port), Handler), token

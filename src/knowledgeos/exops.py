"""ExOps: the loopback HTTP backend shared by the Thin Client, console and CLI (Blueprint §7).

Safety: binds loopback only; the Host header must name this server on 127.0.0.1 or
localhost; an Origin header, when present, must equal ``http://<Host>``; POST bodies must
be ``application/json`` objects; no CORS headers are ever sent; no login or token.

Routes are the core routes below plus routes and read views that capability modules
register (``Registry.route`` / ``Registry.view``). Unknown paths answer 404 JSON.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .owner import BadRequest, Conflict, Owner
from .roles import HermesRunner, ToolDenied, tool_manifest
from .store import StoreError

log = logging.getLogger("knowledgeos.exops")

MAX_BODY = 1 << 20
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".woff2"}


@dataclass
class Req:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


Handler = Callable[[Owner, Req], Any]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, list[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        segments = pattern.strip("/").split("/")
        for existing_method, existing, _ in self._routes:
            if existing_method == method and existing == segments:
                raise ValueError(f"route {method} {pattern} registered twice")
        self._routes.append((method, segments, handler))

    def match(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        parts = path.strip("/").split("/")
        for route_method, segments, handler in self._routes:
            if route_method != method or len(segments) != len(parts):
                continue
            params: dict[str, str] = {}
            for segment, part in zip(segments, parts, strict=True):
                if segment.startswith("{") and segment.endswith("}"):
                    if not part:
                        break
                    params[segment[1:-1]] = unquote(part)
                elif segment != part:
                    break
            else:
                return handler, params
        return None


def _found(value: Any) -> Any:
    return value if value is not None else (404, {"error": "not found"})


def _submit(owner: Owner, req: Req) -> Any:
    assert req.body is not None
    receipt, created = owner.submit(req.body)
    return (202 if created else 200), receipt


def _bad(error: Exception) -> tuple[int, dict[str, str]]:
    return 400, {"error": str(error)}


def _role_tools(owner: Owner, req: Req) -> Any:
    body = req.body or {}
    token = body.get("token")
    runner = owner.runner
    session = runner.session_for(token) if isinstance(runner, HermesRunner) and isinstance(token, str) else None
    if session is None:
        return 403, {"error": "invalid or expired role binding"}
    if req.path.endswith("/list"):
        if set(body) != {"token"}:
            return 400, {"error": "expected token only"}
        return {"tools": tool_manifest(session)}
    if set(body) != {"token", "tool", "args"} or not isinstance(body.get("tool"), str) or not isinstance(body.get("args"), dict):
        return 400, {"error": "expected token, tool and args object"}
    tool, args = body["tool"], body["args"]
    schema = next((item["inputSchema"] for item in tool_manifest(session) if item["name"] == tool), None)
    if schema is not None:
        props = schema["properties"]
        if set(args) - set(props) or any(key not in args for key in schema["required"]):
            return 400, {"error": "invalid tool arguments"}
        for name, value in args.items():
            kind = props[name]["type"]
            valid = (isinstance(value, str) if kind == "string" else isinstance(value, dict) if kind == "object"
                     else isinstance(value, int) and not isinstance(value, bool))
            if not valid or (kind == "integer" and not 1 <= value <= 20):
                return 400, {"error": f"invalid argument {name}"}
    try:
        return session.call(tool, **args)
    except ToolDenied as error:
        return 403, {"error": str(error)}


def _roles(owner: Owner, req: Req) -> Any:
    entries = owner.roles_view()
    if isinstance(owner.runner, HermesRunner):
        return [{**entry, **owner.runner.profile_state(entry["profile"])} for entry in entries]
    return entries


CORE_ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/api/health", lambda owner, req: owner.health()),
    ("POST", "/api/requests", _submit),
    ("GET", "/api/requests/{request_id}", lambda owner, req: _found(owner.get_request(req.params["request_id"]))),
    ("POST", "/api/search", lambda owner, req: owner.search(req.body or {})),
    ("GET", "/api/works", lambda owner, req: owner.works()),
    ("GET", "/api/works/{work_id}", lambda owner, req: _found(owner.get_work(req.params["work_id"]))),
    ("GET", "/api/notices", lambda owner, req: owner.notices()),
    ("GET", "/api/unknowns", lambda owner, req: owner.unknowns()),
    ("GET", "/api/roles", _roles),
    ("POST", "/api/index/rebuild", lambda owner, req: owner.index_rebuild()),
    ("GET", "/api/index/verify", lambda owner, req: owner.index_verify()),
    ("GET", "/api/vault-check", lambda owner, req: owner.vault_check()),
    ("POST", "/api/role-tools", _role_tools),
    ("POST", "/api/role-tools/list", _role_tools),
]


def build_router(owner: Owner) -> Router:
    router = Router()
    for method, pattern, handler in CORE_ROUTES:
        router.add(method, pattern, handler)
    for route in owner.registry.routes:
        router.add(route.method, route.pattern, route.handler)
    for name, (list_fn, get_fn) in owner.registry.views.items():
        router.add("GET", f"/api/{name}", lambda owner, req, fn=list_fn: fn(owner, req.query))
        if get_fn is not None:
            router.add(
                "GET",
                f"/api/{name}/{{item_id}}",
                lambda owner, req, fn=get_fn: _found(fn(owner, req.params["item_id"])),
            )
    return router


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], exops: ExOps):
        self.exops = exops
        super().__init__(address, _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "KnowledgeOS"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s " + format, self.address_string(), *args)

    # ------------------------------------------------------------ responses

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    # ------------------------------------------------------------ safety

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers.get('Host', '')}"

    # ------------------------------------------------------------ dispatch

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _not_allowed(self) -> None:
        self._read_body()
        self._json(405, {"error": "method not allowed"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

    def _read_body(self) -> bytes | None:
        """Read the declared body (so a rejection never leaves unread bytes); None if too large."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length) if length else b""

    def _dispatch(self, method: str) -> None:
        raw = self._read_body() if method == "POST" else b""
        if not self._host_ok():
            self._json(403, {"error": "forbidden host"})
            return
        if not self._origin_ok():
            self._json(403, {"error": "forbidden origin"})
            return
        split = urlsplit(self.path)
        path = split.path
        body = None
        if method == "POST":
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(415, {"error": "content type must be application/json"})
                return
            if raw is None:
                self.close_connection = True
                self._json(413, {"error": "invalid body length"})
                return
            try:
                body = json.loads(raw or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "body must be JSON"})
                return
            if not isinstance(body, dict):
                self._json(400, {"error": "body must be a JSON object"})
                return
        exops = self.server.exops
        matched = exops.router.match(method, path)
        if matched is None:
            if method == "GET" and not path.startswith(("/api/", "/interops/")):
                self._static(path)
                return
            self._json(404, {"error": "not found"})
            return
        handler, params = matched
        query = {key: values[0] for key, values in parse_qs(split.query).items()}
        req = Req(method, path, params, query, body, {key.lower(): value for key, value in self.headers.items()})
        try:
            result = handler(exops.owner, req)
        except BadRequest as error:
            self._json(*_bad(error))
            return
        except StoreError:
            self._json(404, {"error": "not found"})
            return
        except Conflict:
            self._json(409, {"error": "conflict"})
            return
        except Exception:
            log.exception("%s %s failed", method, path)
            self._json(500, {"error": "internal error"})
            return
        if isinstance(result, tuple):
            self._json(result[0], result[1])
        else:
            self._json(200, result)

    def _static(self, path: str) -> None:
        root = self.server.exops.frontend
        name = "index.html" if path in ("", "/") else unquote(path.lstrip("/"))
        target = (root / name).resolve()
        if (
            not target.is_relative_to(root)
            or target.suffix not in STATIC_TYPES
            or not target.is_file()
            or any(part.startswith(".") for part in Path(name).parts)
        ):
            self._json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)


class ExOps:
    """The HTTP server for one Owner. ``port=0`` binds an ephemeral port."""

    def __init__(self, owner: Owner, port: int | None = None):
        self.owner = owner
        self.router = build_router(owner)
        self.frontend = (owner.config.root / "frontend").resolve()
        host = "127.0.0.1" if owner.config.host == "localhost" else owner.config.host
        self.server = _Server((host, owner.config.port if port is None else port), self)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> ExOps:
        self._thread = threading.Thread(target=self.server.serve_forever, name="kos-exops", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

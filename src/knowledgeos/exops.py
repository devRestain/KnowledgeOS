"""KnowledgeOS routes, Owner admission and composition of the shared HTTP boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from operationkit.exops import ExOpsServer, ExposurePolicy, Handler, Req, build_router
from operationkit.sites import PublishedSite

from .owner import BadRequest, Conflict, Owner
from .roles import HermesRunner, ToolDenied, tool_manifest
from .store import StoreError


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


def owner_router(owner: Owner):
    router = build_router(CORE_ROUTES)
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


def _owner_error(error: Exception) -> tuple[int, dict[str, str]] | None:
    if isinstance(error, BadRequest):
        return _bad(error)
    if isinstance(error, StoreError):
        return 404, {"error": "not found"}
    if isinstance(error, Conflict):
        return 409, {"error": "conflict"}
    return None


def create_server(owner: Owner, port: int | None = None) -> ExOpsServer:
    """Keep all routes/effects with this Owner; optionally mount only its published site."""
    settings = owner.config.settings.get("exops", {})
    policy = ExposurePolicy(
        bind_host=owner.config.host,
        bind_port=owner.config.port if port is None else port,
        allowed_hosts=tuple(settings.get("allowed_hosts", ())),
        allowed_origins=tuple(settings.get("allowed_origins", ())),
    )
    sites_root = Path(settings.get("sites_root") or owner.config.root.parents[1] / "Sites")
    site = PublishedSite(sites_root) if (sites_root / "export-manifest.json").is_file() else None
    return ExOpsServer(
        owner, policy=policy, router=owner_router(owner), static_root=owner.config.root / "frontend",
        site=site, owner_operation_id=owner.config.operation_id, on_error=_owner_error,
    )

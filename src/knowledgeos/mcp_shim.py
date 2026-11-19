"""Session-local MCP stdio server. Hermes launches this from the ACP mcpServers spec.

Only the random KOS_BINDING selects a RoleSession; no model argument selects a Work or
widens scope. No credentials, Vault access, stdout logging, or non-stdlib dependencies.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


class OwnerClient:
    def __init__(self, endpoint: str, token: str):
        address = urlsplit(endpoint)
        if (address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1")
                or address.username or address.password or address.path not in ("", "/")
                or address.query or address.fragment or not token):
            raise ValueError("a loopback Owner URL and role binding are required")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path: str, **body: Any) -> Any:
        data = json.dumps({"token": self.token, **body}).encode("utf-8")
        request = urllib.request.Request(self.endpoint + path, data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=25) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get("error", "Owner refused the call")
            except (ValueError, AttributeError):
                message = "Owner refused the call"
            raise ValueError(str(message)) from error
        except (OSError, urllib.error.URLError, ValueError) as error:
            # A timed-out submission may already have been admitted. Never retry here.
            raise ValueError("Owner transport lost; outcome may be unknown; do not repeat the call") from error


class Server:
    def __init__(self, owner: OwnerClient):
        self.owner = owner
        self.tools: list[dict[str, Any]] | None = None

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if "id" not in request:
            return None
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        method, params = request.get("method"), request.get("params", {})
        if not isinstance(params, dict):
            reply["error"] = {"code": -32602, "message": "params must be an object"}
            return reply
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "knowledgeos-role", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                if self.tools is None:
                    self.tools = self.owner.post("/api/role-tools/list")["tools"]
                result = {"tools": self.tools}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise ValueError("tool name and arguments object required")
                try:
                    value = self.owner.post("/api/role-tools", tool=name, args=args)
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                              "isError": False}
                except ValueError as error:
                    result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            else:
                reply["error"] = {"code": -32601, "message": "method not found"}
                return reply
            reply["result"] = result
        except (ValueError, KeyError, TypeError) as error:
            reply["error"] = {"code": -32602, "message": str(error)}
        return reply


def main() -> int:
    try:
        server = Server(OwnerClient(os.environ.get("KOS_OWNER_URL", ""), os.environ.get("KOS_BINDING", "")))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    for line in sys.stdin.buffer:
        try:
            response = server.handle(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The published relay preserves JSON transport without owning domain data."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from vaultops.interfaces.owner_relay import create_owner_relay


def test_relay_forwards_json_and_rejects_invalid_ingress():
    observed = []

    class Owner(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.path, body))
            allowed = self.path == "/owner/search/query" and body == b'{"query":"fixture"}'
            response = json.dumps({"status": "PASS" if allowed else "FAIL"}).encode()
            self.send_response(200 if allowed else 403)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    opener = build_opener(ProxyHandler({}))
    with (ThreadingHTTPServer(("127.0.0.1", 0), Owner) as upstream,
          create_owner_relay(upstream_host="127.0.0.1",
                             upstream_port=upstream.server_port, listen_port=0) as relay):
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (upstream, relay)]
        for thread in threads:
            thread.start()
        url = f"http://127.0.0.1:{relay.server_port}/owner/search/query"

        def post(target: str, body: bytes):
            return opener.open(Request(target, data=body, method="POST", headers={
                "Content-Type": "application/json",
            }), timeout=3)

        try:
            with post(url, b'{"query":"fixture"}') as response:
                assert response.status == 200
                assert json.load(response) == {"status": "PASS"}
                assert response.headers["Cache-Control"] == "no-store"
            with pytest.raises(HTTPError) as invalid_route:
                post(url + "?unexpected=true", b"{}")
            assert invalid_route.value.code == 404
            with pytest.raises(HTTPError) as oversized:
                post(url, b"x" * 16385)
            assert oversized.value.code == 413
            assert observed == [("/owner/search/query", b'{"query":"fixture"}')]
        finally:
            relay.shutdown()
            upstream.shutdown()
            for thread in threads:
                thread.join(timeout=3)

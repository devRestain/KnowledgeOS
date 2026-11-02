"""Exercise installed console assets and actual Owner, without importing source implementation."""
from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
from importlib.resources import files
from pathlib import Path

from support.web_console_smoke import ACTOR, QUERY, SEARCH_SOURCE, compose
from vaultops.interfaces import loopback_owner


def main():
    implementation = Path(loopback_owner.__file__).resolve()
    if "/workspace/" in str(implementation) or "/opt/knowledgeos/ops/src/" not in str(implementation):
        raise RuntimeError("Installed smoke must import the image package, not a source mount")
    with tempfile.TemporaryDirectory(prefix="web-installed-") as directory:
        exops, _officer, _admitted, intake, templates = compose(Path(directory))
        server = loopback_owner.create_loopback_owner(
            exops, actor_id=ACTOR, port=0, admit_request=intake, admit_template=templates)
        before = exops.application.journal.read()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, route, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request(method, route, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        try:
            hashes = {}
            for asset in ("index.html", "app.js", "style.css"):
                path = "/console/" if asset == "index.html" else "/console/" + asset
                status, headers, body = request("GET", path)
                packaged = files("vaultops.interfaces").joinpath("web_console", asset).read_bytes()
                assert status == 200 and body == packaged
                assert headers["Cache-Control"] == "no-store"
                hashes[asset] = hashlib.sha256(body).hexdigest()
            assert request("GET", "/console/../exops_web.py")[0] == 404
            assert request("GET", "/owner/owner/status")[0] == 404
            headers = {"Content-Type": "application/json"}
            status, _headers, body = request("POST", "/owner/owner/status", "{}", headers)
            assert status == 200 and json.loads(body)["runtime_observation"] == "unavailable"
            status, _headers, body = request("POST", "/owner/search/query",
                json.dumps({"query": QUERY, "mode": "lexical"}), headers)
            assert status == 200
            selected = next(item for item in json.loads(body)["results"]
                            if item["path"] == SEARCH_SOURCE)
            assert selected["resource_reference"] == exops.application.reference(SEARCH_SOURCE)
            assert QUERY in selected["excerpt"]
            assert request("POST", "/owner/owner/status", "{}",
                           {**headers, "Origin": "https://foreign.invalid"})[0] == 403
            assert exops.application.journal.read() == before
            print(json.dumps({"installed_module": str(implementation), "assets": hashes,
                              "actual_http": True, "unchanged_owner": True,
                              "browser_proof": False}, sort_keys=True))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


if __name__ == "__main__":
    main()

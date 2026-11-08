"""Exercise installed console assets and actual Owner, without importing source implementation."""
from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import tempfile
import threading
from pathlib import Path

from support.web_console_smoke import ACTOR, administrative_seed, compose
from vaultops.interfaces import loopback_owner


def main():
    implementation = Path(loopback_owner.__file__).resolve()
    if "/workspace/" in str(implementation) or "/opt/knowledgeos/ops/src/" not in str(implementation):
        raise RuntimeError("Installed smoke must import the image package, not a source mount")
    with tempfile.TemporaryDirectory(prefix="web-installed-") as directory:
        exops, _officer, _admitted, intake, templates = compose(Path(directory))
        seed = administrative_seed(exops, intake)
        # Exercise role inputs from the installed package, not source-mounted copies.
        installed_control = implementation.parents[4]
        control = exops.application.roots.control
        for relative in ("ops/config/agent-profiles.json", "ops/config/team-catalog.json",
                         "ops/clients/hermes/role_policy.py"):
            shutil.copyfile(installed_control / relative, control / relative)
        shutil.copytree(installed_control / "ops/prompts/hermes",
                        control / "ops/prompts/hermes", dirs_exist_ok=True)
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
                maintained = Path("/workspace/control/frontend", asset).read_bytes()
                assert status == 200 and body == maintained
                assert headers["Cache-Control"] == "no-store"
                hashes[asset] = hashlib.sha256(body).hexdigest()
            assert request("GET", "/console/../exops_web.py")[0] == 404
            assert request("GET", "/owner/owner/status")[0] == 404
            headers = {"Content-Type": "application/json"}
            status, _headers, body = request("POST", "/owner/owner/status", "{}", headers)
            assert status == 200 and json.loads(body)["runtime_observation"] == "unavailable"
            status, _headers, body = request("POST", "/owner/work/history", "{}", headers)
            history = json.loads(body)
            assert status == 200 and history["works"][0]["work_run_id"] == seed["work_run_id"]
            assert history["works"][0]["run_state"] == "admitted"
            status, _headers, body = request("POST", "/owner/work/inspection",
                json.dumps({"work_run_id": seed["work_run_id"]}), headers)
            inspection = json.loads(body)
            assert status == 200 and inspection["segments"] == [] and inspection["assessment"] is None
            status, _headers, body = request("POST", "/owner/agents/policy", "{}", headers)
            policy = json.loads(body)
            assert status == 200 and policy["availability"] == "available"
            assert len(policy["roles"]) == 37 and len(policy["routes"]) == 5
            officer = next(item for item in policy["roles"] if item["role_id"] == "knowledgeos.normalization_officer")
            assert officer["kind"] == "officer" and officer["purpose"]
            role = next(item for item in policy["roles"] if item["role_id"] == "knowledgeos.director")
            status, _headers, body = request("POST", "/owner/agents/prompt",
                json.dumps({"role_id": role["role_id"]}), headers)
            prompt = json.loads(body)
            assert status == 200 and prompt["availability"] == "available"
            raw = prompt["common"]["text"].encode() + b"\n" + prompt["soul"]["text"].encode()
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == role["prompt_digest"]
            assert raw == ((installed_control / prompt["common"]["path"]).read_bytes() + b"\n"
                           + (installed_control / prompt["soul"]["path"]).read_bytes())
            assert request("POST", "/owner/agents/prompt",
                           json.dumps({"path": "/private/credentials"}), headers)[0] == 403
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

"""Hermes adapters through fake executables, real ACP/MCP stdio and loopback ExOps.

The fake models the gateway's per-session MCP launch, not a direct RoleSession callback.
No installed Hermes, model, credential, Discord or host scheduler is used.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import conforming_note

from knowledgeos import cli
from knowledgeos.config import ROOT, load
from knowledgeos.hermes import SendNotifier, host_from_config
from knowledgeos.roles import HermesRunner


# Written into tmp_path as a Python executable, also shared by the profile/CLI tests.
def fake_main():
    import json
    import os
    import shutil
    import subprocess
    import sys
    import threading
    from pathlib import Path

    import yaml

    root = Path(__file__).parent
    home = Path(os.environ["HERMES_HOME"])
    original = sys.argv[1:]
    args = original[:]
    profile = None
    if args[:1] == ["-p"]:
        profile = args[1]
        args = args[2:]

    def log(event, **fields):
        with (root / "calls.jsonl").open("a") as stream:
            stream.write(json.dumps({"event": event, "profile": profile, "pid": os.getpid(), **fields}) + "\n")

    def install(source, name):
        target = home / "profiles" / name
        manifest = yaml.safe_load((source / "distribution.yaml").read_text())
        target.mkdir(parents=True, exist_ok=True)
        for rel in manifest["distribution_owned"]:
            src, dest = source / rel, target / rel
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            elif src.is_file():
                shutil.copyfile(src, dest)
        manifest["source"] = str(source)
        (target / "distribution.yaml").write_text(yaml.safe_dump(manifest))
        (target / "skills").mkdir(exist_ok=True)
        (target / "memories").mkdir(exist_ok=True)

    log("start", argv=original)
    if args[:2] == ["profile", "install"]:
        install(Path(args[2]), args[args.index("--name") + 1])
        return 0
    if args[:2] == ["profile", "update"]:
        target = home / "profiles" / args[2]
        manifest = yaml.safe_load((target / "distribution.yaml").read_text())
        install(Path(manifest["source"]), args[2])
        return 0
    if args[:1] == ["send"]:
        log("send", argv=original, body=sys.stdin.read())
        result = json.loads((root / "send.json").read_text()) if (root / "send.json").exists() else {"code": 0, "payload": {"success": True}}
        print(result.get("raw", json.dumps(result.get("payload"))), flush=True)
        return result["code"]
    if args != ["acp"]:
        return 2

    modes = json.loads((root / "modes.json").read_text()) if (root / "modes.json").exists() else {}
    mode = modes.get(profile, "ok")
    shim = None
    manifest = []
    prompts = 0

    def rpc(method, params):
        rpc.counter += 1
        shim.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rpc.counter, "method": method, "params": params}) + "\n")
        shim.stdin.flush()
        response = json.loads(shim.stdout.readline())
        assert response["id"] == rpc.counter, response
        assert "error" not in response, response
        return response["result"]

    rpc.counter = 0

    def tool(name, **arguments):
        result = rpc("tools/call", {"name": name, "arguments": arguments})
        log("tool", tool=name, is_error=result.get("isError", False))
        if result.get("isError"):
            return {"error": result["content"][0]["text"]}
        return json.loads(result["content"][0]["text"])

    def play():
        names = [entry["name"] for entry in manifest]
        submit = next(name for name in names if name.startswith("submit_"))
        tool("feedback")
        if submit == "submit_graph":
            tool("team_view")
            payload = {"steps": ["retriever", "synthesizer"], "evaluation": "after_last"}
        elif submit == "submit_team_review":
            view = tool("team_view")
            payload = {"ratings": {name: {"rating": 4, "note": "Useful bounded report"} for name in view["producing"]}, "proposals": []}
        elif submit == "submit_report":
            view = tool("work_context")
            handle = view["focus"]["handles"][0]
            tool("read", handle=handle)
            tool("search", query="Tools", limit=3)
            tool("related", handle=handle, limit=3)
            assert "error" in tool("team_view")  # token never grants the Manager's tools
            sections = {name: [{"text": "Not covered in the Vault.", "label": "not_found", "citations": []}] for name in view["sections"]}
            sections[view["sections"][0]] = [{"text": "Tools shape how we think.", "label": "cited", "citations": [handle]}]
            payload = {"sections": sections}
        elif submit == "submit_evaluation":
            view = tool("eval_view")
            assert "error" in tool("read", handle="e1")
            payload = {"judgments": [{"criterion": item["id"], "target": None, "verdict": "satisfied", "reason": "Supported by the supplied evidence."} for item in view["criteria"]]}
        elif submit == "submit_workspec":
            tool("catalog_view")
            payload = {"refuse": "No offered capability matches this request."}
        else:
            raise AssertionError(submit)
        if mode == "corrective" and prompts == 1:
            payload = {}
        outcome = tool(submit, payload=payload)
        log("submission", ok=outcome.get("ok"), diagnostics=outcome.get("diagnostics"))

    try:
        for line in sys.stdin:
            request = json.loads(line)
            method, params = request.get("method"), request.get("params", {})
            if "id" not in request:
                continue
            reply = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                if mode == "nonzero_setup":
                    return 7
                reply["result"] = {"protocolVersion": 1, "agentCapabilities": {"mcpCapabilities": {}}, "authMethods": []}
            elif method == "session/new":
                if mode == "unavailable":
                    reply["error"] = {"code": -32000, "message": "model route unavailable"}
                else:
                    spec = params["mcpServers"][0]
                    log("new", cwd=params["cwd"], cwd_files=os.listdir(params["cwd"]), server=spec)
                    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "TMPDIR") if key in os.environ}
                    env.update({item["name"]: item["value"] for item in spec["env"]})
                    shim = subprocess.Popen([spec["command"], *spec["args"]], cwd=params["cwd"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "fake-hermes", "version": "1"}})
                    manifest = rpc("tools/list", {})["tools"]
                    assert manifest == rpc("tools/list", {})["tools"]
                    log("manifest", tools=[entry["name"] for entry in manifest])
                    reply["result"] = {"sessionId": "fake-session"}
            elif method == "session/prompt":
                prompts += 1
                log("prompt", number=prompts, session=params["sessionId"], text=params["prompt"][0]["text"])
                if mode == "lost":
                    return 9
                if mode == "timeout":
                    threading.Event().wait(20)
                if mode != "empty":
                    play()
                reply["result"] = {"stopReason": "cancelled" if mode == "bad_stop" else "end_turn"}
            else:
                reply["error"] = {"code": -32601, "message": "method not found"}
            print(json.dumps(reply), flush=True)
    finally:
        if shim is not None:
            shim.stdin.close()
            try:
                shim.wait(timeout=1)
            except subprocess.TimeoutExpired:
                shim.kill()
                shim.wait(timeout=1)
    return 0


def make_fake(tmp_path: Path) -> Path:
    directory = tmp_path / "fake-hermes"
    directory.mkdir()
    binary = directory / "hermes"
    binary.write_text(f"#!{sys.executable}\n" + inspect.getsource(fake_main) + "\nraise SystemExit(fake_main())\n")
    binary.chmod(0o755)
    return binary


def fake_calls(binary: Path, event: str | None = None) -> list[dict]:
    path = binary.parent / "calls.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if event is None or row["event"] == event]


def cli_call(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def fake_config(tmp_path: Path, binary: Path, vault: Path, port: int = 0) -> Path:
    path = tmp_path / "owner.json"
    path.write_text(json.dumps({"vault": str(vault), "state": str(tmp_path / "state"),
                               "runtime": str(tmp_path / "runtime"), "root": str(ROOT), "port": port,
                               "settings": {"hermes": {"binary": str(binary), "home": str(tmp_path / "hermes-home"),
                                                       "setup_timeout_seconds": 10, "turn_timeout_seconds": 10}}}))
    return path


def adapter(tmp_path, vault, make_kos, modes=None):
    binary = make_fake(tmp_path)
    if modes:
        (binary.parent / "modes.json").write_text(json.dumps(modes))
    config_file = fake_config(tmp_path, binary, vault.root)
    code, out, err = cli_call("install", "profiles", "--config", str(config_file))
    assert code == 0, (out, err)
    config = load(config_file)
    runner = HermesRunner(host_from_config(config), config.runtime)
    kos = make_kos(runner=runner)
    runner.owner_url = kos.endpoint
    return kos, binary


def enhance(kos, vault):
    path = "40_Knowledge/Ideas/Tools.md"
    vault.write(path, conforming_note("idea", "seed", "Tools shape how we think.\n"))
    return kos.run("enhance", {"mode": "develop", "path": path, "digest": vault.sha(path)})


def test_ENH_1_hermes_acp_submits_through_real_mcp_and_bound_owner(tmp_path, vault, make_kos):
    kos, binary = adapter(tmp_path, vault, make_kos, {"kos-eval": "corrective"})
    receipt = enhance(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert vault.path(receipt["outcome"]["report"]).is_file()
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    step = next(step for step in work["segments"][0]["steps"] if step["role"] == "synthesizer")
    assert step["invocation"]["profile"] == "kos-synthesizer"
    assert step["invocation"]["model_route"] == "openai-codex/gpt-6.1-terra"
    assert len(step["invocation"]["skill_digest"]) == 64
    starts = [row for row in fake_calls(binary, "start") if row["argv"][-1:] == ["acp"]]
    assert len(starts) == 4  # graph, report, independent evaluation, TeamReview
    prompts = [row for row in fake_calls(binary, "prompt") if row["profile"] == "kos-eval"]
    assert len(prompts) == 2 and len({row["pid"] for row in prompts}) == 1
    assert len({row["session"] for row in prompts}) == 1 and "Diagnostics:" in prompts[1]["text"]
    issued = fake_calls(binary, "new")
    assert all(row["cwd_files"] == [] for row in issued)
    assert all(Path(row["cwd"]).is_relative_to(tmp_path / "runtime") for row in issued)
    assert all(row["server"]["args"] == ["-m", "knowledgeos.mcp_shim"] for row in issued)
    tokens = [{item["name"]: item["value"] for item in row["server"]["env"]}["KOS_BINDING"] for row in issued]
    assert len(tokens) == len(set(tokens))
    assert any(row["tool"] == "team_view" and row["is_error"] for row in fake_calls(binary, "tool"))
    for token in ["not-a-binding", *tokens]:
        assert kos.post("/api/role-tools", {"token": token, "tool": "feedback", "args": {}})[0] == 403


@pytest.mark.parametrize("mode", ["unavailable", "nonzero_setup"])
def test_JOB_3_unavailable_hermes_route_fails_visibly_without_reroute(tmp_path, vault, make_kos, mode):
    kos, binary = adapter(tmp_path, vault, make_kos, {"kos-thinking-manager": mode})
    receipt = enhance(kos, vault)
    assert receipt["status"] == "failed", receipt
    assert "not retried or rerouted" in receipt["notice"]
    assert not list(vault.root.glob("01_AI_Review/Reports/*.md"))
    starts = [row for row in fake_calls(binary, "start") if row["argv"][-1:] == ["acp"]]
    assert len(starts) == 1 and starts[0]["profile"] == "kos-thinking-manager"


def test_ENH_1_lost_acp_transport_retains_unknown_without_resending(tmp_path, vault, make_kos):
    kos, binary = adapter(tmp_path, vault, make_kos, {"kos-synthesizer": "lost"})
    receipt = enhance(kos, vault)
    assert receipt["status"] == "unknown", receipt
    assert "not re-run" in receipt["notice"]
    assert not list(vault.root.glob("01_AI_Review/Reports/*.md"))
    assert any(row["kind"] == "role_invocation" for row in kos.get("/api/unknowns")[1])
    prompts = [row for row in fake_calls(binary, "prompt") if row["profile"] == "kos-synthesizer"]
    assert len(prompts) == 1
    old = fake_calls(binary, "new")[-1]
    token = next(item["value"] for item in old["server"]["env"] if item["name"] == "KOS_BINDING")
    assert kos.post("/api/role-tools", {"token": token, "tool": "submit_report", "args": {"payload": {}}})[0] == 403


def test_SUR_4_role_tools_keep_exops_loopback_json_checks(kos):
    body = json.dumps({"token": "wrong", "tool": "feedback", "args": {}}).encode()
    assert kos.raw("POST", "/api/role-tools", body, {"Content-Type": "application/json", "Origin": "https://other.example"})[0] == 403
    assert kos.raw("POST", "/api/role-tools", body, {"Content-Type": "text/plain"})[0] == 415
    assert kos.post("/api/role-tools", json.loads(body))[0] == 403


@pytest.mark.parametrize(("code", "payload", "raw", "expected"), [
    (0, {"success": True}, None, "delivered"),
    (1, {"success": False, "error": "not configured"}, None, "not_delivered"),
    (1, None, "lost receipt", "unknown"),
    (2, {"error": "usage"}, None, "unknown"),
])
def test_NTF_3_hermes_send_maps_receipts_without_retry(tmp_path, vault, make_kos, code, payload, raw, expected):
    binary = make_fake(tmp_path)
    response = {"code": code, "payload": payload}
    if raw is not None:
        response["raw"] = raw
    (binary.parent / "send.json").write_text(json.dumps(response))
    config = load(fake_config(tmp_path, binary, vault.root))
    notifier = SendNotifier(host_from_config(config), "123456789")
    kos = make_kos(notifier=notifier)
    receipt = kos.run("digest.run", {}, actor="schedule/digest")
    assert receipt["status"] == "completed", receipt
    notice = next(row for row in kos.get("/api/notices")[1] if row["notice_id"] == receipt["outcome"]["notice_id"])
    assert notice["delivery"] == expected
    [sent] = fake_calls(binary, "send")
    assert sent["argv"] == ["-p", "kos-ops", "send", "--to", "discord:123456789", "--json", "--file", "-"]
    assert sent["body"].startswith(notice["title"]) and "notice_id" not in sent["body"]
    kos.get("/api/notices")
    kos.get("/api/health")
    assert len(fake_calls(binary, "send")) == 1


def test_ENH_1_end_turn_without_accepted_submission_is_not_success(tmp_path, vault, make_kos):
    kos, binary = adapter(tmp_path, vault, make_kos, {"kos-thinking-manager": "empty"})
    receipt = enhance(kos, vault)
    assert receipt["status"] == "failed", receipt
    assert not list(vault.root.glob("01_AI_Review/Reports/*.md"))
    prompts = fake_calls(binary, "prompt")
    assert len(prompts) == 2 and len({row["pid"] for row in prompts}) == 1
    assert fake_calls(binary, "submission") == []


def test_SUR_3_serve_wires_configured_hermes_profiles(tmp_path, vault, temporal_settings):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    settings = json.loads(config_file.read_text())
    settings["settings"]["runtime"] = dict(temporal_settings)
    config_file.write_text(json.dumps(settings))
    assert cli_call("install", "profiles", "--config", str(config_file))[0] == 0
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-m", "knowledgeos.cli", "serve", "--config", str(config_file)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert line.startswith("KnowledgeOS ExOps listening on "), line
        client = cli.Client(line.strip().split()[-1])
        receipt = client.request("free_text", {"text": "Do something not in the catalog."}, wait=10)
        assert receipt["status"] == "refused", receipt
        assert len([row for row in fake_calls(binary, "prompt") if row["profile"] == "kos-director"]) == 1
    finally:
        process.terminate()
        process.communicate(timeout=10)
    assert process.returncode == 0


# ---------------------------------------------------------------- credential state (key presence only)

FAKE_POOL = {"credential_pool": {"openai-codex": [{"id": "fixture", "access_token": "fake-not-a-token"}]}}


def write_auth(home: Path, data: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps(data))


def test_CRED_1_credential_is_local_borrowed_or_missing_by_key_presence(tmp_path):
    from knowledgeos.hermes import credential

    root, profile = tmp_path / "root", tmp_path / "root" / "profiles" / "kos-classifier"
    profile.mkdir(parents=True)
    args = {"root": root, "provider": "openai-codex"}
    assert credential(profile, **args) == "needs credential"
    write_auth(root, {"credential_pool": {"copilot": [{"id": "x"}], "openai-codex": []}})
    assert credential(profile, **args) == "needs credential"  # empty list or other provider is not a credential
    write_auth(root, FAKE_POOL)
    assert credential(profile, **args) == "borrowed"
    assert credential(profile, root=root, provider="copilot") == "needs credential"
    assert credential(profile, root=root, provider=None) == "needs credential"
    assert credential(profile, root=root, provider="openai-codex", model=False) == "needs credential"
    # A profile-local login for the provider shadows the root pool.
    write_auth(profile, {"credential_pool": {"openai-codex": [{"id": "local"}]}})
    assert credential(profile, **args) == "present"
    write_auth(profile, {"providers": {"openai-codex": {"tokens": {}}}})
    assert credential(profile, **args) == "present"
    # A profile auth.json for another provider does not hide the root pool.
    write_auth(profile, {"credential_pool": {"copilot": [{"id": "local"}]}})
    assert credential(profile, **args) == "borrowed"
    for broken in ("{", "[]", '{"credential_pool": []}'):
        (root / "auth.json").write_text(broken)
        assert credential(profile, **args) == "needs credential"


def test_CRED_1_kos_ops_discord_env_is_local_and_never_borrowed(tmp_path):
    from knowledgeos.hermes import credential

    root, profile = tmp_path / "root", tmp_path / "root" / "profiles" / "kos-ops"
    profile.mkdir(parents=True)
    write_auth(root, FAKE_POOL)
    assert credential(profile, root=root, provider=None, model=False) == "needs credential"
    (profile / ".env").write_text("DISCORD_BOT_TOKEN=fake\n")
    assert credential(profile, root=root, provider=None, model=False) == "present"


def test_CRED_1_install_and_roles_report_borrowed_root_pool(tmp_path, vault):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    home = tmp_path / "hermes-home"
    write_auth(home, FAKE_POOL)
    code, output, error = cli_call("install", "profiles", "--config", str(config_file))
    assert code == 0, (output, error)
    assert "fake-not-a-token" not in output
    report = json.loads(output)
    assert report["profiles"] and all(entry["credential"] == "borrowed" for entry in report["profiles"])
    config = load(config_file)
    runner = HermesRunner(host_from_config(config), config.runtime)
    profiles = [entry["profile"] for entry in report["profiles"]]
    # HermesRunner.profile_state is the source of GET /api/roles per-role state.
    assert all(runner.profile_state(profile)["credential"] == "borrowed" for profile in profiles)
    # One profile-local login shadows root for that role only; removing root leaves the rest missing.
    write_auth(home / "profiles" / "kos-classifier", {"credential_pool": {"openai-codex": [{"id": "local"}]}})
    (home / "auth.json").unlink()
    after = {profile: runner.profile_state(profile)["credential"] for profile in profiles}
    assert after.pop("kos-classifier") == "present"
    assert set(after.values()) == {"needs credential"}

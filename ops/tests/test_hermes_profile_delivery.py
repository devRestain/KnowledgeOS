"""Private-home alignment is previewable, exact and separate from native authority."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

CONTROL = Path(__file__).resolve().parents[2]
COMMAND = CONTROL / "ops/clients/hermes/align_profiles.py"
POLICY = json.loads((CONTROL / "ops/config/agent-profiles.json").read_bytes())


def homes(tmp_path):
    profiles = tmp_path / "profiles"
    for name in POLICY["home_roles"]:
        home = profiles / name
        home.mkdir(parents=True)
        config = {"model": {"default": "obsolete", "provider": "auto"},
                  "fallback_model": {"provider": "auto", "model": "obsolete"},
                  "private_setting": {"preserve": True}}
        if name == "kos-cited-answer":
            config["mcp_servers"] = {"knowledgeos": {
                "command": "/selected/bin/knowledgeos-mcp-readonly", "args": []}}
        (home / "config.yaml").write_text(yaml.safe_dump(config))
        (home / "SOUL.md").write_text("obsolete prompt")
        (home / "auth.json").write_text("private-auth-must-stay-untouched")
    return profiles


def invoke(profiles, *options):
    return subprocess.run([sys.executable, str(COMMAND), "--control-root", str(CONTROL),
                           "--profiles-root", str(profiles), *options],
                          capture_output=True, text=True, check=False)


def snapshot(profiles):
    return {str(path.relative_to(profiles)): path.read_bytes()
            for path in profiles.rglob("*") if path.is_file()}


def test_alignment_previews_then_applies_only_committed_home_policy(tmp_path):
    profiles = homes(tmp_path)
    before = snapshot(profiles)
    preview = invoke(profiles)
    assert preview.returncode == 0, preview.stdout
    assert json.loads(preview.stdout)["mode"] == "preview"
    assert snapshot(profiles) == before
    applied = invoke(profiles, "--apply")
    assert applied.returncode == 0, applied.stdout
    report = json.loads(applied.stdout)
    assert len(report["homes"]) == 13
    for item in report["homes"]:
        home = profiles / item["home"]
        role = POLICY["profiles"][item["role_id"]]
        route = POLICY["routes"][role["route"]]
        config = yaml.safe_load((home / "config.yaml").read_bytes())
        assert config["model"] == {"default": route["model"], "provider": route["provider"],
                                   "base_url": route["base_url"]}
        assert config["agent"]["reasoning_effort"] == route["reasoning_effort"]
        assert "fallback_model" not in config
        assert config["private_setting"] == {"preserve": True}
        assert (home / "auth.json").read_bytes() == before[item["home"] + "/auth.json"]
        if item["home"] == "kos-cited-answer":
            server = config["mcp_servers"]["knowledgeos"]
            assert server["tools"]["include"] == ["index_status_read", "knowledge_search",
                                                     "knowledge_retrieve", "knowledge_read", "citation_verify"]
            assert server["tools"]["resources"] is False and server["tools"]["prompts"] is False
        else:
            assert config["mcp_servers"] == {}
            assert config["platform_toolsets"]["cli"] == ["no_mcp"]
    assert "private-auth" not in applied.stdout
    repeated = invoke(profiles, "--apply")
    assert repeated.returncode == 0
    assert all(not item["changed_files"] for item in json.loads(repeated.stdout)["homes"])


def test_alignment_preflights_every_home_without_printing_private_values(tmp_path):
    profiles = homes(tmp_path)
    config = profiles / "kos-maintenance-manager/config.yaml"
    config.write_text("invalid: [private-marker")
    before = snapshot(profiles)
    result = invoke(profiles, "--apply")
    assert result.returncode == 1
    assert snapshot(profiles) == before
    assert "private-marker" not in result.stdout + result.stderr


def test_alignment_cli_parses_before_loading_managed_yaml():
    result = subprocess.run([sys.executable, "-S", str(COMMAND), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "--hermes-root" in result.stdout



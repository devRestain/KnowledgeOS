"""C9 isolated native-learning profiles installed from source through the CLI."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from test_hermes_adapter import cli_call, fake_calls, fake_config, make_fake

from knowledgeos.config import ROOT, load
from knowledgeos.hermes import host_from_config
from knowledgeos.roles import HermesRunner, binding


def test_LRN_2_classifier_skill_update_leaves_other_profiles_unchanged(tmp_path, vault, make_kos):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    code, output, error = cli_call("install", "profiles", "--config", str(config_file))
    assert code == 0, (output, error)
    report = json.loads(output)
    assert all(entry["credential"] == "needs credential" for entry in report["profiles"])
    config = load(config_file)
    runner = HermesRunner(host_from_config(config), config.runtime)
    kos = make_kos(runner=runner)
    runner.owner_url = kos.endpoint
    status, before = kos.get("/api/roles")
    assert status == 200 and before
    profile = config.settings["hermes"]["home"]
    classifier = Path(profile) / "profiles/kos-classifier"
    learned = classifier / "skills/classify/SKILL.md"
    learned.parent.mkdir(parents=True)
    learned.write_text("Keep title suggestions precise; reuse admitted tags.\n")
    (classifier / "skills/.curator_ledger.jsonl").write_text(json.dumps({"skill": "classify", "action": "updated", "at": "2026-11-19T09:00:00+09:00"}) + "\n")
    after = kos.get("/api/roles")[1]
    changed = [new["role"] for old, new in zip(before, after, strict=True) if old["skill_digest"] != new["skill_digest"]]
    assert changed == ["classifier"]
    assert all(old == new for old, new in zip(before, after, strict=True) if old["role"] != "classifier")
    state = next(role for role in after if role["role"] == "classifier")
    assert state["ledger"][0]["skill"] == "classify"
    pin = runner.identity(binding(kos.owner.catalog, "classifier", "work-observation", "classifier"))
    assert pin["skill_digest"] == state["skill_digest"] and pin["profile"] == "kos-classifier"
    # Updating declared config/SOUL never wipes native skill or memory data.
    memory = classifier / "memories/learning.txt"
    memory.write_text("Profile-owned memory.\n")
    assert cli_call("install", "profiles", "--config", str(config_file))[0] == 0
    assert learned.read_text() == "Keep title suggestions precise; reuse admitted tags.\n"
    assert memory.read_text() == "Profile-owned memory.\n"
    assert kos.get("/api/roles")[1] == after
    updates = [row["argv"][2] for row in fake_calls(binary, "start") if row["argv"][:2] == ["profile", "update"]]
    assert len(updates) == len(before) + 1 and updates[-1] == "kos-ops"  # every role profile, then the send profile


def test_LRN_2_profiles_pin_routes_tools_and_learning_without_credentials(tmp_path, vault):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    assert cli_call("install", "profiles", "--config", str(config_file))[0] == 0
    config = load(config_file)
    document = json.loads((ROOT / "config/roles.json").read_text())
    common = (ROOT / document["common"]).read_text().rstrip("\n")
    disabled = {"terminal", "file", "browser", "web", "code_execution", "delegation", "cronjob", "vision", "image_gen", "tts", "clarify", "kanban", "computer_use", "connections", "session_search", "todo"}
    for role, declared in document["roles"].items():
        home = Path(config.settings["hermes"]["home"]) / "profiles" / declared["profile"]
        settings = yaml.safe_load((home / "config.yaml").read_text())
        provider, model = declared["model_route"].split("/", 1)
        assert settings["model"] == {"provider": provider, "default": model}, role
        assert settings["fallback_providers"] == []
        assert settings["agent"]["auto_recovery_cycles"] == 0
        assert set(settings["agent"]["disabled_toolsets"]) == disabled
        assert settings["platform_toolsets"] == {"cli": [], "acp": []}
        assert settings["tools"]["tool_search"]["enabled"] == "off"
        assert settings["skills"]["ledger"] is True and settings["curator"]["enabled"] is True
        assert settings["auxiliary"]["background_review"]["enabled"] is True
        assert (home / "SOUL.md").read_text() == common + "\n\n" + (ROOT / declared["soul"]).read_text()
        assert not (home / "auth.json").exists() and not (home / ".env").exists()
        manifest = yaml.safe_load((home / "distribution.yaml").read_text())
        assert "skills" not in manifest["distribution_owned"]
        assert "memories" not in manifest["distribution_owned"]

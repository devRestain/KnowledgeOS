from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.homepage_settings import P08_ALLOWED_DAILY_URI, build_homepage_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p08_registry_binds_one_safe_homepage_target_and_plugin_free_mobile_fallback_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/homepage/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["homepage_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "homepage"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/homepage/manifest.json",
        "id": "homepage",
        "version": "3.6.0",
    }
    assert len(registry["homepage_targets"]) == 1
    target = registry["approved_startup"]
    assert target == {
        "name": "Main Homepage",
        "value": "Home",
        "kind": "File",
        "open_on_startup": True,
        "open_mode": "Replace last note",
        "manual_open_mode": "Replace last note",
        "view": "Reading view",
        "revert_view": True,
        "open_when_empty": True,
        "refresh_dataview": False,
        "auto_create": False,
        "auto_scroll": False,
        "pin": False,
        "commands": [],
        "always_apply": True,
    }
    assert registry["startup_policy"] == {
        "one_target": True,
        "target": "Home.md",
        "open_mode": "Replace last note",
        "view": "Reading view",
        "open_when_empty": True,
        "auto_create": False,
        "refresh_dataview": False,
        "commands": [],
        "auto_run": "forbidden_for_unreviewed_commands",
    }
    assert registry["home_action_inventory"]["home_actions"] == {
        "daily_direction": "pass",
        "now": "pass",
        "needs_a_decision": "pass",
        "next_actions": "pass",
        "knowledge_radar": "pass",
        "inbox": "pass",
        "ai_review": "pass",
        "support_status": "pass",
        "quick_navigation": "pass",
    }
    assert registry["home_action_inventory"]["quick_capture"] == {
        "state": "pass",
        "actions": ["CAPTURE_THOUGHT", "NEW_IDEA", "NEW_PROJECT", "NEW_QUESTION", "NEW_KNOWLEDGE"],
        "hotkeys": ["option_command_c", "option_command_j", "option_command_p", "option_command_q", "option_command_k"],
        "launcher": "quickadd_verified_command_ids_with_hotkeys",
    }
    assert registry["home_action_inventory"]["external_links"] == [P08_ALLOWED_DAILY_URI]
    assert registry["home_action_inventory"]["forbidden_links"] == []
    assert registry["home_action_inventory"]["mobile_fallback"] == {
        "state": "pass",
        "community_plugin_required": False,
    }
    assert registry["mobile_policy"]["community_plugins"] == []
    assert registry["mobile_policy"]["inferred_from_mac"] is False
    assert registry["mobile_policy"]["fallback"] == "KnowledgeHub/Mobile.md"
    assert registry["mobile_policy"]["state"] == "pass"
    assert registry["fallback"] == {
        "canonical_home": "KnowledgeHub/Home.md",
        "canonical_mobile": "KnowledgeHub/Mobile.md",
        "plugin_free": True,
        "dataview_dependency": "not_required",
        "command_palette_or_file_open": "explicit_human_recovery_only",
    }
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "runtime": "not_run",
        "device": "not_run",
        "startup_execution": "not_run",
        "plugin_data_mutation": "out_of_scope",
        "mobile_profile_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p08_registry_keeps_missing_profile_and_home_sources_unknown(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_homepage_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["approved_startup"] is None
    assert registry["startup_policy"]["one_target"] is False
    assert registry["home_action_inventory"]["home_file"] == "unknown"
    assert registry["home_action_inventory"]["mobile_fallback"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"


def test_p08_registry_rejects_unreviewed_startup_commands_auto_create_and_wrong_target(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/homepage"
    plugin_root.mkdir(parents=True)
    vault_root = root / "KnowledgeHub"
    vault_root.mkdir(exist_ok=True)
    (vault_root / "Home.md").write_text("# Home\n", encoding="utf-8")
    (vault_root / "Mobile.md").write_text("# Mobile\nsnapshot\n", encoding="utf-8")
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "homepage", "version": "3.6.0"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "homepages": {
                    "Unsafe": {
                        "value": "Other",
                        "kind": "File",
                        "openOnStartup": True,
                        "openMode": "New pane",
                        "manualOpenMode": "New pane",
                        "view": "Live Preview",
                        "autoCreate": True,
                        "refreshDataview": True,
                        "commands": ["unreviewed-command"],
                    }
                },
                "separateMobile": True,
            }
        ),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_homepage_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P08_STARTUP_TARGET_NOT_HOME" in codes
    assert "P08_OPEN_MODE_NOT_ACCEPTED" in codes
    assert "P08_VIEW_MODE_NOT_ACCEPTED" in codes
    assert "P08_AUTO_CREATE_ENABLED" in codes
    assert "P08_DATAVIEW_REFRESH_ENABLED" in codes
    assert "P08_STARTUP_COMMANDS_NOT_EMPTY" in codes
    assert "P08_HOME_ACTION_UNRESOLVED" in codes

from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.note_toolbar_settings import build_note_toolbar_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p11_registry_binds_reviewed_contextual_actions_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/note-toolbar/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["note_toolbar_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "note-toolbar"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/note-toolbar/manifest.json",
        "id": "note-toolbar",
        "version": "1.25.23",
    }
    assert registry["serialized_version"] == 20250313.1
    assert registry["global_policy"]["state"] == "pass"
    assert registry["global_policy"]["settings"]["scripting_enabled"] == {
        "observed": False,
        "expected": False,
        "state": "pass",
        "policy": "arbitrary scripting remains disabled",
    }
    assert registry["folder_mappings"]["state"] == "pass"
    assert registry["folder_mappings"]["observed"] == {
        "/": {"toolbar_uuid": "86151f9e-d3de-49ff-beec-834b6add2f31", "toolbar_name": "KnowledgeOS Home"},
        "01_AI_Review": {"toolbar_uuid": "66f3ca7e-4c9a-4ead-a6da-dc8329f48aaf", "toolbar_name": "KnowledgeOS Review"},
        "10_Journal": {"toolbar_uuid": "cbe297a3-6e2e-4396-a945-b3003e3e3ab3", "toolbar_name": "KnowledgeOS Daily"},
        "20_Projects": {"toolbar_uuid": "c80aab67-134b-47ca-9e6e-fff3d577ac00", "toolbar_name": "KnowledgeOS Project"},
        "00_Inbox": {"toolbar_uuid": "9121b9b8-1202-493e-a420-a7b8aa5bc09c", "toolbar_name": "KnowledgeOS Inbox"},
        "40_Knowledge": {"toolbar_uuid": "b7472a0a-ad8b-4dc4-bf75-4bff46dc976c", "toolbar_name": "KnowledgeOS Knowledge"},
    }
    assert registry["toolbars"]["state"] == "pass"
    assert registry["toolbars"]["toolbar_names"] == [
        "KnowledgeOS Daily",
        "KnowledgeOS Home",
        "KnowledgeOS Inbox",
        "KnowledgeOS Knowledge",
        "KnowledgeOS Project",
        "KnowledgeOS Review",
    ]
    assert registry["position_policy"] == {
        "desktop": "bottom",
        "mobile": "out_of_scope",
        "tablet": "out_of_scope",
        "source": "KnowledgeHub/.obsidian-mac/plugins/note-toolbar/data.json#/toolbars/*/position",
    }
    for toolbar in registry["toolbars"]["toolbars"]:
        assert toolbar["presentation"]["position_policy"] == {
            "desktop": "bottom",
            "mobile": "out_of_scope",
            "tablet": "out_of_scope",
        }
    assert registry["command_allowlist"]["allowed_ids"] == [
        "backlink:open-backlinks",
        "breadcrumbs:open-tree-view",
        "daily-notes",
        "homepage:open-homepage",
        "outgoing-links:open-for-current",
    ]
    assert registry["home_contract"]["state"] == "pass"
    assert registry["home_contract"]["capture"]["hotkeys"] == [
        "option_command_c",
        "option_command_j",
        "option_command_p",
        "option_command_q",
        "option_command_k",
    ]
    assert registry["home_contract"]["capture"]["display_contract"] == "QuickAdd inventory remains profile-owned and intentionally hidden from Home"
    assert registry["home_contract"]["capture"]["navigation_replacement"] == "desktop_bottom_toolbar_replaces_removed_home_footer"

    actions = registry["action_inventory"]["actions"]
    for action_id in (
        "home",
        "today_daily",
        "weekly_open",
        "monthly_open",
        "tasks",
        "weekly_review",
        "review",
        "inbox",
        "projects",
        "knowledge",
        "sources",
        "backlinks",
        "outgoing_links",
        "breadcrumbs",
        "capture",
    ):
        assert actions[action_id]["state"] == "pass"
    assert actions["capture"]["toolbar_contexts"] == []
    assert actions["capture"]["toolbar_policy"] == "compact_navigation_only"
    assert actions["today_daily"]["mutation_class"] == "core_daily_note_open_or_create"
    assert actions["today_daily"]["human_action_required"] is True
    assert registry["mutation_policy"] == {
        "toolbar_role": "presentation_and_navigation_only",
        "scripting": "forbidden",
        "uri_callbacks": "forbidden",
        "external_processes": "forbidden",
        "network": "forbidden",
        "git": "forbidden",
        "ai": "forbidden",
        "vaultctl": "forbidden",
        "canonical_apply": "forbidden_without_separate_human_workflow",
        "write_capable_action": "human_action_required_with_mutation_class_and_rollback",
        "plugin_data_mutation": "out_of_scope",
        "note_mutation": "out_of_scope",
    }
    assert registry["fallback"]["state"] == "pass"
    assert registry["fallback"]["plugin_free"] is True
    assert registry["fallback"]["command_palette"] == "recovery_or_diagnostic_only"
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "toolbar_execution": "not_run",
        "runtime": "not_run",
        "device": "not_run",
        "command_execution": "not_run",
        "plugin_data_mutation": "out_of_scope",
        "canonical_note_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p11_registry_keeps_missing_profile_and_home_unknown_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_note_toolbar_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["global_policy"]["state"] == "unknown"
    assert registry["folder_mappings"]["state"] == "unknown"
    assert registry["toolbars"]["state"] == "unknown"
    assert registry["home_contract"]["state"] == "unknown"
    assert registry["action_inventory"]["state"] == "unknown"
    assert registry["fallback"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"


def test_p11_registry_rejects_scripts_variables_unverified_commands_and_fallback_drift(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/note-toolbar"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "note-toolbar", "version": "1.25.23"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "scriptingEnabled": True,
                "debugEnabled": True,
                "showLaunchpad": True,
                "showToolbarInFileMenu": True,
                "showToolbarInOther": "unsafe",
                "toolbarProp": "status",
                "rules": [{"when": "unsafe"}],
                "showToolbarIn": {"audio": True},
                "folderMappings": [{"folder": "/", "toolbar": "unknown"}],
                "toolbars": [
                    {
                        "uuid": "unsafe",
                        "name": "KnowledgeOS Home",
                        "items": [
                            {
                                "hasCommand": True,
                                "label": "Unsafe",
                                "link": "javascript:unsafe()",
                                "linkAttr": {
                                    "commandCheck": True,
                                    "commandId": "quickadd:unsafe",
                                    "hasVars": True,
                                    "type": "command",
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "KnowledgeHub/.obsidian-mac/core-plugins.json").write_text(
        json.dumps({"file-explorer": False}),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_note_toolbar_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P11_FORBIDDEN_SURFACE_ENABLED" in codes
    assert "P11_FORBIDDEN_LINK_TARGET" in codes
    assert "P11_ITEM_VARIABLES_ENABLED" in codes
    assert "P11_UNVERIFIED_COMMAND_ID" in codes
    assert "P11_FOLDER_MAPPING_UNRESOLVED" in codes
    assert "P11_TOOLBAR_NAME_MISSING" in codes
    assert "P11_FALLBACK_UNAVAILABLE" in codes

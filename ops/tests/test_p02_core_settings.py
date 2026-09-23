from __future__ import annotations

import json
import shutil
from pathlib import Path

from vaultops.core_settings import CORE_SETTING_POLICIES, P02_POLICY_CLASSES
from vaultops.diagnostics import EXIT_CONFIG_INVALID, plugins_audit_report

CONTROL_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_IDS = [
    "quickadd",
    "templater-obsidian",
    "obsidian-tasks-plugin",
    "obsidian-linter",
    "obsidian-git",
    "homepage",
    "note-toolbar",
    "breadcrumbs",
    "notebook-navigator",
    "obsidian-meta-bind-plugin",
]


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(CONTROL_ROOT / "blueprint", root / "blueprint")
    (root / "ops").mkdir()
    shutil.copy2(CONTROL_ROOT / "ops/vaultops.toml", root / "ops/vaultops.toml")
    profile_root = root / "KnowledgeHub/.obsidian-mac"
    profile_root.mkdir(parents=True)
    (profile_root / "community-plugins.json").write_text(
        json.dumps(PLUGIN_IDS),
        encoding="utf-8",
    )
    (root / "KnowledgeHub/.knowledgeos-root.json").write_text("{}", encoding="utf-8")
    (root / "runtime").mkdir()
    return root


def _core_flags(*, include_unknown: bool = False) -> dict[str, bool]:
    flags = {policy.plugin_id: True for policy in CORE_SETTING_POLICIES}
    if include_unknown:
        flags["future-core-flag"] = True
    return flags


def _registry(report: dict) -> dict:
    return report["setting_registry"]["core_setting_registry"]


def test_p02_registry_covers_observed_flags_and_preserves_ui_unknowns(tmp_path: Path) -> None:
    root = _root(tmp_path)
    profile_root = root / "KnowledgeHub/.obsidian-mac"
    (profile_root / "core-plugins.json").write_text(
        json.dumps(_core_flags()),
        encoding="utf-8",
    )
    (profile_root / "app.json").write_text(
        json.dumps({"propertiesInDocument": "visible"}),
        encoding="utf-8",
    )
    (profile_root / "daily-notes.json").write_text(
        json.dumps(
            {
                "folder": "10_Journal/Daily",
                "template": "99_System/Templates/T10_Daily.md",
            }
        ),
        encoding="utf-8",
    )

    report, exit_code = plugins_audit_report(root)

    assert exit_code == 0, report
    registry = _registry(report)
    assert registry["schema_version"] == 1
    assert registry["policy_classes"] == list(P02_POLICY_CLASSES)
    assert registry["unknown_observed_flags"] == []
    assert set(registry["observed_core_flags"]) == {
        policy.plugin_id for policy in CORE_SETTING_POLICIES
    }
    assert len(registry["entries"]) == len(CORE_SETTING_POLICIES)
    assert {entry["policy_class"] for entry in registry["entries"]} == set(P02_POLICY_CLASSES)
    assert all(
        {
            "workflow",
            "source_locator",
            "allowed_value_domain",
            "fallback",
            "mutation_risk",
            "verification_method",
            "rollback_action",
        }
        <= entry.keys()
        for entry in registry["entries"]
    )

    properties = next(entry for entry in registry["entries"] if entry["component_id"] == "core:properties")
    assert properties["current_value"]["properties_in_document"] == "visible"
    assert properties["setting_state"] == "configured"

    daily = next(entry for entry in registry["entries"] if entry["component_id"] == "core:daily-notes")
    assert daily["current_value"] == {
        "enabled": True,
        "folder": "10_Journal/Daily",
        "template": "99_System/Templates/T10_Daily.md",
    }
    assert daily["setting_state"] == "configured"
    assert daily["source_locator"]["ui_labels"] == ["Daily notes date format"]

    daily_contract = registry["daily_notes_contract"]
    assert daily_contract["expected"] == {
        "folder": "10_Journal/Daily",
        "date_format": "YYYY-MM-DD",
        "template": "99_System/Templates/T10_Daily.md",
    }
    assert daily_contract["configuration_state"] == "configured"
    assert daily_contract["date_format_state"] == "unknown"
    assert registry["core_templates_contract"]["setting_state"] == "unknown"

    for component_id in ("core:file-explorer", "core:bases"):
        fallback = next(item for item in registry["fallbacks"] if item["component_id"] == component_id)
        assert fallback["state"] == "available"
    for plugin_id in ("file-explorer", "graph", "canvas", "tag-pane", "outline", "word-count", "sync"):
        entry = next(item for item in registry["entries"] if item["component_id"] == f"core:{plugin_id}")
        assert entry["policy_class"] == "optional"


def test_p02_registry_rejects_unregistered_core_flags_without_mutation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    core_path = root / "KnowledgeHub/.obsidian-mac/core-plugins.json"
    core_path.write_text(json.dumps(_core_flags(include_unknown=True)), encoding="utf-8")
    before = core_path.read_bytes()

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["errors"][0]["code"] == "PLUGIN_CORE_POLICY_UNREGISTERED"
    assert _registry(report)["unknown_observed_flags"] == ["future-core-flag"]
    assert core_path.read_bytes() == before


def test_p02_mobile_policy_is_blueprint_bound_and_not_inferred_from_mac_flags(tmp_path: Path) -> None:
    root = _root(tmp_path)
    profile_root = root / "KnowledgeHub/.obsidian-mac"
    flags = _core_flags()
    flags["workspaces"] = False
    flags["daily-notes"] = False
    (profile_root / "core-plugins.json").write_text(json.dumps(flags), encoding="utf-8")

    report, exit_code = plugins_audit_report(root)

    assert exit_code == 0, report
    mobile = _registry(report)["mobile_core_policy"]
    assert mobile["required_all_devices"] == [
        "Backlinks",
        "Bases",
        "Bookmarks",
        "Daily notes",
        "File recovery",
        "Outgoing links",
        "Properties",
        "Search",
        "Templates",
    ]
    assert mobile["required_mac_only"] == ["Workspaces"]
    assert mobile["observed_from_mac_profile"] is False
    assert mobile["state"] == "not_inferred"

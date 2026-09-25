from __future__ import annotations

import json
import shutil
from pathlib import Path

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
    "knowledgeos-thin-client",
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


def test_p01_registry_reports_independent_states_without_raw_plugin_values(tmp_path: Path) -> None:
    root = _root(tmp_path)
    profile_root = root / "KnowledgeHub/.obsidian-mac"
    (profile_root / "core-plugins.json").write_text(
        json.dumps({"properties": True, "bases": False}),
        encoding="utf-8",
    )
    (profile_root / "app.json").write_text(
        json.dumps({"propertiesInDocument": "visible"}),
        encoding="utf-8",
    )
    plugin_root = profile_root / "plugins/quickadd"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "quickadd", "version": "2.9.3"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps({"capture": {"private_value": "do-not-report"}}),
        encoding="utf-8",
    )
    before = {
        path: path.read_bytes()
        for path in profile_root.rglob("*")
        if path.is_file()
    }

    report, exit_code = plugins_audit_report(root)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    registry = report["setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["state_dimensions"] == [
        "declared",
        "installed",
        "configured",
        "enabled",
        "verified",
        "healthy",
        "fallback_available",
    ]
    assert len(registry["entries"]) == 21
    assert registry["mobile_community_plugins"] == {
        "declared": [],
        "state": "empty",
        "source_locator": "blueprint/blueprint.yaml#/plugin_profiles/mobile_baseline/community_plugins",
    }
    quickadd = next(item for item in registry["entries"] if item["component_id"] == "plugin:quickadd")
    assert quickadd["current_value"] == {
        "manifest_version": "2.9.3",
        "serialized_keys": ["capture"],
    }
    assert quickadd["states"] == {
        "declared": "declared",
        "installed": "installed",
        "configured": "configured",
        "enabled": "enabled",
        "verified": "not_run",
        "healthy": "not_run",
        "fallback_available": "available",
    }
    assert "do-not-report" not in json.dumps(report)
    thin_client = next(
        item for item in registry["entries"] if item["component_id"] == "plugin:knowledgeos-thin-client"
    )
    assert thin_client["role"] == "proposal_only_presentation_client"
    assert thin_client["desired_policy"] == "allow_authenticated_proposal_only_presentation"
    assert thin_client["allowed_value_domain"] == "loopback_broker_and_digest_bound_review"
    assert thin_client["mutation_risk"] == "presentation_only_no_canonical_writer"
    properties = next(item for item in registry["entries"] if item["component_id"] == "core:properties")
    assert properties["states"]["enabled"] == "enabled"
    assert properties["current_value"]["observed_setting_paths"] == ["app.json#/propertiesInDocument"]
    assert {
        path: path.read_bytes()
        for path in profile_root.rglob("*")
        if path.is_file()
    } == before


def test_p01_registry_keeps_missing_data_unconfigured_and_unverified(tmp_path: Path) -> None:
    report, exit_code = plugins_audit_report(_root(tmp_path))

    assert exit_code == 0, report
    quickadd = next(
        item for item in report["setting_registry"]["entries"] if item["component_id"] == "plugin:quickadd"
    )
    assert quickadd["current_value"] == {
        "manifest_version": None,
        "serialized_keys": [],
    }
    assert quickadd["states"]["configured"] == "unconfigured"
    assert quickadd["states"]["verified"] == "not_run"
    assert quickadd["states"]["fallback_available"] == "available"


def test_p01_registry_rejects_invalid_plugin_data_without_mutation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    data_path = root / "KnowledgeHub/.obsidian-mac/plugins/quickadd/data.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text("{", encoding="utf-8")
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["status"] == "DEGRADED"
    assert report["errors"][0]["code"] == "PLUGIN_DATA_INVALID"
    assert data_path.read_bytes() == before

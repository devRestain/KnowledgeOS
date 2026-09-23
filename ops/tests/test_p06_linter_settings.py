from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.linter_settings import P06_PROTECTED_FOLDERS, build_linter_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p06_registry_records_safe_all_rules_off_manual_baseline_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/obsidian-linter/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["linter_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "obsidian-linter"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/obsidian-linter/manifest.json",
        "id": "obsidian-linter",
        "version": "1.31.0",
    }
    allowlist = registry["rule_allowlist"]
    assert allowlist["approved_rule_ids"] == []
    assert allowlist["candidate_rule_ids"] == []
    assert allowlist["enabled_rule_ids"] == []
    assert allowlist["observed_rule_count"] == 65
    assert allowlist["state"] == "safe_all_rules_off_baseline"
    assert allowlist["healthy"] is False
    assert allowlist["healthy_state"] == "safe_baseline_not_hygiene_completion"

    records = registry["rule_records"]
    assert len(records) == 65
    assert all(record["enabled"] is False for record in records)
    assert all(record["state"] == "disabled_safe_baseline" for record in records)
    assert all(record["allowlist_state"] == "not_accepted" for record in records)
    remove_keys = next(record for record in records if record["rule_id"] == "remove-yaml-keys")
    assert remove_keys["destructive_risk"] == "high"
    assert remove_keys["affected_surface"] == "frontmatter keys"
    assert remove_keys["affected_fields"] == ["arbitrary configured YAML keys"]
    assert "sha256" in remove_keys["before_after_evidence"]
    assert "restore the original bytes" in remove_keys["rollback_action"]

    assert registry["triggers"] == {
        "lint_on_save": {"observed": False, "state": "disabled"},
        "lint_on_file_change": {"observed": False, "state": "disabled"},
        "lint_commands": {"observed": [], "state": "empty"},
        "custom_regexes": {"observed": [], "state": "empty"},
        "manual_execution": {"state": "manual_only", "runtime_evidence": "not_run"},
    }
    assert registry["scope"]["protected_folders"] == {
        "expected": list(P06_PROTECTED_FOLDERS),
        "observed": list(P06_PROTECTED_FOLDERS),
        "state": "pass",
        "policy": "canonical templates bases dashboards and schemas remain outside automatic lint scope",
    }
    assert registry["scope"]["ignored_files"] == {"observed": [], "state": "empty"}
    assert registry["scope"]["canonical_contract"]["state"] == "pass"
    assert registry["scope"]["canonical_contract"]["property_dictionary"]["state"] == "pass"
    assert registry["rollback_contract"]["per_file"] is True
    assert registry["safety_policy"]["bulk_rewrite"] == "forbidden"
    assert registry["safety_policy"]["yaml_key_removal"] == "disabled_and_not_approved"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"
    assert data_path.read_bytes() == before


def test_p06_registry_keeps_missing_profile_and_contract_sources_unknown(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_linter_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["rule_allowlist"]["observed_rule_count"] == 0
    assert registry["triggers"]["lint_on_save"]["state"] == "unknown"
    assert registry["scope"]["canonical_contract"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"


def test_p06_registry_rejects_enabled_rules_automatic_triggers_and_custom_commands(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/obsidian-linter"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "obsidian-linter", "version": "1.31.0"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "ruleConfigs": {"yaml-title": {"enabled": True}},
                "lintOnSave": True,
                "lintOnFileChange": False,
                "lintCommands": ["unreviewed"],
                "customRegexes": [{"name": "unsafe"}],
                "foldersToIgnore": list(P06_PROTECTED_FOLDERS),
                "filesToIgnore": [],
            }
        ),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_linter_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P06_ENABLED_RULE_NOT_ACCEPTED" in codes
    assert "P06_AUTOMATIC_TRIGGER_ENABLED" in codes
    assert "P06_UNREVIEWED_LINT_COMMAND" in codes
    assert "P06_UNREVIEWED_CUSTOM_REGEX" in codes


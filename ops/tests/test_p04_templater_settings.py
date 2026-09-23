from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.templater_settings import build_templater_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p04_registry_separates_core_period_and_bounded_templater_surfaces_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/templater-obsidian/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["templater_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "templater-obsidian"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/templater-obsidian/manifest.json",
        "id": "templater-obsidian",
        "version": "2.18.1",
    }
    assert registry["canonical_template_folder"] == {
        "blueprint": "99_System/Templates",
        "expected": "99_System/Templates",
        "state": "pass",
        "source": "blueprint/blueprint.yaml#/templates/directory",
    }
    settings = registry["serialized_settings"]
    assert settings["templates_folder"]["state"] == "pass"
    assert settings["trigger_on_file_creation"] == {"observed": False, "state": "disabled"}
    assert settings["enable_system_commands"] == {"observed": False, "state": "disabled"}
    assert settings["shell_path"] == {"state": "empty"}
    assert settings["user_scripts_folder"] == {"state": "empty"}
    assert settings["folder_templates"]["mapping_state"] == "empty"
    assert settings["folder_templates"]["effective_state"] == "unconfigured"
    assert settings["file_templates"]["mapping_state"] == "empty"
    assert settings["file_templates"]["effective_state"] == "unconfigured"
    assert settings["templates_pairs"] == {"observed": [["", ""]], "state": "empty"}
    assert settings["startup_templates"] == {"observed": [""], "state": "empty"}

    records = {item["template"]: item for item in registry["template_records"]}
    assert all(item["state"] == "pass" for item in records.values())
    assert records["T10_Daily.md"]["syntax"] == "obsidian_core_date_tokens"
    assert records["T10_Daily.md"]["ownership"] == {
        "owner": "obsidian-core-daily-notes",
        "renderer": "obsidian-core-date-token-renderer",
        "invocation_boundary": "Core Daily Notes creates the daily note",
    }
    for name in ("T11_Weekly.md", "T12_Monthly.md"):
        assert records[name]["syntax"] == "templater_expression"
        assert records[name]["ownership"]["owner"] == "notebook-navigator"
        assert records[name]["ownership"]["renderer"] == "templater-obsidian"
        assert records[name]["forbidden_code"] == []
    assert registry["policy_capabilities"]["global_new_file_trigger"]["state"] == "disabled"
    assert registry["policy_capabilities"]["system_commands"]["state"] == "disabled"
    assert registry["policy_capabilities"]["arbitrary_folder_mappings"]["state"] == "unconfigured"
    assert registry["policy_capabilities"]["arbitrary_file_mappings"]["state"] == "unconfigured"
    assert registry["policy_capabilities"]["network"]["state"] == "forbidden_by_contract"
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "rendered_fixture": "not_run",
        "runtime": "not_run",
        "device": "not_run",
        "plugin_data_mutation": "out_of_scope",
        "existing_note_rewrite": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p04_registry_keeps_missing_profile_and_templates_unknown_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_templater_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["serialized_settings"]["state"] == "unknown"
    assert all(item["state"] == "unknown" for item in registry["template_records"])
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"


def test_p04_registry_rejects_executable_forbidden_code_and_global_trigger(tmp_path: Path) -> None:
    root = tmp_path / "control"
    template_root = root / "KnowledgeHub/99_System/Templates"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/templater-obsidian"
    template_root.mkdir(parents=True)
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "templater-obsidian", "version": "2.18.1"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "templates_folder": "99_System/Templates",
                "trigger_on_file_creation": True,
                "enable_system_commands": False,
                "shell_path": "",
                "user_scripts_folder": "",
                "folder_templates": [{"folder": "", "template": ""}],
                "file_templates": [{"regex": ".*", "template": ""}],
                "startup_templates": [""],
            }
        ),
        encoding="utf-8",
    )
    (template_root / "T11_Weekly.md").write_text('<%* tp.system("unsafe") %>', encoding="utf-8")
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_templater_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P04_FORBIDDEN_TEMPLATE_CODE" in codes
    assert "P04_GLOBAL_NEW_FILE_TRIGGER_ENABLED" in codes


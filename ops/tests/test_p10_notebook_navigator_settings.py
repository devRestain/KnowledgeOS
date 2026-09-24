from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.notebook_navigator_settings import (
    P10_FILE_VISIBILITY,
    build_notebook_navigator_setting_registry,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p10_registry_binds_exact_period_mapping_and_single_template_owners_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/notebook-navigator/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["notebook_navigator_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "notebook-navigator"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/notebook-navigator/manifest.json",
        "id": "notebook-navigator",
        "version": "3.4.1",
    }
    assert registry["active_profile"]["state"] == "pass"
    assert registry["active_profile"]["name"] == "KnowledgeOS Mac"
    assert registry["active_profile"]["selection"] == "last vaultProfiles entry"

    mapping = registry["period_mapping"]
    assert mapping["state"] == "pass"
    assert mapping["observed"] == {
        "periodic_notes_folder": "10_Journal",
        "daily_pattern": "[Daily]/YYYY-MM-DD",
        "weekly_pattern": "[Weekly]/GGGG/GGGG-[W]WW",
        "monthly_pattern": "[Monthly]/YYYY/YYYY-MM",
        "daily_template": "99_System/Templates/T10_Daily.md",
        "weekly_template": "99_System/Templates/T11_Weekly.md",
        "monthly_template": "99_System/Templates/T12_Monthly.md",
        "calendar_enabled": True,
        "integration_mode": "notebook-navigator",
        "locale_source": "calendar",
    }
    assert mapping["paths"] == {
        "daily": "10_Journal/Daily/YYYY-MM-DD.md",
        "weekly": "10_Journal/Weekly/{iso_year}/{iso_year}-W{iso_week}.md",
        "monthly": "10_Journal/Monthly/{year}/{year}-{month}.md",
    }

    ownership = registry["template_ownership"]
    assert ownership["mode"] == "templater-owned-period-rendering"
    assert ownership["state"] == "pass"
    assert ownership["daily_owner"] == "obsidian-core-daily-notes"
    assert ownership["period_owner"] == "notebook-navigator-plus-templater"
    assert ownership["global_trigger"] == "disabled_by_P04_contract"
    assert ownership["records"]["daily"]["owner"] == "obsidian-core-daily-notes"
    assert ownership["records"]["daily"]["observed_syntax"] == "core_date_tokens"
    for note_type in ("weekly", "monthly"):
        assert ownership["records"][note_type]["owner"] == "templater-obsidian"
        assert ownership["records"][note_type]["observed_syntax"] == "templater_expressions"

    assert registry["template_settings"]["template_engine"]["state"] == "pass"
    assert registry["template_settings"]["calendar_template_folder"]["state"] == "pass"
    assert registry["template_settings"]["folder_templates"]["state"] == "pass"
    assert registry["template_settings"]["template_commands"]["state"] == "pass"
    assert registry["scope"]["profile"]["state"] == "pass"
    assert all(record["state"] == "pass" for record in registry["scope"]["profile"]["fields"].values())
    assert registry["scope"]["profile"]["file_visibility"]["observed"] == P10_FILE_VISIBILITY
    assert registry["scope"]["profile"]["file_visibility"]["expected"] == P10_FILE_VISIBILITY
    assert registry["scope"]["profile"]["file_visibility"]["state"] == "pass"
    assert registry["scope"]["display"]["observed"]["calendar_show_hidden_items"]["state"] == "pass"

    confirmation = registry["confirmation_policy"]
    assert confirmation["state"] == "pass"
    assert confirmation["existing_file_behavior"] == {
        "new_period": "confirm_before_create",
        "existing_period": "open_existing_without_overwrite",
        "overwrite": "forbidden_by_contract",
        "conflict_resolution": "human_action_required",
        "runtime_or_device": "not_run",
    }
    assert registry["calendar_commands"]["command_ids"] == []
    assert registry["calendar_commands"]["command_id_inference"] is False
    assert registry["mutation_policy"]["bulk_move"] == "forbidden_by_contract"
    assert registry["mutation_policy"]["bulk_delete"] == "forbidden_by_contract"
    assert registry["mutation_policy"]["bulk_property_edit"] == "forbidden_by_contract"
    assert registry["fallback"]["state"] == "pass"
    assert registry["fallback"]["plugin_free"] is True
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "mapping": "pass",
        "template_ownership": "pass",
        "rendered_fixture": "not_run",
        "runtime": "not_run",
        "device": "not_run",
        "creation_opening_no_overwrite": "not_run",
        "note_move_delete_property": "out_of_scope",
        "plugin_data_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p10_registry_keeps_missing_profile_and_files_unknown_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_notebook_navigator_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["active_profile"]["state"] == "unknown"
    assert registry["period_mapping"]["state"] == "unknown"
    assert registry["template_ownership"]["state"] == "unknown"
    assert registry["fallback"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"


def test_p10_registry_rejects_mapping_scope_template_and_fallback_drift(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/notebook-navigator"
    plugin_root.mkdir(parents=True)
    (root / "KnowledgeHub/.obsidian-mac").mkdir(parents=False, exist_ok=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "notebook-navigator", "version": "3.4.1"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "vaultProfiles": [
                    {
                        "id": "unsafe",
                        "name": "Wrong profile",
                        "fileVisibility": "unsupported",
                        "hiddenFolders": ["99_System"],
                    }
                ],
                "calendarEnabled": True,
                "calendarIntegrationMode": "other",
                "calendarPeriodicNotesLocaleSource": "calendar",
                "calendarCustomFilePattern": "[Daily]/YYYY-MM-DD",
                "calendarCustomWeekPattern": "wrong",
                "calendarCustomMonthPattern": "wrong",
                "calendarCustomFileTemplate": "99_System/Templates/T10_Daily.md",
                "calendarCustomWeekTemplate": "wrong",
                "calendarCustomMonthTemplate": "wrong",
                "templateEngine": "unsupported",
                "calendarTemplateFolder": "unsafe",
                "folderTemplates": {".*": "unsafe"},
                "templateCommands": ["unsafe"],
                "calendarConfirmBeforeCreate": False,
                "confirmBeforeDelete": False,
                "deleteAttachments": "always",
                "moveFileConflicts": "overwrite",
                "confirmBeforeManualSort": False,
                "calendarShowHiddenItems": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "KnowledgeHub/.obsidian-mac/core-plugins.json").write_text(
        json.dumps({"file-explorer": False}),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_notebook_navigator_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P10_ACTIVE_PROFILE_NOT_ACCEPTED" in codes
    assert "P10_HIDDEN_SCOPE_DRIFT" in codes
    assert "P10_FILE_VISIBILITY_DRIFT" in codes
    assert "P10_PERIOD_MAPPING_DRIFT" in codes
    assert "P10_TEMPLATE_SETTING_DRIFT" in codes
    assert "P10_CONFIRMATION_POLICY_DRIFT" in codes
    assert "P10_CALENDAR_HIDDEN_ITEMS_ENABLED" in codes
    assert "P10_FILE_EXPLORER_FALLBACK_UNAVAILABLE" in codes

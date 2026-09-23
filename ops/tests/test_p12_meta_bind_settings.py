from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.meta_bind_settings import build_meta_bind_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p12_registry_binds_reviewed_property_inputs_and_fail_closed_protected_paths_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/obsidian-meta-bind-plugin/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["meta_bind_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "obsidian-meta-bind-plugin"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/obsidian-meta-bind-plugin/manifest.json",
        "id": "obsidian-meta-bind-plugin",
        "version": "1.5.1",
    }
    assert registry["global_policy"]["state"] == "pass"
    assert registry["global_policy"]["settings"]["dev_mode"] == {
        "observed": False,
        "expected": False,
        "state": "pass",
        "policy": "developer mode remains disabled",
    }
    assert registry["global_policy"]["settings"]["ignore_code_block_restrictions"]["observed"] is False
    assert registry["global_policy"]["settings"]["enable_js"]["observed"] is False
    assert registry["global_policy"]["settings"]["button_templates"]["observed"] == []

    assert registry["property_dictionary"]["state"] == "pass"
    assert registry["note_type_scope"]["state"] == "pass"
    assert registry["note_type_scope"]["fields"] == {
        "status": {
            "state": "pass",
            "observed_note_types": ["project"],
            "expected_note_types": ["project"],
            "source": "blueprint/blueprint.yaml#/note_types/*/status",
        },
        "priority": {
            "state": "pass",
            "observed_note_types": ["project", "question"],
            "expected_note_types": ["project", "question"],
            "source": "blueprint/blueprint.yaml#/note_types/*/priority",
        },
        "next_action": {
            "state": "pass",
            "observed_note_types": ["project"],
            "expected_note_types": ["project"],
            "source": "blueprint/blueprint.yaml#/note_types/*/next_action",
            "conditional_requirements": {
                "state": "pass",
                "observed": [
                    {"when": {"status": "active"}, "require": ["focus_rank", "next_action"]},
                    {"when": {"status": "blocked"}, "require": ["focus_rank", "next_action"]},
                ],
                "expected": [
                    {"when": {"status": "active"}, "require": ["focus_rank", "next_action"]},
                    {"when": {"status": "blocked"}, "require": ["focus_rank", "next_action"]},
                ],
            },
        },
        "today_focus": {
            "state": "pass",
            "observed_note_types": ["daily"],
            "expected_note_types": ["daily"],
            "source": "blueprint/blueprint.yaml#/note_types/*/today_focus",
        },
    }

    inputs = registry["input_templates"]
    assert inputs["state"] == "pass"
    assert set(inputs["records"]) == {"status", "priority", "next_action", "today_focus"}
    assert inputs["records"]["status"]["allowed_values"] == ["planned", "active", "blocked", "done", "cancelled"]
    assert inputs["records"]["priority"]["allowed_values"] == ["high", "medium", "low"]
    assert inputs["records"]["next_action"]["control"] == "text"
    assert inputs["records"]["today_focus"]["note_type_scope"] == ["daily"]
    for record in inputs["records"].values():
        assert record["state"] == "pass"
        assert record["mutation"]["write_target"] == "one note YAML/frontmatter property"
        assert record["mutation"]["mutation_class"] == "single_note_frontmatter_edit"
        assert record["mutation"]["human_action_required"] is True
        assert record["mutation"]["rollback"]

    assert registry["button_contract"] == {
        "state": "pass",
        "templates": [],
        "execution": "not_run",
        "policy": "button execution remains forbidden until individually reviewed",
    }
    assert registry["view_contract"]["state"] == "unconfigured"
    assert registry["protected_paths"]["state"] == "pass"
    assert all(item["state"] == "pass" for item in registry["protected_paths"]["paths"].values())
    assert registry["protected_paths"]["write_controls"] == "forbidden regardless of serialized excludedFolders semantics"
    assert registry["folder_exclusion"]["state"] == "unknown"
    assert registry["folder_exclusion"]["observed"] == ["templates"]
    assert registry["folder_exclusion"]["canonical_path_matches"] == []
    assert registry["folder_exclusion"]["verification"] == "exact installed UI label and folder-matching behavior not_run"
    assert registry["mutation_policy"]["protected_paths"] == "write controls forbidden regardless of excludedFolders semantics"
    assert registry["mutation_policy"]["javascript"] == "forbidden"
    assert registry["mutation_policy"]["canonical_apply"] == "forbidden_without_separate_human_workflow"
    assert registry["fallback"]["state"] == "pass"
    assert registry["fallback"]["plugin_free"] is True
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "folder_exclusion_semantics": "unknown",
        "input_rendering": "not_run",
        "input_edit": "not_run",
        "view_rendering": "not_run",
        "button_execution": "not_run",
        "runtime": "not_run",
        "device": "not_run",
        "plugin_data_mutation": "out_of_scope",
        "protected_note_mutation": "out_of_scope",
        "canonical_note_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p12_registry_keeps_missing_profile_and_dictionary_unknown_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_meta_bind_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["global_policy"]["state"] == "unknown"
    assert registry["property_dictionary"]["state"] == "unknown"
    assert registry["input_templates"]["state"] == "unknown"
    assert registry["folder_exclusion"]["state"] == "unknown"
    assert registry["fallback"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["static"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"


def test_p12_registry_rejects_capability_drift_unapproved_inputs_and_protected_controls(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/obsidian-meta-bind-plugin"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "obsidian-meta-bind-plugin", "version": "1.5.1"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "devMode": True,
                "ignoreCodeBlockRestrictions": True,
                "enableJs": True,
                "buttonTemplates": [{"name": "unsafe"}],
                "inputFieldTemplates": [
                    {"name": "unsafe", "declaration": "INPUT[text:ai_status]"},
                ],
                "excludedFolders": ["templates"],
            }
        ),
        encoding="utf-8",
    )
    protected = root / "KnowledgeHub/99_System/Templates/T20_Project.md"
    protected.parent.mkdir(parents=True)
    protected.write_text("---\nstatus: planned\n---\nINPUT[text:unsafe]\n", encoding="utf-8")
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_meta_bind_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P12_FORBIDDEN_CAPABILITY_ENABLED" in codes
    assert "P12_UNAPPROVED_INPUT_FIELD" in codes
    assert "P12_INPUT_TEMPLATE_MISSING" in codes
    assert "P12_PROTECTED_PATH_CONTROL_EXPOSED" in codes

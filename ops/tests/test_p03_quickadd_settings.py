from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.quickadd_settings import P03_PRIMARY_CHOICES, build_quickadd_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p03_registry_binds_primary_choices_to_blueprint_targets_without_inventing_command_ids() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/quickadd/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["quickadd_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["canonical_template_folder"] == {
        "expected": "99_System/Templates",
        "observed": "99_System/Templates",
        "state": "pass",
    }
    expected_observed_choice_ids = sorted({"KOS_HELPERS", *P03_PRIMARY_CHOICES})
    assert registry["observed_choices"] == {
        "state": "observed",
        "ids": expected_observed_choice_ids,
        "required_primary_actions": list(P03_PRIMARY_CHOICES),
        "runtime_and_device_evidence": "not_run",
    }
    assert [entry["choice_id"] for entry in registry["primary_choices"]] == list(P03_PRIMARY_CHOICES)
    assert [entry["hotkey"] for entry in registry["primary_choices"]] == [
        "option_command_c",
        "option_command_j",
        "option_command_p",
        "option_command_q",
        "option_command_k",
    ]
    assert all(entry["template_observation"]["state"] == "pass" for entry in registry["primary_choices"])
    assert all(entry["create_only"] is True for entry in registry["primary_choices"])
    assert all(entry["overwrite_existing"] is False for entry in registry["primary_choices"])
    assert all(entry["command_identity"]["installed_command_id"] is None for entry in registry["primary_choices"])
    assert registry["safety_policy"]["disable_online_features"] is True
    assert registry["safety_policy"]["dev_mode"] is False
    assert data_path.read_bytes() == before


def test_p03_registry_records_missing_profile_evidence_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_quickadd_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["observed_choices"]["state"] == "unknown"
    assert all(entry["template_observation"]["state"] == "unknown" for entry in registry["primary_choices"])


def test_p03_registry_rejects_unsafe_declared_target_without_reading_plugin_as_authority(tmp_path: Path) -> None:
    root = tmp_path / "control"
    profile_root = root / "KnowledgeHub/.obsidian-mac/plugins/quickadd"
    profile_root.mkdir(parents=True)
    (root / "KnowledgeHub/99_System/Templates").mkdir(parents=True)
    (profile_root / "manifest.json").write_text(json.dumps({"id": "quickadd", "version": "2.9.3"}), encoding="utf-8")
    (profile_root / "data.json").write_text(
        json.dumps(
            {
                "choices": [],
                "templateFolderPath": "99_System/Templates",
                "disableOnlineFeatures": True,
                "devMode": False,
            }
        ),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    blueprint["quickadd_choices"]["NEW_IDEA"]["target_pattern"] = "../outside.md"

    registry, errors = build_quickadd_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert registry["observed_choices"]["state"] == "unconfigured"
    assert any(error["code"] == "P03_CHOICE_TARGET_INVALID" for error in errors)

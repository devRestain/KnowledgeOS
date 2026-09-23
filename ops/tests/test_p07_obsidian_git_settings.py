from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.obsidian_git_settings import build_obsidian_git_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p07_registry_keeps_git_manual_local_and_remote_boundaries_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/obsidian-git/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["obsidian_git_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "obsidian-git"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/obsidian-git/manifest.json",
        "id": "obsidian-git",
        "version": "2.39.0",
    }
    assert registry["blueprint_contract"] == {
        "state": "pass",
        "mac_writer": "obsidian_git_manual_or_vaultctl_not_both_concurrently",
        "mobile_writer": "working_copy",
        "one_writer_per_device": True,
        "exact_path_commit": True,
        "remote_unattended": False,
        "always_on_git_roundtrip": False,
        "github_is_separate_remote_boundary": True,
        "mac_profile_git_writer": "obsidian_git_manual",
        "vault_remote_role": "github_notes_repository",
        "source": "blueprint/blueprint.yaml#/defaults /repositories/vault /device_profiles/mac /privacy /git",
    }

    automation = registry["automation_policy"]
    assert {name: record["observed"] for name, record in automation["intervals"].items()} == {
        "autoSaveInterval": 0,
        "autoPushInterval": 0,
        "autoPullInterval": 0,
    }
    assert all(record["state"] == "disabled" for record in automation["intervals"].values())
    assert all(record["observed"] is False for record in automation["booleans"].values())
    assert automation["commit_message_script"] == {
        "observed": "",
        "state": "empty",
        "policy": "manual commit message scripts remain empty",
    }

    visibility = registry["local_status_visibility"]
    assert visibility["status_bar"] == {"observed": True, "state": "pass"}
    assert visibility["branch_status_bar"] == {"observed": True, "state": "pass"}
    assert visibility["file_menu_for_staged_review"] == {"observed": True, "state": "pass"}
    assert visibility["refresh_source_control"] == {"observed": True, "state": "pass"}
    assert visibility["refresh_timer"]["observed"] == 7000
    assert visibility["network_calls"] == "not_invoked_by_status_contract"
    assert visibility["remote_sync_claim"] == "forbidden_from_local_status_only"

    assert registry["commit_policy"]["manual_commit"] == "human_reviewed_exact_path_commit"
    assert registry["commit_policy"]["staged_set_review"] == "required_before_manual_commit"
    assert registry["remote_policy"]["push"]["control"] == "disabled_by_profile"
    assert registry["remote_policy"]["pull"]["network_authorization"] == "required_separately"
    assert registry["remote_policy"]["successful_local_status_does_not_prove_remote_sync"] is True
    assert registry["fallback"] == {
        "ordinary_local_git": "available_only_as_explicit_human_action",
        "no_git_operation": "valid",
        "automatic_recovery": "not_authorized",
    }
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "runtime": "not_run",
        "network": "not_run",
        "device": "not_run",
        "git_identity": "not_run",
        "remote_sync": "not_run",
        "plugin_data_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p07_registry_keeps_missing_profile_unconfigured_and_unverified(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_obsidian_git_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["automation_policy"]["intervals"]["autoSaveInterval"]["state"] == "unknown"
    assert registry["local_status_visibility"]["status_bar"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["network"] == "not_run"


def test_p07_registry_rejects_automatic_git_effects_scripts_and_hidden_status(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/obsidian-git"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "obsidian-git", "version": "2.39.0"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "autoSaveInterval": 1,
                "autoPushInterval": 0,
                "autoPullInterval": 0,
                "autoPullOnBoot": True,
                "autoBackupAfterFileChange": False,
                "pullBeforePush": False,
                "differentIntervalCommitAndPush": False,
                "commitMessageScript": "unsafe",
                "showStatusBar": False,
                "showBranchStatusBar": True,
                "showFileMenu": True,
                "refreshSourceControl": True,
            }
        ),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_obsidian_git_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P07_AUTOMATIC_INTERVAL_ENABLED" in codes
    assert "P07_UNATTENDED_BOOLEAN_ENABLED" in codes
    assert "P07_COMMIT_SCRIPT_NOT_ACCEPTED" in codes
    assert "P07_LOCAL_STATUS_VISIBILITY_DISABLED" in codes

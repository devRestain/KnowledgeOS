from __future__ import annotations

import json
import shutil
from pathlib import Path

from vaultops.cli import main
from vaultops.diagnostics import (
    EXIT_CONFIG_INVALID,
    EXIT_INPUT_INVALID,
    EXIT_VALIDATION_FAILED,
    DiagnosticError,
    doctor_report,
    git_status_report,
    load_strict_config,
    plugins_audit_report,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_doctor_reports_valid_core_and_separate_inactive_overlays_without_mutation() -> None:
    before = (CONTROL_ROOT / "KnowledgeHub/.knowledgeos-root.json").read_bytes()

    report, exit_code = doctor_report(CONTROL_ROOT)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["validation"] == {
        "blueprint": "PASS",
        "json_schema": "PASS",
        "semantic_validation": "PASS",
        "generated_artifacts": "PASS",
    }
    assert report["overlays"]["git_identity_configured"]["state"] == "verified"
    assert report["overlays"]["obsidian_mac_core_verified"]["state"] == "inactive"
    assert report["overlays"]["mobile_transport_verified"]["state"] == "deferred"
    assert report["plugins"]["status"] == "INACTIVE"
    assert (CONTROL_ROOT / "KnowledgeHub/.knowledgeos-root.json").read_bytes() == before


def test_git_status_distinguishes_dirty_worktree_from_diagnostic_failure() -> None:
    report, exit_code = git_status_report(CONTROL_ROOT, "both")

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert set(report["repositories"]) == {"control", "vault"}
    assert report["repositories"]["control"]["worktree"] in {"clean", "dirty"}
    assert report["repositories"]["vault"]["worktree"] in {"clean", "dirty"}
    assert all(not path.startswith("BSIDIAN_") for path in report["repositories"]["control"]["staged_paths"])
    assert "https://github.com" not in json.dumps(report)


def test_plugins_audit_reports_unconfigured_profile_as_inactive() -> None:
    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0
    assert report["status"] == "INACTIVE"
    assert report["profile_state"] == "not_configured"
    assert all(item["state"] == "inactive" for item in report["community_plugins"])


def test_wrong_root_has_stable_input_exit_class(tmp_path: Path) -> None:
    report, exit_code = doctor_report(tmp_path)

    assert exit_code == EXIT_INPUT_INVALID
    assert report["errors"][0]["code"] == "ROOT_LAYOUT_INVALID"


def test_strict_config_rejects_unknown_keys_and_value_drift(tmp_path: Path) -> None:
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops/vaultops.toml").write_text(
        'schema_version = 1\nproject_name = "KnowledgeOS"\ncontrol_root = "/workspace/control"\n'
        'vault_root = "/workspace/KnowledgeHub"\nruntime_root = "/workspace/runtime"\n'
        'timezone = "UTC"\nextra = true\n',
        encoding="utf-8",
    )

    try:
        load_strict_config(tmp_path)
    except DiagnosticError as error:
        assert error.code == "CONFIG_SCHEMA_INVALID"
        assert error.exit_code == EXIT_CONFIG_INVALID
    else:
        raise AssertionError("strict config unexpectedly accepted drift")


def test_cli_exposes_c12_namespaces(capsys) -> None:
    assert main(["git", "status", "--repo", "vault", "--root", str(CONTROL_ROOT)]) == 0
    git_report = json.loads(capsys.readouterr().out)
    assert git_report["requested_repo"] == "vault"

    assert main(["plugins", "audit", "--root", str(CONTROL_ROOT)]) == 0
    plugin_report = json.loads(capsys.readouterr().out)
    assert plugin_report["operation"] == "plugins audit"


def test_note_validation_failure_uses_the_stable_validation_exit_class(tmp_path: Path, capsys) -> None:
    root = tmp_path / "control"
    shutil.copytree(CONTROL_ROOT / "blueprint", root / "blueprint")
    note_path = root / "KnowledgeHub/40_Knowledge/Ideas/Invalid.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Invalid\ntitle: Duplicate\n---\n", encoding="utf-8")

    assert main(["note", "validate", "40_Knowledge/Ideas/Invalid.md", "--root", str(root)]) == EXIT_VALIDATION_FAILED
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "NOTE_FRONTMATTER_INVALID"


def test_note_validation_input_failure_uses_the_stable_input_exit_class(tmp_path: Path, capsys) -> None:
    root = tmp_path / "control"
    shutil.copytree(CONTROL_ROOT / "blueprint", root / "blueprint")
    (root / "KnowledgeHub").mkdir()

    assert main(["note", "validate", "../escape.md", "--root", str(root)]) == EXIT_INPUT_INVALID
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "NOTE_INPUT_INVALID"

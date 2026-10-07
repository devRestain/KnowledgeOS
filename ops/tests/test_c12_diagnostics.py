from __future__ import annotations

import json
from pathlib import Path

from support.control_factory import fixture_path, make_control_root, make_diagnostic_root
from support.plugin_factory import make_plugin_profile

from vaultops.cli import main
from vaultops.diagnostics import (
    EXIT_CONFIG_INVALID,
    EXIT_INPUT_INVALID,
    EXIT_VALIDATION_FAILED,
    DiagnosticError,
    discover_project_roots,
    doctor_report,
    git_status_report,
    load_strict_config,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_doctor_reports_current_contracts_without_mutation(
    tmp_path: Path,
) -> None:
    root = make_diagnostic_root(tmp_path)
    roots = discover_project_roots(root)
    before = {
        "control": sorted(path.relative_to(roots.control).as_posix() for path in roots.control.rglob("*") if ".git" not in path.parts),
        "vault": sorted(path.relative_to(roots.vault).as_posix() for path in roots.vault.rglob("*")),
        "runtime": sorted(path.relative_to(roots.runtime).as_posix() for path in roots.runtime.rglob("*")),
    }

    report, exit_code = doctor_report(roots)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["validation"] == {
        "blueprint": "PASS",
        "json_schema": "PASS",
        "semantic_validation": "PASS",
        "generated_artifacts": "PASS",
    }
    assert "overlays" not in report
    assert "local_models" not in report
    assert "vector" not in report
    assert report["plugins"]["profile"] == "mac"
    assert report["roots"] == {
        "core": str(roots.core),
        "state": str(roots.state),
        "control": str(roots.control),
        "vault": str(roots.vault),
        "runtime": str(roots.runtime),
    }
    assert report["plugins"]["profile_root"] == str(roots.vault / ".obsidian-mac")
    after = {
        "control": sorted(path.relative_to(roots.control).as_posix() for path in roots.control.rglob("*") if ".git" not in path.parts),
        "vault": sorted(path.relative_to(roots.vault).as_posix() for path in roots.vault.rglob("*")),
        "runtime": sorted(path.relative_to(roots.runtime).as_posix() for path in roots.runtime.rglob("*")),
    }
    assert after == before


def test_git_status_distinguishes_dirty_worktree_from_diagnostic_failure(tmp_path: Path) -> None:
    root = make_diagnostic_root(tmp_path)
    roots = discover_project_roots(root)
    (roots.vault / "temporary-note.md").write_text("temporary\n", encoding="utf-8")
    report, exit_code = git_status_report(root, "both")

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert set(report["repositories"]) == {"control", "vault"}
    assert report["repositories"]["control"]["worktree"] == "clean"
    assert report["repositories"]["vault"]["worktree"] == "dirty"
    assert report["repositories"]["vault"]["untracked_paths"] == ["temporary-note.md"]
    assert "https://github.com" not in json.dumps(report)


def _minimal_plugin_audit_root(tmp_path: Path) -> Path:
    root, _profile = make_plugin_profile(tmp_path)
    return root


def test_wrong_root_has_stable_input_exit_class(tmp_path: Path) -> None:
    report, exit_code = doctor_report(tmp_path)

    assert exit_code in {EXIT_INPUT_INVALID, EXIT_CONFIG_INVALID}
    assert report["errors"][0]["code"] == "CONFIG_FILE_MISSING"


def test_strict_config_rejects_unknown_keys_and_value_drift(tmp_path: Path) -> None:
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops/vaultops.toml").write_text(
        'schema_version = 3\nproject_name = "KnowledgeOS"\ncontrol_root = "/workspace/control"\n'
        'core_root = "/workspace/Core"\nstate_root = "/workspace/state"\nvault_root = "/workspace/KnowledgeHub"\nruntime_root = "/workspace/runtime"\n'
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


def test_cli_exposes_c12_namespaces(tmp_path: Path, capsys) -> None:
    git_root = make_diagnostic_root(tmp_path / "git")
    assert main(["git", "status", "--repo", "vault", "--root", str(git_root)]) == 0
    git_report = json.loads(capsys.readouterr().out)
    assert git_report["requested_repo"] == "vault"

    plugin_root = _minimal_plugin_audit_root(tmp_path / "plugins")
    assert main(["plugins", "audit", "--root", str(plugin_root)]) == 0
    plugin_report = json.loads(capsys.readouterr().out)
    assert plugin_report["operation"] == "plugins audit"


def test_note_validation_failure_uses_the_stable_validation_exit_class(tmp_path: Path, capsys) -> None:
    root = make_control_root(tmp_path, ("blueprint",))
    note_path = (fixture_path(root, "vault") / "99_System/Templates/Invalid.md")
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Invalid\ntitle: Duplicate\n---\n", encoding="utf-8")

    assert main(["note", "validate", "--path", "99_System/Templates/Invalid.md", "--root", str(root)]) == EXIT_VALIDATION_FAILED
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "NOTE_FRONTMATTER_INVALID"


def test_note_validation_input_failure_uses_the_stable_input_exit_class(tmp_path: Path, capsys) -> None:
    root = make_control_root(tmp_path, ("blueprint",))

    assert main(["note", "validate", "--path", "../escape.md", "--root", str(root)]) == EXIT_INPUT_INVALID
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "NOTE_INPUT_INVALID"

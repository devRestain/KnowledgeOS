from __future__ import annotations

import json
import shutil
from pathlib import Path

from vaultops.cli import main

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    (root / "KnowledgeHub").mkdir()
    return root


def test_version_command(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_blueprint_status_reports_the_implemented_contract_profile(capsys) -> None:
    assert main(["blueprint", "status"]) == 0
    output = capsys.readouterr().out
    assert "capability_profile=portable_core" in output
    assert "implemented:C02" in output
    assert "implemented:C03-C04" in output
    assert "generated_zero_diff=implemented:C05-C06" in output


def test_help_is_a_real_package_entrypoint(capsys) -> None:
    assert main([]) == 0
    assert "KnowledgeOS deterministic runtime" in capsys.readouterr().out


def test_blueprint_validate_reports_json_schema_pass_without_writing(capsys) -> None:
    control_root = CONTROL_ROOT
    before = {
        relative: (control_root / relative).read_bytes()
        for relative in ("blueprint/blueprint.yaml", "blueprint/blueprint.schema.json")
    }

    assert main(["blueprint", "validate", "--root", str(control_root)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["status"] == "PASS"
    assert report["validation"]["draft"] == "2020-12"
    assert report["validation"]["json_schema"] == "PASS"
    assert report["validation"]["semantic_validation"] == "PASS"
    assert report["validation"]["generated_artifact_validation"] == "NOT_RUN:separate:vaultctl schema export --check"
    assert report["contract_id"] == {
        "expected": "knowledgeos-blueprint-v2",
        "matches": True,
        "observed": "knowledgeos-blueprint-v2",
    }
    assert report["provenance"]["checksum_manifest"]["status"] == "PASS"
    assert {
        relative: (control_root / relative).read_bytes()
        for relative in before
    } == before


def test_c07_cli_commands_expose_dry_run_and_create_only_workflows(tmp_path: Path, capsys) -> None:
    control_root = _fresh_control_copy(tmp_path)
    root = str(control_root)

    assert main(["bootstrap", "--dry-run", "--root", root]) == 0
    bootstrap_preview = json.loads(capsys.readouterr().out)
    assert bootstrap_preview["status"] == "PASS"
    assert bootstrap_preview["would_create"]

    assert main(["bootstrap", "--root", root]) == 0
    bootstrap_apply = json.loads(capsys.readouterr().out)
    assert bootstrap_apply["status"] == "PASS"

    assert main(
        [
            "project",
            "create",
            "--title",
            "CLI Demo Project",
            "--created-at",
            "2026-09-09T09:00:00+09:00",
            "--dry-run",
            "--root",
            root,
        ]
    ) == 0
    project_preview = json.loads(capsys.readouterr().out)
    assert project_preview["status"] == "PASS"
    assert project_preview["would_create"] == [
        "20_Projects/CLI Demo Project/CLI Demo Project.md",
        "20_Projects/CLI Demo Project/Working",
        "20_Projects/CLI Demo Project/Artifacts",
    ]

    assert main(
        [
            "period",
            "create",
            "--kind",
            "weekly",
            "--date",
            "2024-12-30",
            "--root",
            root,
        ]
    ) == 0
    period_report = json.loads(capsys.readouterr().out)
    assert period_report["status"] == "PASS"
    assert period_report["path"] == "10_Journal/Weekly/2025/2025-W01.md"

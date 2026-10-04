from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.control_factory import make_control_root, make_diagnostic_root

from vaultops.cli import build_parser, main

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_control_root(tmp_path, ("blueprint",))


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


def test_invalid_cli_root_does_not_fall_through_to_control_environment(tmp_path: Path, capsys, monkeypatch) -> None:
    environment_root = make_diagnostic_root(tmp_path / "environment")
    monkeypatch.setenv("KNOWLEDGEOS_CONTROL_ROOT", str(environment_root))
    missing_root = tmp_path / "missing explicit control"

    assert main(["blueprint", "validate", "--root", str(missing_root)]) == 11

    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "owner admission"
    assert report["errors"][0]["code"] == "CONFIG_FILE_MISSING"


def test_explicit_missing_config_does_not_fall_through_to_project_config(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    missing_config = tmp_path / "missing explicit config.toml"
    monkeypatch.setenv("KNOWLEDGEOS_CONFIG_FILE", str(missing_config))

    assert main(["blueprint", "validate", "--root", str(CONTROL_ROOT)]) == 11

    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "owner admission"
    assert report["errors"][0]["code"] == "CONFIG_FILE_MISSING"


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



def test_period_writer_is_absent_from_the_public_parser() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["period", "create", "--kind", "weekly"])
    assert error.value.code == 2


def test_c40_ollama_cli_uses_the_c31_maximum_timeout_by_default() -> None:
    args = build_parser().parse_args(
        [
            "ai",
            "ollama",
            "--job-id",
            "00000000-0000-4000-8000-000000000001",
            "--base-url",
            "http://127.0.0.1:11434",
        ]
    )
    assert args.timeout_seconds == 600

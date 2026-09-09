from __future__ import annotations

import json
from pathlib import Path

from vaultops.cli import main


def test_version_command(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_blueprint_status_reports_the_implemented_contract_profile(capsys) -> None:
    assert main(["blueprint", "status"]) == 0
    output = capsys.readouterr().out
    assert "capability_profile=contract_validated" in output
    assert "implemented:S02" in output
    assert "implemented:S03A-S03B" in output
    assert "generated_zero_diff=implemented:S03C" in output


def test_help_is_a_real_package_entrypoint(capsys) -> None:
    assert main([]) == 0
    assert "KnowledgeOS deterministic runtime" in capsys.readouterr().out


def test_blueprint_validate_reports_json_schema_pass_without_writing(capsys) -> None:
    control_root = Path(__file__).resolve().parents[2]
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

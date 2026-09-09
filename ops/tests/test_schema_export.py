from __future__ import annotations

import json
import shutil
from pathlib import Path

from vaultops.cli import main
from vaultops.schema_export import (
    NOT_APPLICABLE_ARTIFACTS,
    OWNED_ARTIFACTS,
    export_schema_artifacts,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "vault",
            "runtime",
        ),
    )
    for spec in OWNED_ARTIFACTS:
        (root / spec.path).unlink(missing_ok=True)
    return root


def test_schema_export_writes_only_explicit_owned_artifacts(tmp_path: Path) -> None:
    root = _control_copy(tmp_path)

    result = export_schema_artifacts(root)

    assert result.passed
    assert {item["status"] for item in result.report["artifacts"][: len(OWNED_ARTIFACTS)]} == {"WRITTEN"}
    assert all(
        item["status"] == "NOT_APPLICABLE_FOR_PROFILE"
        for item in result.report["artifacts"][len(OWNED_ARTIFACTS) :]
    )
    assert not (root / "vault/99_System/Schemas/Property_Dictionary.md").exists()
    assert not (root / "runtime").exists()
    assert (root / "ops/expected/Property_Dictionary.md").read_text(encoding="utf-8").startswith(
        "<!-- GENERATED: BEGIN knowledgeos-property-dictionary -->"
    )


def test_schema_export_check_is_deterministic_and_reports_future_profiles(tmp_path: Path) -> None:
    root = _control_copy(tmp_path)
    first = export_schema_artifacts(root)
    assert first.passed
    generated = {
        spec.path: (root / spec.path).read_bytes()
        for spec in OWNED_ARTIFACTS
    }

    checked = export_schema_artifacts(root, check=True)

    assert checked.passed
    assert checked.report["validation"]["future_artifacts"] == "NOT_APPLICABLE_FOR_PROFILE"
    assert {item["status"] for item in checked.report["artifacts"][: len(OWNED_ARTIFACTS)]} == {"PASS"}
    assert {
        item["path"] for item in checked.report["artifacts"] if item["status"] == "NOT_APPLICABLE_FOR_PROFILE"
    } == {spec.path for spec in NOT_APPLICABLE_ARTIFACTS}
    assert generated == {spec.path: (root / spec.path).read_bytes() for spec in OWNED_ARTIFACTS}


def test_schema_export_check_rejects_one_byte_artifact_mutation(tmp_path: Path) -> None:
    root = _control_copy(tmp_path)
    assert export_schema_artifacts(root).passed
    target = root / "ops/policies/privacy.yaml"
    target.write_bytes(target.read_bytes() + b" ")

    result = export_schema_artifacts(root, check=True)

    assert not result.passed
    assert "SCHEMA_EXPORT_ARTIFACT_MISMATCH" in {error["code"] for error in result.report["errors"]}
    privacy = next(item for item in result.report["artifacts"] if item["path"] == str(target.relative_to(root)))
    assert privacy["status"] == "MISMATCH"


def test_schema_export_rejects_ownership_drift_without_writing(tmp_path: Path) -> None:
    root = _control_copy(tmp_path)
    ownership = root / "ops/config/generated-artifacts.yaml"
    ownership.write_text(
        ownership.read_text(encoding="utf-8").replace("ops/policies/privacy.yaml", "ops/policies/*.yaml", 1),
        encoding="utf-8",
    )

    result = export_schema_artifacts(root, check=True)

    assert not result.passed
    assert "SCHEMA_EXPORT_OWNERSHIP_MISMATCH" in {error["code"] for error in result.report["errors"]}
    assert not (root / "ops/policies/privacy.yaml").exists()


def test_schema_export_cli_returns_machine_readable_report(tmp_path: Path, capsys) -> None:
    root = _control_copy(tmp_path)

    assert main(["schema", "export", "--root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["status"] == "PASS"
    assert report["validation"]["capability_profile"] == "contract_validated"

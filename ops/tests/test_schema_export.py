from __future__ import annotations

import json
from pathlib import Path

from support.control_factory import fixture_path, make_control_root

from vaultops.cli import main
from vaultops.schema_export import (
    NOT_APPLICABLE_ARTIFACTS,
    OWNED_ARTIFACTS,
    OWNERSHIP_CONTRACT_PATH,
    export_schema_artifacts,
)


def _control_copy(tmp_path: Path) -> Path:
    inputs = {
        OWNERSHIP_CONTRACT_PATH,
        "blueprint/blueprint.schema.json",
        "blueprint/blueprint.yaml",
        *(
            path
            for spec in OWNED_ARTIFACTS
            for path, _selectors in spec.authoritative_inputs
        ),
    }
    root = make_control_root(
        tmp_path,
        sorted(inputs),
    )
    (fixture_path(root, "vault")).rmdir()
    vault = tmp_path / "KnowledgeHub Ω"
    vault.mkdir()
    runtime = tmp_path / "runtime Ω"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    (root / "ops/vaultops.toml").write_text(
        "\n".join(
            (
                "schema_version = 3",
                'project_name = "KnowledgeOS"',
                f"control_root = {json.dumps(str(root), ensure_ascii=False)}",
                f"vault_root = {json.dumps(str(vault), ensure_ascii=False)}",
                f"runtime_root = {json.dumps(str(runtime), ensure_ascii=False)}",
                f'core_root = {json.dumps(str(fixture_path(root, "core")), ensure_ascii=False)}',
                f'state_root = {json.dumps(str(fixture_path(root, "state")), ensure_ascii=False)}',
                'timezone = "Asia/Seoul"',
                "",
            )
        ),
        encoding="utf-8",
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
    assert all(not spec.path.startswith("KnowledgeHub/") for spec in OWNED_ARTIFACTS)
    assert not (root / "KnowledgeHub").exists()
    assert not (root / "runtime").exists()
    assert (root / "ops/expected/Property_Dictionary.md").read_text(encoding="utf-8").startswith(
        "<!-- GENERATED: BEGIN knowledgeos-property-dictionary -->"
    )
    assert (root / "ops/schemas/blueprint.schema.json").is_file()
    assert (root / "ops/policies/retrieval.yaml").is_file()


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
    assert report["validation"]["capability_profile"] == "portable_core"


def test_note_schema_and_dictionary_share_the_registry_contract(tmp_path: Path) -> None:
    root = _control_copy(tmp_path)
    assert export_schema_artifacts(root).passed

    schema = json.loads((root / "ops/schemas/note.schema.json").read_text(encoding="utf-8"))
    blueprint = json.loads((root / "ops/schemas/blueprint.schema.json").read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(schema["oneOf"]) == 18
    assert all(branch["additionalProperties"] is False for branch in schema["oneOf"])
    assert (root / "ops/expected/Property_Dictionary.md").read_text(encoding="utf-8").startswith(
        "<!-- GENERATED: BEGIN knowledgeos-property-dictionary -->"
    )
    assert blueprint["$schema"] == "https://json-schema.org/draft/2020-12/schema"

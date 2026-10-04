from __future__ import annotations

import json
from pathlib import Path

from support.control_factory import fixture_path, make_control_root

from vaultops.cli import main
from vaultops.vault_artifacts import (
    SYSTEM_ARTIFACT_PATHS,
    check_vault_artifacts,
    expected_system_artifacts,
)

CONTROL_INPUTS = (
    "blueprint/blueprint.yaml",
    "ops/expected/PrepareTitle.js",
    "ops/expected/Property_Dictionary.md",
)


def _deployed_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, bytes]]:
    root = make_control_root(tmp_path, CONTROL_INPUTS)
    vault = tmp_path / "KnowledgeHub Ω"
    (fixture_path(root, "vault")).rename(vault)
    runtime = tmp_path / "runtime Ω"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    config = root / "ops/vaultops.toml"
    config.write_text(
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
    expected = expected_system_artifacts(root)
    for relative, payload in expected.items():
        target = vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return root, vault, runtime, expected


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_named_system_artifacts_pass_read_only_parity(tmp_path: Path) -> None:
    root, vault, runtime, _expected = _deployed_fixture(tmp_path)
    before = (_snapshot(root), _snapshot(vault), _snapshot(runtime))

    result = check_vault_artifacts(root)

    assert result.passed, result.report
    assert len(SYSTEM_ARTIFACT_PATHS) == 30
    assert {item["status"] for item in result.report["artifacts"]} == {"PASS"}
    assert (_snapshot(root), _snapshot(vault), _snapshot(runtime)) == before
    assert not (root / "KnowledgeHub").exists()
    assert not (root / "runtime").exists()


def test_unlisted_vault_and_profile_bytes_do_not_affect_system_parity(tmp_path: Path) -> None:
    root, vault, _runtime, _expected = _deployed_fixture(tmp_path)
    (vault / "Home.md").write_text("# User Home\n", encoding="utf-8")
    plugin = vault / ".obsidian-mac/plugins/unrelated/data.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text('{"anything": true}\n', encoding="utf-8")
    extra = vault / "99_System/User Extension.md"
    extra.write_text("user-owned extension\n", encoding="utf-8")

    assert check_vault_artifacts(root).passed


def test_one_mismatch_and_one_missing_artifact_are_local(tmp_path: Path) -> None:
    root, vault, _runtime, _expected = _deployed_fixture(tmp_path)
    mismatch = vault / "99_System/Bases/Inbox.base"
    missing = vault / "99_System/Templates/T60_Meeting.md"
    mismatch.write_bytes(mismatch.read_bytes() + b"\n")
    missing.unlink()

    result = check_vault_artifacts(root)

    assert not result.passed
    assert {(error["code"], error["path"]) for error in result.report["errors"]} == {
        ("VAULT_ARTIFACT_MISMATCH", "99_System/Bases/Inbox.base"),
        ("VAULT_ARTIFACT_MISSING", "99_System/Templates/T60_Meeting.md"),
    }


def test_checker_rejects_every_path_outside_the_exact_allowlist(tmp_path: Path) -> None:
    root, _vault, _runtime, _expected = _deployed_fixture(tmp_path)

    for forbidden in (
        "Home.md",
        "Mobile.md",
        ".obsidian-mac/community-plugins.json",
        ".vault-bridge/protocol/request.schema.json",
        "99_System/Smoke/kos-smoke-forbidden.md",
        "99_System/**/*.md",
    ):
        result = check_vault_artifacts(root, paths=[forbidden])
        assert not result.passed
        assert result.report["errors"] == [
            {
                "code": "VAULT_ARTIFACT_PATH_NOT_ALLOWED",
                "path": forbidden,
                "message": "deployed parity is restricted to the named 99_System allowlist",
            }
        ]


def test_checker_rejects_symlinked_artifact_and_cli_reports_json(
    tmp_path: Path,
    capsys,
) -> None:
    root, vault, _runtime, _expected = _deployed_fixture(tmp_path)
    target = vault / "99_System/CSS/dashboard.css"
    target.unlink()
    target.symlink_to(vault / "Home.md")

    result = check_vault_artifacts(root)
    assert not result.passed
    assert ("VAULT_ARTIFACT_UNSAFE_PATH", "99_System/CSS/dashboard.css") in {
        (error["code"], error["path"]) for error in result.report["errors"]
    }

    assert main(["vault-artifacts", "check", "--root", str(root)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["mode"] == "read_only"

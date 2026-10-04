from __future__ import annotations

import json
import subprocess
from pathlib import Path

from support.control_factory import fixture_path, make_control_root

from vaultops.foundation import (
    REQUIRED_DIRECTORIES,
    REQUIRED_FILES,
    check_bound_git_roots,
    check_foundation,
)
from vaultops.paths import resolve_paths


def _foundation_root(tmp_path: Path) -> Path:
    root = make_control_root(tmp_path, REQUIRED_FILES)
    (fixture_path(root, "vault")).rmdir()
    for relative in REQUIRED_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def test_control_foundation_passes_without_deployed_vault_or_runtime(tmp_path: Path) -> None:
    root = _foundation_root(tmp_path)

    assert check_foundation(root) == []
    assert not (root / "KnowledgeHub").exists()
    assert not (root / "runtime").exists()


def _configure_external_roots(root: Path, vault: Path, runtime: Path) -> None:
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


def test_foundation_keeps_external_vault_as_an_independent_git_root(tmp_path: Path) -> None:
    root = _foundation_root(tmp_path)
    vault = tmp_path / "KnowledgeHub Ω"
    vault.mkdir()
    runtime = tmp_path / "runtime Ω"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    _configure_external_roots(root, vault, runtime)
    subprocess.run(["git", "init", "--quiet", str(vault)], check=True, capture_output=True)

    assert check_foundation(root) == []
    assert not (root / "KnowledgeHub").exists()
    assert not (root / "runtime").exists()


def test_foundation_rejects_a_configured_vault_without_its_own_git_root(tmp_path: Path) -> None:
    root = _foundation_root(tmp_path)
    vault = tmp_path / "KnowledgeHub Ω"
    vault.mkdir()
    runtime = tmp_path / "runtime Ω"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    _configure_external_roots(root, vault, runtime)

    assert check_foundation(root) == []
    assert "configured Vault must remain an independent Git root" in check_bound_git_roots(resolve_paths(root))


def test_control_foundation_names_one_missing_control_file(tmp_path: Path) -> None:
    root = _foundation_root(tmp_path)
    (root / "ops/config/generated-artifacts.yaml").unlink()

    assert "missing required file: ops/config/generated-artifacts.yaml" in check_foundation(root)


def test_control_foundation_rejects_a_required_symlink(tmp_path: Path) -> None:
    root = _foundation_root(tmp_path)
    target = root / "ops/tests/AGENTS.md"
    target.unlink()
    target.symlink_to(root / "AGENTS.md")

    assert "required file is a symlink: ops/tests/AGENTS.md" in check_foundation(root)

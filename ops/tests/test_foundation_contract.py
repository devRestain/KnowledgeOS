from __future__ import annotations

import subprocess
from pathlib import Path

from support.control_factory import make_control_root

from vaultops.foundation import REQUIRED_DIRECTORIES, REQUIRED_FILES, check_foundation


def _foundation_root(tmp_path: Path) -> Path:
    root = make_control_root(tmp_path, REQUIRED_FILES)
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
    assert list((root / "KnowledgeHub").iterdir()) == []
    assert not (root / "runtime").exists()


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

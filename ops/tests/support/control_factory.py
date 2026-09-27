"""Build minimal isolated control roots from explicitly declared inputs."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

CONTROL_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_INPUT_ROOTS = frozenset({".git", "KnowledgeHub", "runtime"})
DIAGNOSTIC_CONTROL_INPUTS = (
    "blueprint",
    "ops/actions",
    "ops/config",
    "ops/expected",
    "ops/launchd",
    "ops/policies",
    "ops/prompts",
    "ops/schemas",
    "ops/src/vaultops",
    "ops/vaultops.toml",
)
APPLICATION_CONTROL_INPUTS = DIAGNOSTIC_CONTROL_INPUTS


def _validated_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"test control input must be one safe relative path: {relative}")
    if path.parts[0] in _FORBIDDEN_INPUT_ROOTS:
        raise ValueError(f"test control input crosses a mutable boundary: {relative}")
    return path


def make_control_root(
    tmp_path: Path,
    inputs: Iterable[str],
    *,
    vault_files: Mapping[str, bytes | str] | None = None,
    with_vault: bool = True,
    with_runtime: bool = False,
) -> Path:
    """Copy declared control inputs and synthesize disposable mutable roots."""

    root = tmp_path / "control"
    root.mkdir(parents=True)
    for relative_value in dict.fromkeys(inputs):
        relative = _validated_relative(relative_value)
        source = CONTROL_ROOT.joinpath(*relative.parts)
        target = root.joinpath(*relative.parts)
        if source.is_symlink() or not source.exists():
            raise ValueError(f"declared test control input is missing or unsafe: {relative_value}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    if with_vault:
        vault = root / "KnowledgeHub"
        vault.mkdir()
        for relative_value, payload in (vault_files or {}).items():
            relative = _validated_vault_relative(relative_value)
            target = vault.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)
    elif vault_files:
        raise ValueError("vault_files require with_vault=True")
    if with_runtime:
        (root / "runtime").mkdir()
    return root


def _validated_vault_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"test Vault input must be one safe relative path: {relative}")
    return path


def populate_vault_from_fixture(root: Path, fixture_relative: str) -> None:
    """Copy one declared checked-in fixture tree into a disposable Vault."""

    relative = _validated_relative(fixture_relative)
    if relative.parts[:3] != ("ops", "tests", "fixtures"):
        raise ValueError(f"Vault fixture must live below ops/tests/fixtures: {fixture_relative}")
    source = CONTROL_ROOT.joinpath(*relative.parts)
    vault = root / "KnowledgeHub"
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"declared Vault fixture is missing or unsafe: {fixture_relative}")
    shutil.copytree(source, vault, dirs_exist_ok=True)


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-c", "user.name=KnowledgeOS Test", "-c", "user.email=test@local.invalid", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"test Git command failed: {' '.join(arguments)}: {completed.stderr}")


def _initialize_git_repository(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


def make_diagnostic_root(tmp_path: Path) -> Path:
    """Build a complete disposable doctor input without reading mutable roots."""

    from vaultops.runtime import RUNTIME_DIRECTORIES

    root = make_control_root(tmp_path, DIAGNOSTIC_CONTROL_INPUTS)
    (root / ".gitignore").write_text("/KnowledgeHub/\n/runtime/\n", encoding="utf-8")
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    for relative in RUNTIME_DIRECTORIES:
        (runtime / relative).mkdir(mode=0o700)
    _initialize_git_repository(root)

    vault = root / "KnowledgeHub"
    (vault / ".fixture-anchor").write_text("temporary test repository\n", encoding="utf-8")
    _initialize_git_repository(vault)
    return root

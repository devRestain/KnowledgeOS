from __future__ import annotations

from pathlib import Path

import conftest
import pytest


def test_pytest_requires_the_makefile_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOWLEDGEOS_TEST_ENTRYPOINT", raising=False)
    monkeypatch.setenv("KNOWLEDGEOS_TEST_HERMETIC", "1")

    with pytest.raises(pytest.UsageError, match="repository-root Makefile"):
        conftest.pytest_configure(None)


def test_pytest_requires_the_hermetic_compose_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGEOS_TEST_ENTRYPOINT", "make")
    monkeypatch.delenv("KNOWLEDGEOS_TEST_HERMETIC", raising=False)

    with pytest.raises(pytest.UsageError, match="hermetic Compose override"):
        conftest.pytest_configure(None)


def test_boundary_scan_detects_direct_vault_and_whole_control_copies(tmp_path: Path) -> None:
    direct = tmp_path / "direct.py"
    direct.write_text('target = CONTROL_ROOT / "KnowledgeHub/Home.md"\n', encoding="utf-8")
    joined = tmp_path / "joined.py"
    joined.write_text('VAULT_ROOT = CONTROL_ROOT.joinpath("KnowledgeHub", "Home.md")\n', encoding="utf-8")
    broad = tmp_path / "broad.py"
    broad.write_text("shutil.copytree(\n    CONTROL_ROOT,\n    root,\n)\n", encoding="utf-8")
    safe = tmp_path / "safe.py"
    safe.write_text('target = tmp_path / "control/KnowledgeHub/Home.md"\n', encoding="utf-8")

    assert conftest._source_has_boundary_violation(direct)
    assert conftest._source_has_boundary_violation(joined)
    assert conftest._source_has_boundary_violation(broad)
    assert not conftest._source_has_boundary_violation(safe)


def test_removed_vault_alias_requires_read_only_control_and_refuses_legacy_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setattr(conftest, "_CONTROL_ROOT", control)

    with pytest.raises(pytest.UsageError, match="explicitly read-only"):
        conftest._validate_control_vault_boundary({control: frozenset({"rw"})})

    mounts = {control: frozenset({"ro"})}
    conftest._validate_control_vault_boundary(mounts)
    legacy = control / "KnowledgeHub"
    legacy.mkdir()
    (legacy / "Home.md").write_text("legacy corpus must not enter test collection")
    with pytest.raises(pytest.UsageError, match="unexpected legacy Vault"):
        conftest._validate_control_vault_boundary(mounts)


def test_test_process_imports_mounted_candidate_ahead_of_installed_image() -> None:
    import vaultops
    from vaultops.interfaces import cli

    candidate_source = conftest._TEST_ROOT.parent / "src" / "vaultops"
    installed_image_source = Path("/opt/knowledgeos/ops/src/vaultops")

    assert Path(vaultops.__file__).resolve() == (candidate_source / "__init__.py").resolve()
    assert Path(cli.__file__).resolve() == (candidate_source / "interfaces/cli.py").resolve()
    assert installed_image_source.is_dir()
    assert Path(cli.__file__).resolve() != (installed_image_source / "interfaces/cli.py").resolve()

from __future__ import annotations

import tomllib
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


def test_every_configured_and_legacy_vault_state_alias_is_masked() -> None:
    config_path = conftest._CONFIG_FILE
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    def configured_root(value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else config_path.parent / path).resolve(strict=False)

    control = configured_root(config["control_root"])
    state_key = "runtime_root" if config["schema_version"] == 1 else "state_root"
    expected = {
        control / "KnowledgeHub",
        configured_root(config["vault_root"]),
        control / "runtime",
        Path("/workspace/runtime"),
        configured_root(config[state_key]),
    }
    assert set(conftest._MASKED_PATHS) == expected

    compose_test = (conftest._TEST_ROOT.parent / "compose.test.yaml").read_text(
        encoding="utf-8"
    )
    for alias in expected:
        assert f"target: {alias.as_posix()}" in compose_test


def test_compose_keeps_state_runtime_sources_independent_and_masks_both() -> None:
    control_root = conftest._TEST_ROOT.parent.parent
    makefile = (control_root / "Makefile").read_text(encoding="utf-8")
    compose = (conftest._TEST_ROOT.parent / "compose.yaml").read_text(encoding="utf-8")
    compose_test = (conftest._TEST_ROOT.parent / "compose.test.yaml").read_text(
        encoding="utf-8"
    )

    assert "KNOWLEDGEOS_STATE_SOURCE ?= $(KNOWLEDGEOS_RUNTIME_SOURCE)" not in makefile
    assert "source: ${KNOWLEDGEOS_STATE_SOURCE:?Set KNOWLEDGEOS_STATE_SOURCE through Make}" in compose
    assert "target: /workspace/state" in compose
    assert "source: knowledgeos-state-mask" in compose_test
    assert "target: /workspace/state" in compose_test
    assert "target: /workspace/runtime" in compose_test
    assert "mode=0700" in compose_test


def test_test_process_imports_mounted_candidate_ahead_of_installed_image() -> None:
    import vaultops
    from vaultops import cli

    candidate_source = conftest._TEST_ROOT.parent / "src" / "vaultops"
    installed_image_source = Path("/opt/knowledgeos/ops/src/vaultops")

    assert Path(vaultops.__file__).resolve() == (candidate_source / "__init__.py").resolve()
    assert Path(cli.__file__).resolve() == (candidate_source / "interfaces/cli.py").resolve()
    assert installed_image_source.is_dir()
    assert Path(cli.__file__).resolve() != (installed_image_source / "cli.py").resolve()


def test_acceptance_checks_the_selected_independent_vault_git_root() -> None:
    makefile = (conftest._TEST_ROOT.parent.parent / "Makefile").read_text(encoding="utf-8")

    assert 'git -C "$(KNOWLEDGEOS_VAULT_SOURCE)" diff --check' in makefile

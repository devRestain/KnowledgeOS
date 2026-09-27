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
    broad = tmp_path / "broad.py"
    broad.write_text("shutil.copytree(\n    CONTROL_ROOT,\n    root,\n)\n", encoding="utf-8")
    safe = tmp_path / "safe.py"
    safe.write_text('target = tmp_path / "control/KnowledgeHub/Home.md"\n', encoding="utf-8")

    assert conftest._source_has_boundary_violation(direct)
    assert conftest._source_has_boundary_violation(broad)
    assert not conftest._source_has_boundary_violation(safe)

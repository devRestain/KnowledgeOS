"""Enforce the Make-only hermetic KnowledgeOS regression boundary."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_MAKE_ENTRYPOINT = "KNOWLEDGEOS_TEST_ENTRYPOINT"
_HERMETIC_ENTRYPOINT = "KNOWLEDGEOS_TEST_HERMETIC"
_MASKED_PATHS = (
    Path("/workspace/control/KnowledgeHub"),
    Path("/workspace/KnowledgeHub"),
    Path("/workspace/control/runtime"),
    Path("/workspace/runtime"),
)
_TEST_ROOT = Path(__file__).resolve().parent
_BOUNDARY_PATTERNS = (
    re.compile(r"CONTROL_ROOT\s*/\s*[\"']KnowledgeHub"),
    re.compile(r"CONTROL_ROOT\.joinpath\(\s*[\"']KnowledgeHub(?:/|[\"'])"),
    re.compile(r"VAULT_ROOT\s*=\s*CONTROL_ROOT\s*/\s*[\"']KnowledgeHub[\"']"),
    re.compile(r"shutil\.copytree\(\s*CONTROL_ROOT\s*,", re.DOTALL),
    re.compile(r"(?:shutil\.)?copytree\(\s*CONTROL_ROOT\s*/\s*[\"']KnowledgeHub", re.DOTALL),
    re.compile(r"doctor_report\(\s*CONTROL_ROOT\s*\)"),
    re.compile(r"check_foundation\(\s*CONTROL_ROOT\s*\)"),
)

def pytest_configure(config: pytest.Config) -> None:
    """Reject direct pytest execution before collection touches test inputs."""

    del config
    if os.environ.get(_MAKE_ENTRYPOINT) != "make":
        raise pytest.UsageError(
            "KnowledgeOS tests must run through the repository-root Makefile; "
            "use `make test` or `make test PYTEST_ARGS=\"...\"`."
        )
    if os.environ.get(_HERMETIC_ENTRYPOINT) != "1":
        raise pytest.UsageError(
            "KnowledgeOS tests require the hermetic Compose override; use the repository-root `make test`."
        )


def _mount_options() -> dict[Path, frozenset[str]]:
    mounts: dict[Path, frozenset[str]] = {}
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise pytest.UsageError(f"cannot inspect hermetic mounts: {error}") from error
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        mountpoint = Path(fields[4].replace("\\040", " "))
        mounts[mountpoint] = frozenset(fields[5].split(","))
    return mounts


def _validate_masked_paths() -> None:
    mounts = _mount_options()
    errors: list[str] = []
    for path in _MASKED_PATHS:
        options = mounts.get(path)
        if options is None:
            errors.append(f"not an explicit mount: {path}")
            continue
        if "ro" not in options:
            errors.append(f"mount is not read-only: {path}")
        try:
            entries = tuple(path.iterdir())
        except OSError as error:
            errors.append(f"cannot inspect masked path {path}: {error}")
            continue
        if entries:
            errors.append(f"masked path is not empty: {path}")
    if errors:
        raise pytest.UsageError("invalid KnowledgeOS hermetic boundary: " + "; ".join(errors))


def _source_has_boundary_violation(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return any(pattern.search(text) for pattern in _BOUNDARY_PATTERNS)


def _validate_test_sources() -> None:
    violations: list[str] = []
    for path in sorted(_TEST_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name in {"conftest.py", "test_test_runner_contract.py"}:
            continue
        if not _source_has_boundary_violation(path):
            continue
        relative = path.relative_to(_TEST_ROOT).as_posix()
        violations.append(relative)
    if violations:
        raise pytest.UsageError(
            "test source crosses the hermetic Vault/runtime boundary: " + ", ".join(violations)
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail before collection when mounts or test sources cross the boundary."""

    del session
    _validate_masked_paths()
    _validate_test_sources()

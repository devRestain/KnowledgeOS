"""Read-only parity checks for the named deployed ``99_System`` projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .base_dashboard import (
    BASE_PATHS,
    SYSTEM_DASHBOARD_PATHS,
    render_base_documents,
    system_dashboard_sources,
)
from .template_engine import template_paths, template_source
from .yaml_safe import load_yaml_file

PROPERTY_DICTIONARY_PATH = "99_System/Schemas/Property_Dictionary.md"
PREPARE_TITLE_PATH = "99_System/Scripts/QuickAdd/PrepareTitle.js"
SYSTEM_ARTIFACT_PATHS = (
    *template_paths(),
    *BASE_PATHS,
    *SYSTEM_DASHBOARD_PATHS,
    PROPERTY_DICTIONARY_PATH,
    PREPARE_TITLE_PATH,
)
_SYSTEM_ARTIFACT_SET = frozenset(SYSTEM_ARTIFACT_PATHS)


@dataclass(frozen=True)
class VaultArtifactCheckResult:
    """Machine-readable result for one read-only deployed-copy check."""

    report: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.report["status"] == "PASS"

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def as_json(self) -> str:
        return json.dumps(self.report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _error(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _requested_paths(paths: Iterable[str] | None) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    requested = SYSTEM_ARTIFACT_PATHS if paths is None else tuple(dict.fromkeys(paths))
    errors: list[dict[str, str]] = []
    for value in requested:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(character in value for character in "*?[]")
            or value not in _SYSTEM_ARTIFACT_SET
        ):
            errors.append(
                _error(
                    "VAULT_ARTIFACT_PATH_NOT_ALLOWED",
                    value,
                    "deployed parity is restricted to the named 99_System allowlist",
                )
            )
    return requested, errors


def expected_system_artifacts(control_root: str | Path) -> dict[str, bytes]:
    """Build exact expected bytes without reading the deployed Vault."""

    root = Path(control_root).resolve()
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    if not isinstance(blueprint, Mapping):
        raise TypeError("Blueprint root must be a mapping")
    expected = {
        path: template_source(PurePosixPath(path).name).encode("utf-8")
        for path in template_paths()
    }
    expected.update(
        {path: source.encode("utf-8") for path, source in render_base_documents(blueprint).items()}
    )
    expected.update(
        {path: source.encode("utf-8") for path, source in system_dashboard_sources().items()}
    )
    expected[PROPERTY_DICTIONARY_PATH] = (root / "ops/expected/Property_Dictionary.md").read_bytes()
    expected[PREPARE_TITLE_PATH] = (root / "ops/expected/PrepareTitle.js").read_bytes()
    if set(expected) != _SYSTEM_ARTIFACT_SET:
        raise ValueError("system artifact generators do not match the exact allowlist")
    return expected


def _deployed_target(vault_root: Path, relative: str) -> tuple[Path | None, str | None]:
    path = PurePosixPath(relative)
    current = vault_root
    if current.is_symlink():
        return None, "Vault root is a symlink"
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            return None, f"deployed artifact path traverses a symlink: {relative}"
    return current, None


def check_vault_artifacts(
    control_root: str | Path,
    *,
    paths: Iterable[str] | None = None,
) -> VaultArtifactCheckResult:
    """Compare named deployed copies without writing any Vault or control byte."""

    root = Path(control_root).resolve()
    requested, errors = _requested_paths(paths)
    artifacts: list[dict[str, Any]] = []
    if errors:
        return VaultArtifactCheckResult(
            {
                "status": "FAIL",
                "mode": "read_only",
                "vault_scope": "KnowledgeHub/99_System",
                "artifacts": artifacts,
                "errors": errors,
            }
        )
    try:
        expected = expected_system_artifacts(root)
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as error:
        errors.append(
            _error("VAULT_ARTIFACT_EXPECTATION_INVALID", "99_System", str(error))
        )
        expected = {}

    vault_root = root / "KnowledgeHub"
    for relative in requested:
        expected_bytes = expected.get(relative)
        item: dict[str, Any] = {"path": relative, "status": "NOT_CHECKED"}
        if expected_bytes is None:
            artifacts.append(item)
            continue
        item["expected_sha256"] = _sha256(expected_bytes)
        target, target_error = _deployed_target(vault_root, relative)
        if target_error or target is None:
            item["status"] = "UNSAFE"
            errors.append(
                _error("VAULT_ARTIFACT_UNSAFE_PATH", relative, target_error or "unsafe path")
            )
        elif not target.is_file():
            item["status"] = "MISSING"
            errors.append(
                _error("VAULT_ARTIFACT_MISSING", relative, "named system artifact is missing")
            )
        else:
            actual = target.read_bytes()
            item["actual_sha256"] = _sha256(actual)
            if actual == expected_bytes:
                item["status"] = "PASS"
            else:
                item["status"] = "MISMATCH"
                errors.append(
                    _error(
                        "VAULT_ARTIFACT_MISMATCH",
                        relative,
                        "deployed system artifact differs from the control expectation",
                    )
                )
        artifacts.append(item)

    errors.sort(key=lambda item: (item["path"], item["code"]))
    return VaultArtifactCheckResult(
        {
            "status": "PASS" if not errors else "FAIL",
            "mode": "read_only",
            "vault_scope": "KnowledgeHub/99_System",
            "artifacts": artifacts,
            "errors": errors,
        }
    )

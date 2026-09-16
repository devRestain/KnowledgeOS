"""Additive bootstrap for canonical directories and portable C07-C08 files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base_dashboard import dashboard_sources, render_base_documents
from .blueprint import validate_blueprint
from .note_engine import UnsafePathError, normalize_vault_relative_path
from .template_engine import TEMPLATE_SPECS, template_source
from .yaml_safe import load_yaml_file


class BootstrapConflict(ValueError):
    """Raised when bootstrap would overwrite or cross an existing boundary."""


NON_DIRECTORY_FIXED_PATHS = frozenset({".knowledgeos-root.json", "Home.md", "Mobile.md"})


def _directory_targets(blueprint: Mapping[str, Any]) -> tuple[str, ...]:
    fixed = blueprint.get("fixed_paths", {}).get("vault", ())
    # ``fixed_paths.vault`` also names the three root-level files.  The
    # sentinel and Home/Mobile files are intentionally not bootstrap-owned;
    # every remaining entry is a canonical directory namespace.
    directories = [str(path) for path in fixed if str(path) not in NON_DIRECTORY_FIXED_PATHS]
    return tuple(dict.fromkeys(sorted(directories)))


def _safe_vault_path(vault_root: Path, relative: str) -> Path:
    normalized = normalize_vault_relative_path(relative)
    candidate = vault_root.joinpath(*normalized.split("/"))
    current = vault_root
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise BootstrapConflict(f"bootstrap path crosses a symlink: {relative}")
    return candidate


def _atomic_create_bytes(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise BootstrapConflict(f"bootstrap target appeared during write: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.bootstrap.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise BootstrapConflict(f"bootstrap temporary path already exists: {temporary}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def bootstrap(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Plan or apply additive bootstrap without overwriting any file.

    The whole target set is preflighted before the first write.  A conflict
    therefore produces no new directory or template file from this operation.
    """

    workspace = Path(root).resolve()
    validation = validate_blueprint(workspace)
    if not validation.passed:
        return {
            "status": "FAIL",
            "operation": "bootstrap",
            "mode": "dry-run" if dry_run else "apply",
            "errors": validation.report.get("errors", []),
        }
    try:
        blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
        vault_root = workspace / "KnowledgeHub"
        if vault_root.is_symlink():
            raise BootstrapConflict("Vault root is a symlink")
        directory_relatives = _directory_targets(blueprint)
        directory_paths = [(relative, _safe_vault_path(vault_root, relative)) for relative in directory_relatives]
        artifact_sources = {
            spec.relative_path: template_source(spec.filename).encode("utf-8")
            for spec in TEMPLATE_SPECS
        }
        artifact_sources.update(
            {
                relative: content.encode("utf-8")
                for relative, content in {
                    **render_base_documents(blueprint),
                    **dashboard_sources(),
                }.items()
            }
        )
        artifact_checks: list[dict[str, str]] = []
        conflicts: list[str] = []

        for relative, path in directory_paths:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                conflicts.append(relative)
        for relative, expected_bytes in artifact_sources.items():
            path = _safe_vault_path(vault_root, relative)
            if path.is_symlink():
                artifact_checks.append({"path": relative, "status": "CONFLICT"})
                conflicts.append(relative)
            elif path.exists():
                status = (
                    "EXISTING"
                    if path.is_file()
                    and path.read_bytes() == expected_bytes
                    else "CONFLICT"
                )
                artifact_checks.append({"path": relative, "status": status})
                if status == "CONFLICT":
                    conflicts.append(relative)
            else:
                artifact_checks.append({"path": relative, "status": "CREATE"})

        if conflicts:
            return {
                "status": "CONFLICT",
                "operation": "bootstrap",
                "mode": "dry-run" if dry_run else "apply",
                "directories": [{"path": relative, "status": "CONFLICT" if relative in conflicts else ("EXISTING" if path.exists() else "CREATE")} for relative, path in directory_paths],
                "artifacts": artifact_checks,
                "conflicts": sorted(set(conflicts)),
            }

        directories = [
            {"path": relative, "status": "EXISTING" if path.exists() else "CREATE"}
            for relative, path in directory_paths
        ]
        report: dict[str, Any] = {
            "status": "PASS",
            "operation": "bootstrap",
            "mode": "dry-run" if dry_run else "apply",
            "directories": directories,
            "artifacts": artifact_checks,
            "created": [],
            "existing": [
                item["path"] for item in [*directories, *artifact_checks] if item["status"] == "EXISTING"
            ],
        }
        if dry_run:
            report["would_create"] = [
                item["path"] for item in [*directories, *artifact_checks] if item["status"] == "CREATE"
            ]
            return report

        created: list[str] = []
        for relative, path in directory_paths:
            if not path.exists():
                path.mkdir(parents=True, exist_ok=False)
                created.append(relative)
        for item in artifact_checks:
            if item["status"] != "CREATE":
                continue
            relative = item["path"]
            path = _safe_vault_path(vault_root, relative)
            _atomic_create_bytes(path, artifact_sources[relative])
            created.append(relative)
        report["created"] = created
        report["existing"] = [
            item["path"] for item in [*directories, *artifact_checks] if item["status"] == "EXISTING"
        ]
        return report
    except (OSError, UnicodeError, TypeError, ValueError, UnsafePathError, BootstrapConflict) as error:
        return {
            "status": "FAIL",
            "operation": "bootstrap",
            "mode": "dry-run" if dry_run else "apply",
            "errors": [{"code": "BOOTSTRAP_INPUT_INVALID", "locator": "/", "message": str(error)}],
        }


def bootstrap_json(root: str | Path, *, dry_run: bool = False) -> str:
    return json.dumps(bootstrap(root, dry_run=dry_run), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

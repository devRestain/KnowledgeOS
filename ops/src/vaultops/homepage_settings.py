"""Read-only P08 Homepage owned-entry safety inspection.

The Homepage audit examines only the stable KnowledgeOS-owned entry in the
serialized plugin profile. User-created Homepage entries and note contents are
outside this capability check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

P08_HOMEPAGE_REGISTRY_SCHEMA_VERSION = 1
P08_PLUGIN_ID = "homepage"
P08_OWNED_STARTUP_NAME = "KnowledgeOS Home"

_SAFE_TARGET = re.compile(r"^[^\r\n\x00\\:]+$")
_FORBIDDEN_TARGET_MARKERS = (
    "javascript:",
    "shell:",
    "quickadd://",
    "templater://",
    "vaultctl://",
    "http://",
    "https://",
    "obsidian:",
)
_FORBIDDEN_TARGET_ROOTS = frozenset({".obsidian", ".obsidian-mac", ".obsidian-mobile", ".vault-bridge", ".git", "runtime"})


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs), None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return None, str(error)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": "error"}


def _safe_vault_target(value: Any) -> bool:
    if not isinstance(value, str) or not value or not _SAFE_TARGET.fullmatch(value):
        return False
    if value.startswith("/") or any(marker in value.lower() for marker in _FORBIDDEN_TARGET_MARKERS):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts) and parts[0] not in _FORBIDDEN_TARGET_ROOTS


def _blueprint_home_target(blueprint: dict[str, Any]) -> str | None:
    dashboards = blueprint.get("dashboards")
    home = dashboards.get("home") if isinstance(dashboards, dict) else None
    target = home.get("path") if isinstance(home, dict) else None
    return target if _safe_vault_target(target) else None


def _homepage_entry(
    data: dict[str, Any],
    *,
    data_path: Path,
    root: Path,
    expected_target: str | None,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    homepages = data.get("homepages")
    locator = _relative(root, data_path) + f"#/homepages/{P08_OWNED_STARTUP_NAME}"
    if homepages is None:
        return {
            "state": "not_configured",
            "name": P08_OWNED_STARTUP_NAME,
            "open_on_startup": None,
            "target": None,
            "presentation": {},
        }
    if not isinstance(homepages, dict):
        errors.append(_error("P08_HOMEPAGES_INVALID", _relative(root, data_path) + "#/homepages", "homepages must be an object"))
        return {
            "state": "invalid",
            "name": P08_OWNED_STARTUP_NAME,
            "open_on_startup": None,
            "target": None,
            "presentation": {},
        }

    entry = homepages.get(P08_OWNED_STARTUP_NAME)
    if entry is None:
        return {
            "state": "not_configured",
            "name": P08_OWNED_STARTUP_NAME,
            "open_on_startup": None,
            "target": None,
            "presentation": {},
        }
    if not isinstance(entry, dict):
        errors.append(_error("P08_OWNED_ENTRY_INVALID", locator, "the KnowledgeOS-owned Homepage entry must be an object"))
        return {
            "state": "invalid",
            "name": P08_OWNED_STARTUP_NAME,
            "open_on_startup": None,
            "target": None,
            "presentation": {},
        }

    value = entry.get("value")
    kind = entry.get("kind")
    if not _safe_vault_target(value) or kind != "File":
        errors.append(_error("P08_UNSAFE_STARTUP_TARGET", locator, "the KnowledgeOS Homepage target must be a safe relative file path"))
    else:
        accepted_targets = {expected_target} if expected_target else set()
        if expected_target and "/" not in expected_target:
            accepted_targets.add(PurePosixPath(expected_target).stem)
        if value not in accepted_targets:
            errors.append(_error("P08_STARTUP_TARGET_NOT_HOME", locator, "the KnowledgeOS Homepage target must match the Blueprint home path"))

    open_on_startup = entry.get("openOnStartup")
    if open_on_startup is not None and not isinstance(open_on_startup, bool):
        errors.append(_error("P08_STARTUP_FLAG_INVALID", locator + "/openOnStartup", "openOnStartup must be a boolean when configured"))

    auto_create = entry.get("autoCreate")
    if auto_create is True:
        errors.append(_error("P08_AUTO_CREATE_ENABLED", locator + "/autoCreate", "the KnowledgeOS Homepage entry must not create notes automatically"))
    refresh_dataview = entry.get("refreshDataview")
    if refresh_dataview is True:
        errors.append(_error("P08_DATAVIEW_REFRESH_ENABLED", locator + "/refreshDataview", "the KnowledgeOS Homepage entry must not refresh Dataview automatically"))
    commands = entry.get("commands")
    if commands not in (None, []):
        errors.append(_error("P08_STARTUP_COMMANDS_NOT_EMPTY", locator + "/commands", "the KnowledgeOS Homepage entry must not run startup commands"))

    return {
        "state": "configured" if open_on_startup is True else "not_configured",
        "name": P08_OWNED_STARTUP_NAME,
        "open_on_startup": open_on_startup,
        "target": value if _safe_vault_target(value) else "invalid",
        "kind": kind,
        "safety": {
            "auto_create": auto_create,
            "refresh_dataview": refresh_dataview,
            "commands_empty": commands in (None, []),
        },
        "presentation": {
            "open_mode": entry.get("openMode"),
            "manual_open_mode": entry.get("manualOpenMode"),
            "view": entry.get("view"),
            "revert_view": entry.get("revertView"),
            "open_when_empty": entry.get("openWhenEmpty"),
            "auto_scroll": entry.get("autoScroll"),
            "pin": entry.get("pin"),
            "always_apply": entry.get("alwaysApply"),
        },
    }


def build_homepage_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P08 registry from the owned Homepage profile entry only."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/homepage/manifest.json"
    data_path = profile_root / "plugins/homepage/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P08_HOMEPAGE_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Homepage manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P08_HOMEPAGE_DATA_ROOT_INVALID", _relative(root, data_path), "Homepage data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P08_PLUGIN_ID):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Homepage manifest id does not match the installed plugin id"))

    expected_target = _blueprint_home_target(blueprint)
    if expected_target is None:
        errors.append(_error("P08_BLUEPRINT_TARGET_INVALID", "blueprint/blueprint.yaml#/dashboards/home/path", "Blueprint home target must be a safe relative path"))
    serialized = data if isinstance(data, dict) else {}
    owned_entry = _homepage_entry(
        serialized,
        data_path=data_path,
        root=root,
        expected_target=expected_target,
        errors=errors,
    )
    registry = {
        "schema_version": P08_HOMEPAGE_REGISTRY_SCHEMA_VERSION,
        "plugin": P08_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "owned_startup_entry": owned_entry,
        "startup_policy": {
            "owned_entry_name": P08_OWNED_STARTUP_NAME,
            "state": owned_entry["state"],
            "target": owned_entry.get("target"),
            "auto_create": owned_entry.get("safety", {}).get("auto_create"),
            "refresh_dataview": owned_entry.get("safety", {}).get("refresh_dataview"),
            "commands_empty": owned_entry.get("safety", {}).get("commands_empty"),
        },
        "blueprint_contract": {
            "home_target": expected_target,
            "source": "blueprint/blueprint.yaml#/dashboards/home/path",
            "note_content_inspected": False,
        },
        "mobile_policy": {
            "state": "not_inferred",
            "policy": "mobile profile and note content are outside the Mac Homepage audit",
        },
        "fallback": {
            "state": "not_inspected",
            "policy": "Markdown and Core navigation remain independently available",
        },
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if not errors else "blocked",
            "runtime": "not_run",
            "device": "not_run",
            "startup_execution": "not_run",
            "note_content": "not_inspected",
            "plugin_data_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P08_HOMEPAGE_REGISTRY_SCHEMA_VERSION",
    "P08_OWNED_STARTUP_NAME",
    "P08_PLUGIN_ID",
    "build_homepage_setting_registry",
]

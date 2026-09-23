"""Read-only P11 Note Toolbar contextual-surface contract inspection.

P11 treats Note Toolbar as a presentation and navigation layer.  Stable file
links and exact installed command IDs are reviewed into an action inventory;
the toolbar is never treated as a policy engine, shell launcher, approval
authority, or automatic canonical writer.

This module inspects the checked-in Mac profile and Home action inventory only.
It never edits toolbar data, executes a button, changes a note, or operates
Obsidian.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .gui_contracts import ALLOWED_COMMAND_IDS

P11_NOTE_TOOLBAR_REGISTRY_SCHEMA_VERSION = 1
P11_PLUGIN_ID = "note-toolbar"

_SAFE_VAULT_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\r\n\x00]+$")
_CURRENT_WEEKLY_FILE = re.compile(r"^10_Journal/Weekly/(?P<year>\d{4})/(?P=year)-W(?:0[1-9]|[1-4]\d|5[0-3])\.md$")
_CURRENT_MONTHLY_FILE = re.compile(r"^10_Journal/Monthly/(?P<year>\d{4})/(?P=year)-(?:0[1-9]|1[0-2])\.md$")
_FORBIDDEN_LINK_MARKERS = (
    "obsidian:",
    "javascript:",
    "vaultctl",
    "shell",
    "script",
    "eval",
    "http://",
    "https://",
)

_EXPECTED_FOLDER_TOOLBARS = {
    "/": "KnowledgeOS Home",
    "01_AI_Review": "KnowledgeOS Review",
    "10_Journal": "KnowledgeOS Daily",
    "20_Projects": "KnowledgeOS Project",
    "00_Inbox": "KnowledgeOS Inbox",
    "40_Knowledge": "KnowledgeOS Knowledge",
}

_COMMAND_POLICIES: dict[str, dict[str, Any]] = {
    "daily-notes": {
        "action_id": "today_daily",
        "surface": "Obsidian Core Daily Notes",
        "mutation_class": "core_daily_note_open_or_create",
        "human_action_required": True,
        "rollback": "review and remove only a human-created empty daily note if needed",
    },
    "homepage:open-homepage": {
        "action_id": "home",
        "surface": "Homepage opening Home.md",
        "mutation_class": "navigation_only",
        "human_action_required": True,
        "rollback": "no canonical mutation claimed",
    },
    "backlink:open-backlinks": {
        "action_id": "backlinks",
        "surface": "Core Backlinks navigation",
        "mutation_class": "navigation_only",
        "human_action_required": True,
        "rollback": "no canonical mutation claimed",
    },
    "outgoing-links:open-for-current": {
        "action_id": "outgoing_links",
        "surface": "Core Outgoing links navigation",
        "mutation_class": "navigation_only",
        "human_action_required": True,
        "rollback": "no canonical mutation claimed",
    },
    "breadcrumbs:open-tree-view": {
        "action_id": "breadcrumbs",
        "surface": "Breadcrumbs navigation tree",
        "mutation_class": "navigation_only",
        "human_action_required": True,
        "rollback": "no canonical mutation claimed",
    },
}

_STATIC_FILE_ACTIONS = {
    "Home.md": ("home", "Home dashboard"),
    "99_System/Dashboards/Tasks.md": ("tasks", "Tasks dashboard"),
    "99_System/Dashboards/Weekly_Review.md": ("weekly_review", "weekly review dashboard"),
    "99_System/Bases/Inbox.base": ("inbox", "Inbox base"),
    "99_System/Bases/Knowledge.base": ("knowledge", "Knowledge base"),
    "99_System/Bases/Projects.base": ("projects", "Projects base"),
    "99_System/Bases/Review.base": ("review", "Review base"),
    "99_System/Bases/Sources.base": ("sources", "Sources base"),
}

_EXPECTED_TOOLBAR_TARGETS: dict[str, set[tuple[str, str]]] = {
    "KnowledgeOS Daily": {
        ("file", "Home.md"),
        ("file", "99_System/Dashboards/Tasks.md"),
        ("file", "99_System/Dashboards/Weekly_Review.md"),
        ("command", "daily-notes"),
        ("file_pattern", "weekly"),
        ("file_pattern", "monthly"),
    },
    "KnowledgeOS Home": {
        ("command", "homepage:open-homepage"),
        ("command", "daily-notes"),
        ("file", "99_System/Dashboards/Tasks.md"),
        ("file", "99_System/Bases/Review.base"),
    },
    "KnowledgeOS Inbox": {
        ("file", "Home.md"),
        ("file", "99_System/Bases/Inbox.base"),
        ("file", "99_System/Bases/Projects.base"),
        ("file", "99_System/Bases/Review.base"),
        ("command", "backlink:open-backlinks"),
    },
    "KnowledgeOS Knowledge": {
        ("file", "Home.md"),
        ("file", "99_System/Bases/Knowledge.base"),
        ("file", "99_System/Bases/Sources.base"),
        ("command", "backlink:open-backlinks"),
        ("command", "outgoing-links:open-for-current"),
    },
    "KnowledgeOS Project": {
        ("file", "Home.md"),
        ("file", "99_System/Bases/Projects.base"),
        ("file", "99_System/Dashboards/Weekly_Review.md"),
        ("command", "breadcrumbs:open-tree-view"),
        ("command", "backlink:open-backlinks"),
        ("command", "outgoing-links:open-for-current"),
    },
    "KnowledgeOS Review": {
        ("file", "Home.md"),
        ("file", "99_System/Bases/Review.base"),
        ("file", "99_System/Dashboards/Weekly_Review.md"),
    },
}


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


def _read_text(path: Path) -> tuple[str | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "missing"
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as error:
        return None, str(error)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": "error"}


def _exact_setting(value: Any, expected: Any, *, policy: str) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "expected": expected, "state": "unknown", "policy": policy}
    if type(value) is not type(expected):
        return {"observed": "invalid", "expected": expected, "state": "invalid", "policy": policy}
    state = "pass" if value == expected else "drift"
    return {"observed": value, "expected": expected, "state": state, "policy": policy}


def _list_setting(value: Any, *, expected: list[Any], policy: str) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "expected": expected, "state": "unknown", "policy": policy}
    if not isinstance(value, list):
        return {"observed": "invalid", "expected": expected, "state": "invalid", "policy": policy}
    state = "pass" if value == expected else "drift"
    return {"observed": value, "expected": expected, "state": state, "policy": policy}


def _append_drift_errors(
    settings: dict[str, dict[str, Any]],
    *,
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
    code: str,
) -> None:
    for key, record in settings.items():
        if record["state"] in {"drift", "invalid"}:
            errors.append(
                _error(
                    code,
                    _relative(root, data_path) + f"#/{key}",
                    f"Note Toolbar setting {key} does not satisfy the accepted P11 contract",
                )
            )


def _home_contract(root: Path, blueprint: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    path = root / "KnowledgeHub/Home.md"
    text, read_error = _read_text(path)
    if read_error:
        return {
            "state": "unknown",
            "source": _relative(root, path),
            "capture": {"state": "unknown", "actions": [], "hotkeys": []},
            "command_palette": "recovery_or_diagnostic_only",
        }
    dashboards = blueprint.get("dashboards") if isinstance(blueprint.get("dashboards"), dict) else {}
    home = dashboards.get("home") if isinstance(dashboards.get("home"), dict) else {}
    sections = home.get("sections") if isinstance(home.get("sections"), list) else []
    quick_capture = next(
        (section for section in sections if isinstance(section, dict) and section.get("name") == "quick_capture"),
        {},
    )
    actions = quick_capture.get("actions") if isinstance(quick_capture.get("actions"), list) else []
    hotkeys = quick_capture.get("hotkeys") if isinstance(quick_capture.get("hotkeys"), list) else []
    capture_state = "pass" if all(str(action) in (text or "") for action in actions) and hotkeys == [
        "option_command_c",
        "option_command_j",
        "option_command_p",
        "option_command_q",
        "option_command_k",
    ] else "blocked"
    if capture_state == "blocked":
        errors.append(
            _error(
                "P11_HOME_CAPTURE_INVENTORY_UNRESOLVED",
                _relative(root, path) + "#/quick_capture",
                "Home must preserve the reviewed capture action and hotkey inventory",
            )
        )
    if "⌥⌘C" not in (text or ""):
        errors.append(
            _error(
                "P11_CAPTURE_DISPLAY_CONTRACT_DRIFT",
                _relative(root, path),
                "Home must display the accepted Option-Command-C capture contract",
            )
        )
    return {
        "state": "pass" if capture_state == "pass" else "blocked",
        "source": _relative(root, path),
        "capture": {
            "state": capture_state,
            "actions": list(actions),
            "hotkeys": list(hotkeys),
            "display_contract": "⌥⌘C is the accepted CAPTURE_THOUGHT display; QuickAdd command identity remains unclaimed",
            "primary_surface": "Home.md",
            "toolbar_surface": "not_configured_until_exact QuickAdd command IDs are verified",
        },
        "command_palette": "recovery_or_diagnostic_only",
        "normal_journey": "Home.md or contextual toolbar first",
    }


def _global_policy(data: dict[str, Any], *, data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    settings = {
        "scripting_enabled": _exact_setting(data.get("scriptingEnabled"), False, policy="arbitrary scripting remains disabled"),
        "debug_enabled": _exact_setting(data.get("debugEnabled"), False, policy="debug surface remains disabled"),
        "show_launchpad": _exact_setting(data.get("showLaunchpad"), False, policy="launchpad command aggregation remains disabled"),
        "show_toolbar_in_file_menu": _exact_setting(data.get("showToolbarInFileMenu"), False, policy="toolbar is contextual rather than a global file-menu command surface"),
        "show_toolbar_in_other": _exact_setting(data.get("showToolbarInOther"), "", policy="other editor surfaces remain unconfigured"),
        "toolbar_property": _exact_setting(data.get("toolbarProp"), "none", policy="no implicit property-driven toolbar authority"),
        "rules": _list_setting(data.get("rules"), expected=[], policy="rule callbacks remain unconfigured"),
        "show_edit_in_fab_menu": _exact_setting(data.get("showEditInFabMenu"), False, policy="toolbar editing remains outside the runtime surface"),
        "keep_props_state": _exact_setting(data.get("keepPropsState"), False, policy="property state is not persisted as hidden write authority"),
        "lock_callouts": _exact_setting(data.get("lockCallouts"), False, policy="callout locking remains disabled"),
        "show_toolbar_in": _exact_setting(
            data.get("showToolbarIn"),
            {"audio": False, "bases": False, "canvas": False, "image": False, "kanban": False, "pdf": False, "video": False},
            policy="non-note editor surfaces remain disabled",
        ),
    }
    _append_drift_errors(
        {
            "scriptingEnabled": settings["scripting_enabled"],
            "debugEnabled": settings["debug_enabled"],
            "showLaunchpad": settings["show_launchpad"],
            "showToolbarInFileMenu": settings["show_toolbar_in_file_menu"],
            "showToolbarInOther": settings["show_toolbar_in_other"],
            "toolbarProp": settings["toolbar_property"],
            "rules": settings["rules"],
            "showEditInFabMenu": settings["show_edit_in_fab_menu"],
            "keepPropsState": settings["keep_props_state"],
            "lockCallouts": settings["lock_callouts"],
            "showToolbarIn": settings["show_toolbar_in"],
        },
        data_path=data_path,
        root=root,
        errors=errors,
        code="P11_FORBIDDEN_SURFACE_ENABLED",
    )
    state = "pass" if all(record["state"] == "pass" for record in settings.values()) else "unknown" if any(record["state"] == "unknown" for record in settings.values()) and not errors else "blocked"
    return {
        "state": state,
        "settings": settings,
        "export": data.get("export"),
        "empty_view_toolbar": data.get("emptyViewToolbar"),
        "policy": "presentation and navigation only no scripting no callbacks no arbitrary execution",
    }


def _file_target(link: str, root: Path) -> tuple[tuple[str, str] | None, str]:
    if link in _STATIC_FILE_ACTIONS:
        action_id, _description = _STATIC_FILE_ACTIONS[link]
        return ("file", link), action_id
    if _CURRENT_WEEKLY_FILE.fullmatch(link):
        return ("file_pattern", "weekly"), "weekly_open"
    if _CURRENT_MONTHLY_FILE.fullmatch(link):
        return ("file_pattern", "monthly"), "monthly_open"
    return None, "unknown"


def _item_record(
    toolbar_name: str,
    item: Any,
    *,
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], tuple[str, str] | None, str]:
    locator = _relative(root, data_path) + f"#/toolbars/{toolbar_name}/items"
    if not isinstance(item, dict):
        errors.append(_error("P11_TOOLBAR_ITEM_INVALID", locator, "toolbar item must be an object"))
        return {"state": "invalid", "toolbar": toolbar_name}, None, "unknown"
    link = item.get("link")
    attributes = item.get("linkAttr")
    if not isinstance(link, str) or not isinstance(attributes, dict):
        errors.append(_error("P11_TOOLBAR_ITEM_TARGET_INVALID", locator, "toolbar item must declare a link and linkAttr object"))
        return {"state": "invalid", "toolbar": toolbar_name, "label": item.get("label")}, None, "unknown"
    kind = attributes.get("type")
    command_id = attributes.get("commandId")
    has_vars = attributes.get("hasVars")
    command_check = attributes.get("commandCheck")
    record: dict[str, Any] = {
        "state": "pass",
        "toolbar": toolbar_name,
        "label": item.get("label"),
        "type": kind,
        "link": link,
        "command_id": command_id,
        "has_vars": has_vars,
        "command_check": command_check,
        "has_command": item.get("hasCommand"),
    }
    if item.get("hasCommand") is not False:
        errors.append(_error("P11_ITEM_COMMAND_METADATA_UNRESOLVED", locator, "toolbar item hasCommand must remain false"))
    if has_vars is not False:
        errors.append(_error("P11_ITEM_VARIABLES_ENABLED", locator, "toolbar items must not use variable substitution"))
    if any(marker in link.lower() for marker in _FORBIDDEN_LINK_MARKERS):
        errors.append(_error("P11_FORBIDDEN_LINK_TARGET", locator, "toolbar link contains an external or executable capability marker"))
    if kind == "file":
        if command_id != "" or command_check is not False:
            errors.append(_error("P11_FILE_TARGET_METADATA_INVALID", locator, "file toolbar items must have empty commandId and commandCheck false"))
        if not _SAFE_VAULT_PATH.fullmatch(link):
            errors.append(_error("P11_UNSAFE_FILE_TARGET", locator, f"toolbar file target is not a safe relative path: {link!r}"))
            return {**record, "state": "blocked"}, None, "unknown"
        target, action_id = _file_target(link, root)
        if target is None:
            errors.append(_error("P11_UNREVIEWED_FILE_TARGET", locator, f"toolbar file target is not in the reviewed action inventory: {link!r}"))
            return {**record, "state": "blocked"}, None, "unknown"
        if not (root / "KnowledgeHub" / link).is_file():
            errors.append(_error("P11_FILE_TARGET_MISSING", locator, f"reviewed toolbar file target does not exist: {link!r}"))
            record["state"] = "unknown"
        record.update(
            {
                "action_id": action_id,
                "target_kind": "reviewed_file",
                "mutation_class": "navigation_only",
                "human_action_required": True,
                "rollback": "no canonical mutation claimed",
            }
        )
        return record, target, action_id
    if kind == "command":
        if not isinstance(command_id, str) or command_id not in ALLOWED_COMMAND_IDS or command_id not in _COMMAND_POLICIES:
            errors.append(_error("P11_UNVERIFIED_COMMAND_ID", locator, f"toolbar command ID is not in the installed reviewed allowlist: {command_id!r}"))
            return {**record, "state": "blocked"}, None, "unknown"
        policy = _COMMAND_POLICIES[command_id]
        if command_check is not False:
            errors.append(_error("P11_COMMAND_CHECK_UNRESOLVED", locator, "command toolbar items must keep commandCheck false"))
        record.update(policy)
        record["target_kind"] = "verified_command"
        return record, ("command", command_id), policy["action_id"]
    errors.append(_error("P11_UNSUPPORTED_TARGET_TYPE", locator, f"unsupported toolbar target type: {kind!r}"))
    return {**record, "state": "blocked"}, None, "unknown"


def _toolbars_contract(
    data: dict[str, Any],
    *,
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    toolbars = data.get("toolbars")
    if toolbars is None:
        return {"state": "unknown", "toolbars": [], "toolbar_names": []}, {}
    if not isinstance(toolbars, list):
        errors.append(_error("P11_TOOLBARS_INVALID", _relative(root, data_path) + "#/toolbars", "toolbars must be a list"))
        return {"state": "blocked", "toolbars": [], "toolbar_names": []}, {}
    records: list[dict[str, Any]] = []
    targets_by_toolbar: dict[str, list[dict[str, Any]]] = {}
    names: list[str] = []
    for index, toolbar in enumerate(toolbars):
        locator = _relative(root, data_path) + f"#/toolbars/{index}"
        if not isinstance(toolbar, dict) or not isinstance(toolbar.get("name"), str):
            errors.append(_error("P11_TOOLBAR_INVALID", locator, "toolbar must be an object with a name"))
            continue
        name = toolbar["name"]
        names.append(name)
        if name in names[:-1]:
            errors.append(_error("P11_DUPLICATE_TOOLBAR_NAME", locator, f"toolbar name is duplicated: {name!r}"))
        items = toolbar.get("items")
        if not isinstance(items, list):
            errors.append(_error("P11_TOOLBAR_ITEMS_INVALID", locator + "/items", "toolbar items must be a list"))
            items = []
        item_records: list[dict[str, Any]] = []
        target_records: list[dict[str, Any]] = []
        target_keys: list[tuple[str, str]] = []
        for item in items:
            item_record, target, action_id = _item_record(
                name,
                item,
                root=root,
                data_path=data_path,
                errors=errors,
            )
            item_records.append(item_record)
            if target is not None:
                target_keys.append(target)
                target_records.append({"target": target, "action_id": action_id, "record": item_record})
        if len(target_keys) != len(set(target_keys)):
            errors.append(_error("P11_DUPLICATE_TOOLBAR_TARGET", locator + "/items", f"toolbar {name!r} repeats a target"))
        expected = _EXPECTED_TOOLBAR_TARGETS.get(name)
        observed = set(target_keys)
        if expected is None:
            errors.append(_error("P11_UNREVIEWED_TOOLBAR", locator + "/name", f"toolbar name is not in the reviewed inventory: {name!r}"))
        else:
            missing = sorted(expected - observed)
            extra = sorted(observed - expected)
            if missing or extra:
                errors.append(
                    _error(
                        "P11_TOOLBAR_ACTION_DRIFT",
                        locator + "/items",
                        f"toolbar {name!r} differs from the reviewed target inventory missing={missing!r} extra={extra!r}",
                    )
                )
        records.append(
            {
                "name": name,
                "uuid": toolbar.get("uuid"),
                "item_count": len(items),
                "items": item_records,
                "targets": target_records,
                "state": "pass" if not any(item["state"] in {"blocked", "invalid"} for item in item_records) else "blocked",
                "presentation": {
                    "position": toolbar.get("position"),
                    "default_styles": toolbar.get("defaultStyles"),
                    "custom_classes": toolbar.get("customClasses"),
                    "mobile_styles": toolbar.get("mobileStyles"),
                },
            }
        )
        targets_by_toolbar[name] = target_records
    expected_names = set(_EXPECTED_TOOLBAR_TARGETS)
    missing_names = sorted(expected_names - set(names))
    if missing_names:
        errors.append(_error("P11_TOOLBAR_NAME_MISSING", _relative(root, data_path) + "#/toolbars", f"reviewed toolbars are missing: {missing_names!r}"))
    state = "pass" if not errors and set(names) == expected_names else "blocked" if errors else "unknown"
    return {"state": state, "toolbars": records, "toolbar_names": names}, targets_by_toolbar


def _folder_mapping_contract(
    data: dict[str, Any],
    *,
    root: Path,
    data_path: Path,
    toolbar_records: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    mappings = data.get("folderMappings")
    if mappings is None:
        return {"state": "unknown", "observed": [], "expected_folders": sorted(_EXPECTED_FOLDER_TOOLBARS)}
    if not isinstance(mappings, list):
        errors.append(_error("P11_FOLDER_MAPPINGS_INVALID", _relative(root, data_path) + "#/folderMappings", "folderMappings must be a list"))
        return {"state": "blocked", "observed": [], "expected_folders": sorted(_EXPECTED_FOLDER_TOOLBARS)}
    uuid_to_name = {record.get("uuid"): record.get("name") for record in toolbar_records}
    observed: dict[str, Any] = {}
    for index, mapping in enumerate(mappings):
        locator = _relative(root, data_path) + f"#/folderMappings/{index}"
        if not isinstance(mapping, dict) or not isinstance(mapping.get("folder"), str):
            errors.append(_error("P11_FOLDER_MAPPING_INVALID", locator, "folder mapping must contain a folder string"))
            continue
        folder = mapping["folder"]
        toolbar_uuid = mapping.get("toolbar")
        toolbar_name = uuid_to_name.get(toolbar_uuid)
        observed[folder] = {"toolbar_uuid": toolbar_uuid, "toolbar_name": toolbar_name}
        if toolbar_name is None:
            errors.append(_error("P11_FOLDER_MAPPING_UNRESOLVED", locator, f"folder mapping references an unknown toolbar UUID: {toolbar_uuid!r}"))
        if folder not in _EXPECTED_FOLDER_TOOLBARS:
            errors.append(_error("P11_UNREVIEWED_FOLDER_MAPPING", locator, f"folder mapping is not in the reviewed inventory: {folder!r}"))
        elif toolbar_name != _EXPECTED_FOLDER_TOOLBARS[folder]:
            errors.append(_error("P11_FOLDER_MAPPING_DRIFT", locator, f"folder {folder!r} does not resolve to its reviewed contextual toolbar"))
    expected = {
        folder: {"toolbar_name": toolbar_name}
        for folder, toolbar_name in _EXPECTED_FOLDER_TOOLBARS.items()
    }
    missing = sorted(set(_EXPECTED_FOLDER_TOOLBARS) - set(observed))
    if missing:
        errors.append(_error("P11_FOLDER_MAPPING_MISSING", _relative(root, data_path) + "#/folderMappings", f"reviewed folder mappings are missing: {missing!r}"))
    return {
        "state": "pass" if not errors and set(observed) == set(_EXPECTED_FOLDER_TOOLBARS) else "blocked" if errors else "unknown",
        "observed": observed,
        "expected": expected,
        "source": _relative(root, data_path) + "#/folderMappings",
    }


def _action_inventory(
    *,
    home_contract: dict[str, Any],
    toolbar_records: list[dict[str, Any]],
    root: Path,
) -> dict[str, Any]:
    contexts: dict[str, set[str]] = {}
    for toolbar in toolbar_records:
        for target in toolbar.get("targets", []):
            action_id = target.get("action_id")
            if isinstance(action_id, str) and action_id != "unknown":
                contexts.setdefault(action_id, set()).add(toolbar.get("name", "unknown"))
    expected_actions = {
        "home": "Home dashboard or Homepage command",
        "today_daily": "Core Daily Notes command",
        "weekly_open": "reviewed current weekly file link",
        "monthly_open": "reviewed current monthly file link",
        "tasks": "Tasks dashboard file",
        "weekly_review": "Weekly Review dashboard file",
        "review": "Review base file",
        "inbox": "Inbox base file",
        "projects": "Projects base file",
        "knowledge": "Knowledge base file",
        "sources": "Sources base file",
        "backlinks": "verified Core Backlinks command",
        "outgoing_links": "verified Core Outgoing links command",
        "breadcrumbs": "verified Breadcrumbs tree command",
    }
    inventory: dict[str, Any] = {}
    for action_id, primary_surface in expected_actions.items():
        toolbar_contexts = sorted(contexts.get(action_id, set()))
        state = "pass" if toolbar_contexts else "unknown"
        inventory[action_id] = {
            "state": state,
            "primary_surface": primary_surface,
            "toolbar_contexts": toolbar_contexts,
            "mutation_class": "core_daily_note_open_or_create" if action_id == "today_daily" else "navigation_only",
            "human_action_required": True,
            "rollback": "review and remove only a human-created empty daily note if needed" if action_id == "today_daily" else "no canonical mutation claimed",
        }
    capture = home_contract.get("capture", {})
    inventory["capture"] = {
        "state": capture.get("state", "unknown"),
        "primary_surface": "Home.md",
        "toolbar_contexts": [],
        "toolbar_policy": "not_configured_until_exact QuickAdd command IDs are verified",
        "display_contract": capture.get("display_contract"),
        "mutation_class": "human_capture_router_create_only",
        "human_action_required": True,
        "rollback": "remove only a newly created capture after human review",
    }
    return {
        "state": "pass" if all(item["state"] == "pass" for item in inventory.values()) else "unknown" if any(item["state"] == "unknown" for item in inventory.values()) else "blocked",
        "actions": inventory,
        "policy": "Home or contextual toolbar is the normal journey; Command Palette is recovery only",
        "source": "Home.md; blueprint/blueprint.yaml#/dashboards/home; note-toolbar data",
    }


def _fallback_contract(root: Path) -> dict[str, Any]:
    home_path = root / "KnowledgeHub/Home.md"
    core_path = root / "KnowledgeHub/.obsidian-mac/core-plugins.json"
    core_plugins, core_error = _read_json(core_path)
    file_explorer = core_plugins.get("file-explorer") if isinstance(core_plugins, dict) else None
    if file_explorer is False:
        state = "blocked"
    elif core_error == "missing" or file_explorer is None or not home_path.is_file():
        state = "unknown"
    elif file_explorer is True:
        state = "pass"
    else:
        state = "blocked"
    return {
        "state": state,
        "canonical_home": "KnowledgeHub/Home.md",
        "core_file_explorer": {
            "observed": file_explorer,
            "expected": True,
            "source": _relative(root, core_path) + "#/file-explorer",
        },
        "markdown_links": "reviewed canonical Markdown links",
        "command_palette": "recovery_or_diagnostic_only",
        "plugin_free": True,
    }


def build_note_toolbar_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P11 registry from the installed Note Toolbar profile."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/note-toolbar/manifest.json"
    data_path = profile_root / "plugins/note-toolbar/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P11_TOOLBAR_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P11_TOOLBAR_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P11_TOOLBAR_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Note Toolbar manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P11_TOOLBAR_DATA_ROOT_INVALID", _relative(root, data_path), "Note Toolbar data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P11_PLUGIN_ID):
        errors.append(_error("P11_TOOLBAR_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Note Toolbar manifest id does not match the installed plugin id"))

    serialized = data if isinstance(data, dict) else {}
    global_policy = _global_policy(serialized, data_path=data_path, root=root, errors=errors)
    toolbar_contract, _toolbar_targets = _toolbars_contract(
        serialized,
        root=root,
        data_path=data_path,
        errors=errors,
    )
    folder_mapping = _folder_mapping_contract(
        serialized,
        root=root,
        data_path=data_path,
        toolbar_records=toolbar_contract["toolbars"],
        errors=errors,
    )
    home_contract = _home_contract(root, blueprint, errors)
    action_inventory = _action_inventory(
        home_contract=home_contract,
        toolbar_records=toolbar_contract["toolbars"],
        root=root,
    )
    fallback = _fallback_contract(root)
    if fallback["state"] == "blocked":
        errors.append(
            _error(
                "P11_FALLBACK_UNAVAILABLE",
                fallback["core_file_explorer"]["source"],
                "Core File Explorer must remain available as the immediate plugin-free fallback",
            )
        )

    registry = {
        "schema_version": P11_NOTE_TOOLBAR_REGISTRY_SCHEMA_VERSION,
        "plugin": P11_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "serialized_version": serialized.get("version"),
        "global_policy": global_policy,
        "folder_mappings": folder_mapping,
        "toolbars": toolbar_contract,
        "command_allowlist": {
            "allowed_ids": sorted(ALLOWED_COMMAND_IDS),
            "policies": _COMMAND_POLICIES,
            "source": "current installed commandId values plus P01 GUI allowlist",
            "unknown_command_policy": "reject",
        },
        "home_contract": home_contract,
        "action_inventory": action_inventory,
        "mutation_policy": {
            "toolbar_role": "presentation_and_navigation_only",
            "scripting": "forbidden",
            "uri_callbacks": "forbidden",
            "external_processes": "forbidden",
            "network": "forbidden",
            "git": "forbidden",
            "ai": "forbidden",
            "vaultctl": "forbidden",
            "canonical_apply": "forbidden_without_separate_human_workflow",
            "write_capable_action": "human_action_required_with_mutation_class_and_rollback",
            "plugin_data_mutation": "out_of_scope",
            "note_mutation": "out_of_scope",
        },
        "fallback": fallback,
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if isinstance(data, dict) and not errors else "blocked" if errors else "unknown",
            "toolbar_execution": "not_run",
            "runtime": "not_run",
            "device": "not_run",
            "command_execution": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "canonical_note_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P11_NOTE_TOOLBAR_REGISTRY_SCHEMA_VERSION",
    "P11_PLUGIN_ID",
    "build_note_toolbar_setting_registry",
]

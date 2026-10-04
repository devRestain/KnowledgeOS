"""Read-only P11 inspection of KnowledgeOS-owned Note Toolbar contexts.

Only stable KnowledgeOS toolbar identifiers and their required safe actions
participate in health. User toolbar presentation, ordering, and mappings outside
protected contexts remain user-owned state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .gui_contracts import ALLOWED_COMMAND_IDS

P11_NOTE_TOOLBAR_REGISTRY_SCHEMA_VERSION = 1
P11_PLUGIN_ID = "note-toolbar"

_SAFE_VAULT_PATH = re.compile(r"^[^\r\n\x00\\:]+$")
_CURRENT_WEEKLY_FILE = re.compile(r"^10_Journal/Weekly/(?P<year>\d{4})/(?P=year)-W(?:0[1-9]|[1-4]\d|5[0-3])\.md$")
_CURRENT_MONTHLY_FILE = re.compile(r"^10_Journal/Monthly/(?P<year>\d{4})/(?P=year)-(?:0[1-9]|1[0-2])\.md$")
_FORBIDDEN_LINK_MARKERS = (
    "javascript:",
    "obsidian:",
    "shell:",
    "command:",
    "http://",
    "https://",
    "quickadd://",
    "templater://",
    "vaultctl://",
)
_URI_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)

_EXECUTABLE_ITEM_KEYS = frozenset(
    {"script", "javascript", "function", "callback", "variables", "shell", "process", "eval"}
)
_VARIABLE_SYNTAX = re.compile(r"\$\{[^}]*\}|\{\{.*?}}|<%.*?%>")
_FORBIDDEN_PATH_ROOTS = frozenset({".obsidian", ".obsidian-mac", ".obsidian-mobile", ".vault-bridge", ".git", "runtime"})

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
    "99_System/Bases/Journal.base#Open Reviews": ("period_reviews", "Journal period review view"),
}

_EXPECTED_TOOLBAR_TARGETS: dict[str, set[tuple[str, str]]] = {
    "KnowledgeOS Daily": {
        ("file", "Home.md"),
        ("file", "99_System/Dashboards/Tasks.md"),
        ("file", "99_System/Dashboards/Weekly_Review.md"),
        ("command", "daily-notes"),
        ("file", "99_System/Bases/Journal.base#Open Reviews"),
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


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": "error"}


def _global_policy(data: dict[str, Any], *, data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    locator = _relative(root, data_path)
    scripting = data.get("scriptingEnabled")
    scripting_state = "pass" if scripting is False else "invalid" if scripting is not None else "unknown"
    if scripting is not False:
        errors.append(_error("P11_SCRIPTING_NOT_DISABLED", locator + "#/scriptingEnabled", "Note Toolbar scripting must be explicitly disabled"))

    rules = data.get("rules")
    if rules is None:
        rules_record = {"state": "unknown", "count": None}
    elif not isinstance(rules, list):
        rules_record = {"state": "invalid", "count": None}
        errors.append(_error("P11_RULES_INVALID", locator + "#/rules", "Note Toolbar rules must be an array"))
    elif rules:
        rules_record = {"state": "blocked", "count": len(rules)}
        errors.append(_error("P11_EXECUTABLE_RULES_ENABLED", locator + "#/rules", "Note Toolbar callback rules are outside the reviewed P11 capability"))
    else:
        rules_record = {"state": "pass", "count": 0}

    state = "pass" if scripting is False and rules_record["state"] in {"pass", "unknown"} else "blocked"
    return {
        "state": state,
        "settings": {
            "scripting_enabled": {"observed": scripting, "expected": False, "state": scripting_state},
            "rules": rules_record,
        },
        "policy": "presentation and navigation only no scripting no callbacks no arbitrary execution",
    }


def _file_target(link: str) -> tuple[tuple[str, str] | None, str]:
    if link in _STATIC_FILE_ACTIONS:
        action_id, _description = _STATIC_FILE_ACTIONS[link]
        return ("file", link), action_id
    if _CURRENT_WEEKLY_FILE.fullmatch(link):
        return ("file_pattern", "weekly"), "weekly_open"
    if _CURRENT_MONTHLY_FILE.fullmatch(link):
        return ("file_pattern", "monthly"), "monthly_open"
    return None, "unknown"


def _safe_file_path(link: str) -> bool:
    if not _SAFE_VAULT_PATH.fullmatch(link) or link.startswith("/"):
        return False
    parts = link.split("/")
    return all(part not in {"", ".", ".."} for part in parts) and parts[0] not in _FORBIDDEN_PATH_ROOTS


def _protected_target(link: str) -> bool:
    parts = PurePosixPath(link).parts
    return "99_System" in parts or any(
        parts[index : index + 2] == ("01_AI_Review", "Pending")
        for index in range(max(0, len(parts) - 1))
    )


def _has_unreviewed_protected_item(toolbar: Any) -> bool:
    items = toolbar.get("items") if isinstance(toolbar, dict) else None
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if isinstance(link, str) and _protected_target(link):
            return True
    return False


def _has_executable_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in _EXECUTABLE_ITEM_KEYS or normalized.startswith(("script", "javascript", "callback", "eval")):
                return True
            if _has_executable_metadata(child):
                return True
    elif isinstance(value, list):
        return any(_has_executable_metadata(item) for item in value)
    return False


def _item_record(
    toolbar_name: str,
    item: Any,
    *,
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], tuple[str, str] | None, str]:
    locator = _relative(root, data_path) + f"#/toolbars/{toolbar_name}/items"
    starting_error_count = len(errors)
    if not isinstance(item, dict):
        errors.append(_error("P11_TOOLBAR_ITEM_INVALID", locator, "toolbar item must be an object"))
        return {"state": "invalid", "toolbar": toolbar_name}, None, "unknown"
    link = item.get("link")
    attributes = item.get("linkAttr")
    if not isinstance(link, str) or not isinstance(attributes, dict):
        errors.append(_error("P11_TOOLBAR_ITEM_TARGET_INVALID", locator, "toolbar item must declare a link and linkAttr object"))
        return {"state": "invalid", "toolbar": toolbar_name}, None, "unknown"
    kind = attributes.get("type")
    command_id = attributes.get("commandId")
    has_vars = attributes.get("hasVars")
    command_check = attributes.get("commandCheck")
    record: dict[str, Any] = {
        "state": "pass",
        "toolbar": toolbar_name,
        "type": kind if isinstance(kind, str) and kind in {"file", "command"} else "unsupported",
    }
    if item.get("hasCommand") is not False:
        errors.append(_error("P11_ITEM_COMMAND_METADATA_UNRESOLVED", locator, "toolbar item hasCommand must remain false"))
    if has_vars is not False:
        errors.append(_error("P11_ITEM_VARIABLES_ENABLED", locator, "toolbar items must not use variable substitution"))
    if _has_executable_metadata(item) or _has_executable_metadata(attributes):
        errors.append(_error("P11_EXECUTABLE_ITEM_METADATA", locator, "toolbar items must not include script, callback, or variable payloads"))
    reviewed_command_link = kind == "command" and isinstance(command_id, str) and link == command_id and command_id in ALLOWED_COMMAND_IDS
    if (
        _VARIABLE_SYNTAX.search(link)
        or any(marker in link.lower() for marker in _FORBIDDEN_LINK_MARKERS)
        or ("://" in link)
        or (_URI_SCHEME.match(link) is not None and not reviewed_command_link)
    ):
        errors.append(_error("P11_FORBIDDEN_LINK_TARGET", locator, "toolbar link contains an external or executable capability marker"))
    if kind == "file":
        if command_id != "" or command_check is not False:
            errors.append(_error("P11_FILE_TARGET_METADATA_INVALID", locator, "file toolbar items must have empty commandId and commandCheck false"))
        if not _safe_file_path(link):
            errors.append(_error("P11_UNSAFE_FILE_TARGET", locator, "toolbar file target must be a safe relative vault path"))
            return {**record, "state": "blocked"}, None, "unknown"
        target, action_id = _file_target(link)
        if target is None:
            if _protected_target(link):
                errors.append(_error("P11_UNREVIEWED_PROTECTED_TARGET", locator, "toolbar target enters a protected path without a reviewed action contract"))
                return {**record, "state": "blocked"}, None, "unknown"
            state = "blocked" if len(errors) > starting_error_count else "user_owned_ignored"
            return {**record, "state": state, "target_kind": "user_file"}, None, "unknown"
        record.update(
            {
                "action_id": action_id,
                "target_kind": "reviewed_file",
            }
        )
        if len(errors) > starting_error_count:
            record["state"] = "blocked"
        return record, target, action_id
    if kind == "command":
        if not isinstance(command_id, str) or command_id not in ALLOWED_COMMAND_IDS or command_id not in _COMMAND_POLICIES:
            errors.append(_error("P11_UNVERIFIED_COMMAND_ID", locator, "toolbar command ID is not in the reviewed allowlist"))
            return {**record, "state": "blocked"}, None, "unknown"
        policy = _COMMAND_POLICIES[command_id]
        if command_check is not False:
            errors.append(_error("P11_COMMAND_CHECK_UNRESOLVED", locator, "command toolbar items must keep commandCheck false"))
        record.update({"action_id": policy["action_id"], "target_kind": "verified_command"})
        if len(errors) > starting_error_count:
            record["state"] = "blocked"
        return record, ("command", command_id), policy["action_id"]
    errors.append(_error("P11_UNSUPPORTED_TARGET_TYPE", locator, "toolbar target type is not a reviewed file or command"))
    return {**record, "state": "blocked"}, None, "unknown"


def _toolbars_contract(
    data: dict[str, Any],
    *,
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    starting_error_count = len(errors)
    toolbars = data.get("toolbars")
    if toolbars is None:
        return {"state": "unknown", "toolbars": [], "toolbar_names": []}, {}
    if not isinstance(toolbars, list):
        errors.append(_error("P11_TOOLBARS_INVALID", _relative(root, data_path) + "#/toolbars", "toolbars must be a list"))
        return {"state": "blocked", "toolbars": [], "toolbar_names": []}, {}
    records: list[dict[str, Any]] = []
    targets_by_toolbar: dict[str, list[dict[str, Any]]] = {}
    names: list[str] = []
    seen_names: set[str] = set()
    for index, toolbar in enumerate(toolbars):
        locator = _relative(root, data_path) + f"#/toolbars/{index}"
        if not isinstance(toolbar, dict) or not isinstance(toolbar.get("name"), str):
            continue
        name = toolbar["name"]
        expected = _EXPECTED_TOOLBAR_TARGETS.get(name)
        if expected is None:
            if _has_unreviewed_protected_item(toolbar):
                errors.append(_error("P11_UNREVIEWED_PROTECTED_MAPPING", locator, "user toolbar maps an unreviewed action into a protected path"))
            continue
        names.append(name)
        if name in seen_names:
            errors.append(_error("P11_DUPLICATE_TOOLBAR_NAME", locator, f"toolbar name is duplicated: {name!r}"))
            continue
        seen_names.add(name)
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
        observed = set(target_keys)
        missing = sorted(expected - observed)
        if missing:
            errors.append(_error("P11_REQUIRED_ACTION_MISSING", locator + "/items", "KnowledgeOS toolbar is missing a required reviewed action"))
        unreviewed_protected = [
            target
            for target in observed - expected
            if target[0] == "file" and _protected_target(target[1])
        ]
        if unreviewed_protected:
            errors.append(_error("P11_UNREVIEWED_PROTECTED_MAPPING", locator + "/items", "KnowledgeOS toolbar maps an unreviewed action into a protected path"))
        records.append(
            {
                "name": name,
                "item_count": len(item_records),
                "items": item_records,
                "targets": target_records,
                "state": "blocked" if missing or unreviewed_protected or any(item["state"] in {"blocked", "invalid"} for item in item_records) else "pass",
                "presentation": "user_owned_advisory",
            }
        )
        targets_by_toolbar[name] = target_records
    expected_names = set(_EXPECTED_TOOLBAR_TARGETS)
    missing_names = expected_names - set(names)
    if missing_names:
        errors.append(_error("P11_TOOLBAR_NAME_MISSING", _relative(root, data_path) + "#/toolbars", "one or more KnowledgeOS-owned toolbar contexts are not configured"))
    state = "blocked" if missing_names or len(errors) > starting_error_count else "pass"
    return {"state": state, "toolbars": records, "toolbar_names": names}, targets_by_toolbar


def _folder_mapping_contract(
    data: dict[str, Any],
    *,
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    mappings = data.get("folderMappings")
    if mappings is None:
        return {"state": "unknown", "protected_mapping_count": 0, "user_mapping_count": 0}
    if not isinstance(mappings, list):
        errors.append(_error("P11_FOLDER_MAPPINGS_INVALID", _relative(root, data_path) + "#/folderMappings", "folderMappings must be a list"))
        return {"state": "blocked", "protected_mapping_count": 0, "user_mapping_count": 0}
    observed_error_count = len(errors)
    protected_mapping_count = 0
    user_mapping_count = 0
    for index, mapping in enumerate(mappings):
        locator = _relative(root, data_path) + f"#/folderMappings/{index}"
        if not isinstance(mapping, dict) or not isinstance(mapping.get("folder"), str):
            continue
        folder = mapping["folder"]
        safe_folder = folder == "/" or _safe_file_path(folder)
        if not safe_folder:
            errors.append(_error("P11_UNSAFE_FOLDER_MAPPING", locator, "folder mappings must use safe relative vault paths"))
            continue
        if _protected_target(folder):
            protected_mapping_count += 1
            errors.append(_error("P11_UNREVIEWED_PROTECTED_MAPPING", locator, "folder mapping enters a protected path without a reviewed P11 context"))
        else:
            user_mapping_count += 1
    return {
        "state": "blocked" if len(errors) > observed_error_count else "pass",
        "protected_mapping_count": protected_mapping_count,
        "user_mapping_count": user_mapping_count,
        "user_mappings": "ignored_outside_protected_contexts",
        "source": _relative(root, data_path) + "#/folderMappings",
    }


def _action_inventory(
    *,
    toolbar_records: list[dict[str, Any]],
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
        "period_reviews": "Journal Open Reviews view without a frozen period note",
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
    return {
        "state": "pass" if all(item["state"] == "pass" for item in inventory.values()) else "unknown" if any(item["state"] == "unknown" for item in inventory.values()) else "blocked",
        "actions": inventory,
        "policy": "reviewed toolbar actions are navigation or explicit human-triggered Core commands",
        "source": "note-toolbar owned toolbar entries",
    }


def _fallback_contract() -> dict[str, Any]:
    return {
        "state": "not_inspected",
        "core_file_explorer": "not_inferred",
        "markdown_navigation": "independent_fallback",
        "command_palette": "recovery_or_diagnostic_only",
        "plugin_free": True,
    }


def build_note_toolbar_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P11 registry from owned entries in the installed profile."""

    del blueprint  # P11 no longer inspects dashboard note content or layouts.
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
        errors=errors,
    )
    action_inventory = _action_inventory(
        toolbar_records=toolbar_contract["toolbars"],
    )
    fallback = _fallback_contract()

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
        "position_policy": {
            "desktop": "user_owned_advisory",
            "mobile": "user_owned_advisory",
            "tablet": "user_owned_advisory",
        },
        "global_policy": global_policy,
        "folder_mappings": folder_mapping,
        "toolbars": toolbar_contract,
        "command_allowlist": {
            "allowed_ids": sorted(ALLOWED_COMMAND_IDS),
            "policies": _COMMAND_POLICIES,
            "source": "current installed commandId values plus P01 GUI allowlist",
            "unknown_command_policy": "reject",
        },
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

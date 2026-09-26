"""Read-only P08 Homepage startup and plugin-free fallback inspection.

P08 allows one reviewed Mac startup target for ``Home.md``.  Homepage is a
navigation adapter only: its command list stays empty, auto-create and
Dataview refresh stay off, and the canonical Markdown dashboard remains the
fallback when the plugin is unavailable or not installed on another device.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

P08_HOMEPAGE_REGISTRY_SCHEMA_VERSION = 1
P08_PLUGIN_ID = "homepage"
P08_ALLOWED_DAILY_URI = "obsidian://daily?vault=KnowledgeHub"
P08_FORBIDDEN_ACTION_MARKERS = (
    "javascript:",
    "shell",
    "quickadd://",
    "templater",
    "vaultctl",
)


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


def _observed(value: Any, *, expected: Any = None, compare: bool = False) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if compare:
        return {"observed": value, "state": "pass" if value == expected else "drift", "expected": expected}
    return {"observed": value, "state": "observed"}


def _home_blueprint_contract(blueprint: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    dashboards = blueprint.get("dashboards")
    dashboards = dashboards if isinstance(dashboards, dict) else {}
    home = dashboards.get("home") if isinstance(dashboards.get("home"), dict) else {}
    mobile = dashboards.get("mobile") if isinstance(dashboards.get("mobile"), dict) else {}
    expected_home = {"path": "Home.md", "type": "home", "audience": "desktop"}
    observed_home = {key: home.get(key) for key in expected_home}
    if observed_home != expected_home:
        errors.append(
            _error(
                "P08_HOME_BLUEPRINT_DRIFT",
                "blueprint/blueprint.yaml#/dashboards/home",
                "Homepage target must remain the desktop Home.md dashboard",
            )
        )
    expected_mobile = {"path": "Mobile.md", "type": "home", "audience": "mobile", "community_plugin_required": False}
    observed_mobile = {key: mobile.get(key) for key in expected_mobile}
    if observed_mobile != expected_mobile:
        errors.append(
            _error(
                "P08_MOBILE_BLUEPRINT_DRIFT",
                "blueprint/blueprint.yaml#/dashboards/mobile",
                "Mobile.md must remain the plugin-free mobile fallback dashboard",
            )
        )
    return {
        "home": observed_home,
        "mobile": observed_mobile,
        "home_sections": [section.get("name") for section in home.get("sections", []) if isinstance(section, dict)],
        "mobile_sections": [section.get("name") for section in mobile.get("sections", []) if isinstance(section, dict)],
        "source": "blueprint/blueprint.yaml#/dashboards/home and /dashboards/mobile",
    }


def _homepage_targets(homepages: Any, data_path: Path, root: Path, errors: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if homepages is None:
        return [], []
    if not isinstance(homepages, dict):
        errors.append(_error("P08_HOMEPAGES_INVALID", _relative(root, data_path) + "#/homepages", "homepages must be an object"))
        return [], []
    targets: list[dict[str, Any]] = []
    startup: list[dict[str, Any]] = []
    for name in sorted(homepages):
        value = homepages[name]
        if not isinstance(value, dict):
            errors.append(_error("P08_HOMEPAGE_ENTRY_INVALID", _relative(root, data_path) + f"#/homepages/{name}", "Homepage entry must be an object"))
            continue
        record = {
            "name": name,
            "value": value.get("value"),
            "kind": value.get("kind"),
            "open_on_startup": value.get("openOnStartup"),
            "open_mode": value.get("openMode"),
            "manual_open_mode": value.get("manualOpenMode"),
            "view": value.get("view"),
            "revert_view": value.get("revertView"),
            "open_when_empty": value.get("openWhenEmpty"),
            "refresh_dataview": value.get("refreshDataview"),
            "auto_create": value.get("autoCreate"),
            "auto_scroll": value.get("autoScroll"),
            "pin": value.get("pin"),
            "commands": value.get("commands"),
            "always_apply": value.get("alwaysApply"),
        }
        targets.append(record)
        if value.get("openOnStartup") is True:
            startup.append(record)
    if len(startup) != 1:
        errors.append(
            _error(
                "P08_STARTUP_TARGET_COUNT_INVALID",
                _relative(root, data_path) + "#/homepages",
                "exactly one Homepage target must be marked openOnStartup",
            )
        )
    return targets, startup


def _home_action_inventory(
    *,
    root: Path,
    home_path: Path,
    mobile_path: Path,
    home_text: str | None,
    mobile_text: str | None,
    blueprint: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    home = home_text or ""
    mobile = mobile_text or ""
    dashboard = blueprint.get("dashboards", {}).get("home", {}) if isinstance(blueprint.get("dashboards"), dict) else {}
    expected_tokens = {
        "tasks": ("description regex matches /\\S/",),
        "inbox": ("99_System/Bases/Inbox.base#Unprocessed",),
        "ai_review": ("99_System/Bases/Review.base#PendingOrConflict",),
        "projects": ("99_System/Bases/Projects.base#Now",),
        "decisions": ("99_System/Bases/Decisions.base#Open",),
        "review_pulse": ("99_System/Bases/Sources.base#Reading queue", "99_System/Bases/Journal.base#Open Reviews"),
        "compass": ("99_System/Bases/Compass.base#Signals", "99_System/Bases/Compass.base#Tensions"),
    }
    action_states: dict[str, str] = {}
    for name, tokens in expected_tokens.items():
        action_states[name] = "pass" if all(token in home for token in tokens) else "unknown"
        if home_text is not None and action_states[name] != "pass":
            errors.append(_error("P08_HOME_ACTION_UNRESOLVED", _relative(root, home_path), f"Home action inventory is missing a reviewed token for {name}"))

    capture_contract = dashboard.get("capture_contract", {}) if isinstance(dashboard, dict) else {}
    capture_actions = capture_contract.get("actions", []) if isinstance(capture_contract, dict) else []
    capture_hotkeys = capture_contract.get("hotkeys", []) if isinstance(capture_contract, dict) else []
    expected_hotkeys = ["option_command_c", "option_command_j", "option_command_p", "option_command_q", "option_command_k"]
    quick_capture_state = (
        "hidden_by_contract"
        if isinstance(capture_contract, dict)
        and capture_contract.get("visible_on_home") is False
        and capture_actions == ["CAPTURE_THOUGHT", "NEW_IDEA", "NEW_PROJECT", "NEW_QUESTION", "NEW_KNOWLEDGE"]
        and capture_hotkeys == expected_hotkeys
        else "unknown"
    )
    if home_text is not None and quick_capture_state != "hidden_by_contract":
        errors.append(_error("P08_QUICK_CAPTURE_CONTRACT_UNRESOLVED", _relative(root, home_path) + "#/capture_contract", "Home capture must remain a profile-owned, hidden contract"))

    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", home)
    external_links = [link for link in links if "://" in link]
    inline_urls = re.findall(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9+.-]*://\S+)", home)
    external_links.extend(url for url in inline_urls if url not in external_links)
    forbidden_links = [link for link in external_links if any(marker in link.lower() for marker in P08_FORBIDDEN_ACTION_MARKERS) or link != P08_ALLOWED_DAILY_URI]
    if home_text is not None and forbidden_links:
        errors.append(_error("P08_FORBIDDEN_HOME_ACTION", _relative(root, home_path), "Home contains an executable or external action link outside the mobile fallback"))
    forbidden_home_markers = (
        "QuickAdd",
        "CAPTURE_THOUGHT",
        "⌥⌘C",
        "Obsidian Git",
        "vaultctl",
        "Next Actions",
        "ko-home-strip",
        "ko-home-footer",
        "Today Focus",
        "Due Areas",
        "ko-home-connections",
        "ko-home-attention",
        "Research Questions",
        "기한이 지난·오늘 Task",
        "전체",
    )
    visible_capture_markers = [marker for marker in forbidden_home_markers if marker in home]
    if home_text is not None and visible_capture_markers:
        errors.append(_error("P08_HOME_SURFACE_OVERLOADED", _relative(root, home_path), "Home must not expose plugin identity, capture hotkeys, support commands, or duplicate next-action surfaces"))

    mobile_fallback_state = "pass" if all(token in mobile for token in ("# Mobile", "snapshot", "shortcuts://run-shortcut", "obsidian://daily?vault=KnowledgeHub")) else "unknown"
    if mobile_text is not None and mobile_fallback_state != "pass":
        errors.append(_error("P08_MOBILE_FALLBACK_UNRESOLVED", _relative(root, mobile_path), "Mobile.md plugin-free fallback is incomplete"))

    return {
        "home_path": _relative(root, home_path),
        "mobile_path": _relative(root, mobile_path),
        "home_file": "pass" if home_text is not None else "unknown",
        "mobile_file": "pass" if mobile_text is not None else "unknown",
        "home_actions": action_states,
        "quick_capture": {
            "state": quick_capture_state,
            "actions": list(capture_actions),
            "hotkeys": list(capture_hotkeys),
            "launcher": None,
            "visible_on_home": capture_contract.get("visible_on_home") if isinstance(capture_contract, dict) else None,
        },
        "external_links": external_links,
        "forbidden_links": forbidden_links,
        "mobile_fallback": {"state": mobile_fallback_state, "community_plugin_required": False},
        "source": "Home.md; Mobile.md; blueprint/blueprint.yaml#/dashboards",
    }


def build_homepage_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P08 registry from the installed Homepage profile."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/homepage/manifest.json"
    data_path = profile_root / "plugins/homepage/data.json"
    home_path = root / "KnowledgeHub/Home.md"
    mobile_path = root / "KnowledgeHub/Mobile.md"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    home_text, home_error = _read_text(home_path)
    mobile_text, mobile_error = _read_text(mobile_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P08_HOMEPAGE_DATA_INVALID", _relative(root, data_path), data_error))
    if home_error not in (None, "missing"):
        errors.append(_error("P08_HOME_INVALID", _relative(root, home_path), home_error))
    if mobile_error not in (None, "missing"):
        errors.append(_error("P08_MOBILE_INVALID", _relative(root, mobile_path), mobile_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Homepage manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P08_HOMEPAGE_DATA_ROOT_INVALID", _relative(root, data_path), "Homepage data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P08_PLUGIN_ID):
        errors.append(_error("P08_HOMEPAGE_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Homepage manifest id does not match the installed plugin id"))

    serialized = data if isinstance(data, dict) else {}
    targets, startup_targets = _homepage_targets(serialized.get("homepages"), data_path, root, errors)
    startup = startup_targets[0] if len(startup_targets) == 1 else None
    if startup is not None:
        if startup.get("value") != "Home" or startup.get("kind") != "File":
            errors.append(_error("P08_STARTUP_TARGET_NOT_HOME", _relative(root, data_path) + "#/homepages", "the approved startup target must be the Home file"))
        if startup.get("open_mode") not in (None, "Replace last note") or startup.get("manual_open_mode") not in (None, "Replace last note"):
            errors.append(_error("P08_OPEN_MODE_NOT_ACCEPTED", _relative(root, data_path) + "#/homepages", "Homepage must use replace-last-note behavior"))
        if startup.get("view") not in (None, "Reading view"):
            errors.append(_error("P08_VIEW_MODE_NOT_ACCEPTED", _relative(root, data_path) + "#/homepages", "Homepage must use Reading view"))
        if startup.get("auto_create") is True:
            errors.append(_error("P08_AUTO_CREATE_ENABLED", _relative(root, data_path) + "#/homepages", "Homepage auto-create must remain disabled"))
        if startup.get("refresh_dataview") is True:
            errors.append(_error("P08_DATAVIEW_REFRESH_ENABLED", _relative(root, data_path) + "#/homepages", "Homepage must not refresh Dataview automatically"))
        if startup.get("commands") not in (None, []):
            errors.append(_error("P08_STARTUP_COMMANDS_NOT_EMPTY", _relative(root, data_path) + "#/homepages", "Homepage startup commands must remain empty unless individually reviewed"))

    blueprint_contract = _home_blueprint_contract(blueprint, errors)
    action_inventory = _home_action_inventory(
        root=root,
        home_path=home_path,
        mobile_path=mobile_path,
        home_text=home_text,
        mobile_text=mobile_text,
        blueprint=blueprint,
        errors=errors,
    )
    mobile_plugins = blueprint.get("plugin_profiles", {}).get("mobile_baseline", {}).get("community_plugins", []) if isinstance(blueprint.get("plugin_profiles"), dict) else None
    mobile_profile = {
        "homepage_serialized_setting": _observed(serialized.get("separateMobile")),
        "inferred_from_mac": False,
        "community_plugins": list(mobile_plugins) if isinstance(mobile_plugins, list) else None,
        "fallback": "KnowledgeHub/Mobile.md",
        "state": "pass" if mobile_plugins == [] and action_inventory["mobile_fallback"]["state"] == "pass" else "unknown",
        "policy": "do not infer a mobile Homepage profile from Mac data",
    }
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
        "homepage_targets": targets,
        "approved_startup": startup,
        "startup_policy": {
            "one_target": len(startup_targets) == 1,
            "target": "Home.md",
            "open_mode": "Replace last note",
            "view": "Reading view",
            "open_when_empty": startup.get("open_when_empty") if startup else None,
            "auto_create": startup.get("auto_create") if startup else None,
            "refresh_dataview": startup.get("refresh_dataview") if startup else None,
            "commands": startup.get("commands") if startup else None,
            "auto_run": "forbidden_for_unreviewed_commands",
        },
        "blueprint_contract": blueprint_contract,
        "home_action_inventory": action_inventory,
        "mobile_policy": mobile_profile,
        "fallback": {
            "canonical_home": "KnowledgeHub/Home.md",
            "canonical_mobile": "KnowledgeHub/Mobile.md",
            "plugin_free": True,
            "dataview_dependency": "not_required",
            "command_palette_or_file_open": "explicit_human_recovery_only",
        },
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if not errors else "blocked",
            "runtime": "not_run",
            "device": "not_run",
            "startup_execution": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "mobile_profile_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P08_HOMEPAGE_REGISTRY_SCHEMA_VERSION",
    "P08_PLUGIN_ID",
    "build_homepage_setting_registry",
]

"""Read-only F03/F04 checks for serialized GUI contracts.

This module inspects the checked-in Mac profile only.  It never edits
Obsidian settings, opens the app, executes a toolbar item, or treats JSON as
device/runtime evidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

EXPECTED_DAILY = {
    "folder": "10_Journal/Daily",
    "template": "99_System/Templates/T10_Daily.md",
}
EXPECTED_WEEKLY_PATTERN = "[Weekly]/GGGG/GGGG-[W]WW"
EXPECTED_MONTHLY_PATTERN = "[Monthly]/YYYY/YYYY-MM"
EXPECTED_WEEKLY_TEMPLATE = "99_System/Templates/T11_Weekly.md"
EXPECTED_MONTHLY_TEMPLATE = "99_System/Templates/T12_Monthly.md"
ALLOWED_COMMAND_IDS = frozenset(
    {
        "backlink:open-backlinks",
        "breadcrumbs:open-tree-view",
        "daily-notes",
        "homepage:open-homepage",
        "outgoing-links:open-for-current",
    }
)
_FORBIDDEN_LINK_MARKERS = (
    "obsidian:",
    "vaultctl",
    "shell",
    "script",
    "eval",
    "http://",
    "https://",
)
_SAFE_VAULT_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\r\n\x00]+$")
_EXPECTED_WEEKLY_FILE = re.compile(
    r"^10_Journal/Weekly/(?P<year>\d{4})/(?P=year)-W(?:0[1-9]|[1-4]\d|5[0-3])\.md$"
)
_EXPECTED_MONTHLY_FILE = re.compile(
    r"^10_Journal/Monthly/(?P<year>\d{4})/(?P=year)-(?:0[1-9]|1[0-2])\.md$"
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


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _periodic_contract(profile_root: Path, control: Path) -> dict[str, Any]:
    path = profile_root / "plugins/notebook-navigator/data.json"
    document, error = _read_json(path)
    if error:
        return {
            "state": "not_configured" if error == "missing" else "invalid",
            "source": _relative(path, control),
            "error": None if error == "missing" else error,
        }
    if not isinstance(document, dict):
        return {"state": "invalid", "source": _relative(path, control), "error": "JSON root must be an object"}

    profiles = document.get("vaultProfiles")
    active_profile = profiles[-1] if isinstance(profiles, list) and profiles else {}
    observed = {
        "periodic_notes_folder": active_profile.get("periodicNotesFolder")
        if isinstance(active_profile, dict)
        else None,
        "weekly_pattern": document.get("calendarCustomWeekPattern"),
        "monthly_pattern": document.get("calendarCustomMonthPattern"),
        "weekly_template": document.get("calendarCustomWeekTemplate"),
        "monthly_template": document.get("calendarCustomMonthTemplate"),
    }
    expected = {
        "periodic_notes_folder": "10_Journal",
        "weekly_pattern": EXPECTED_WEEKLY_PATTERN,
        "monthly_pattern": EXPECTED_MONTHLY_PATTERN,
        "weekly_template": EXPECTED_WEEKLY_TEMPLATE,
        "monthly_template": EXPECTED_MONTHLY_TEMPLATE,
    }
    checks = {
        name: {
            "state": "pass" if observed[name] == expected[name] else "blocked",
            "expected": expected[name],
            "observed": observed[name],
        }
        for name in expected
    }
    paths_pass = all(item["state"] == "pass" for item in checks.values())
    return {
        "state": "pass" if paths_pass else "blocked",
        "source": _relative(path, control),
        "checks": checks,
        "reason": None
        if paths_pass
        else "installed Navigator patterns do not express the required year-nested weekly/monthly paths; setting mutation is not authorized in F",
        "fallback": "Core File Explorer and reviewed Markdown links remain available",
    }


def _daily_contract(profile_root: Path, control: Path) -> dict[str, Any]:
    path = profile_root / "daily-notes.json"
    document, error = _read_json(path)
    if error:
        return {
            "state": "not_configured" if error == "missing" else "invalid",
            "source": _relative(path, control),
            "error": None if error == "missing" else error,
        }
    if not isinstance(document, dict):
        return {"state": "invalid", "source": _relative(path, control), "error": "JSON root must be an object"}
    observed = {key: document.get(key) for key in EXPECTED_DAILY}
    return {
        "state": "pass" if observed == EXPECTED_DAILY else "blocked",
        "source": _relative(path, control),
        "expected": EXPECTED_DAILY,
        "observed": observed,
        "owner": "Obsidian Core Daily Notes",
    }


def _has_existing_period_file(
    file_links: set[str], pattern: re.Pattern[str], control: Path
) -> bool:
    vault_root = control / "KnowledgeHub"
    return any(
        pattern.fullmatch(link) is not None
        and (vault_root / link).is_file()
        for link in file_links
    )


def _toolbar_contract(profile_root: Path, control: Path) -> dict[str, Any]:
    path = profile_root / "plugins/note-toolbar/data.json"
    document, error = _read_json(path)
    if error:
        return {
            "state": "not_configured" if error == "missing" else "invalid",
            "source": _relative(path, control),
            "error": None if error == "missing" else error,
        }
    if not isinstance(document, dict):
        return {"state": "invalid", "source": _relative(path, control), "error": "JSON root must be an object"}

    violations: list[str] = []
    command_ids: set[str] = set()
    file_links: set[str] = set()
    toolbars = document.get("toolbars")
    if document.get("scriptingEnabled") is not False:
        violations.append("scriptingEnabled must be false")
    if not isinstance(toolbars, list):
        violations.append("toolbars must be a list")
        toolbars = []
    for toolbar in toolbars:
        if not isinstance(toolbar, dict) or not isinstance(toolbar.get("items"), list):
            violations.append("toolbar items must be lists")
            continue
        for item in toolbar["items"]:
            if not isinstance(item, dict):
                violations.append("toolbar item must be an object")
                continue
            link = item.get("link")
            attributes = item.get("linkAttr")
            if not isinstance(link, str) or not isinstance(attributes, dict):
                violations.append("toolbar item must declare a link and linkAttr")
                continue
            if any(marker in link.lower() for marker in _FORBIDDEN_LINK_MARKERS):
                violations.append(f"forbidden toolbar capability in link {link!r}")
            kind = attributes.get("type")
            if kind == "file":
                if not _SAFE_VAULT_PATH.fullmatch(link):
                    violations.append(f"unsafe toolbar file target {link!r}")
                else:
                    file_links.add(link)
            elif kind == "command":
                command_id = attributes.get("commandId")
                if not isinstance(command_id, str) or command_id not in ALLOWED_COMMAND_IDS:
                    violations.append(f"unverified toolbar command {command_id!r}")
                else:
                    command_ids.add(command_id)
            else:
                violations.append(f"unsupported toolbar target kind {kind!r}")

    actions = {
        "home": "configured" if "Home.md" in file_links or "homepage:open-homepage" in command_ids else "not_configured",
        "daily": "configured" if "daily-notes" in command_ids else "not_configured",
        "weekly": "configured"
        if _has_existing_period_file(file_links, _EXPECTED_WEEKLY_FILE, control)
        else "not_configured",
        "monthly": "configured"
        if _has_existing_period_file(file_links, _EXPECTED_MONTHLY_FILE, control)
        else "not_configured",
        "weekly_review": "configured" if "99_System/Dashboards/Weekly_Review.md" in file_links else "not_configured",
    }
    return {
        "state": "pass" if not violations else "blocked",
        "source": _relative(path, control),
        "scripting_enabled": document.get("scriptingEnabled"),
        "command_ids": sorted(command_ids),
        "file_links": sorted(file_links),
        "actions": actions,
        "violations": violations,
        "fallback": "plugin-free Markdown links and File Explorer remain available",
        "device_evidence": "not_run",
    }


def inspect_gui_contract(root: str | Path, profile: str = "mac") -> dict[str, Any]:
    """Inspect F03/F04 serialized settings without operating Obsidian."""

    control = Path(root).expanduser().resolve()
    if profile != "mac":
        return {"state": "not_applicable", "profile": profile, "evidence_class": "static"}
    profile_root = control / "KnowledgeHub/.obsidian-mac"
    if profile_root.is_symlink() or not profile_root.is_dir():
        return {
            "state": "not_configured",
            "profile": profile,
            "evidence_class": "static",
            "source": "KnowledgeHub/.obsidian-mac",
        }
    daily = _daily_contract(profile_root, control)
    periodic = _periodic_contract(profile_root, control)
    toolbar = _toolbar_contract(profile_root, control)
    return {
        "state": "pass"
        if daily["state"] == "pass" and periodic["state"] == "pass" and toolbar["state"] == "pass"
        else "blocked",
        "profile": profile,
        "evidence_class": "static",
        "daily": daily,
        "periodic": periodic,
        "toolbar": toolbar,
        "runtime_or_device_evidence": "not_run",
    }

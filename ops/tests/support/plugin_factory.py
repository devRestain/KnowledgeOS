"""Build small, disposable Obsidian profiles for plugin contract tests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from support.control_factory import fixture_path
from vaultops.yaml_safe import load_yaml_file

from .control_factory import PLUGIN_CONTROL_INPUTS, make_control_root

CONTROL_ROOT = Path(__file__).resolve().parents[3]

_REQUIRED_CORE_FLAG_IDS = frozenset(
    {
        "properties",
        "bases",
        "daily-notes",
        "templates",
        "global-search",
        "backlink",
        "outgoing-link",
        "bookmarks",
        "file-recovery",
        "workspaces",
    }
)
_REQUIRED_TASK_STATUS_NAMES = ("Todo", "Done", "In Progress", "Cancelled")
_P08_OWNED_STARTUP_NAME = "KnowledgeOS Home"
_P09_CONTEXT_FIELDS = ("projects", "sources", "related")
_P10_PROFILE_NAME = "KnowledgeOS Mac"
_P10_PERIODIC_FOLDER = "10_Journal"
_P10_DAILY_PATTERN = "[Daily]/YYYY-MM-DD"
_P10_WEEKLY_PATTERN = "[Weekly]/GGGG/GGGG-[W]WW"
_P10_MONTHLY_PATTERN = "[Monthly]/YYYY/YYYY-MM"
_P10_DAILY_TEMPLATE = "99_System/Templates/T10_Daily.md"
_P10_WEEKLY_TEMPLATE = "99_System/Templates/T11_Weekly.md"
_P10_MONTHLY_TEMPLATE = "99_System/Templates/T12_Monthly.md"
_P11_REQUIRED_TOOLBAR_TARGETS: dict[str, set[tuple[str, str]]] = {
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
_P12_TEST_INPUTS = (
    ("상태", "status", "inlineSelect", ("planned", "active", "blocked", "done", "cancelled")),
    ("우선순위", "priority", "inlineSelect", ("high", "medium", "low")),
    ("다음 행동", "next_action", "text", None),
    ("오늘의 방향", "today_focus", "text", None),
)


def declared_community_plugins(root: Path) -> list[str]:
    """Return Blueprint-declared Mac plugin IDs from the provided control root."""

    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    plugins = blueprint.get("plugin_profiles", {}).get("mac_baseline", [])
    return [item["id"] for item in plugins if isinstance(item, dict) and isinstance(item.get("id"), str)]


def required_core_flags() -> dict[str, bool]:
    """Synthesize only required Core flags, without snapshotting a profile."""

    return {plugin_id: True for plugin_id in _REQUIRED_CORE_FLAG_IDS}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _merge_settings(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, Mapping):
        return override
    merged = dict(base)
    for key, value in override.items():
        merged[key] = _merge_settings(merged[key], value) if key in merged else value
    return merged


def _minimal_plugin_settings(
    root: Path, *, plugin_ids: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    selected = plugin_ids if plugin_ids is not None else set(declared_community_plugins(root))
    home = blueprint.get("dashboards", {}).get("home", {})
    capture_contract = home.get("capture_contract", {}) if isinstance(home, dict) else {}
    quickadd_choices = list(capture_contract.get("actions", []))
    quickadd_templates = {
        item.get("template")
        for item in blueprint.get("quickadd_choices", {}).values()
        if isinstance(item, dict) and isinstance(item.get("template"), str)
    }
    quickadd_templates.update({"T11_Weekly.md", "T12_Monthly.md", "T20_Project.md"})
    template_names: set[str] = set()
    if "quickadd" in selected:
        template_names.update(quickadd_templates)
        template_names.update({"T11_Weekly.md", "T12_Monthly.md", "T20_Project.md"})
    if "templater-obsidian" in selected:
        required_templates = blueprint.get("templates", {}).get("required", [])
        template_names.update(name for name in required_templates if isinstance(name, str))
    if "obsidian-meta-bind-plugin" in selected:
        template_names.add("T20_Project.md")
    for name in template_names:
        path = (fixture_path(root, "vault") / "99_System/Templates") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        contents = "# Temporary owned template fixture\n"
        if name == "T10_Daily.md":
            contents = "# Daily fixture\n\n{{date:YYYY-MM-DD}}\n"
        elif name in {"T11_Weekly.md", "T12_Monthly.md"}:
            contents += '<% tp.date.now("YYYY") %>\n'
        path.write_text(contents, encoding="utf-8")

    if selected.intersection({"breadcrumbs", "obsidian-meta-bind-plugin"}):
        expected_dictionary = root / "ops/expected/Property_Dictionary.md"
        dictionary = (fixture_path(root, "vault") / "99_System/Schemas/Property_Dictionary.md")
        dictionary.parent.mkdir(parents=True, exist_ok=True)
        dictionary.write_bytes(expected_dictionary.read_bytes())

    if "obsidian-tasks-plugin" in selected:
        task_query = (
            "> [!tip] Core-only fallback: inspect the source Markdown task line\n\n"
            "```tasks\ntags include #task\nnot done\n```\n"
        )
        for relative in ("99_System/Dashboards/Tasks.md", "99_System/Dashboards/Weekly_Review.md"):
            path = fixture_path(root, "vault") / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Temporary Tasks fixture\n\n" + task_query, encoding="utf-8")

    status_names = list(_REQUIRED_TASK_STATUS_NAMES)
    toolbar_items: list[dict[str, Any]]
    toolbars: list[dict[str, Any]] = []
    for name, targets in _P11_REQUIRED_TOOLBAR_TARGETS.items():
        toolbar_items = []
        for kind, target in sorted(targets):
            link = target
            command_id = target if kind == "command" else ""
            toolbar_items.append(
                {
                    "link": link,
                    "linkAttr": {
                        "type": "command" if kind == "command" else "file",
                        "commandId": command_id,
                        "commandCheck": False,
                        "hasVars": False,
                    },
                    "hasCommand": False,
                }
            )
        toolbars.append({"name": name, "items": toolbar_items})

    meta_bind_templates = []
    for name, field, control, allowed in _P12_TEST_INPUTS:
        declaration = (
            f"INPUT[inlineSelect({', '.join(f'option({value})' for value in allowed)}):{field}]"
            if control == "inlineSelect" and allowed is not None
            else f"INPUT[text:{field}]"
        )
        meta_bind_templates.append({"name": name, "declaration": declaration})

    relation_registry = blueprint.get("relation_registry", {})
    semantic_fields = list(relation_registry.get("canonical_predicates", {}))
    context_fields = list(_P09_CONTEXT_FIELDS)
    relation_fields = [*context_fields, *semantic_fields]
    return {
        "quickadd": {
            "choices": quickadd_choices,
            "disableOnlineFeatures": True,
            "devMode": False,
        },
        "templater-obsidian": {
            "trigger_on_file_creation": False,
            "enable_system_commands": False,
            "shell_path": "",
            "user_scripts_folder": "",
            "folder_templates": [{"folder": "", "template": ""}],
            "file_templates": [{"regex": ".*", "template": ""}],
            "startup_templates": [""],
            "templates_folder": "99_System/Templates",
        },
        "obsidian-tasks-plugin": {
            "globalFilter": "#task",
            "removeGlobalFilter": False,
            "statusSettings": {
                "coreStatuses": [{"name": name} for name in status_names[:2]],
                "customStatuses": [{"name": name} for name in status_names[2:]],
            },
        },
        "obsidian-linter": {
            "lintOnSave": False,
            "lintOnFileChange": False,
            "lintCommands": [],
            "customRegexes": [],
            "foldersToIgnore": ["99_System"],
            "ruleConfigs": {},
        },
        "obsidian-git": {
            "autoSaveInterval": 0,
            "autoPushInterval": 0,
            "autoPullInterval": 0,
            "autoPullOnBoot": False,
            "autoBackupAfterFileChange": False,
            "pullBeforePush": False,
            "differentIntervalCommitAndPush": False,
            "updateSubmodules": False,
            "submoduleRecurseCheckout": False,
            "commitMessageScript": "",
        },
        "homepage": {
            "homepages": {
                _P08_OWNED_STARTUP_NAME: {
                    "value": "Home",
                    "kind": "File",
                    "openOnStartup": True,
                    "autoCreate": False,
                    "refreshDataview": False,
                    "commands": [],
                }
            }
        },
        "breadcrumbs": {
            "edge_fields": [{"label": field} for field in relation_fields],
            "edge_field_groups": [
                {"label": "Context relation group", "fields": context_fields},
                {"label": "Semantic relation group", "fields": semantic_fields},
            ],
            "explicit_edge_sources": {},
            "implied_relations": {},
            "views": {
                "page": {"trail": {"field_group_labels": ["Context relation group"]}},
                "side": {
                    "matrix": {
                        "field_group_labels": [
                            "Context relation group",
                            "Semantic relation group",
                            "sames",
                            "nexts",
                            "prevs",
                        ]
                    },
                    "tree": {"field_group_labels": ["ups", "downs"]},
                },
            },
            "commands": {},
            "suggestors": {"edge_field": {"enabled": False}},
        },
        "notebook-navigator": {
            "vaultProfiles": [
                {"name": _P10_PROFILE_NAME, "periodicNotesFolder": _P10_PERIODIC_FOLDER}
            ],
            "calendarCustomFilePattern": _P10_DAILY_PATTERN,
            "calendarCustomWeekPattern": _P10_WEEKLY_PATTERN,
            "calendarCustomMonthPattern": _P10_MONTHLY_PATTERN,
            "calendarCustomFileTemplate": _P10_DAILY_TEMPLATE,
            "calendarCustomWeekTemplate": _P10_WEEKLY_TEMPLATE,
            "calendarCustomMonthTemplate": _P10_MONTHLY_TEMPLATE,
            "calendarEnabled": True,
            "calendarIntegrationMode": "notebook-navigator",
            "templateEngine": "automatic",
            "calendarConfirmBeforeCreate": True,
            "confirmBeforeDelete": True,
            "deleteAttachments": "ask",
            "moveFileConflicts": "ask",
            "confirmBeforeManualSort": True,
        },
        "note-toolbar": {
            "scriptingEnabled": False,
            "rules": [],
            "toolbars": toolbars,
            "folderMappings": [],
        },
        "obsidian-meta-bind-plugin": {
            "devMode": False,
            "ignoreCodeBlockRestrictions": False,
            "enableJs": False,
            "buttonTemplates": [],
            "inputFieldTemplates": meta_bind_templates,
            "viewFieldTemplates": [],
        },
    }


def make_plugin_profile(
    tmp_path: Path,
    *,
    plugins: Sequence[str] | None = None,
    plugin_ids_to_write: Sequence[str] | None = None,
    core_flags: Mapping[str, Any] | None = None,
    plugin_data: Mapping[str, Mapping[str, Any]] | None = None,
    plugin_versions: Mapping[str, Any] | None = None,
    unmanaged_files: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    """Create a temporary control root and minimal Mac profile.

    Only declared control inputs are copied. Profile files are synthesized from
    the arguments; no installed profile, Vault document, or plugin data is read.
    """

    inputs = tuple(item for item in PLUGIN_CONTROL_INPUTS if item != "ops/vaultops.toml")
    root = make_control_root(tmp_path, inputs, with_vault=True, with_runtime=True)
    profile = fixture_path(root, "vault") / ".obsidian-mac"
    profile.mkdir(parents=True)
    ids = list(plugins) if plugins is not None else declared_community_plugins(root)
    write_json(profile / "community-plugins.json", ids)
    write_json(profile / "core-plugins.json", dict(core_flags or required_core_flags()))
    write_json(
        profile / "daily-notes.json",
        {
            "folder": "10_Journal/Daily",
            "template": "99_System/Templates/T10_Daily.md",
        },
    )
    write_json(profile / "app.json", {"propertiesInDocument": "visible"})
    template = fixture_path(root, "vault") / "99_System" / "Templates" / "T10_Daily.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("# Daily\n\n{{date:YYYY-MM-DD}}\n", encoding="utf-8")

    versions = dict(plugin_versions or {})
    all_plugin_data = _minimal_plugin_settings(
        root,
        plugin_ids=None if plugin_ids_to_write is None else set(plugin_ids_to_write),
    )
    for plugin_id, override in (plugin_data or {}).items():
        base = all_plugin_data.get(plugin_id, {})
        all_plugin_data[plugin_id] = _merge_settings(base, override)
    if plugin_ids_to_write is not None:
        unknown_ids = set(plugin_ids_to_write) - set(all_plugin_data)
        if unknown_ids:
            raise ValueError(f"unknown synthesized plugin IDs: {sorted(unknown_ids)}")
        all_plugin_data = {
            plugin_id: data
            for plugin_id, data in all_plugin_data.items()
            if plugin_id in plugin_ids_to_write
        }

    for plugin_id, data in all_plugin_data.items():
        write_json(
            profile / "plugins" / plugin_id / "manifest.json",
            {"id": plugin_id, "version": versions.get(plugin_id, "fixture-version")},
        )
        write_json(profile / "plugins" / plugin_id / "data.json", dict(data))

    for relative, contents in (unmanaged_files or {}).items():
        path = Path(relative)
        if len(path.parts) < 3 or path.parts[0] != "plugins" or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unmanaged fixture must be below plugins/<id>: {relative}")
        target = profile.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    return root, profile

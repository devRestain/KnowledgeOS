from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support.plugin_factory import make_plugin_profile, write_json

from vaultops.breadcrumbs_settings import build_breadcrumbs_setting_registry
from vaultops.homepage_settings import build_homepage_setting_registry
from vaultops.linter_settings import build_linter_setting_registry
from vaultops.meta_bind_settings import build_meta_bind_setting_registry
from vaultops.note_toolbar_settings import build_note_toolbar_setting_registry
from vaultops.notebook_navigator_settings import (
    build_notebook_navigator_setting_registry,
)
from vaultops.obsidian_git_settings import build_obsidian_git_setting_registry
from vaultops.quickadd_settings import build_quickadd_setting_registry
from vaultops.tasks_settings import build_tasks_setting_registry
from vaultops.templater_settings import build_templater_setting_registry
from vaultops.yaml_safe import load_yaml_file

Builder = Callable[..., tuple[dict[str, Any], list[dict[str, str]]]]
_P08_OWNED_STARTUP_NAME = "KnowledgeOS Home"
_P10_PROFILE_NAME = "KnowledgeOS Mac"


def _lane_profile(
    tmp_path: Path, plugin_id: str, settings: dict[str, Any] | None = None
) -> tuple[Path, Path]:
    return make_plugin_profile(
        tmp_path,
        plugins=(plugin_id,),
        plugin_ids_to_write=(plugin_id,),
        plugin_data={plugin_id: settings or {}},
    )


_LANES: tuple[tuple[str, str, Builder], ...] = (
    ("P03", "quickadd", build_quickadd_setting_registry),
    ("P04", "templater-obsidian", build_templater_setting_registry),
    ("P05", "obsidian-tasks-plugin", build_tasks_setting_registry),
    ("P06", "obsidian-linter", build_linter_setting_registry),
    ("P07", "obsidian-git", build_obsidian_git_setting_registry),
    ("P08", "homepage", build_homepage_setting_registry),
    ("P09", "breadcrumbs", build_breadcrumbs_setting_registry),
    ("P10", "notebook-navigator", build_notebook_navigator_setting_registry),
    ("P11", "note-toolbar", build_note_toolbar_setting_registry),
    ("P12", "obsidian-meta-bind-plugin", build_meta_bind_setting_registry),
)

_SAFETY_CASES: tuple[tuple[str, str, Builder, dict[str, Any], tuple[str, ...]], ...] = (
    (
        "P03",
        "quickadd",
        build_quickadd_setting_registry,
        {"disableOnlineFeatures": False, "devMode": True},
        ("P03_ONLINE_FEATURES_NOT_DISABLED", "P03_DEV_MODE_ENABLED"),
    ),
    (
        "P04",
        "templater-obsidian",
        build_templater_setting_registry,
        {
            "trigger_on_file_creation": True,
            "enable_system_commands": True,
            "shell_path": "/bin/sh",
            "user_scripts_folder": "UserScripts",
            "folder_templates": [{"folder": "99_System/Templates", "template": "T20_Project.md"}],
            "file_templates": [{"regex": ".*", "template": "T20_Project.md"}],
            "startup_templates": ["T11_Weekly.md"],
        },
        (
            "P04_GLOBAL_NEW_FILE_TRIGGER_ENABLED",
            "P04_SYSTEM_COMMANDS_ENABLED",
            "P04_SHELL_PATH_CONFIGURED",
            "P04_USER_SCRIPTS_CONFIGURED",
            "P04_PROTECTED_FOLDER_MAPPING_CONFIGURED",
            "P04_UNSCOPED_FILE_MAPPING_CONFIGURED",
            "P04_STARTUP_TEMPLATE_CONFIGURED",
        ),
    ),
    (
        "P05",
        "obsidian-tasks-plugin",
        build_tasks_setting_registry,
        {"globalQuery": "filter by function task.file.folder === query.file.folder"},
        ("P05_JAVASCRIPT_QUERY_ACTIVE",),
    ),
    (
        "P06",
        "obsidian-linter",
        build_linter_setting_registry,
        {
            "ruleConfigs": {"yaml-title": {"enabled": True}},
            "lintOnSave": True,
            "lintOnFileChange": True,
            "lintCommands": ["unreviewed"],
            "customRegexes": [{"name": "unsafe"}],
            "foldersToIgnore": [],
        },
        (
            "P06_AUTOMATIC_TRIGGER_ENABLED",
            "P06_UNREVIEWED_LINT_COMMAND",
            "P06_UNREVIEWED_CUSTOM_REGEX",
            "P06_PROTECTED_FOLDER_SCOPE_DRIFT",
        ),
    ),
    (
        "P07",
        "obsidian-git",
        build_obsidian_git_setting_registry,
        {
            "autoSaveInterval": 1,
            "autoPullOnBoot": True,
            "updateSubmodules": True,
            "commitMessageScript": "unsafe",
        },
        (
            "P07_AUTOMATIC_INTERVAL_ENABLED",
            "P07_UNATTENDED_BOOLEAN_ENABLED",
            "P07_COMMIT_SCRIPT_NOT_ACCEPTED",
        ),
    ),
    (
        "P08",
        "homepage",
        build_homepage_setting_registry,
        {
            "homepages": {
                _P08_OWNED_STARTUP_NAME: {
                    "name": _P08_OWNED_STARTUP_NAME,
                    "value": "Other",
                    "kind": "File",
                    "openOnStartup": True,
                    "autoCreate": True,
                    "refreshDataview": True,
                    "commands": ["unreviewed-command"],
                }
            }
        },
        (
            "P08_STARTUP_TARGET_NOT_HOME",
            "P08_AUTO_CREATE_ENABLED",
            "P08_DATAVIEW_REFRESH_ENABLED",
            "P08_STARTUP_COMMANDS_NOT_EMPTY",
        ),
    ),
    (
        "P09",
        "breadcrumbs",
        build_breadcrumbs_setting_registry,
        {
            "edge_fields": [{"label": "unapproved"}],
            "edge_field_groups": [],
            "implied_relations": {
                "transitive": [{"name": "unsafe", "rounds": 1}],
                "inverse": [{"name": "unsafe"}],
            },
            "explicit_edge_sources": {"dataview_note": {"default_field": "unsafe"}},
            "suggestors": {"edge_field": {"enabled": True}},
            "commands": {
                "rebuild_graph": {"trigger": {"note_save": True}},
                "write_frontmatter": True,
            },
        },
        (
            "P09_EDGE_FIELD_REGISTRY_DRIFT",
            "P09_EDGE_FIELD_GROUP_DRIFT",
            "P09_TRANSITIVE_RELATION_NOT_ACCEPTED",
            "P09_INVERSE_RELATION_NOT_ACCEPTED",
            "P09_EXTERNAL_EDGE_SOURCE_NOT_ACCEPTED",
            "P09_AUTOMATIC_FIELD_SUGGESTOR_ENABLED",
            "P09_AUTOMATIC_GRAPH_REBUILD",
            "P09_CANONICAL_WRITE_NOT_ACCEPTED",
        ),
    ),
    (
        "P10",
        "notebook-navigator",
        build_notebook_navigator_setting_registry,
        {
            "calendarConfirmBeforeCreate": False,
            "moveFileConflicts": "overwrite",
            "calendarTemplateFolder": "../outside",
            "folderTemplates": {"10_Journal/Weekly": "T11_Weekly.md"},
            "vaultProfiles": [
                {"name": _P10_PROFILE_NAME},
                {"name": _P10_PROFILE_NAME},
            ],
        },
        (
            "P10_DUPLICATE_OWNED_PROFILE",
            "P10_CONFIRMATION_POLICY_DRIFT",
            "P10_PATH_ESCAPE",
            "P10_PERIOD_TEMPLATE_OWNER_CONFLICT",
        ),
    ),
    (
        "P11",
        "note-toolbar",
        build_note_toolbar_setting_registry,
        {
            "scriptingEnabled": True,
            "toolbars": [
                {
                    "name": "KnowledgeOS Home",
                    "items": [
                        {
                            "label": "Unsafe",
                            "link": "javascript:unsafe()",
                            "hasCommand": True,
                            "linkAttr": {
                                "type": "command",
                                "commandId": "quickadd:unsafe",
                                "commandCheck": True,
                                "hasVars": True,
                            },
                        }
                    ],
                }
            ],
        },
        (
            "P11_SCRIPTING_NOT_DISABLED",
            "P11_ITEM_VARIABLES_ENABLED",
            "P11_ITEM_COMMAND_METADATA_UNRESOLVED",
            "P11_FORBIDDEN_LINK_TARGET",
        ),
    ),
    (
        "P12",
        "obsidian-meta-bind-plugin",
        build_meta_bind_setting_registry,
        {
            "devMode": True,
            "enableJs": True,
            "ignoreCodeBlockRestrictions": True,
            "buttonTemplates": [{"name": "unsafe"}],
            "inputFieldTemplates": [{"name": "상태", "declaration": "INPUT[text:ai_status]"}],
        },
        ("P12_FORBIDDEN_CAPABILITY_ENABLED", "P12_UNAPPROVED_INPUT_FIELD"),
    ),
)


@pytest.mark.parametrize(("lane", "plugin_id", "builder"), _LANES)
def test_plugin_builders_accept_minimal_owned_subsets(
    tmp_path: Path,
    lane: str,
    plugin_id: str,
    builder: Builder,
) -> None:
    root, profile = _lane_profile(tmp_path, plugin_id)
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")

    registry, errors = builder(root=root, profile_root=profile, blueprint=blueprint)

    assert errors == [], (lane, errors)
    assert registry["plugin"] == plugin_id
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"
    if lane == "P10":
        assert registry["active_profile"]["name"] == _P10_PROFILE_NAME
        assert registry["period_mapping"]["state"] == "pass"
    if lane == "P11":
        assert registry["toolbars"]["state"] == "pass"


def _apply_open_world_extension(profile: Path, lane: str, plugin_id: str) -> None:
    data_path = profile / "plugins" / plugin_id / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    if lane == "P03":
        data["choices"] = list(reversed(data["choices"]))
        data["choices"].append({"name": "USER_OWNED_CHOICE", "type": "macro"})
        data["hotkeys"] = {"USER_OWNED_CHOICE": "Alt+Shift+U"}
    elif lane == "P04":
        data["folder_templates"].append(
            {"folder": "20_Projects", "template": "T20_Project.md"}
        )
    elif lane == "P05":
        data["statusSettings"]["customStatuses"].append({"name": "User Review"})
        data["dateFormat"] = "YYYY-MM-DD"
    elif lane == "P06":
        data["ruleConfigs"]["heading-style"] = {"enabled": True, "style": "ATX"}
    elif lane == "P07":
        data["showStatusBar"] = False
        data["showFileMenu"] = False
    elif lane == "P08":
        data["homepages"]["User Home"] = {
            "value": "20_Projects/Home",
            "kind": "File",
            "openOnStartup": False,
            "autoCreate": False,
            "refreshDataview": False,
            "commands": [],
        }
    elif lane == "P09":
        data["edge_fields"].append({"label": "user_relation"})
        data["edge_field_groups"].append(
            {"label": "User relation group", "fields": ["user_relation"]}
        )
        data["views"]["user_view"] = {"enabled": True}
    elif lane == "P10":
        data["vaultProfiles"].insert(0, {"name": "User profile", "periodicNotesFolder": "20_Projects"})
        data["vaultProfiles"][-1]["hiddenFolders"] = ["20_Projects/Archive"]
        data["fileVisibility"] = "visible"
    elif lane == "P11":
        data["toolbars"].append(
            {
                "name": "User Toolbar",
                "position": "mobile-bottom",
                "items": [
                    {
                        "link": "20_Projects/User.md",
                        "linkAttr": {
                            "type": "file",
                            "commandId": "",
                            "commandCheck": False,
                            "hasVars": False,
                        },
                        "hasCommand": False,
                    }
                ],
            }
        )
        data["folderMappings"] = [
            {"folder": "20_Projects", "toolbar": "User Toolbar"}
        ]
    elif lane == "P12":
        data["inputFieldTemplates"].append(
            {"name": "User caption", "declaration": "INPUT[text:user_caption]"}
        )
        data["viewFieldTemplates"].append({"name": "User view", "declaration": "VIEW[user_caption]"})
        data["excludedFolders"] = {"user": "serialized however the plugin chooses"}
    else:
        raise AssertionError(f"unknown plugin lane: {lane}")
    data["userOwnedExtension"] = {"display": "compact", "enabled": True}
    data_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize(("lane", "plugin_id", "builder"), _LANES)
def test_plugin_builders_ignore_unowned_extension_keys(
    tmp_path: Path,
    lane: str,
    plugin_id: str,
    builder: Builder,
) -> None:
    root, profile = _lane_profile(
        tmp_path,
        plugin_id,
        {"userOwnedExtension": {"display": "compact", "enabled": True}},
    )
    write_json(
        profile / "plugins" / plugin_id / "manifest.json",
        {"id": plugin_id, "version": "unconstrained-user-version"},
    )
    _apply_open_world_extension(profile, lane, plugin_id)
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")

    registry, errors = builder(root=root, profile_root=profile, blueprint=blueprint)

    assert errors == [], (lane, errors)
    assert registry["plugin"] == plugin_id
    assert registry["manifest"]["version"] == "unconstrained-user-version"


@pytest.mark.parametrize(("lane", "plugin_id", "builder", "unsafe_data", "expected_codes"), _SAFETY_CASES)
def test_plugin_builders_reject_lane_specific_safety_mutations(
    tmp_path: Path,
    lane: str,
    plugin_id: str,
    builder: Builder,
    unsafe_data: dict[str, Any],
    expected_codes: tuple[str, ...],
) -> None:
    root, profile = _lane_profile(tmp_path, plugin_id, unsafe_data)
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")

    _, errors = builder(root=root, profile_root=profile, blueprint=blueprint)

    observed_codes = {item["code"] for item in errors}
    assert set(expected_codes).issubset(observed_codes), (lane, expected_codes, errors)


def test_quickadd_blueprint_target_cannot_escape_the_canonical_vault(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "quickadd")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    blueprint["quickadd_choices"]["NEW_IDEA"]["target_pattern"] = "../outside.md"

    _, errors = build_quickadd_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P03_CHOICE_TARGET_INVALID" in {item["code"] for item in errors}


def test_templater_rejects_executable_code_in_an_owned_period_template(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "templater-obsidian")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    weekly = root / "KnowledgeHub/99_System/Templates/T11_Weekly.md"
    weekly.write_text('<%* tp.system("unsafe") %>\n', encoding="utf-8")

    _, errors = build_templater_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P04_FORBIDDEN_TEMPLATE_CODE" in {item["code"] for item in errors}


def test_tasks_rejects_executable_query_in_owned_99_system_dashboard(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "obsidian-tasks-plugin")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    tasks_dashboard = root / "KnowledgeHub/99_System/Dashboards/Tasks.md"
    tasks_dashboard.write_text(
        "# Tasks\n\n```tasks\nfilter by function task.file.folder === query.file.folder\n```\n",
        encoding="utf-8",
    )

    _, errors = build_tasks_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P05_JAVASCRIPT_QUERY_ACTIVE" in {item["code"] for item in errors}


def test_note_toolbar_rejects_unverified_commands_and_unscoped_write_targets(
    tmp_path: Path,
) -> None:
    root, profile = _lane_profile(tmp_path, "note-toolbar")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    data_path = profile / "plugins/note-toolbar/data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    home_toolbar = next(item for item in data["toolbars"] if item["name"] == "KnowledgeOS Home")
    home_toolbar["items"].extend(
        [
            {
                "link": "unreviewed-command",
                "linkAttr": {
                    "type": "command",
                    "commandId": "unreviewed-command",
                    "commandCheck": False,
                    "hasVars": False,
                },
                "hasCommand": False,
            },
            {
                "link": "../outside.md",
                "linkAttr": {
                    "type": "file",
                    "commandId": "",
                    "commandCheck": False,
                    "hasVars": False,
                },
                "hasCommand": False,
            },
        ]
    )
    data["folderMappings"] = [{"folder": "99_System", "toolbar": "KnowledgeOS Home"}]
    write_json(data_path, data)

    _, errors = build_note_toolbar_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    codes = {item["code"] for item in errors}
    assert {
        "P11_UNVERIFIED_COMMAND_ID",
        "P11_UNSAFE_FILE_TARGET",
        "P11_UNREVIEWED_PROTECTED_MAPPING",
    }.issubset(codes)


def test_meta_bind_rejects_controls_only_in_owned_99_system_documents(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "obsidian-meta-bind-plugin")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    template = root / "KnowledgeHub/99_System/Templates/T20_Project.md"
    template.write_text("---\nstatus: planned\n---\nINPUT[text:unsafe]\n", encoding="utf-8")

    _, errors = build_meta_bind_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P12_PROTECTED_PATH_CONTROL_EXPOSED" in {item["code"] for item in errors}


def test_meta_bind_ignores_user_owned_review_document_content(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "obsidian-meta-bind-plugin")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    review_note = root / "KnowledgeHub/01_AI_Review/Pending/User Proposal.md"
    review_note.parent.mkdir(parents=True)
    review_note.write_text("INPUT[text:user_owned_field]\n", encoding="utf-8")

    _, errors = build_meta_bind_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert errors == []


def test_tasks_require_the_owned_query_tag_and_status_subset(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "obsidian-tasks-plugin")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    data_path = profile / "plugins/obsidian-tasks-plugin/data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["statusSettings"]["coreStatuses"] = []
    data["statusSettings"]["customStatuses"] = []
    write_json(data_path, data)
    tasks = root / "KnowledgeHub/99_System/Dashboards/Tasks.md"
    tasks.write_text("# Tasks\n\n```tasks\nnot done\n```\n", encoding="utf-8")

    _, errors = build_tasks_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    codes = {item["code"] for item in errors}
    assert "P05_STATUS_MAPPING_MISSING" in codes
    assert "P05_TASK_TAG_MISSING" in codes


def test_homepage_allows_an_unconfigured_optional_entry_but_rejects_unsafe_target(
    tmp_path: Path,
) -> None:
    root, profile = _lane_profile(tmp_path, "homepage", {"homepages": {}})
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    data_path = profile / "plugins/homepage/data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["homepages"] = {}
    write_json(data_path, data)

    registry, errors = build_homepage_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert errors == []
    assert registry["owned_startup_entry"]["state"] == "not_configured"

    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["homepages"]["KnowledgeOS Home"] = {
        "value": "../outside.md",
        "kind": "File",
    }
    write_json(data_path, data)

    _, errors = build_homepage_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P08_UNSAFE_STARTUP_TARGET" in {item["code"] for item in errors}


def test_note_toolbar_requires_each_owned_reviewed_action(tmp_path: Path) -> None:
    root, profile = _lane_profile(tmp_path, "note-toolbar")
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    data_path = profile / "plugins/note-toolbar/data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    toolbar = next(item for item in data["toolbars"] if item["name"] == "KnowledgeOS Home")
    toolbar["items"] = [
        item for item in toolbar["items"] if item.get("link") != "99_System/Bases/Review.base"
    ]
    write_json(data_path, data)

    _, errors = build_note_toolbar_setting_registry(root=root, profile_root=profile, blueprint=blueprint)

    assert "P11_REQUIRED_ACTION_MISSING" in {item["code"] for item in errors}

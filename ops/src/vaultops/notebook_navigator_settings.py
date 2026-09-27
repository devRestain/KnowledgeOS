"""Read-only P10 Notebook Navigator bounded-calendar contract inspection.

P10 records the exact installed Notebook Navigator mapping for the GUI-first
period-note workflow.  Notebook Navigator remains a navigation/create surface
for weekly and monthly notes; Templater renders those newly created notes under
the already accepted F02 contract.  Obsidian Core Daily Notes remains the only
daily-note creation owner.

This module observes serialized plugin data, Blueprint paths, and template
syntax only.  It never changes plugin data, creates/moves/deletes notes, or
operates Obsidian.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

P10_NOTEBOOK_NAVIGATOR_REGISTRY_SCHEMA_VERSION = 1
P10_PLUGIN_ID = "notebook-navigator"
P10_PROFILE_NAME = "KnowledgeOS Mac"
P10_PERIODIC_FOLDER = "10_Journal"
P10_DAILY_PATTERN = "[Daily]/YYYY-MM-DD"
P10_WEEKLY_PATTERN = "[Weekly]/GGGG/GGGG-[W]WW"
P10_MONTHLY_PATTERN = "[Monthly]/YYYY/YYYY-MM"
P10_DAILY_TEMPLATE = "99_System/Templates/T10_Daily.md"
P10_WEEKLY_TEMPLATE = "99_System/Templates/T11_Weekly.md"
P10_MONTHLY_TEMPLATE = "99_System/Templates/T12_Monthly.md"
P10_FILE_VISIBILITY = "all"

_PROFILE_EMPTY_LISTS = (
    "hiddenFolders",
    "descendantExcludedFolders",
    "hiddenTags",
    "hiddenFileNames",
    "hiddenFileTags",
    "hiddenFileProperties",
    "propertyKeys",
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
    if isinstance(expected, (bool, int, float, str)) and type(value) is not type(expected):
        return {"observed": "invalid", "expected": expected, "state": "invalid", "policy": policy}
    if value == expected:
        return {"observed": value, "expected": expected, "state": "pass", "policy": policy}
    return {"observed": value, "expected": expected, "state": "drift", "policy": policy}


def _safe_vault_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    normalized = value.rstrip("/")
    if not normalized:
        return False
    path = PurePosixPath(normalized)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in normalized.split("/")
    )


def _append_setting_errors(
    *,
    settings: dict[str, dict[str, Any]],
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
    error_code: str,
) -> None:
    for key, record in settings.items():
        if record["state"] in {"drift", "invalid"}:
            errors.append(
                _error(
                    error_code,
                    _relative(root, data_path) + f"#/{key}",
                    f"Notebook Navigator setting {key} does not satisfy the accepted P10 contract",
                )
            )


def _blueprint_contract(blueprint: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    note_types = blueprint.get("note_types")
    note_types = note_types if isinstance(note_types, dict) else {}
    templates = blueprint.get("templates")
    templates = templates if isinstance(templates, dict) else {}

    expected = {
        "daily": {
            "path_globs": ["10_Journal/Daily/**/*.md"],
            "template": "T10_Daily.md",
            "owner": "obsidian-core-daily-notes",
        },
        "weekly": {
            "path_globs": ["10_Journal/Weekly/**/*.md"],
            "template": "T11_Weekly.md",
            "owner": "notebook-navigator-plus-templater",
        },
        "monthly": {
            "path_globs": ["10_Journal/Monthly/**/*.md"],
            "template": "T12_Monthly.md",
            "owner": "notebook-navigator-plus-templater",
        },
    }
    observed: dict[str, Any] = {}
    for note_type, contract in expected.items():
        source = note_types.get(note_type)
        source = source if isinstance(source, dict) else {}
        observed[note_type] = {
            "path_globs": source.get("path_globs"),
            "template": source.get("template"),
            "owner": contract["owner"],
        }
        if source and (
            source.get("path_globs") != contract["path_globs"]
            or source.get("template") != contract["template"]
        ):
            errors.append(
                _error(
                    "P10_BLUEPRINT_PERIOD_MAPPING_DRIFT",
                    f"blueprint/blueprint.yaml#/note_types/{note_type}",
                    f"Blueprint {note_type} path or template does not match the accepted P10 mapping",
                )
            )

    expected_template_folder = "99_System/Templates"
    observed_template_folder = templates.get("directory")
    if templates and observed_template_folder != expected_template_folder:
        errors.append(
            _error(
                "P10_BLUEPRINT_TEMPLATE_FOLDER_DRIFT",
                "blueprint/blueprint.yaml#/templates/directory",
                "Blueprint template directory does not match the P10 period-template contract",
            )
        )

    state = "pass" if not errors else "blocked"
    return {
        "state": state,
        "periodic_folder": P10_PERIODIC_FOLDER,
        "template_folder": observed_template_folder,
        "expected_template_folder": expected_template_folder,
        "note_types": observed,
        "ownership": {
            "daily": "Obsidian Core Daily Notes",
            "weekly": "Notebook Navigator selects/creates; Templater renders",
            "monthly": "Notebook Navigator selects/creates; Templater renders",
        },
        "source": "blueprint/blueprint.yaml#/note_types and /templates",
    }


def _active_profile(
    document: dict[str, Any],
    *,
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    profiles = document.get("vaultProfiles")
    if profiles is None:
        return None, {
            "state": "unknown",
            "selection": f"profile named {P10_PROFILE_NAME}",
            "source": _relative(root, data_path) + "#/vaultProfiles",
        }
    if not isinstance(profiles, list):
        errors.append(
            _error(
                "P10_VAULT_PROFILES_INVALID",
                _relative(root, data_path) + "#/vaultProfiles",
                "vaultProfiles must be a list when configured",
            )
        )
        return None, {
            "state": "invalid",
            "selection": f"profile named {P10_PROFILE_NAME}",
            "source": _relative(root, data_path) + "#/vaultProfiles",
        }
    owned_profiles = [
        (index, profile)
        for index, profile in enumerate(profiles)
        if isinstance(profile, dict) and profile.get("name") == P10_PROFILE_NAME
    ]
    if not owned_profiles:
        errors.append(
            _error(
                "P10_VAULT_PROFILES_EMPTY",
                _relative(root, data_path) + "#/vaultProfiles",
                f"vaultProfiles must contain the {P10_PROFILE_NAME!r} capability profile",
            )
        )
        return None, {
            "state": "invalid",
            "selection": f"profile named {P10_PROFILE_NAME}",
            "source": _relative(root, data_path) + "#/vaultProfiles",
        }
    if len(owned_profiles) > 1:
        errors.append(
            _error(
                "P10_DUPLICATE_OWNED_PROFILE",
                _relative(root, data_path) + "#/vaultProfiles",
                f"vaultProfiles must contain only one profile named {P10_PROFILE_NAME!r}",
            )
        )
    index, profile = owned_profiles[0]
    name = profile.get("name")
    return profile, {
        "state": "pass" if len(owned_profiles) == 1 else "blocked",
        "selection": f"profile named {P10_PROFILE_NAME}",
        "index": index,
        "id": profile.get("id"),
        "name": name,
        "expected_name": P10_PROFILE_NAME,
        "source": _relative(root, data_path) + "#/vaultProfiles",
    }


def _blueprint_paths_and_templates(
    *,
    root: Path,
    blueprint_contract: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    del root
    records: dict[str, dict[str, Any]] = {}
    expected = {
        "daily": (P10_DAILY_TEMPLATE, "obsidian-core-daily-notes", "core_date_tokens"),
        "weekly": (P10_WEEKLY_TEMPLATE, "templater-obsidian", "templater_expressions"),
        "monthly": (P10_MONTHLY_TEMPLATE, "templater-obsidian", "templater_expressions"),
    }
    for note_type, (template_path, owner, expected_syntax) in expected.items():
        blueprint_note_type = blueprint_contract.get("note_types", {}).get(note_type, {})
        observed_template = blueprint_note_type.get("template") if isinstance(blueprint_note_type, dict) else None
        state = "unknown" if observed_template is None else "pass" if observed_template == Path(template_path).name else "blocked"
        if state == "blocked":
            errors.append(
                _error(
                    "P10_TEMPLATE_OWNER_DRIFT",
                    f"blueprint/blueprint.yaml#/note_types/{note_type}/template",
                    f"{note_type} period must retain its KnowledgeOS-owned template reference",
                )
            )
        records[note_type] = {
            "template": template_path,
            "owner": owner,
            "renderer": "obsidian-core-date-token-renderer" if note_type == "daily" else "templater-obsidian",
            "expected_syntax": expected_syntax,
            "observed_syntax": "not_read_from_deployed_vault",
            "state": state,
            "source": f"blueprint/blueprint.yaml#/note_types/{note_type}/template",
            "invocation_boundary": (
                "Core Daily Notes creates the daily note"
                if note_type == "daily"
                else "explicit Notebook Navigator create/open followed by one bounded Templater render"
            ),
        }
    return records


def _profile_scope(
    profile: dict[str, Any] | None,
    *,
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    del data_path, root, errors
    if profile is None:
        return {
            "state": "unknown",
            "fields": {key: {"state": "unknown"} for key in _PROFILE_EMPTY_LISTS},
            "file_visibility": {"state": "unknown"},
        }
    fields = {}
    for key in _PROFILE_EMPTY_LISTS:
        value = profile.get(key)
        fields[key] = {
            "state": "unconfigured" if value in (None, [], {}) else "observed",
            "configured": value not in (None, [], {}),
        }
    visibility = profile.get("fileVisibility")
    file_visibility = {
        "state": "unknown" if visibility is None else "observed",
        "configured": visibility is not None,
    }
    return {
        "state": "observed",
        "fields": fields,
        "file_visibility": file_visibility,
    }


def _display_scope(data: dict[str, Any], *, data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    del data_path, root, errors
    observed = {
        "calendar_show_hidden_items": data.get("calendarShowHiddenItems"),
        "file_visibility": data.get("fileVisibility"),
        "show_root_folder": data.get("showRootFolder"),
        "show_tags": data.get("showTags"),
        "show_properties": data.get("showProperties"),
        "calendar_show_week_number": data.get("calendarShowWeekNumber"),
        "calendar_show_quarter": data.get("calendarShowQuarter"),
        "calendar_show_year_calendar": data.get("calendarShowYearCalendar"),
        "calendar_show_outside_month_days": data.get("calendarShowOutsideMonthDays"),
    }
    return {
        "state": "observed" if any(value is not None for value in observed.values()) else "unknown",
        "observed": observed,
        "policy": "user-owned presentation values do not change P10 capability or authorize note mutation",
    }


def _template_settings(data: dict[str, Any], *, data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    engine = _exact_setting(
        data.get("templateEngine"),
        "automatic",
        policy="the KnowledgeOS period templates use Notebook Navigator's Templater resolver",
    )
    if engine["state"] in {"drift", "invalid"}:
        errors.append(
            _error(
                "P10_TEMPLATE_SETTING_DRIFT",
                _relative(root, data_path) + "#/templateEngine",
                "the owned period templates must retain the accepted Templater resolver",
            )
        )

    calendar_template_folder = data.get("calendarTemplateFolder")
    if calendar_template_folder not in (None, "") and not _safe_vault_relative_path(calendar_template_folder):
        errors.append(
            _error(
                "P10_PATH_ESCAPE",
                _relative(root, data_path) + "#/calendarTemplateFolder",
                "Notebook Navigator template folders must remain safe Vault-relative paths",
            )
        )

    folder_templates = data.get("folderTemplates")
    conflicts: list[str] = []
    tolerated_count = 0
    if isinstance(folder_templates, dict):
        owned_roots = (
            PurePosixPath("10_Journal/Daily"),
            PurePosixPath("10_Journal/Weekly"),
            PurePosixPath("10_Journal/Monthly"),
        )
        for folder in folder_templates:
            if not isinstance(folder, str) or not _safe_vault_relative_path(folder):
                conflicts.append("invalid_path")
                continue
            path = PurePosixPath(folder.rstrip("/"))
            if any(path == owned or path in owned.parents or owned in path.parents for owned in owned_roots):
                conflicts.append(folder)
            else:
                tolerated_count += 1
    if conflicts:
        errors.append(
            _error(
                "P10_PERIOD_TEMPLATE_OWNER_CONFLICT",
                _relative(root, data_path) + "#/folderTemplates",
                "additional folder templates cannot overlap KnowledgeOS-owned periodic-note paths",
            )
        )
    return {
        "template_engine": engine,
        "calendar_template_folder": {
            "state": "unknown" if "calendarTemplateFolder" not in data else "observed",
            "configured": bool(calendar_template_folder),
        },
        "folder_templates": {
            "state": "unknown" if folder_templates is None else "blocked" if conflicts else "pass",
            "tolerated_user_mappings": tolerated_count,
            "owned_path_conflicts": len(conflicts),
        },
        "template_commands": {
            "state": "unknown" if "templateCommands" not in data else "observed",
            "configured": bool(data.get("templateCommands")),
        },
        "folder_notes": {
            "state": "unknown" if "enableFolderNotes" not in data else "observed",
            "enabled": data.get("enableFolderNotes"),
        },
    }


def _confirmation_policy(data: dict[str, Any], *, data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    settings = {
        "calendar_confirm_before_create": _exact_setting(
            data.get("calendarConfirmBeforeCreate"),
            True,
            policy="confirm before Notebook Navigator creates a new periodic note",
        ),
        "confirm_before_delete": _exact_setting(
            data.get("confirmBeforeDelete"),
            True,
            policy="delete remains an explicit human action with confirmation",
        ),
        "delete_attachments": _exact_setting(
            data.get("deleteAttachments"),
            "ask",
            policy="attachment deletion remains explicitly confirmed",
        ),
        "move_file_conflicts": _exact_setting(
            data.get("moveFileConflicts"),
            "ask",
            policy="move conflicts remain explicitly resolved per file",
        ),
        "confirm_before_manual_sort": _exact_setting(
            data.get("confirmBeforeManualSort"),
            True,
            policy="manual sort remains a confirmed human action",
        ),
    }
    _append_setting_errors(
        settings={
            "calendarConfirmBeforeCreate": settings["calendar_confirm_before_create"],
            "confirmBeforeDelete": settings["confirm_before_delete"],
            "deleteAttachments": settings["delete_attachments"],
            "moveFileConflicts": settings["move_file_conflicts"],
            "confirmBeforeManualSort": settings["confirm_before_manual_sort"],
        },
        data_path=data_path,
        root=root,
        errors=errors,
        error_code="P10_CONFIRMATION_POLICY_DRIFT",
    )
    static_pass = all(record["state"] == "pass" for record in settings.values())
    static_unknown = any(record["state"] == "unknown" for record in settings.values())
    return {
        "state": "pass" if static_pass else "unknown" if static_unknown and not errors else "blocked",
        "settings": settings,
        "existing_file_behavior": {
            "new_period": "confirm_before_create",
            "existing_period": "open_existing_without_overwrite",
            "overwrite": "forbidden_by_contract",
            "conflict_resolution": "human_action_required",
            "runtime_or_device": "not_run",
        },
    }


def _mapping_contract(
    document: dict[str, Any],
    profile: dict[str, Any] | None,
    *,
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    observed = {
        "periodic_notes_folder": profile.get("periodicNotesFolder") if profile is not None else None,
        "daily_pattern": document.get("calendarCustomFilePattern"),
        "weekly_pattern": document.get("calendarCustomWeekPattern"),
        "monthly_pattern": document.get("calendarCustomMonthPattern"),
        "daily_template": document.get("calendarCustomFileTemplate"),
        "weekly_template": document.get("calendarCustomWeekTemplate"),
        "monthly_template": document.get("calendarCustomMonthTemplate"),
        "calendar_enabled": document.get("calendarEnabled"),
        "integration_mode": document.get("calendarIntegrationMode"),
        "locale_source": document.get("calendarPeriodicNotesLocaleSource"),
    }
    expected = {
        "periodic_notes_folder": P10_PERIODIC_FOLDER,
        "daily_pattern": P10_DAILY_PATTERN,
        "weekly_pattern": P10_WEEKLY_PATTERN,
        "monthly_pattern": P10_MONTHLY_PATTERN,
        "daily_template": P10_DAILY_TEMPLATE,
        "weekly_template": P10_WEEKLY_TEMPLATE,
        "monthly_template": P10_MONTHLY_TEMPLATE,
        "calendar_enabled": True,
        "integration_mode": "notebook-navigator",
    }
    checks: dict[str, dict[str, Any]] = {}
    for name, expected_value in expected.items():
        record = _exact_setting(
            observed[name],
            expected_value,
            policy="required KnowledgeOS-owned periodic mapping",
        )
        checks[name] = record
        if record["state"] in {"drift", "invalid"}:
            errors.append(
                _error(
                    "P10_PERIOD_MAPPING_DRIFT",
                    _relative(root, data_path) + f"#/{name}",
                    f"Notebook Navigator mapping {name} does not match the accepted period-note contract",
                )
            )
    for name in ("periodic_notes_folder", "daily_template", "weekly_template", "monthly_template"):
        if observed[name] is not None and not _safe_vault_relative_path(observed[name]):
            errors.append(
                _error(
                    "P10_PATH_ESCAPE",
                    _relative(root, data_path) + f"#/{name}",
                    "Notebook Navigator owned paths must remain confined to safe Vault-relative paths",
                )
            )
    mapping_state = "pass" if all(record["state"] == "pass" for record in checks.values()) else "unknown" if any(record["state"] == "unknown" for record in checks.values()) else "blocked"
    return {
        "state": mapping_state,
        "observed": observed,
        "expected": expected,
        "checks": checks,
        "paths": {
            "daily": "10_Journal/Daily/YYYY-MM-DD.md",
            "weekly": "10_Journal/Weekly/{iso_year}/{iso_year}-W{iso_week}.md",
            "monthly": "10_Journal/Monthly/{year}/{year}-{month}.md",
        },
        "titles": {
            "daily": "YYYY-MM-DD",
            "weekly": "YYYY-Www",
            "monthly": "YYYY-MM",
        },
        "source": _relative(root, data_path),
    }


def _command_policy(manifest: dict[str, Any] | None, data: dict[str, Any]) -> dict[str, Any]:
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return {
        "calendar_surface": "Notebook Navigator calendar",
        "serialized_command_ids": "not_present_in_installed_data",
        "command_ids": [],
        "command_id_inference": False,
        "version_evidence": {
            "plugin": P10_PLUGIN_ID,
            "version": version,
            "manifest_observed": isinstance(manifest, dict),
            "data_keys_observed": [
                key
                for key in (
                    "calendarEnabled",
                    "calendarIntegrationMode",
                    "calendarCustomWeekPattern",
                    "calendarCustomMonthPattern",
                    "calendarCustomWeekTemplate",
                    "calendarCustomMonthTemplate",
                )
                if key in data
            ],
        },
        "policy": "use the calendar surface only; do not assert an unverified Command Palette or toolbar ID",
        "fallback": "Core File Explorer and reviewed Markdown links",
    }


def _mutation_policy(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "bulk_move": "forbidden_by_contract",
        "bulk_delete": "forbidden_by_contract",
        "bulk_property_edit": "forbidden_by_contract",
        "serialized_bulk_controls": "not_present_in_installed_data",
        "quick_actions": {
            "show": data.get("showQuickActions"),
            "add_tag": data.get("quickActionAddTag"),
            "add_to_shortcuts": data.get("quickActionAddToShortcuts"),
            "pin_note": data.get("quickActionPinNote"),
            "open_in_new_tab": data.get("quickActionOpenInNewTab"),
            "policy": "presentation/navigation controls remain explicit human single-note actions",
        },
        "manual_sort": {
            "property_key": data.get("manualSortPropertyKey"),
            "group_header_property": data.get("manualSortGroupHeaderProperty"),
            "new_note_placement": data.get("manualSortNewNotePlacement"),
            "policy": "explicit human action with one-file rollback; never a bulk canonical rewrite",
        },
        "note_mutation": "out_of_scope",
        "plugin_data_mutation": "out_of_scope",
    }


def _fallback_contract(root: Path) -> dict[str, Any]:
    del root
    return {
        "state": "owned_by_P02",
        "core_file_explorer": {
            "expected": True,
            "state": "not_assessed_by_P10",
            "source": "P02 Core settings capability",
        },
        "plugin_free": True,
        "navigation": "Core File Explorer and reviewed canonical Markdown links",
        "navigator_disablement": "not_authorized",
    }


def build_notebook_navigator_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P10 registry from installed Navigator data without mutation."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/notebook-navigator/manifest.json"
    data_path = profile_root / "plugins/notebook-navigator/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P10_NAVIGATOR_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P10_NAVIGATOR_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(
            _error(
                "P10_NAVIGATOR_MANIFEST_ROOT_INVALID",
                _relative(root, manifest_path),
                "Notebook Navigator manifest root must be an object",
            )
        )
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(
            _error(
                "P10_NAVIGATOR_DATA_ROOT_INVALID",
                _relative(root, data_path),
                "Notebook Navigator data root must be an object",
            )
        )
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P10_PLUGIN_ID):
        errors.append(
            _error(
                "P10_NAVIGATOR_MANIFEST_ID_MISMATCH",
                _relative(root, manifest_path),
                "Notebook Navigator manifest id does not match the installed plugin id",
            )
        )

    serialized = data if isinstance(data, dict) else {}
    profile, profile_contract = _active_profile(
        serialized,
        data_path=data_path,
        root=root,
        errors=errors,
    )
    blueprint_contract = _blueprint_contract(blueprint, errors)
    mapping = _mapping_contract(
        serialized,
        profile,
        data_path=data_path,
        root=root,
        errors=errors,
    )
    template_records = _blueprint_paths_and_templates(
        root=root,
        blueprint_contract=blueprint_contract,
        errors=errors,
    )
    template_settings = _template_settings(serialized, data_path=data_path, root=root, errors=errors)
    profile_scope = _profile_scope(profile, data_path=data_path, root=root, errors=errors)
    display_scope = _display_scope(serialized, data_path=data_path, root=root, errors=errors)
    confirmation = _confirmation_policy(serialized, data_path=data_path, root=root, errors=errors)
    fallback = _fallback_contract(root)
    if fallback["state"] == "blocked":
        errors.append(
            _error(
                "P10_FILE_EXPLORER_FALLBACK_UNAVAILABLE",
                fallback["core_file_explorer"]["source"],
                "Core File Explorer must remain available as the immediate plugin-free fallback",
            )
        )

    mapping_state = mapping["state"]
    template_state = "pass" if all(item["state"] == "pass" for item in template_records.values()) else "unknown" if any(item["state"] == "unknown" for item in template_records.values()) else "blocked"
    registry = {
        "schema_version": P10_NOTEBOOK_NAVIGATOR_REGISTRY_SCHEMA_VERSION,
        "plugin": P10_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "active_profile": profile_contract,
        "blueprint_contract": blueprint_contract,
        "period_mapping": mapping,
        "template_ownership": {
            "mode": "templater-owned-period-rendering",
            "state": template_state,
            "basis": "accepted F02 artifact and D93 ownership decision",
            "daily_owner": "obsidian-core-daily-notes",
            "period_owner": "notebook-navigator-plus-templater",
            "notebook_navigator_role": "select/create weekly or monthly target and open existing target",
            "templater_role": "render one newly created weekly or monthly note only",
            "global_trigger": "disabled_by_P04_contract",
            "records": template_records,
        },
        "template_settings": template_settings,
        "scope": {
            "profile": profile_scope,
            "display": display_scope,
            "state": "pass"
            if profile_scope["state"] == "pass" and display_scope["state"] in {"pass", "unknown"}
            else "unknown"
            if profile_scope["state"] == "unknown" or display_scope["state"] == "unknown"
            else "blocked",
        },
        "confirmation_policy": confirmation,
        "calendar_commands": _command_policy(manifest, serialized),
        "mutation_policy": _mutation_policy(serialized),
        "fallback": fallback,
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if isinstance(data, dict) and not errors else "blocked" if errors else "unknown",
            "mapping": mapping_state,
            "template_ownership": template_state,
            "rendered_fixture": "not_run",
            "runtime": "not_run",
            "device": "not_run",
            "creation_opening_no_overwrite": "not_run",
            "note_move_delete_property": "out_of_scope",
            "plugin_data_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P10_FILE_VISIBILITY",
    "P10_NOTEBOOK_NAVIGATOR_REGISTRY_SCHEMA_VERSION",
    "P10_PLUGIN_ID",
    "build_notebook_navigator_setting_registry",
]

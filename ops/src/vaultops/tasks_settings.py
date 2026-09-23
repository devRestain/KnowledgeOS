"""Read-only P05 Tasks query and human-completion contract inspection.

P05 records the installed Tasks plugin's query, status, date, recurrence, and
editor-assistance settings without treating a query result as a write
authority.  It also distinguishes an unused JavaScript preset from an active
JavaScript query so an absent serialized JavaScript flag is never guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

P05_TASKS_REGISTRY_SCHEMA_VERSION = 1
P05_PLUGIN_ID = "obsidian-tasks-plugin"
P05_GLOBAL_FILTER = "#task"
P05_QUERY_SOURCES = (
    "99_System/Dashboards/Tasks.md",
    "99_System/Dashboards/Weekly_Review.md",
)

_TASKS_BLOCK = re.compile(r"```tasks[ \t]*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
_LIMIT = re.compile(r"^limit\s+(?P<value>\d+)$", re.IGNORECASE)
_TAGS_INCLUDE = re.compile(r"^tags\s+include\s+(?P<tag>#[^\s]+)$", re.IGNORECASE)
_SORT = re.compile(r"^sort\s+by\s+(?P<field>.+)$", re.IGNORECASE)

_EXPECTED_STATUS_TYPES = {
    "Todo": "TODO",
    "Done": "DONE",
    "In Progress": "IN_PROGRESS",
    "Cancelled": "CANCELLED",
}

_ALLOWED_QUERY_PREFIXES = (
    "not done",
    "done",
    "due before today",
    "due today",
    "tags include ",
    "limit ",
    "sort by ",
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


def _value_state(value: Any, *, expected: Any) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    return {"observed": value, "state": "pass" if value == expected else "drift"}


def _date_setting(value: Any, *, mode: str) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown", "mode": mode}
    if not isinstance(value, bool):
        return {"observed": "invalid", "state": "invalid", "mode": mode}
    if mode == "human_completion" and value:
        state = "human_action_write_enabled"
    elif mode == "disabled" and not value:
        state = "disabled"
    elif mode == "preserved" and not value:
        state = "preserved"
    else:
        state = "enabled" if value else "disabled"
    return {"observed": value, "state": state, "mode": mode}


def _active_javascript(value: Any) -> bool:
    return isinstance(value, str) and "filter by function" in value.lower()


def _query_record(
    *,
    root: Path,
    path: Path,
    ordinal: int,
    body: str,
    source_text: str,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    source = _relative(root, path)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    forbidden = [line for line in lines if "filter by function" in line.lower() or "javascript" in line.lower()]
    unknown = [
        line
        for line in lines
        if not any(line.lower().startswith(prefix) for prefix in _ALLOWED_QUERY_PREFIXES)
    ]
    if forbidden:
        errors.append(
            _error(
                "P05_JAVASCRIPT_QUERY_ACTIVE",
                f"{source}#tasks[{ordinal}]",
                "JavaScript or filter-by-function execution is outside the P05 query domain",
            )
        )
    if unknown:
        errors.append(
            _error(
                "P05_QUERY_DIRECTIVE_UNKNOWN",
                f"{source}#tasks[{ordinal}]",
                "query contains a directive outside the bounded P05 allowlist: " + "; ".join(unknown),
            )
        )
    limit_match = next((_LIMIT.match(line) for line in lines if _LIMIT.match(line)), None)
    tags = sorted(
        match.group("tag")
        for line in lines
        if (match := _TAGS_INCLUDE.match(line)) is not None
    )
    if "#task" not in {tag.lower() for tag in tags}:
        errors.append(
            _error(
                "P05_TASK_TAG_MISSING",
                f"{source}#tasks[{ordinal}]",
                "every canonical Tasks query must retain the #task tag convention",
            )
        )
    sort_fields = [
        match.group("field").strip()
        for line in lines
        if (match := _SORT.match(line)) is not None
    ]
    return {
        "source": source,
        "ordinal": ordinal,
        "lines": lines,
        "filters": {
            "not_done": any(line.lower() == "not done" for line in lines),
            "done": any(line.lower() == "done" for line in lines),
            "due_before_today": any(line.lower() == "due before today" for line in lines),
            "due_today": any(line.lower() == "due today" for line in lines),
            "tags": tags,
        },
        "limit": int(limit_match.group("value")) if limit_match else None,
        "limit_state": "bounded" if limit_match else "unbounded_read_only",
        "sort": sort_fields,
        "forbidden_javascript": forbidden,
        "unknown_directives": unknown,
        "query_owner": "obsidian-tasks-plugin",
        "authority": "read_only_query_result_not_write_authority",
        "human_completion": "explicit_status_action_on_source_markdown_task_line",
        "fallback": "Core Search and ordinary Markdown checkbox inspection",
        "fallback_observed": "Core-only fallback:" in source_text,
        "runtime_and_device_evidence": "not_run",
    }


def _query_records(
    *,
    root: Path,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in P05_QUERY_SOURCES:
        path = root / "KnowledgeHub" / relative
        source = _relative(root, path)
        if path.is_symlink() or not path.is_file():
            records.append(
                {
                    "source": source,
                    "state": "unknown",
                    "queries": [],
                    "runtime_and_device_evidence": "not_run",
                }
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(_error("P05_QUERY_SOURCE_READ_FAILED", source, str(error)))
            records.append({"source": source, "state": "unknown", "queries": []})
            continue
        blocks = [match.group("body") for match in _TASKS_BLOCK.finditer(text)]
        if "Core-only fallback:" not in text:
            errors.append(_error("P05_QUERY_FALLBACK_MISSING", source, "query source must retain its Core-only fallback"))
        queries = [
            _query_record(
                root=root,
                path=path,
                ordinal=index,
                body=body,
                source_text=text,
                errors=errors,
            )
            for index, body in enumerate(blocks, start=1)
        ]
        records.append(
            {
                "source": source,
                "state": "pass" if not any(item["forbidden_javascript"] or item["unknown_directives"] for item in queries) else "blocked",
                "queries": queries,
                "runtime_and_device_evidence": "not_run",
            }
        )
    return records


def _status_registry(data: dict[str, Any] | None, errors: list[dict[str, str]], root: Path, data_path: Path) -> dict[str, Any]:
    raw = data.get("statusSettings") if isinstance(data, dict) else None
    if raw is None:
        return {"state": "unknown", "core": [], "custom": [], "canonical_frontmatter_status_is_separate": True}
    if not isinstance(raw, dict):
        errors.append(_error("P05_STATUS_SETTINGS_INVALID", _relative(root, data_path) + "#/statusSettings", "statusSettings must be an object"))
        return {"state": "invalid", "core": [], "custom": [], "canonical_frontmatter_status_is_separate": True}

    result: dict[str, Any] = {"state": "observed", "core": [], "custom": [], "canonical_frontmatter_status_is_separate": True}
    observed_names: set[str] = set()
    expected_sections = {"coreStatuses": "core", "customStatuses": "custom"}
    for key, output_key in expected_sections.items():
        values = raw.get(key)
        if values is None:
            result[output_key] = []
            result[f"{output_key}_state"] = "unknown"
            continue
        if not isinstance(values, list):
            errors.append(_error("P05_STATUS_SETTINGS_INVALID", _relative(root, data_path) + f"#/statusSettings/{key}", "status status list must be an array"))
            result[output_key] = []
            result[f"{output_key}_state"] = "invalid"
            continue
        normalized: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                errors.append(_error("P05_STATUS_SETTINGS_INVALID", _relative(root, data_path) + f"#/statusSettings/{key}", "status entry must be an object"))
                continue
            name = item.get("name")
            observed_names.add(name) if isinstance(name, str) else None
            normalized.append(
                {
                    "symbol": item.get("symbol"),
                    "name": name,
                    "next_status_symbol": item.get("nextStatusSymbol"),
                    "available_as_command": item.get("availableAsCommand"),
                    "type": item.get("type"),
                    "human_action": "explicit_status_command_or_checkbox_edit",
                    "canonical_task_state": {
                        "Todo": "open",
                        "In Progress": "in_progress",
                        "Done": "done",
                        "Cancelled": "cancelled",
                    }.get(name, "unknown"),
                }
            )
        result[output_key] = normalized
        result[f"{output_key}_state"] = "observed"
    expected_names = set(_EXPECTED_STATUS_TYPES)
    result["mapping_state"] = "pass" if observed_names == expected_names else "unknown" if not observed_names else "drift"
    if observed_names and observed_names != expected_names:
        errors.append(_error("P05_STATUS_MAPPING_DRIFT", _relative(root, data_path) + "#/statusSettings", "Tasks status names must cover the Blueprint task status contract"))
    return result


def build_tasks_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P05 registry from the installed Tasks profile and Markdown queries."""

    del blueprint  # P05 uses the shared #task and note-template contract directly.
    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/obsidian-tasks-plugin/manifest.json"
    data_path = profile_root / "plugins/obsidian-tasks-plugin/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P05_TASKS_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P05_TASKS_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P05_TASKS_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Tasks manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P05_TASKS_DATA_ROOT_INVALID", _relative(root, data_path), "Tasks data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P05_PLUGIN_ID):
        errors.append(_error("P05_TASKS_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Tasks manifest id does not match the installed plugin id"))

    global_query = data.get("globalQuery") if isinstance(data, dict) else None
    global_filter = data.get("globalFilter") if isinstance(data, dict) else None
    remove_global_filter = data.get("removeGlobalFilter") if isinstance(data, dict) else None
    if _active_javascript(global_query):
        errors.append(_error("P05_JAVASCRIPT_QUERY_ACTIVE", _relative(root, data_path) + "#/globalQuery", "globalQuery cannot execute filter-by-function JavaScript"))
    if global_filter is not None and global_filter != P05_GLOBAL_FILTER:
        errors.append(_error("P05_GLOBAL_FILTER_DRIFT", _relative(root, data_path) + "#/globalFilter", "globalFilter must remain the #task convention"))
    if remove_global_filter is True:
        errors.append(_error("P05_GLOBAL_FILTER_REMOVED", _relative(root, data_path) + "#/removeGlobalFilter", "Tasks global filter removal must remain disabled"))

    presets = data.get("presets") if isinstance(data, dict) else None
    javascript_presets = sorted(
        name
        for name, value in presets.items()
        if isinstance(name, str) and _active_javascript(value)
    ) if isinstance(presets, dict) else []
    javascript_setting = {
        "serialized_key": "javascriptQueries",
        "observed": data.get("javascriptQueries") if isinstance(data, dict) and "javascriptQueries" in data else None,
        "state": "unknown" if not isinstance(data, dict) or "javascriptQueries" not in data else "disabled" if data.get("javascriptQueries") is False else "enabled",
        "reason": "absence is not inferred as false; inspect active query usage and preserve the preset boundary",
    }
    preset_state = "explicitly_unresolved" if javascript_presets else "none_observed"
    active_query_state = "active_forbidden" if _active_javascript(global_query) else "not_active"

    date_policy = {
        "created": {
            **_date_setting(data.get("setCreatedDate") if isinstance(data, dict) else None, mode="disabled"),
            "schema_owner": "Tasks task metadata and source Markdown task line",
            "write_boundary": "no automatic created-date insertion",
        },
        "done": {
            **_date_setting(data.get("setDoneDate") if isinstance(data, dict) else None, mode="human_completion"),
            "schema_owner": "Tasks task metadata and source Markdown task line",
            "write_boundary": "only an explicit human completion action may add the done date",
        },
        "cancelled": {
            **_date_setting(data.get("setCancelledDate") if isinstance(data, dict) else None, mode="human_completion"),
            "schema_owner": "Tasks task metadata and source Markdown task line",
            "write_boundary": "only an explicit human cancellation action may add the cancelled date",
        },
        "filename_as_scheduled_date": {
            **_date_setting(data.get("useFilenameAsScheduledDate") if isinstance(data, dict) else None, mode="disabled"),
            "schema_owner": "Tasks schedule metadata",
            "write_boundary": "filename does not silently become a scheduled date",
        },
        "filename_as_date_folders": {
            "observed": data.get("filenameAsDateFolders") if isinstance(data, dict) else None,
            "state": "empty" if isinstance(data, dict) and data.get("filenameAsDateFolders") == [] else "unknown" if not isinstance(data, dict) else "configured",
            "schema_owner": "Tasks filename and folder policy",
            "write_boundary": "no automatic filename or folder rewrite",
        },
    }

    recurrence = {
        "recurrence_on_next_line": _date_setting(
            data.get("recurrenceOnNextLine") if isinstance(data, dict) else None,
            mode="preserved",
        ),
        "remove_scheduled_date_on_recurrence": _date_setting(
            data.get("removeScheduledDateOnRecurrence") if isinstance(data, dict) else None,
            mode="preserved",
        ),
        "representation": "Tasks plugin recurrence syntax remains on the source Markdown task and is not generated by P05",
        "runtime_evidence": "not_run",
    }

    query_sources = _query_records(root=root, errors=errors)
    status_registry = _status_registry(data if isinstance(data, dict) else None, errors, root, data_path)
    registry = {
        "schema_version": P05_TASKS_REGISTRY_SCHEMA_VERSION,
        "plugin": P05_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "task_format": {
            "observed": data.get("taskFormat") if isinstance(data, dict) else None,
            "state": "observed" if isinstance(data, dict) and data.get("taskFormat") else "unknown",
            "schema_owner": "Tasks source Markdown task-line format",
        },
        "global_query": {
            "observed": global_query,
            "state": "empty" if global_query == "" else "unknown" if global_query is None else "configured",
            "javascript_state": active_query_state,
            "authority": "read_only_query_result_not_write_authority",
        },
        "global_filter": {
            **_value_state(global_filter, expected=P05_GLOBAL_FILTER),
            "remove_global_filter": _value_state(remove_global_filter, expected=False),
            "schema_owner": "Markdown task tag convention and Tasks query surface",
        },
        "status_mapping": status_registry,
        "date_policy": date_policy,
        "recurrence": recurrence,
        "editor_assistance": {
            "auto_suggest_in_editor": _value_state(
                data.get("autoSuggestInEditor") if isinstance(data, dict) else None,
                expected=True,
            ),
            "minimum_match": data.get("autoSuggestMinMatch") if isinstance(data, dict) else None,
            "maximum_items": data.get("autoSuggestMaxItems") if isinstance(data, dict) else None,
            "access_keys": data.get("provideAccessKeys") if isinstance(data, dict) else None,
            "boundary": "editor assistance only; no automatic task completion or canonical apply",
        },
        "javascript_boundary": {
            "setting": javascript_setting,
            "preset_names": javascript_presets,
            "preset_state": preset_state,
            "active_query_state": active_query_state,
            "filter_by_function_execution": "forbidden",
        },
        "query_sources": query_sources,
        "query_policy": {
            "owner": "obsidian-tasks-plugin",
            "authority": "read_only",
            "human_completion": "explicit status action or checkbox edit on the source Markdown task line",
            "automatic_canonical_apply": "forbidden",
            "provider_backed_task_change": "forbidden",
            "bulk_rewrite": "forbidden",
            "fallback": "Core Search plus ordinary Markdown checkboxes and note templates",
        },
        "status_conventions": {
            "task_tag": "#task",
            "note_frontmatter_status_is_separate": True,
            "template_sources": [
                "KnowledgeHub/99_System/Templates/T10_Daily.md",
                "KnowledgeHub/99_System/Templates/T11_Weekly.md",
                "KnowledgeHub/99_System/Templates/T20_Project.md",
                "KnowledgeHub/99_System/Templates/T60_Meeting.md",
            ],
            "property_dictionary_owner": "no frontmatter task-status property; task status remains source Markdown checkbox and Tasks status symbol",
        },
        "safety_policy": {
            "javascript_queries": "forbidden_or_explicitly_unresolved",
            "filter_by_function": "forbidden",
            "unbounded_automation": "forbidden",
            "automatic_completion": "forbidden",
            "automatic_canonical_apply": "forbidden",
            "external_task_service": "forbidden",
        },
        "evidence_boundaries": {
            "static": "observed" if query_sources else "not_run",
            "semantic": "observed" if not errors else "blocked",
            "runtime": "not_run",
            "device": "not_run",
            "task_completion": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "bulk_task_rewrite": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P05_GLOBAL_FILTER",
    "P05_PLUGIN_ID",
    "P05_QUERY_SOURCES",
    "P05_TASKS_REGISTRY_SCHEMA_VERSION",
    "build_tasks_setting_registry",
]

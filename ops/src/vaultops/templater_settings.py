"""Read-only P04 Templater bounded-rendering contract inspection.

P04 separates the syntax used by Obsidian Core, Templater, and the
provider-free proposal/template surfaces.  It observes the installed
Templater profile and canonical template bytes without changing plugin data,
rendering a note, or invoking a script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

P04_TEMPLATER_REGISTRY_SCHEMA_VERSION = 1
P04_PLUGIN_ID = "templater-obsidian"
P04_CANONICAL_TEMPLATE_FOLDER = "99_System/Templates"
P04_DAILY_TEMPLATE = "T10_Daily.md"
P04_WEEKLY_TEMPLATE = "T11_Weekly.md"
P04_MONTHLY_TEMPLATE = "T12_Monthly.md"

_TEMPLATER_TAG = re.compile(r"<%[=_*]?(?P<body>.*?)-?%>", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{(?P<body>[^{}]+)\}\}")
_CORE_DATE_TOKEN = re.compile(r"\{\{(?:date|time):[^{}]+\}\}")

_FORBIDDEN_CODE_MARKERS: tuple[tuple[str, str], ...] = (
    ("tp.system", "Templater system command API"),
    ("tp.user", "Templater user script API"),
    ("tp.file.include", "Templater existing-note include API"),
    ("vaultctl", "terminal control command"),
    ("child_process", "Node child-process API"),
    ("XMLHttpRequest", "network request API"),
    ("fetch(", "network request API"),
    ("http://", "network URL"),
    ("https://", "network URL"),
    ("app.", "direct Obsidian application API"),
    ("git", "Git operation"),
    ("shell", "shell execution"),
    ("exec(", "process execution"),
    ("spawn(", "process execution"),
)

_P04_POLICY_CAPABILITIES = (
    "system_commands",
    "shell_execution",
    "user_scripts",
    "startup_templates",
    "arbitrary_folder_mappings",
    "arbitrary_file_mappings",
    "network",
    "ai",
    "git",
    "vaultctl",
    "canonical_apply",
    "global_new_file_trigger",
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


def _mapping_state(value: Any, *, expected: Any) -> str:
    if value is None:
        return "unknown"
    return "empty" if value == expected else "configured"


def _path_value_state(value: Any) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        return "invalid"
    return "empty" if value == "" else "present"


def _extract_templater_code(source: str) -> tuple[list[str], list[str]]:
    blocks = [match.group("body") for match in _TEMPLATER_TAG.finditer(source)]
    malformed: list[str] = []
    if source.count("<%") != len(blocks):
        malformed.append("unclosed_or_invalid_templater_tag")
    return blocks, malformed


def _forbidden_code(blocks: list[str]) -> list[dict[str, str]]:
    code = "\n".join(blocks)
    findings: list[dict[str, str]] = []
    for marker, capability in _FORBIDDEN_CODE_MARKERS:
        if marker in code:
            findings.append({"marker": marker, "capability": capability})
    return findings


def _syntax(source: str) -> str:
    has_templater = "<%" in source
    has_core_tokens = bool(_CORE_DATE_TOKEN.search(source))
    has_placeholders = bool(_PLACEHOLDER.search(source))
    if has_templater and has_placeholders:
        return "templater_expression_plus_vaultops_placeholders"
    if has_templater:
        return "templater_expression"
    if has_core_tokens:
        return "obsidian_core_date_tokens"
    if has_placeholders:
        return "vaultops_placeholder_tokens"
    return "literal_markdown"


def _ownership(template_name: str, note_type: str, syntax: str) -> dict[str, str]:
    if template_name == P04_DAILY_TEMPLATE or note_type == "daily":
        return {
            "owner": "obsidian-core-daily-notes",
            "renderer": "obsidian-core-date-token-renderer",
            "invocation_boundary": "Core Daily Notes creates the daily note",
        }
    if template_name in {P04_WEEKLY_TEMPLATE, P04_MONTHLY_TEMPLATE} or note_type in {"weekly", "monthly"}:
        return {
            "owner": "notebook-navigator",
            "renderer": "templater-obsidian",
            "invocation_boundary": "explicit Notebook Navigator create/open followed by one bounded Templater render",
        }
    if syntax.startswith("templater"):
        return {
            "owner": "templater-obsidian",
            "renderer": "templater-obsidian",
            "invocation_boundary": "explicit human-reviewed template action; no global new-file trigger",
        }
    if note_type == "proposal":
        return {
            "owner": "vaultops-proposal-bridge",
            "renderer": "vaultops-placeholder-renderer",
            "invocation_boundary": "proposal-only bridge output; Templater is not an execution owner",
        }
    return {
        "owner": "vaultops-template-contract",
        "renderer": "vaultops-placeholder-renderer",
        "invocation_boundary": "explicit human-reviewed create-only action",
    }


def _template_records(
    *,
    root: Path,
    template_folder: str,
    required: list[str],
    note_types: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not isinstance(template_folder, str) or not template_folder:
        template_folder = ""
    note_type_by_template: dict[str, str] = {}
    for note_type, contract in note_types.items():
        if isinstance(contract, dict) and isinstance(contract.get("template"), str):
            note_type_by_template[contract["template"]] = note_type

    folder_path = root / "KnowledgeHub" / template_folder
    vault_present = (root / "KnowledgeHub").is_dir()
    for template_name in required:
        path = folder_path / template_name
        note_type = note_type_by_template.get(template_name, "unknown")
        source = _relative(root, path)
        record: dict[str, Any] = {
            "template": template_name,
            "note_type": note_type,
            "source": source,
            "state": "unknown",
            "syntax": "unknown",
            "ownership": _ownership(template_name, note_type, "literal_markdown"),
            "templater_code_blocks": 0,
            "forbidden_code": [],
            "placeholder_tokens": [],
            "static_evidence": "not_observed",
        }
        if not vault_present or path.is_symlink() or not path.is_file():
            records.append(record)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(_error("P04_TEMPLATE_READ_FAILED", source, str(error)))
            records.append(record)
            continue
        syntax = _syntax(text)
        blocks, malformed = _extract_templater_code(text)
        forbidden = _forbidden_code(blocks)
        placeholders = sorted({match.group("body") for match in _PLACEHOLDER.finditer(text)})
        if malformed:
            errors.append(_error("P04_TEMPLATE_SYNTAX_INVALID", source, "Templater tags are not balanced"))
        if forbidden:
            errors.append(
                _error(
                    "P04_FORBIDDEN_TEMPLATE_CODE",
                    source,
                    "forbidden capability appears in executable Templater code: "
                    + ", ".join(item["marker"] for item in forbidden),
                )
            )
        if template_name == P04_DAILY_TEMPLATE and ("<%" in text or not _CORE_DATE_TOKEN.search(text)):
            errors.append(
                _error(
                    "P04_DAILY_SYNTAX_DRIFT",
                    source,
                    "T10_Daily.md must use Core {{date/time:...}} tokens and no Templater block",
                )
            )
        if template_name in {P04_WEEKLY_TEMPLATE, P04_MONTHLY_TEMPLATE} and "<%" not in text:
            errors.append(
                _error(
                    "P04_PERIOD_TEMPLATER_MISSING",
                    source,
                    "period template must expose a bounded Templater expression",
                )
            )
        record.update(
            {
                "state": "pass",
                "syntax": syntax,
                "ownership": _ownership(template_name, note_type, syntax),
                "templater_code_blocks": len(blocks),
                "forbidden_code": forbidden,
                "placeholder_tokens": placeholders,
                "static_evidence": "observed",
            }
        )
        records.append(record)
    return records


def _setting_state(value: Any, *, expected: Any, disabled_state: str = "disabled") -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if value == expected:
        return {"observed": value, "state": disabled_state}
    return {"observed": value, "state": "enabled"}


def _list_setting(value: Any, *, empty: list[Any]) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if not isinstance(value, list):
        return {"observed": "invalid", "state": "invalid"}
    return {"observed": value, "state": "empty" if value == empty else "configured"}


def _folder_mapping_state(value: Any) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, list):
        return "invalid"
    if value == [{"folder": "", "template": ""}]:
        return "empty"
    return "configured"


def _file_mapping_state(value: Any) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, list):
        return "invalid"
    if value == [{"regex": ".*", "template": ""}]:
        return "empty"
    return "configured"


def build_templater_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P04 registry from Blueprint, plugin data, and template bytes."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/templater-obsidian/manifest.json"
    data_path = profile_root / "plugins/templater-obsidian/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P04_TEMPLATER_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P04_TEMPLATER_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P04_TEMPLATER_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Templater manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P04_TEMPLATER_DATA_ROOT_INVALID", _relative(root, data_path), "Templater data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P04_PLUGIN_ID):
        errors.append(_error("P04_TEMPLATER_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Templater manifest id does not match the installed plugin id"))

    template_config = blueprint.get("templates")
    if not isinstance(template_config, dict):
        errors.append(_error("P04_BLUEPRINT_TEMPLATES_INVALID", "blueprint/blueprint.yaml#/templates", "Blueprint templates must be an object"))
        template_config = {}
    expected_folder = template_config.get("directory")
    required = template_config.get("required")
    if not isinstance(required, list) or not all(isinstance(item, str) and item for item in required):
        errors.append(_error("P04_BLUEPRINT_TEMPLATES_INVALID", "blueprint/blueprint.yaml#/templates/required", "Blueprint required templates must be a string list"))
        required = []
    note_types = blueprint.get("note_types")
    if not isinstance(note_types, dict):
        errors.append(_error("P04_BLUEPRINT_NOTE_TYPES_INVALID", "blueprint/blueprint.yaml#/note_types", "Blueprint note types must be an object"))
        note_types = {}
    folder_state = "pass" if expected_folder == P04_CANONICAL_TEMPLATE_FOLDER else "drift"
    if folder_state != "pass":
        errors.append(_error("P04_TEMPLATE_FOLDER_DRIFT", "blueprint/blueprint.yaml#/templates/directory", "P04 requires the canonical template folder"))
    template_records = _template_records(
        root=root,
        template_folder=str(expected_folder or ""),
        required=required,
        note_types=note_types,
        errors=errors,
    )

    templates_folder = data.get("templates_folder") if isinstance(data, dict) else None
    trigger = data.get("trigger_on_file_creation") if isinstance(data, dict) else None
    system_commands = data.get("enable_system_commands") if isinstance(data, dict) else None
    shell_path = data.get("shell_path") if isinstance(data, dict) else None
    user_scripts = data.get("user_scripts_folder") if isinstance(data, dict) else None
    enable_folder = data.get("enable_folder_templates") if isinstance(data, dict) else None
    folder_templates = data.get("folder_templates") if isinstance(data, dict) else None
    enable_file = data.get("enable_file_templates") if isinstance(data, dict) else None
    file_templates = data.get("file_templates") if isinstance(data, dict) else None
    startup_templates = data.get("startup_templates") if isinstance(data, dict) else None
    template_hotkeys = data.get("enabled_templates_hotkeys") if isinstance(data, dict) else None
    pairs = data.get("templates_pairs") if isinstance(data, dict) else None

    if trigger is True:
        errors.append(_error("P04_GLOBAL_NEW_FILE_TRIGGER_ENABLED", _relative(root, data_path) + "#/trigger_on_file_creation", "global new-file Templater trigger must remain disabled"))
    if system_commands is True:
        errors.append(_error("P04_SYSTEM_COMMANDS_ENABLED", _relative(root, data_path) + "#/enable_system_commands", "Templater system commands must remain disabled"))
    if _path_value_state(shell_path) == "present":
        errors.append(_error("P04_SHELL_PATH_CONFIGURED", _relative(root, data_path) + "#/shell_path", "Templater shell path must remain empty"))
    if _path_value_state(user_scripts) == "present":
        errors.append(_error("P04_USER_SCRIPTS_CONFIGURED", _relative(root, data_path) + "#/user_scripts_folder", "Templater user scripts folder must remain empty"))
    if _folder_mapping_state(folder_templates) == "configured":
        errors.append(_error("P04_FOLDER_MAPPING_CONFIGURED", _relative(root, data_path) + "#/folder_templates", "folder template mappings must remain empty"))
    if _file_mapping_state(file_templates) == "configured":
        errors.append(_error("P04_FILE_MAPPING_CONFIGURED", _relative(root, data_path) + "#/file_templates", "file template mappings must remain empty"))
    if isinstance(startup_templates, list) and startup_templates != [""]:
        errors.append(_error("P04_STARTUP_TEMPLATE_CONFIGURED", _relative(root, data_path) + "#/startup_templates", "startup templates must remain empty"))

    template_folder_setting = {
        "expected": P04_CANONICAL_TEMPLATE_FOLDER,
        "observed": templates_folder,
        "state": "pass" if templates_folder == P04_CANONICAL_TEMPLATE_FOLDER else "unknown" if templates_folder is None else "drift",
    }
    if template_folder_setting["state"] == "drift":
        errors.append(_error("P04_SERIALIZED_TEMPLATE_FOLDER_DRIFT", _relative(root, data_path) + "#/templates_folder", "Templater templates_folder must match the canonical folder"))

    folder_mapping = {
        "enabled_flag": enable_folder,
        "enabled_state": "unknown" if enable_folder is None else "enabled" if enable_folder is True else "disabled",
        "mapping_state": _folder_mapping_state(folder_templates),
        "effective_state": "unconfigured" if _folder_mapping_state(folder_templates) == "empty" else _folder_mapping_state(folder_templates),
        "policy": "an observed true flag with a blank mapping is inert and remains unconfigured until explicitly mapped",
    }
    file_mapping = {
        "enabled_flag": enable_file,
        "enabled_state": "unknown" if enable_file is None else "enabled" if enable_file is True else "disabled",
        "mapping_state": _file_mapping_state(file_templates),
        "effective_state": "unconfigured" if _file_mapping_state(file_templates) == "empty" else _file_mapping_state(file_templates),
    }

    policy_capabilities = {
        "system_commands": _setting_state(system_commands, expected=False),
        "shell_execution": {"observed": _path_value_state(shell_path), "state": "disabled" if _path_value_state(shell_path) == "empty" else _path_value_state(shell_path)},
        "user_scripts": {"observed": _path_value_state(user_scripts), "state": "disabled" if _path_value_state(user_scripts) == "empty" else _path_value_state(user_scripts)},
        "startup_templates": _list_setting(startup_templates, empty=[""]),
        "arbitrary_folder_mappings": {"observed": folder_mapping["mapping_state"], "state": "unconfigured" if folder_mapping["mapping_state"] == "empty" else folder_mapping["mapping_state"]},
        "arbitrary_file_mappings": {"observed": file_mapping["mapping_state"], "state": "unconfigured" if file_mapping["mapping_state"] == "empty" else file_mapping["mapping_state"]},
        "network": {"observed": "not_observed", "state": "forbidden_by_contract"},
        "ai": {"observed": "not_observed", "state": "forbidden_by_contract"},
        "git": {"observed": "not_observed", "state": "forbidden_by_contract"},
        "vaultctl": {"observed": "not_observed_in_executable_code", "state": "forbidden_by_contract"},
        "canonical_apply": {"observed": "not_observed", "state": "forbidden_by_contract"},
        "global_new_file_trigger": _setting_state(trigger, expected=False),
    }

    registry = {
        "schema_version": P04_TEMPLATER_REGISTRY_SCHEMA_VERSION,
        "plugin": P04_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "canonical_template_folder": {
            "blueprint": expected_folder,
            "expected": P04_CANONICAL_TEMPLATE_FOLDER,
            "state": folder_state,
            "source": "blueprint/blueprint.yaml#/templates/directory",
        },
        "serialized_settings": {
            "state": "observed" if isinstance(data, dict) else "unknown",
            "templates_folder": template_folder_setting,
            "command_timeout": data.get("command_timeout") if isinstance(data, dict) else None,
            "trigger_on_file_creation": _setting_state(trigger, expected=False),
            "enable_system_commands": _setting_state(system_commands, expected=False),
            "shell_path": {"state": _path_value_state(shell_path)},
            "user_scripts_folder": {"state": _path_value_state(user_scripts)},
            "enable_folder_templates": enable_folder,
            "folder_templates": folder_mapping,
            "enable_file_templates": enable_file,
            "file_templates": file_mapping,
            "templates_pairs": _list_setting(pairs, empty=[["", ""]]),
            "enabled_templates_hotkeys": _list_setting(template_hotkeys, empty=[""]),
            "startup_templates": _list_setting(startup_templates, empty=[""]),
        },
        "syntax_policy": {
            "allowed": [
                "obsidian_core_date_tokens",
                "templater_expression",
                "templater_expression_plus_vaultops_placeholders",
                "vaultops_placeholder_tokens",
                "literal_markdown",
            ],
            "templater_api_surface": ["tp.date", "tp.file.title", "moment", "crypto.randomUUID", "local variables"],
            "executable_code_scan": "Templater tags only; literal body governance text is not executable code",
        },
        "template_records": template_records,
        "ownership": {
            "daily": _ownership(P04_DAILY_TEMPLATE, "daily", "obsidian_core_date_tokens"),
            "weekly": _ownership(P04_WEEKLY_TEMPLATE, "weekly", "templater_expression"),
            "monthly": _ownership(P04_MONTHLY_TEMPLATE, "monthly", "templater_expression"),
            "general_templater": {
                "owner": "templater-obsidian",
                "renderer": "templater-obsidian",
                "invocation_boundary": "explicit human-reviewed template action; no global new-file trigger",
            },
        },
        "policy_capabilities": policy_capabilities,
        "evidence_boundaries": {
            "static": "observed" if template_records else "not_run",
            "semantic": "observed" if not errors else "blocked",
            "rendered_fixture": "not_run",
            "runtime": "not_run",
            "device": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "existing_note_rewrite": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P04_CANONICAL_TEMPLATE_FOLDER",
    "P04_DAILY_TEMPLATE",
    "P04_MONTHLY_TEMPLATE",
    "P04_PLUGIN_ID",
    "P04_TEMPLATER_REGISTRY_SCHEMA_VERSION",
    "P04_WEEKLY_TEMPLATE",
    "build_templater_setting_registry",
]

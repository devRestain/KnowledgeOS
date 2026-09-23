"""Read-only P06 Linter bounded-hygiene contract inspection.

P06 treats the installed Linter rule set as a candidate surface, not as
authority to rewrite notes.  The current contract deliberately approves zero
rules until a rule has a Property Dictionary/template trace, before/after
evidence, and a one-file rollback plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

P06_LINTER_REGISTRY_SCHEMA_VERSION = 1
P06_PLUGIN_ID = "obsidian-linter"
P06_PROTECTED_FOLDERS = (
    "99_System/Templates",
    "99_System/Bases",
    "99_System/Dashboards",
    "99_System/Schemas",
)

_HIGH_RISK_RULES = {
    "file-name-heading",
    "insert-yaml-attributes",
    "move-tags-to-yaml",
    "remove-yaml-keys",
    "yaml-timestamp",
    "yaml-title",
    "yaml-title-alias",
    "auto-correct-common-misspellings",
    "convert-spaces-to-tabs",
    "remove-empty-lines-between-list-markers-and-checklists",
}
_MEDIUM_RISK_RULES = {
    "dedupe-yaml-array-values",
    "escape-yaml-special-characters",
    "force-yaml-escape",
    "format-tags-in-yaml",
    "format-yaml-array",
    "sort-yaml-array-values",
    "yaml-key-sort",
    "capitalize-headings",
    "header-increment",
    "headings-start-line",
    "remove-trailing-punctuation-in-heading",
    "re-index-footnotes",
    "convert-bullet-list-markers",
    "default-language-for-code-fences",
    "emphasis-style",
    "ordered-list-style",
    "quote-style",
    "strong-style",
    "unordered-list-style",
    "compact-yaml",
    "trailing-spaces",
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


def _boolean_policy(value: Any, *, expected: bool, enabled_state: str = "enabled") -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if not isinstance(value, bool):
        return {"observed": "invalid", "state": "invalid"}
    return {"observed": value, "state": "disabled" if value is False and expected is False else enabled_state if value else "drift"}


def _list_policy(value: Any, *, expected: list[Any]) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if not isinstance(value, list):
        return {"observed": "invalid", "state": "invalid"}
    return {"observed": value, "state": "empty" if value == expected else "configured"}


def _rule_effect(rule_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if rule_id in _HIGH_RISK_RULES:
        risk = "high"
    elif rule_id in _MEDIUM_RISK_RULES:
        risk = "medium"
    else:
        risk = "low_or_unclassified"

    if rule_id == "remove-yaml-keys":
        fields = [config.get("yaml-keys-to-remove")] if config.get("yaml-keys-to-remove") else ["arbitrary configured YAML keys"]
        surface = "frontmatter keys"
    elif rule_id in {"insert-yaml-attributes", "move-tags-to-yaml", "format-tags-in-yaml"}:
        fields = ["tags", "aliases"]
        surface = "frontmatter tags and aliases"
    elif rule_id in {"yaml-title", "yaml-title-alias", "file-name-heading"}:
        fields = ["title", "aliases"]
        surface = "frontmatter title aliases and filename or heading"
    elif rule_id == "yaml-timestamp":
        fields = [config.get("date-created-key"), config.get("date-modified-key")]
        surface = "frontmatter timestamp keys"
    elif "yaml" in rule_id:
        fields = ["frontmatter arrays and key order"]
        surface = "frontmatter YAML formatting"
    elif "tag" in rule_id:
        fields = ["tags"]
        surface = "Markdown and frontmatter tags"
    elif "footnote" in rule_id:
        fields = ["footnotes"]
        surface = "Markdown footnote structure"
    elif "heading" in rule_id or "header" in rule_id:
        fields = ["headings"]
        surface = "Markdown heading structure"
    elif "list" in rule_id or "checklist" in rule_id:
        fields = ["Markdown lists and checklist markers"]
        surface = "Markdown list structure"
    elif rule_id.endswith("-on-paste") or "paste" in rule_id:
        fields = ["pasted Markdown content"]
        surface = "paste-time Markdown content"
    else:
        fields = ["Markdown body formatting"]
        surface = "Markdown body formatting"
    return {
        "affected_surface": surface,
        "affected_fields": [field for field in fields if field],
        "affected_note_types": "all notes unless a future reviewed scope narrows it",
        "destructive_risk": risk,
        "property_dictionary_or_template_traceability": "not_accepted_until_rule_review",
        "before_after_evidence": "required per file: original sha256 plus proposed diff plus post-write sha256",
        "rollback_action": "restore the original bytes for one reviewed file from the pre-run snapshot",
    }


def _rule_registry(rule_configs: Any, root: Path, data_path: Path, errors: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    if rule_configs is None:
        return [], []
    if not isinstance(rule_configs, dict):
        errors.append(_error("P06_RULE_CONFIGS_INVALID", _relative(root, data_path) + "#/ruleConfigs", "ruleConfigs must be an object"))
        return [], []
    records: list[dict[str, Any]] = []
    enabled_rules: list[str] = []
    for rule_id in sorted(rule_configs):
        config = rule_configs[rule_id]
        if not isinstance(config, dict):
            errors.append(_error("P06_RULE_CONFIG_INVALID", _relative(root, data_path) + f"#/ruleConfigs/{rule_id}", "rule configuration must be an object"))
            config = {}
        enabled = config.get("enabled")
        if enabled is True:
            enabled_rules.append(rule_id)
            errors.append(_error("P06_ENABLED_RULE_NOT_ACCEPTED", _relative(root, data_path) + f"#/ruleConfigs/{rule_id}/enabled", "no Linter rule is approved in the P06 baseline"))
        elif enabled is not False and enabled is not None:
            errors.append(_error("P06_RULE_ENABLED_FLAG_INVALID", _relative(root, data_path) + f"#/ruleConfigs/{rule_id}/enabled", "rule enabled flag must be boolean"))
        effect = _rule_effect(rule_id, config)
        records.append(
            {
                "rule_id": rule_id,
                "enabled": enabled,
                "state": "disabled_safe_baseline" if enabled is False else "unknown" if enabled is None else "enabled_unaccepted",
                "allowlist_state": "not_accepted",
                "observed_option_keys": sorted(key for key in config if key != "enabled"),
                **effect,
            }
        )
    return records, enabled_rules


def _template_contract(root: Path, blueprint: dict[str, Any]) -> dict[str, Any]:
    template_config = blueprint.get("templates")
    if not isinstance(template_config, dict):
        return {"state": "unknown", "source": "blueprint/blueprint.yaml#/templates"}
    directory = template_config.get("directory")
    required = template_config.get("required")
    if not isinstance(directory, str) or not isinstance(required, list):
        return {"state": "unknown", "source": "blueprint/blueprint.yaml#/templates"}
    paths = [root / "KnowledgeHub" / directory / name for name in required if isinstance(name, str)]
    state = "pass" if all(path.is_file() and not path.is_symlink() for path in paths) else "unknown"
    return {
        "state": state,
        "directory": directory,
        "required": list(required),
        "source": "blueprint/blueprint.yaml#/templates",
        "property_dictionary": {
            "path": "KnowledgeHub/99_System/Schemas/Property_Dictionary.md",
            "state": "pass" if (root / "KnowledgeHub/99_System/Schemas/Property_Dictionary.md").is_file() else "unknown",
            "source": "generated C06 Property Dictionary",
        },
    }


def build_linter_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P06 registry from the installed Linter profile and contracts."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/obsidian-linter/manifest.json"
    data_path = profile_root / "plugins/obsidian-linter/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P06_LINTER_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P06_LINTER_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P06_LINTER_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Linter manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P06_LINTER_DATA_ROOT_INVALID", _relative(root, data_path), "Linter data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P06_PLUGIN_ID):
        errors.append(_error("P06_LINTER_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Linter manifest id does not match the installed plugin id"))

    rule_configs = data.get("ruleConfigs") if isinstance(data, dict) else None
    rule_records, enabled_rules = _rule_registry(rule_configs, root, data_path, errors)
    lint_on_save = data.get("lintOnSave") if isinstance(data, dict) else None
    lint_on_file_change = data.get("lintOnFileChange") if isinstance(data, dict) else None
    if lint_on_save is True:
        errors.append(_error("P06_AUTOMATIC_TRIGGER_ENABLED", _relative(root, data_path) + "#/lintOnSave", "lint-on-save must remain disabled"))
    if lint_on_file_change is True:
        errors.append(_error("P06_AUTOMATIC_TRIGGER_ENABLED", _relative(root, data_path) + "#/lintOnFileChange", "lint-on-file-change must remain disabled"))

    lint_commands = data.get("lintCommands") if isinstance(data, dict) else None
    custom_regexes = data.get("customRegexes") if isinstance(data, dict) else None
    folders_to_ignore = data.get("foldersToIgnore") if isinstance(data, dict) else None
    files_to_ignore = data.get("filesToIgnore") if isinstance(data, dict) else None
    if isinstance(lint_commands, list) and lint_commands:
        errors.append(_error("P06_UNREVIEWED_LINT_COMMAND", _relative(root, data_path) + "#/lintCommands", "unreviewed Linter commands must remain empty"))
    if isinstance(custom_regexes, list) and custom_regexes:
        errors.append(_error("P06_UNREVIEWED_CUSTOM_REGEX", _relative(root, data_path) + "#/customRegexes", "unreviewed custom regexes must remain empty"))

    protected_folders = {
        "expected": list(P06_PROTECTED_FOLDERS),
        "observed": folders_to_ignore,
        "state": "pass" if folders_to_ignore == list(P06_PROTECTED_FOLDERS) else "unknown" if folders_to_ignore is None else "drift",
        "policy": "canonical templates bases dashboards and schemas remain outside automatic lint scope",
    }
    if protected_folders["state"] == "drift":
        errors.append(_error("P06_PROTECTED_FOLDER_SCOPE_DRIFT", _relative(root, data_path) + "#/foldersToIgnore", "protected canonical folders must remain ignored by Linter"))
    ignored_files = _list_policy(files_to_ignore, expected=[])

    template_contract = _template_contract(root, blueprint)
    registry = {
        "schema_version": P06_LINTER_REGISTRY_SCHEMA_VERSION,
        "plugin": P06_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "rule_allowlist": {
            "approved_rule_ids": [],
            "candidate_rule_ids": [],
            "observed_rule_count": len(rule_records),
            "enabled_rule_ids": enabled_rules,
            "state": "safe_all_rules_off_baseline" if not enabled_rules else "blocked_enabled_rule",
            "healthy": False,
            "healthy_state": "safe_baseline_not_hygiene_completion",
            "rule_id_source": "installed data.json only; no rule IDs invented from external release notes",
        },
        "rule_records": rule_records,
        "triggers": {
            "lint_on_save": _boolean_policy(lint_on_save, expected=False),
            "lint_on_file_change": _boolean_policy(lint_on_file_change, expected=False),
            "lint_commands": _list_policy(lint_commands, expected=[]),
            "custom_regexes": _list_policy(custom_regexes, expected=[]),
            "manual_execution": {
                "state": "manual_only" if lint_on_save is False and lint_on_file_change is False and lint_commands == [] else "unknown",
                "runtime_evidence": "not_run",
            },
        },
        "scope": {
            "protected_folders": protected_folders,
            "ignored_files": ignored_files,
            "canonical_contract": template_contract,
        },
        "rollback_contract": {
            "per_file": True,
            "before_evidence": "sha256 of original bytes plus reviewed target path",
            "after_evidence": "sha256 of proposed bytes plus diff and note validation",
            "rollback_action": "restore original bytes for the one reviewed file from the pre-run snapshot",
            "runtime_evidence": "not_run",
        },
        "safety_policy": {
            "yaml_key_removal": "disabled_and_not_approved",
            "tag_migration": "disabled_and_not_approved",
            "filename_changes": "disabled_and_not_approved",
            "timestamp_overwrites": "disabled_and_not_approved",
            "bulk_rewrite": "forbidden",
            "automatic_rewrite": "forbidden",
            "user_authored_structure_removal": "forbidden",
            "property_dictionary_or_template_inference": "not_approved_without_explicit_rule_review",
        },
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if not errors else "blocked",
            "runtime": "not_run",
            "device": "not_run",
            "file_change": "not_run",
            "bulk_lint": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "canonical_note_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P06_LINTER_REGISTRY_SCHEMA_VERSION",
    "P06_PLUGIN_ID",
    "P06_PROTECTED_FOLDERS",
    "build_linter_setting_registry",
]

"""Read-only P02 contract for Obsidian Core setting state.

The P01 audit reports the installed profile and the ten required community
plugins.  P02 adds a separate, normalized Core registry so every serialized
Core flag observed in a profile has an explicit policy without treating a
serialized value as proof that Obsidian executed the corresponding workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

P02_CORE_REGISTRY_SCHEMA_VERSION = 1
P02_POLICY_CLASSES = ("required", "optional", "explicitly_disabled")


@dataclass(frozen=True)
class CoreSettingPolicy:
    """Static policy metadata for one Obsidian Core flag."""

    plugin_id: str
    component: str
    policy_class: str
    workflow: str
    allowed_value_domain: str
    fallback: str
    mutation_risk: str
    serialized_paths: tuple[str, ...] = ()
    ui_labels: tuple[str, ...] = ()
    profile_scope: str = "all_devices"


def _policy(
    plugin_id: str,
    component: str,
    policy_class: str,
    workflow: str,
    allowed_value_domain: str,
    fallback: str,
    mutation_risk: str,
    *,
    serialized_paths: tuple[str, ...] = (),
    ui_labels: tuple[str, ...] = (),
    profile_scope: str = "all_devices",
) -> CoreSettingPolicy:
    return CoreSettingPolicy(
        plugin_id=plugin_id,
        component=component,
        policy_class=policy_class,
        workflow=workflow,
        allowed_value_domain=allowed_value_domain,
        fallback=fallback,
        mutation_risk=mutation_risk,
        serialized_paths=serialized_paths,
        ui_labels=ui_labels,
        profile_scope=profile_scope,
    )


# Keep this list explicit.  It is the P02 policy surface, not a list of flags
# inferred from one user's current profile.  The required names are checked
# against blueprint/blueprint.yaml below, while optional and disabled flags
# make observed version-specific profile state reviewable and fail closed.
CORE_SETTING_POLICIES = (
    _policy(
        "properties",
        "Properties",
        "required",
        "property_visibility_and_frontmatter_ownership",
        "visible|hidden|unknown",
        "frontmatter_yaml_and_property_dictionary",
        "core_profile_and_property_visibility",
        serialized_paths=("app.json#/propertiesInDocument",),
    ),
    _policy(
        "bases",
        "Bases",
        "required",
        "canonical_base_views",
        "boolean",
        "canonical_markdown_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "daily-notes",
        "Daily notes",
        "required",
        "daily_note_creation",
        "boolean",
        "date_named_markdown_and_vaultctl_note_validate",
        "core_profile_and_note_creation",
        serialized_paths=("daily-notes.json#/folder", "daily-notes.json#/template"),
        ui_labels=("Daily notes date format",),
    ),
    _policy(
        "templates",
        "Templates",
        "required",
        "core_template_access",
        "boolean",
        "canonical_markdown_templates_and_vaultctl_note_validate",
        "core_profile_and_note_creation",
        ui_labels=("Template folder",),
    ),
    _policy(
        "global-search",
        "Search",
        "required",
        "plugin_free_retrieval_fallback",
        "boolean",
        "vaultctl_search_and_plain_markdown",
        "core_profile_only",
    ),
    _policy(
        "backlink",
        "Backlinks",
        "required",
        "typed_relation_navigation",
        "boolean",
        "plain_wikilinks_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "outgoing-link",
        "Outgoing links",
        "required",
        "typed_relation_navigation",
        "boolean",
        "plain_wikilinks_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "bookmarks",
        "Bookmarks",
        "required",
        "review_and_navigation",
        "boolean",
        "plain_wikilinks_and_home_links",
        "core_profile_only",
    ),
    _policy(
        "file-recovery",
        "File recovery",
        "required",
        "safe_rollback",
        "boolean",
        "git_history_and_create_only_writes",
        "core_profile_and_recovery_ui",
    ),
    _policy(
        "workspaces",
        "Workspaces",
        "required",
        "mac_workspace_layouts",
        "boolean",
        "file_explorer_and_home",
        "mac_profile_and_layout_state",
        ui_labels=("Workspace layout persistence",),
        profile_scope="mac_only",
    ),
    _policy(
        "file-explorer",
        "File Explorer",
        "optional",
        "core_file_navigation",
        "boolean",
        "home_links_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "switcher",
        "Quick switcher",
        "optional",
        "core_note_switching",
        "boolean",
        "file_explorer_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "graph",
        "Graph",
        "optional",
        "exploratory_graph_view",
        "boolean",
        "plain_markdown_and_wikilinks",
        "core_profile_only",
    ),
    _policy(
        "canvas",
        "Canvas",
        "optional",
        "freeform_canvas_view",
        "boolean",
        "plain_markdown_and_wikilinks",
        "core_profile_only",
    ),
    _policy(
        "tag-pane",
        "Tag pane",
        "optional",
        "tag_navigation",
        "boolean",
        "frontmatter_tags_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "footnotes",
        "Footnotes",
        "optional",
        "footnote_navigation",
        "boolean",
        "plain_markdown_footnotes",
        "core_profile_only",
    ),
    _policy(
        "page-preview",
        "Page preview",
        "optional",
        "wikilink_preview",
        "boolean",
        "open_the_linked_markdown_note",
        "core_profile_only",
    ),
    _policy(
        "note-composer",
        "Note composer",
        "optional",
        "explicit_note_merge_and_split",
        "boolean",
        "manual_markdown_edit_and_vaultctl_validation",
        "note_rewrite_and_core_profile",
    ),
    _policy(
        "command-palette",
        "Command palette",
        "optional",
        "recovery_and_diagnostics",
        "boolean",
        "home_and_contextual_buttons",
        "core_profile_only",
    ),
    _policy(
        "editor-status",
        "Editor status",
        "optional",
        "editor_status_visibility",
        "boolean",
        "note_contract_validation",
        "core_profile_only",
    ),
    _policy(
        "markdown-importer",
        "Markdown importer",
        "optional",
        "bounded_markdown_import",
        "boolean",
        "reviewed_create_only_import",
        "note_creation_and_core_profile",
    ),
    _policy(
        "outline",
        "Outline",
        "optional",
        "heading_navigation",
        "boolean",
        "plain_markdown_headings",
        "core_profile_only",
    ),
    _policy(
        "word-count",
        "Word count",
        "optional",
        "editor_metrics",
        "boolean",
        "plain_markdown_editor",
        "core_profile_only",
    ),
    _policy(
        "sync",
        "Sync",
        "optional",
        "observed_sync_capability_without_policy_activation",
        "boolean",
        "manual_git_status_and_explicit_bridge",
        "sync_and_core_profile",
    ),
    _policy(
        "webviewer",
        "Web viewer",
        "explicitly_disabled",
        "external_browsing_outside_vault_boundary",
        "boolean",
        "reviewed_external_browser_without_vault_control",
        "external_navigation_and_core_profile",
    ),
    _policy(
        "slash-command",
        "Slash command",
        "explicitly_disabled",
        "avoid_unreviewed_editor_commands",
        "boolean",
        "plain_markdown_editor",
        "editor_command_execution",
    ),
    _policy(
        "zk-prefixer",
        "ZK prefixer",
        "explicitly_disabled",
        "avoid_unreviewed_id_generation",
        "boolean",
        "approved_note_templates",
        "note_creation_and_core_profile",
    ),
    _policy(
        "random-note",
        "Random note",
        "explicitly_disabled",
        "avoid_unbounded_random_navigation",
        "boolean",
        "home_links_and_vaultctl_search",
        "core_profile_only",
    ),
    _policy(
        "slides",
        "Slides",
        "explicitly_disabled",
        "keep_presentation_surface_out_of_scope",
        "boolean",
        "plain_markdown_notes",
        "core_profile_only",
    ),
    _policy(
        "audio-recorder",
        "Audio recorder",
        "explicitly_disabled",
        "keep_audio_capture_out_of_scope",
        "boolean",
        "plain_text_capture",
        "audio_capture_and_core_profile",
    ),
    _policy(
        "publish",
        "Publish",
        "explicitly_disabled",
        "keep_private_vault_unpublished",
        "boolean",
        "private_markdown_vault",
        "publication_and_core_profile",
    ),
)

CORE_SETTING_POLICIES_BY_ID = {policy.plugin_id: policy for policy in CORE_SETTING_POLICIES}
if len(CORE_SETTING_POLICIES_BY_ID) != len(CORE_SETTING_POLICIES):  # pragma: no cover - import invariant
    raise RuntimeError("P02 Core setting policy IDs must be unique")

_REQUIRED_ALL_DEVICE_NAMES = frozenset(
    policy.component
    for policy in CORE_SETTING_POLICIES
    if policy.policy_class == "required" and policy.profile_scope == "all_devices"
)
_REQUIRED_MAC_ONLY_NAMES = frozenset(
    policy.component
    for policy in CORE_SETTING_POLICIES
    if policy.policy_class == "required" and policy.profile_scope == "mac_only"
)


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _setting_locator(root: Path, profile_root: Path, raw_path: str) -> str:
    filename, separator, fragment = raw_path.partition("#")
    locator = _relative_path(root, profile_root / filename)
    return f"{locator}#{fragment}" if separator else locator


def _serialized_path_present(
    serialized_sources: Mapping[str, Mapping[str, Any] | None],
    raw_path: str,
) -> bool:
    filename, separator, fragment = raw_path.partition("#")
    source = serialized_sources.get(filename)
    if source is None:
        return False
    if not separator or not fragment:
        return True
    key = fragment.removeprefix("/")
    return key in source


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": "error"}


def _value_or_none(value: Any, *, expected_type: type = str) -> Any:
    return value if isinstance(value, expected_type) else None


def _policy_entry(
    policy: CoreSettingPolicy,
    *,
    profile: str,
    core_locator: str,
    core_flags: Mapping[str, Any] | None,
    serialized_sources: Mapping[str, Mapping[str, Any] | None],
    root: Path,
    profile_root: Path,
) -> dict[str, Any]:
    raw_enabled = core_flags.get(policy.plugin_id) if core_flags is not None else None
    enabled = raw_enabled if isinstance(raw_enabled, bool) else None
    serialized_paths = [_setting_locator(root, profile_root, path) for path in policy.serialized_paths]
    serialized_state = "configured" if serialized_paths and all(
        _serialized_path_present(serialized_sources, path) for path in policy.serialized_paths
    ) else "unknown" if policy.serialized_paths else "not_applicable"
    setting_state = "configured" if policy.serialized_paths and serialized_state == "configured" else "unknown"

    current_value: dict[str, Any] = {"enabled": enabled}
    if policy.plugin_id == "properties":
        app_settings = serialized_sources.get("app.json") or {}
        current_value["properties_in_document"] = _value_or_none(app_settings.get("propertiesInDocument"))
    elif policy.plugin_id == "daily-notes":
        daily_settings = serialized_sources.get("daily-notes.json") or {}
        current_value.update(
            {
                "folder": _value_or_none(daily_settings.get("folder")),
                "template": _value_or_none(daily_settings.get("template")),
            }
        )

    return {
        "component_id": f"core:{policy.plugin_id}",
        "component": policy.component,
        "owner": "core",
        "profile": profile,
        "profile_scope": policy.profile_scope,
        "policy_class": policy.policy_class,
        "workflow": policy.workflow,
        "source_locator": {
            "core_flag": f"{core_locator}#/{policy.plugin_id}",
            "serialized_settings": serialized_paths,
            "ui_labels": list(policy.ui_labels),
        },
        "current_value": current_value,
        "setting_state": setting_state,
        "serialized_state": serialized_state,
        "allowed_value_domain": policy.allowed_value_domain,
        "fallback": policy.fallback,
        "mutation_risk": policy.mutation_risk,
        "verification_method": "static_profile_then_authorized_ui_observation",
        "rollback_action": "restore_original_profile_bytes_and_preserve_note_bytes",
        "states": {
            "declared": "declared",
            "installed": "not_applicable",
            "configured": "configured" if enabled is not None else "unconfigured",
            "enabled": "enabled" if enabled is True else "disabled" if enabled is False else "unknown",
            "verified": "not_run",
            "healthy": "not_run",
            "fallback_available": "available",
        },
    }


def build_core_setting_registry(
    *,
    root: Path,
    profile: str,
    profile_root: Path,
    blueprint: Mapping[str, Any],
    core_flags: Mapping[str, Any] | None,
    serialized_sources: Mapping[str, Mapping[str, Any] | None],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P02 Core registry and return non-mutating policy errors."""

    errors: list[dict[str, str]] = []
    core_locator = _relative_path(root, profile_root / "core-plugins.json")
    observed_ids = sorted(core_flags) if core_flags is not None else []
    unknown_ids = sorted(set(observed_ids) - set(CORE_SETTING_POLICIES_BY_ID))
    for plugin_id in unknown_ids:
        errors.append(
            _error(
                "PLUGIN_CORE_POLICY_UNREGISTERED",
                f"{core_locator}#/{plugin_id}",
                "observed Core flag has no explicit P02 policy",
            )
        )

    entries = [
        _policy_entry(
            policy,
            profile=profile,
            core_locator=core_locator,
            core_flags=core_flags,
            serialized_sources=serialized_sources,
            root=root,
            profile_root=profile_root,
        )
        for policy in CORE_SETTING_POLICIES
    ]
    entries.extend(
        {
            "component_id": f"core:{plugin_id}",
            "component": plugin_id,
            "owner": "core",
            "profile": profile,
            "profile_scope": "unknown",
            "policy_class": "unresolved",
            "workflow": "unresolved",
            "source_locator": {
                "core_flag": f"{core_locator}#/{plugin_id}",
                "serialized_settings": [],
                "ui_labels": [],
            },
            "current_value": {"enabled": core_flags.get(plugin_id)},
            "setting_state": "unknown",
            "serialized_state": "unknown",
            "allowed_value_domain": "unknown",
            "fallback": "unknown",
            "mutation_risk": "unknown",
            "verification_method": "blocked_until_policy_review",
            "rollback_action": "preserve_original_profile_bytes",
            "states": {
                "declared": "not_declared",
                "installed": "not_applicable",
                "configured": "unknown",
                "enabled": "unknown",
                "verified": "not_run",
                "healthy": "not_run",
                "fallback_available": "unknown",
            },
        }
        for plugin_id in unknown_ids
    )

    plugin_profiles = blueprint.get("plugin_profiles", {})
    blueprint_all_devices = frozenset(plugin_profiles.get("core_all_devices", []))
    blueprint_mac_only = frozenset(plugin_profiles.get("core_mac_only", []))
    if blueprint_all_devices != _REQUIRED_ALL_DEVICE_NAMES:
        errors.append(
            _error(
                "PLUGIN_CORE_POLICY_BLUEPRINT_DRIFT",
                "blueprint/blueprint.yaml#/plugin_profiles/core_all_devices",
                "P02 required all-device Core policy does not match the Blueprint",
            )
        )
    if blueprint_mac_only != _REQUIRED_MAC_ONLY_NAMES:
        errors.append(
            _error(
                "PLUGIN_CORE_POLICY_BLUEPRINT_DRIFT",
                "blueprint/blueprint.yaml#/plugin_profiles/core_mac_only",
                "P02 required Mac-only Core policy does not match the Blueprint",
            )
        )

    daily_settings = serialized_sources.get("daily-notes.json") or {}
    observed_folder = _value_or_none(daily_settings.get("folder"))
    observed_template = _value_or_none(daily_settings.get("template"))
    if "propertiesInDocument" in (serialized_sources.get("app.json") or {}):
        properties_value = (serialized_sources.get("app.json") or {}).get("propertiesInDocument")
        if not isinstance(properties_value, str) or properties_value not in {"visible", "hidden"}:
            errors.append(
                _error(
                    "PLUGIN_CORE_PROPERTIES_SETTING_INVALID",
                    "KnowledgeHub/.obsidian-mac/app.json#/propertiesInDocument",
                    "Properties visibility must be visible or hidden",
                )
            )
    daily_values = (
        ("folder", daily_settings.get("folder")),
        ("template", daily_settings.get("template")),
    )
    for key, value in daily_values:
        if key in daily_settings and not isinstance(value, str):
            errors.append(
                _error(
                    "PLUGIN_CORE_DAILY_SETTING_INVALID",
                    f"KnowledgeHub/.obsidian-mac/daily-notes.json#/{key}",
                    "Daily Notes serialized setting must be a string",
                )
            )
    daily_expected = {
        "folder": "10_Journal/Daily",
        "date_format": "YYYY-MM-DD",
        "template": "99_System/Templates/T10_Daily.md",
    }
    daily_observed = {
        "folder": observed_folder,
        "template": observed_template,
        "date_format": None,
    }
    daily_configuration_state = (
        "configured"
        if observed_folder == daily_expected["folder"] and observed_template == daily_expected["template"]
        else "drifted"
        if observed_folder is not None or observed_template is not None
        else "unknown"
    )

    template_folder = blueprint.get("templates", {}).get("directory")
    if template_folder != "99_System/Templates":
        errors.append(
            _error(
                "PLUGIN_CORE_TEMPLATE_FOLDER_DRIFT",
                "blueprint/blueprint.yaml#/templates/directory",
                "P02 Core template folder does not match the canonical path",
            )
        )
    daily_note_contract = blueprint.get("note_types", {}).get("daily", {})
    if daily_note_contract.get("template") != "T10_Daily.md":
        errors.append(
            _error(
                "PLUGIN_CORE_DAILY_TEMPLATE_DRIFT",
                "blueprint/blueprint.yaml#/note_types/daily/template",
                "P02 Daily Notes template does not match T10_Daily.md",
            )
        )

    template_path = root / "KnowledgeHub" / daily_expected["template"]
    template_source_state = "unknown"
    if template_path.is_file() and not template_path.is_symlink():
        try:
            template_text = template_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            template_text = ""
        if "YYYY-MM-DD" in template_text:
            template_source_state = "pass"

    registry = {
        "schema_version": P02_CORE_REGISTRY_SCHEMA_VERSION,
        "profile": profile,
        "policy_classes": list(P02_POLICY_CLASSES),
        "observed_sources": {
            "core_flags": core_locator,
            "serialized_settings": [
                _relative_path(root, profile_root / filename)
                for filename, value in serialized_sources.items()
                if value is not None
            ],
        },
        "observed_core_flags": observed_ids,
        "unknown_observed_flags": unknown_ids,
        "entries": entries,
        "daily_notes_contract": {
            "owner": "core:daily-notes",
            "workflow": "daily_note_creation",
            "expected": daily_expected,
            "observed_serialized": daily_observed,
            "configuration_state": daily_configuration_state,
            "date_format_state": "unknown",
            "source_locator": {
                "folder": f"{_relative_path(root, profile_root / 'daily-notes.json')}#/folder",
                "template": f"{_relative_path(root, profile_root / 'daily-notes.json')}#/template",
                "date_format": "KnowledgeHub/99_System/Templates/T10_Daily.md",
            },
            "template_source_state": template_source_state,
            "fallback": "date_named_markdown_and_vaultctl_note_validate",
        },
        "core_templates_contract": {
            "owner": "core:templates",
            "expected_folder": "99_System/Templates",
            "source_locator": "blueprint/blueprint.yaml#/templates/directory",
            "setting_state": "unknown",
            "reason": "Core Templates folder setting is not serialized in the observed profile",
            "fallback": "canonical_markdown_templates_and_vaultctl_note_validate",
        },
        "mobile_core_policy": {
            "required_all_devices": sorted(blueprint_all_devices),
            "required_mac_only": sorted(blueprint_mac_only),
            "source_locator": {
                "all_devices": "blueprint/blueprint.yaml#/plugin_profiles/core_all_devices",
                "mac_only": "blueprint/blueprint.yaml#/plugin_profiles/core_mac_only",
            },
            "observed_from_mac_profile": False,
            "state": "not_inferred",
            "fallback": "plugin_free_markdown_yaml_and_vaultctl_surface",
        },
        "fallbacks": [
            {
                "component_id": "core:file-explorer",
                "fallback": "home_links_and_vaultctl_search",
                "state": "available",
            },
            {
                "component_id": "core:bases",
                "fallback": "canonical_markdown_and_vaultctl_search",
                "state": "available",
            },
            {
                "component_id": "core:daily-notes",
                "fallback": "date_named_markdown_and_vaultctl_note_validate",
                "state": "available",
            },
            {
                "component_id": "core:templates",
                "fallback": "canonical_markdown_templates_and_vaultctl_note_validate",
                "state": "available",
            },
        ],
        "evidence_boundaries": {
            "static": "observed",
            "semantic": "observed",
            "runtime": "not_run",
            "device": "not_run",
            "setting_mutation": "out_of_scope",
        },
    }
    return registry, errors

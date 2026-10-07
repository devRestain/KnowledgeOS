"""Read-only C12 diagnostics for the KnowledgeOS control surface.

The diagnostic commands deliberately observe the control repository, the
independent Vault repository, and the runtime layout without changing any of
them.  Optional deployment overlays are reported as inactive or deferred;
their absence is not silently promoted to a failed capability.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .ai_projection import load_current_ai_projection
from .blueprint import validate_blueprint
from .breadcrumbs_settings import build_breadcrumbs_setting_registry
from .core_settings import build_core_setting_registry
from .gui_contracts import inspect_gui_contract
from .homepage_settings import build_homepage_setting_registry
from .linter_settings import build_linter_setting_registry
from .meta_bind_settings import build_meta_bind_setting_registry
from .note_toolbar_settings import build_note_toolbar_setting_registry
from .notebook_navigator_settings import build_notebook_navigator_setting_registry
from .obsidian_git_settings import build_obsidian_git_setting_registry
from .paths import ResolvedPaths, RootResolutionError, load_config_file, resolve_paths
from .projection import load_current_projection
from .quickadd_settings import build_quickadd_setting_registry
from .runtime import RuntimeLayout, StateLayout
from .schema_export import export_schema_artifacts
from .tasks_settings import build_tasks_setting_registry
from .templater_settings import build_templater_setting_registry
from .yaml_safe import load_yaml_file

# Exit classes are stable API for scripts.  Argparse keeps its conventional 2
# for usage errors; these classes are reserved for command-level diagnostics.
EXIT_OK = 0
EXIT_DEGRADED = 4
EXIT_INPUT_INVALID = 10
EXIT_CONFIG_INVALID = 11
EXIT_GIT_INVALID = 12
EXIT_VALIDATION_FAILED = 13

_CORE_PLUGIN_IDS = {
    "Properties": "properties",
    "Bases": "bases",
    "Daily notes": "daily-notes",
    "Templates": "templates",
    "Search": "global-search",
    "Backlinks": "backlink",
    "Outgoing links": "outgoing-link",
    "Bookmarks": "bookmarks",
    "File recovery": "file-recovery",
    "Workspaces": "workspaces",
}

_P01_STATE_DIMENSIONS = (
    "declared",
    "installed",
    "configured",
    "enabled",
    "verified",
    "healthy",
    "fallback_available",
)

_P01_CORE_POLICIES = {
    "Properties": {
        "desired_policy": "preserve_properties_visibility",
        "allowed_value_domain": "visible|hidden|unknown",
        "dependency": "frontmatter_and_property_dictionary",
    },
    "Bases": {
        "desired_policy": "retain_declared_bases_workflow",
        "allowed_value_domain": "boolean",
        "dependency": "blueprint_base_contracts",
    },
    "Daily notes": {
        "desired_policy": "retain_core_daily_note_creation",
        "allowed_value_domain": "boolean",
        "dependency": "core_daily_note_workflow",
    },
    "Templates": {
        "desired_policy": "retain_core_template_access",
        "allowed_value_domain": "boolean",
        "dependency": "bounded_template_contract",
    },
    "Search": {
        "desired_policy": "retain_core_search_fallback",
        "allowed_value_domain": "boolean",
        "dependency": "plugin_free_retrieval_fallback",
    },
    "Backlinks": {
        "desired_policy": "retain_core_backlink_navigation",
        "allowed_value_domain": "boolean",
        "dependency": "typed_relation_navigation",
    },
    "Outgoing links": {
        "desired_policy": "retain_core_outgoing_link_navigation",
        "allowed_value_domain": "boolean",
        "dependency": "typed_relation_navigation",
    },
    "Bookmarks": {
        "desired_policy": "retain_core_bookmark_navigation",
        "allowed_value_domain": "boolean",
        "dependency": "review_and_navigation_fallback",
    },
    "File recovery": {
        "desired_policy": "retain_core_file_recovery",
        "allowed_value_domain": "boolean",
        "dependency": "safe_rollback",
    },
    "Workspaces": {
        "desired_policy": "retain_mac_workspace_layouts",
        "allowed_value_domain": "boolean",
        "dependency": "mac_only_layout_state",
    },
}

_P01_PLUGIN_POLICIES = {
    "quickadd": {
        "desired_policy": "allow_reviewed_capture_routes_only",
        "allowed_value_domain": "blueprint_owned_capture_targets",
        "dependency": "human_review",
        "mutation_risk": "write_capable_human_action",
    },
    "templater-obsidian": {
        "desired_policy": "allow_bounded_template_rendering_only",
        "allowed_value_domain": "verified_template_folder_and_mappings",
        "dependency": "bounded_template_contract",
        "mutation_risk": "write_capable_template_action",
    },
    "obsidian-tasks-plugin": {
        "desired_policy": "allow_read_oriented_task_queries",
        "allowed_value_domain": "bounded_query_and_explicit_completion",
        "dependency": "task_schema_and_status_conventions",
        "mutation_risk": "explicit_human_task_completion",
    },
    "obsidian-linter": {
        "desired_policy": "allow_explicit_rule_allowlist_only",
        "allowed_value_domain": "property_dictionary_compatible_rules",
        "dependency": "property_dictionary_and_templates",
        "mutation_risk": "bulk_note_rewrite",
    },
    "obsidian-git": {
        "desired_policy": "retain_manual_mac_git_ui_only",
        "allowed_value_domain": "manual_actions_with_automation_off",
        "dependency": "independent_git_status",
        "mutation_risk": "git_network_and_worktree_mutation",
    },
    "homepage": {
        "desired_policy": "allow_one_startup_target_with_plugin_free_fallback",
        "allowed_value_domain": "approved_note_or_workspace_target",
        "dependency": "canonical_home",
        "mutation_risk": "startup_and_auto_create",
    },
    "note-toolbar": {
        "desired_policy": "allow_reviewed_contextual_commands_only",
        "allowed_value_domain": "reviewed_targets_and_commands",
        "dependency": "human_review_and_rollback",
        "mutation_risk": "write_capable_human_action",
    },
    "breadcrumbs": {
        "desired_policy": "allow_blueprint_typed_relation_fields_only",
        "allowed_value_domain": "declared_relation_properties",
        "dependency": "typed_relation_contract",
        "mutation_risk": "relation_property_mutation",
    },
    "notebook-navigator": {
        "desired_policy": "allow_bounded_navigation_only",
        "allowed_value_domain": "declared_folders_tags_and_properties",
        "dependency": "file_explorer_fallback",
        "mutation_risk": "bulk_move_delete_or_property_action",
    },
    "obsidian-meta-bind-plugin": {
        "desired_policy": "allow_reviewed_property_views_and_inputs_only",
        "allowed_value_domain": "approved_properties_and_protected_paths",
        "dependency": "property_dictionary_and_human_review",
        "mutation_risk": "write_capable_property_control",
    },
    "knowledgeos-thin-client": {
        "desired_policy": "allow_authenticated_proposal_only_presentation",
        "allowed_value_domain": "loopback_broker_and_digest_bound_review",
        "dependency": "C41_broker_and_C19_review",
        "mutation_risk": "presentation_only_no_canonical_writer",
    },
}

_CAPABILITY_DIMENSIONS = (
    "declared",
    "configured",
    "reachable",
    "authorized",
    "verified",
    "enabled",
    "healthy",
)


def _capability(
    *,
    state: str,
    declared: str,
    configured: str,
    reachable: str,
    authorized: str,
    verified: str,
    enabled: str,
    healthy: str,
    reason: str,
    evidence_class: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Return one explicit capability state without collapsing evidence classes."""

    values = {
        "declared": declared,
        "configured": configured,
        "reachable": reachable,
        "authorized": authorized,
        "verified": verified,
        "enabled": enabled,
        "healthy": healthy,
    }
    if set(values) != set(_CAPABILITY_DIMENSIONS):
        raise AssertionError("capability dimensions drifted")
    return {
        "state": state,
        **values,
        "reason": reason,
        "evidence_class": evidence_class,
        "evidence": list(evidence or []),
    }


def _inactive_capability(
    *,
    reason: str,
    declared: bool = True,
    configured: str = "not_configured",
    state: str = "inactive",
    evidence_class: str = "static",
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return _capability(
        state=state,
        declared="declared" if declared else "not_declared",
        configured=configured,
        reachable="not_run",
        authorized="not_authorized",
        verified="not_run",
        enabled="disabled" if configured != "invalid" else "not_configured",
        healthy="not_ready",
        reason=reason,
        evidence_class=evidence_class,
        evidence=evidence,
    )


class DiagnosticError(ValueError):
    """A stable command-level diagnostic failure."""

    def __init__(self, code: str, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


ProjectRoots = ResolvedPaths


def _issue(code: str, locator: str, message: str, *, severity: str = "error") -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": severity}


def _exit_code_for_errors(errors: list[dict[str, str]]) -> int:
    """Map stable diagnostic codes to the public command exit classes."""

    codes = {item.get("code", "") for item in errors}
    if any(code.startswith("GIT_") for code in codes):
        return EXIT_GIT_INVALID
    if any(code.startswith(("CONFIG_", "PLUGIN_")) for code in codes):
        return EXIT_CONFIG_INVALID
    if any(code.startswith("ROOT_") for code in codes):
        return EXIT_INPUT_INVALID
    return EXIT_VALIDATION_FAILED


def _strict_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DiagnosticError(
            "DIAGNOSTIC_JSON_INVALID",
            f"invalid JSON at {path.name}: {error}",
            exit_code=EXIT_CONFIG_INVALID,
        ) from error


def _read_json_object(path: Path) -> dict[str, Any]:
    value = _read_json_value(path)
    if not isinstance(value, dict):
        raise DiagnosticError(
            "DIAGNOSTIC_JSON_ROOT_INVALID",
            f"JSON root must be an object: {path.name}",
            exit_code=EXIT_CONFIG_INVALID,
        )
    return value


def _read_community_plugin_ids(path: Path) -> list[str]:
    """Read Obsidian's profile-level community-plugins.json manifest.

    Obsidian stores this manifest as a top-level JSON array of plugin IDs.
    It is next to the profile's ``plugins/`` directory, not inside it.
    """

    if path.is_symlink() or not path.is_file():
        raise DiagnosticError(
            "PLUGIN_CONFIG_INVALID",
            f"community-plugins.json must be a regular file: {path.name}",
            exit_code=EXIT_CONFIG_INVALID,
        )
    value = _read_json_value(path)
    if not isinstance(value, list):
        raise DiagnosticError(
            "PLUGIN_CONFIG_ROOT_INVALID",
            f"community-plugins.json root must be an array of plugin IDs: {path.name}",
            exit_code=EXIT_CONFIG_INVALID,
        )
    if not all(isinstance(item, str) and item for item in value):
        raise DiagnosticError(
            "PLUGIN_CONFIG_INVALID",
            f"community-plugins.json must contain only non-empty string IDs: {path.name}",
            exit_code=EXIT_CONFIG_INVALID,
        )
    if len(set(value)) != len(value):
        raise DiagnosticError(
            "PLUGIN_CONFIG_INVALID",
            f"community-plugins.json must not contain duplicate plugin IDs: {path.name}",
            exit_code=EXIT_CONFIG_INVALID,
        )
    return sorted(value)


def discover_project_roots(root: str | Path | ProjectRoots) -> ProjectRoots:
    """Resolve and validate all root identities through the shared resolver."""

    if isinstance(root, ResolvedPaths):
        return root
    try:
        return resolve_paths(root)
    except RootResolutionError as error:
        raise DiagnosticError(error.code, str(error), exit_code=error.exit_code) from error


def load_strict_config(root: str | Path | ProjectRoots) -> dict[str, Any]:
    """Load strict config data while keeping path policy in ``paths.py``."""

    if isinstance(root, ResolvedPaths):
        return dict(root.config)
    path = Path(root).expanduser().resolve() / "ops/vaultops.toml"
    try:
        return load_config_file(path)
    except RootResolutionError as error:
        raise DiagnosticError(error.code, str(error), exit_code=error.exit_code) from error


def _run_git(repo: Path, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)
    # Do not strip Git porcelain output: its first two columns are the staged
    # and worktree state, and a leading space is meaningful.
    stdout = completed.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
    stderr = completed.stderr.decode("utf-8", errors="replace").rstrip("\r\n")
    return completed.returncode, stdout, stderr


def _git_repository_report(name: str, repo: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    code, git_root, _stderr = _run_git(repo, "rev-parse", "--show-toplevel")
    if code != 0:
        return (
            {"name": name, "path": str(repo), "status": "ERROR", "worktree": "unknown"},
            [_issue("GIT_REPOSITORY_INVALID", f"/git/{name}", "expected independent Git root is unavailable")],
        )
    if Path(git_root).resolve() != repo.resolve():
        errors.append(_issue("GIT_ROOT_MISMATCH", f"/git/{name}/root", "Git root is not the expected project boundary"))
    branch_code, branch, _branch_error = _run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    head_code, head, _head_error = _run_git(repo, "rev-parse", "HEAD")
    upstream_code, upstream, _ = _run_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    ahead = behind = None
    if upstream_code == 0 and upstream:
        counts_code, counts, _ = _run_git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        if counts_code == 0:
            values = counts.split()
            if len(values) == 2:
                ahead, behind = (int(values[0]), int(values[1]))
    status_code, status_text, _status_error = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status_code != 0:
        errors.append(_issue("GIT_STATUS_FAILED", f"/git/{name}/status", "Git status could not be read"))
        status_text = ""

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 3:
            continue
        state, path = line[:2], line[3:]
        if state == "??":
            untracked.append(path)
        else:
            if state[0] != " ":
                staged.append(path)
            if state[1] != " ":
                unstaged.append(path)
    remote_code, remote_url, _ = _run_git(repo, "config", "--get", "remote.origin.url")
    report = {
        "name": name,
        "path": str(repo),
        "git_root": git_root,
        "branch": branch if branch_code == 0 else "DETACHED",
        "head": head if head_code == 0 else None,
        "upstream": upstream if upstream_code == 0 else None,
        "ahead": ahead,
        "behind": behind,
        "remote_origin_configured": remote_code == 0 and bool(remote_url),
        "worktree": "dirty" if staged or unstaged or untracked else "clean",
        "staged_paths": sorted(staged),
        "unstaged_paths": sorted(unstaged),
        "untracked_paths": sorted(untracked),
        "status": "PASS" if not errors else "FAIL",
    }
    if branch_code != 0:
        report["branch_error"] = "detached_or_unavailable"
    if head_code != 0:
        errors.append(_issue("GIT_HEAD_UNAVAILABLE", f"/git/{name}/head", "Git HEAD could not be read"))
    return report, errors


def git_status_report(root: str | Path | ProjectRoots, repo: str = "both") -> tuple[dict[str, Any], int]:
    """Return clean/dirty Git state without touching either repository."""

    try:
        roots = discover_project_roots(root)
    except DiagnosticError as error:
        return {"status": "FAIL", "operation": "git status", "errors": [_issue(error.code, "/root", str(error))]}, error.exit_code
    selected = {"control": roots.control, "vault": roots.vault} if repo == "both" else {repo: getattr(roots, repo)}
    reports: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for name, path in selected.items():
        report, report_errors = _git_repository_report(name, path)
        reports[name] = report
        errors.extend(report_errors)
    result = {
        "operation": "git status",
        "mode": "read-only",
        "requested_repo": repo,
        "repositories": reports,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    return result, EXIT_OK if not errors else _exit_code_for_errors(errors)


def _p01_relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _p01_optional_object(
    path: Path,
    *,
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    if path.is_symlink():
        source_kind = {
            "core-plugins.json": "CORE_CONFIG",
            "manifest.json": "MANIFEST",
            "data.json": "DATA",
        }.get(path.name, "SETTING_SOURCE")
        errors.append(
            _issue(
                f"PLUGIN_{source_kind}_INVALID",
                _p01_relative_path(root, path),
                "serialized settings must not be a symlink",
            )
        )
        return None
    if not path.is_file():
        return None
    try:
        return _read_json_object(path)
    except DiagnosticError as error:
        source_kind = {
            "core-plugins.json": "CORE_CONFIG",
            "manifest.json": "MANIFEST",
            "data.json": "DATA",
        }.get(path.name, "SETTING_SOURCE")
        errors.append(
            _issue(
                f"PLUGIN_{source_kind}_INVALID",
                _p01_relative_path(root, path),
                str(error),
            )
        )
        return None


def _p01_registry(
    roots: ProjectRoots,
    *,
    profile: str,
    profile_root: Path,
    expected: list[dict[str, str]],
    installed: list[str],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a read-only P01 registry without exposing serialized values."""

    core_flags_path = profile_root / "core-plugins.json"
    core_flags = _p01_optional_object(core_flags_path, root=roots.control, errors=errors)
    serialized_sources: dict[str, dict[str, Any] | None] = {}
    for filename in (
        "app.json",
        "appearance.json",
        "hotkeys.json",
        "workspace.json",
        "daily-notes.json",
    ):
        serialized_sources[filename] = _p01_optional_object(
            profile_root / filename,
            root=roots.control,
            errors=errors,
        )

    entries: list[dict[str, Any]] = []
    core_locator = _p01_relative_path(roots.control, core_flags_path)
    serialized_locators = [
        _p01_relative_path(roots.control, profile_root / filename)
        for filename, value in serialized_sources.items()
        if value is not None
    ]
    for name, plugin_id in _CORE_PLUGIN_IDS.items():
        policy = _P01_CORE_POLICIES[name]
        enabled_value = core_flags.get(plugin_id) if core_flags is not None else None
        if enabled_value is not None and not isinstance(enabled_value, bool):
            errors.append(
                _issue(
                    "PLUGIN_CORE_FLAG_INVALID",
                    f"{core_locator}#/{plugin_id}",
                    f"core plugin flag must be boolean: {name}",
                )
            )
        enabled_state = (
            "enabled"
            if enabled_value is True
            else "disabled"
            if enabled_value is False
            else "unknown"
        )
        setting_paths = []
        if name == "Properties" and serialized_sources.get("app.json") is not None:
            app_settings = serialized_sources["app.json"] or {}
            if "propertiesInDocument" in app_settings:
                setting_paths.append("app.json#/propertiesInDocument")
        entries.append(
            {
                "component_id": f"core:{plugin_id}",
                "component": name,
                "owner": "core",
                "profile": profile,
                "source_locator": {
                    "core_flags": core_locator,
                    "serialized_settings": serialized_locators,
                },
                "current_value": {
                    "enabled": enabled_value if isinstance(enabled_value, bool) else None,
                    "observed_setting_paths": setting_paths,
                },
                "desired_policy": policy["desired_policy"],
                "allowed_value_domain": policy["allowed_value_domain"],
                "dependency": policy["dependency"],
                "fallback": "plugin_free_core_markdown_and_cli_surface",
                "mutation_risk": "core_profile_mutation",
                "verification_method": "authorized_observation_only",
                "rollback_action": "restore_original_profile_bytes",
                "states": {
                    "declared": "declared",
                    "installed": "not_applicable",
                    "configured": "configured" if enabled_value is not None else "unconfigured",
                    "enabled": enabled_state,
                    "verified": "not_run",
                    "healthy": "not_run",
                    "fallback_available": "available",
                },
            }
        )

    community_locator = _p01_relative_path(roots.control, profile_root / "community-plugins.json")
    for item in expected:
        plugin_id = item["id"]
        plugin_root = profile_root / "plugins" / plugin_id
        manifest_path = plugin_root / "manifest.json"
        data_path = plugin_root / "data.json"
        manifest = _p01_optional_object(manifest_path, root=roots.control, errors=errors)
        data = _p01_optional_object(data_path, root=roots.control, errors=errors)
        if manifest is not None and manifest.get("id") != plugin_id:
            errors.append(
                _issue(
                    "PLUGIN_MANIFEST_ID_MISMATCH",
                    _p01_relative_path(roots.control, manifest_path),
                    f"plugin manifest id does not match profile id: {plugin_id}",
                )
            )
        policy = _P01_PLUGIN_POLICIES.get(
            plugin_id,
            {
                "desired_policy": "unresolved",
                "allowed_value_domain": "unresolved",
                "dependency": "unresolved",
                "mutation_risk": "unresolved",
            },
        )
        entries.append(
            {
                "component_id": f"plugin:{plugin_id}",
                "component": plugin_id,
                "owner": plugin_id,
                "role": item["role"],
                "profile": profile,
                "source_locator": {
                    "community_manifest": community_locator,
                    "manifest": _p01_relative_path(roots.control, manifest_path),
                    "data": _p01_relative_path(roots.control, data_path),
                },
                "current_value": {
                    "manifest_version": manifest.get("version") if manifest is not None else None,
                    "serialized_keys": sorted(data) if data is not None else [],
                },
                "desired_policy": policy["desired_policy"],
                "allowed_value_domain": policy["allowed_value_domain"],
                "dependency": policy["dependency"],
                "fallback": "canonical_markdown_and_plugin_free_surface_available",
                "mutation_risk": policy["mutation_risk"],
                "verification_method": "authorized_observation_only",
                "rollback_action": "restore_original_plugin_data_bytes",
                "states": {
                    "declared": "declared",
                    "installed": "installed" if plugin_id in installed else "not_installed",
                    "configured": "configured" if data is not None else "unconfigured",
                    "enabled": "enabled" if plugin_id in installed else "disabled",
                    "verified": "not_run",
                    "healthy": "not_run" if not errors else "not_ready",
                    "fallback_available": "available",
                },
            }
        )

    blueprint = load_yaml_file(roots.control / "blueprint/blueprint.yaml")
    core_setting_registry, core_registry_errors = build_core_setting_registry(
        root=roots.control,
        profile=profile,
        profile_root=profile_root,
        blueprint=blueprint,
        core_flags=core_flags,
        serialized_sources=serialized_sources,
    )
    errors.extend(core_registry_errors)
    quickadd_setting_registry, quickadd_registry_errors = build_quickadd_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(quickadd_registry_errors)
    templater_setting_registry, templater_registry_errors = build_templater_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(templater_registry_errors)
    tasks_setting_registry, tasks_registry_errors = build_tasks_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(tasks_registry_errors)
    linter_setting_registry, linter_registry_errors = build_linter_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(linter_registry_errors)
    obsidian_git_setting_registry, obsidian_git_registry_errors = build_obsidian_git_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(obsidian_git_registry_errors)
    homepage_setting_registry, homepage_registry_errors = build_homepage_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(homepage_registry_errors)
    breadcrumbs_setting_registry, breadcrumbs_registry_errors = build_breadcrumbs_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(breadcrumbs_registry_errors)
    notebook_navigator_setting_registry, notebook_navigator_registry_errors = build_notebook_navigator_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(notebook_navigator_registry_errors)
    note_toolbar_setting_registry, note_toolbar_registry_errors = build_note_toolbar_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(note_toolbar_registry_errors)
    meta_bind_setting_registry, meta_bind_registry_errors = build_meta_bind_setting_registry(
        root=roots.control,
        profile_root=profile_root,
        blueprint=blueprint,
    )
    errors.extend(meta_bind_registry_errors)
    mobile_baseline = blueprint["plugin_profiles"].get("mobile_baseline", {})
    mobile_plugins = mobile_baseline.get("community_plugins", [])
    return {
        "schema_version": 1,
        "profile": profile,
        "state_dimensions": list(_P01_STATE_DIMENSIONS),
        "entries": entries,
        "core_setting_registry": core_setting_registry,
        "quickadd_setting_registry": quickadd_setting_registry,
        "templater_setting_registry": templater_setting_registry,
        "tasks_setting_registry": tasks_setting_registry,
        "linter_setting_registry": linter_setting_registry,
        "obsidian_git_setting_registry": obsidian_git_setting_registry,
        "homepage_setting_registry": homepage_setting_registry,
        "breadcrumbs_setting_registry": breadcrumbs_setting_registry,
        "notebook_navigator_setting_registry": notebook_navigator_setting_registry,
        "note_toolbar_setting_registry": note_toolbar_setting_registry,
        "meta_bind_setting_registry": meta_bind_setting_registry,
        "mobile_community_plugins": {
            "declared": list(mobile_plugins),
            "state": "empty" if not mobile_plugins else "degraded",
            "source_locator": "blueprint/blueprint.yaml#/plugin_profiles/mobile_baseline/community_plugins",
        },
        "fallback": "canonical_markdown_and_plugin_free_surface_available",
        "device_evidence": {
            "state": "not_run",
            "evidence_class": "device",
            "reason": "diagnostics_do_not_operate_obsidian",
        },
    }


def _plugin_audit(roots: ProjectRoots, profile: str = "mac") -> tuple[dict[str, Any], list[dict[str, str]]]:
    blueprint = load_yaml_file(roots.control / "blueprint/blueprint.yaml")
    profile_root = roots.vault / (".obsidian-mac" if profile == "mac" else ".obsidian")
    baseline = blueprint["plugin_profiles"]["mac_baseline"] if profile == "mac" else []
    expected = [{"id": item["id"], "role": item["role"]} for item in baseline]

    def unsafe_profile_path(code: str, locator: str, message: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
        return {
            "profile": profile,
            "profile_root": str(profile_root),
            "status": "FAIL",
            "profile_state": "invalid",
            "declared_capabilities": [
                {**item, "state": "invalid", "reason": "unsafe_profile_path"}
                for item in expected
            ],
            "unmanaged_plugins": [],
            "fallback": "canonical_markdown_and_plugin_free_surface_available",
            "setting_registry": {"schema_version": 1, "profile": profile, "entries": []},
            "gui_contract": inspect_gui_contract(roots.control, profile),
            "capability": _capability(
                state="degraded",
                declared="declared",
                configured="invalid",
                reachable="not_run",
                authorized="not_applicable",
                verified="not_run",
                enabled="unknown",
                healthy="not_ready",
                reason="unsafe_profile_path",
                evidence_class="static",
                evidence=[locator],
            ),
            "serialized_deployment_evidence": "invalid",
            "device_proof": {
                "state": "not_inferred",
                "evidence_class": "device",
                "reason": "diagnostics_do_not_operate_obsidian",
            },
        }, [_issue(code, locator, message)]

    if profile_root.is_symlink():
        return unsafe_profile_path(
            "PLUGIN_PROFILE_PATH_INVALID",
            "/.obsidian-mac" if profile == "mac" else "/.obsidian",
            "profile root must not be a symlink",
        )
    if not profile_root.is_dir():
        registry = _p01_registry(
            roots,
            profile=profile,
            profile_root=profile_root,
            expected=expected,
            installed=[],
            errors=[],
        )
        gui_contract = inspect_gui_contract(roots.control, profile)
        return {
            "profile": profile,
            "profile_root": str(profile_root),
            "status": "INACTIVE",
            "profile_state": "not_configured",
            "declared_capabilities": [
                {**item, "state": "inactive", "reason": "profile_missing"}
                for item in expected
            ],
            "unmanaged_plugins": [],
            "fallback": "canonical_markdown_and_plugin_free_surface_available",
            "setting_registry": registry,
            "gui_contract": gui_contract,
            "capability": _inactive_capability(
                reason="profile_missing",
                evidence_class="static",
                evidence=["blueprint/blueprint.yaml#/plugin_profiles", str(profile_root)],
            ),
            "serialized_deployment_evidence": "not_configured",
            "device_proof": {
                "state": "not_inferred",
                "evidence_class": "device",
                "reason": "diagnostics_do_not_operate_obsidian",
            },
        }, []
    errors: list[dict[str, str]] = []
    plugins_root = profile_root / "plugins"
    if plugins_root.is_symlink():
        return unsafe_profile_path(
            "PLUGIN_DATA_ROOT_INVALID",
            "/.obsidian-mac/plugins" if profile == "mac" else "/.obsidian/plugins",
            "declared plugin data root must not be a symlink",
        )
    for item in expected:
        plugin_root = plugins_root / item["id"]
        if plugin_root.is_symlink():
            return unsafe_profile_path(
                "PLUGIN_DATA_ROOT_INVALID",
                f"/plugins/{item['id']}",
                "declared plugin data root must not be a symlink",
            )
    community_path = profile_root / "community-plugins.json"
    installed: list[str] = []
    if community_path.exists() or community_path.is_symlink():
        try:
            installed = _read_community_plugin_ids(community_path)
        except DiagnosticError as error:
            errors.append(_issue(error.code, "/community-plugins.json", str(error)))
    declared_capabilities = []
    for item in expected:
        declared_capabilities.append(
            {**item, "state": "installed" if item["id"] in installed else "inactive"}
        )
    unmanaged = sorted(set(installed) - {item["id"] for item in expected})
    registry = _p01_registry(
        roots,
        profile=profile,
        profile_root=profile_root,
        expected=expected,
        installed=installed,
        errors=errors,
    )
    gui_contract = inspect_gui_contract(roots.control, profile)
    core_required = registry["core_setting_registry"].get("required_capabilities", {})
    core_gaps = core_required.get("unavailable", []) if isinstance(core_required, dict) else []
    required_present = (
        all(item["state"] == "installed" for item in declared_capabilities)
        and not core_gaps
    )
    audit_status = "FAIL" if errors else "PASS" if required_present else "DEGRADED"
    evidence_status = "invalid" if errors else "pass" if required_present else "degraded"
    result = {
        "profile": profile,
        "profile_root": str(profile_root),
        "status": audit_status,
        "profile_state": "configured",
        "declared_capabilities": declared_capabilities,
        "degraded_core_capabilities": list(core_gaps),
        "unmanaged_plugins": unmanaged,
        "fallback": "canonical_markdown_and_plugin_free_surface_available",
        "setting_registry": registry,
        "gui_contract": gui_contract,
        "capability": _capability(
            state="configured" if audit_status == "PASS" else "degraded",
            declared="declared",
            configured="configured" if not errors else "invalid",
            reachable="not_applicable",
            authorized="not_applicable",
            verified="not_run",
            enabled="enabled" if required_present else "disabled",
            healthy="not_run" if not errors else "not_ready",
            reason="filesystem_manifest_audited_without_device_probe",
            evidence_class="serialized_deployment",
            evidence=["KnowledgeHub/.obsidian-mac/community-plugins.json", "blueprint/blueprint.yaml#/plugin_profiles"],
        ),
        "serialized_deployment_evidence": evidence_status,
        "device_proof": {
            "state": "not_inferred",
            "evidence_class": "device",
            "reason": "filesystem_configuration_does_not_prove_plugin_execution",
        },
    }
    return result, errors


def plugins_audit_report(root: str | Path | ProjectRoots, profile: str = "mac") -> tuple[dict[str, Any], int]:
    try:
        roots = discover_project_roots(root)
        load_strict_config(roots)
        report, errors = _plugin_audit(roots, profile)
    except DiagnosticError as error:
        return {"operation": "plugins audit", "mode": "read-only", "status": "FAIL", "errors": [_issue(error.code, "/root", str(error))]}, error.exit_code
    except (OSError, TypeError, ValueError, KeyError):
        return {"operation": "plugins audit", "mode": "read-only", "status": "FAIL", "errors": [_issue("PLUGIN_AUDIT_FAILED", "/plugins", "plugin configuration could not be audited")]}, EXIT_CONFIG_INVALID
    result = {"operation": "plugins audit", "mode": "read-only", **report, "errors": errors}
    if errors:
        return result, _exit_code_for_errors(errors)
    if report["status"] == "DEGRADED":
        return result, EXIT_DEGRADED
    return result, EXIT_OK


def _projection_report(roots: ProjectRoots) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "projection_version": None,
        "generation_id": None,
        "usable": False,
        "source_verified": False,
        "state": "unavailable",
        "reason": "no_verified_current_projection",
        "local_ai": {"state": "unavailable", "generation_id": None},
        "remote_ai": {"state": "unavailable", "generation_id": None},
    }
    try:
        projection = load_current_projection(roots.control, verify_sources=True)
    except (OSError, TypeError, ValueError, KeyError) as error:
        errors.append(_issue("PROJECTION_INSPECTION_FAILED", "/projection", str(error), severity="warning"))
    else:
        manifest = projection.manifest
        report.update(
            {
                "projection_version": manifest.get("schema_version"),
                "generation_id": manifest.get("generation_id"),
                "usable": True,
                "source_verified": True,
                "state": "verified",
                "reason": "current_projection_verified",
            }
        )
    for profile in ("local", "remote"):
        try:
            projection = load_current_ai_projection(roots.control, profile=profile, verify_sources=True)
        except (OSError, TypeError, ValueError, KeyError):
            continue
        report[f"{profile}_ai"] = {
            "state": "verified",
            "generation_id": projection.manifest.get("generation_id"),
            "projection_version": projection.manifest.get("schema_version"),
            "source_verified": True,
        }
    return report, errors


def doctor_report(root: str | Path | ProjectRoots) -> tuple[dict[str, Any], int]:
    """Inspect current owner contracts, repositories, GUI profile, and index."""

    try:
        roots = discover_project_roots(root)
        config = load_strict_config(roots)
    except DiagnosticError as error:
        return {"operation": "doctor", "mode": "read-only", "status": "FAIL", "errors": [_issue(error.code, "/root", str(error))]}, error.exit_code

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    blueprint = validate_blueprint(roots.control)
    if not blueprint.passed:
        errors.append(_issue("BLUEPRINT_VALIDATION_FAILED", "/validation/blueprint", "Blueprint validation failed"))
    schema = export_schema_artifacts(roots.control, check=True)
    if not schema.passed:
        errors.append(_issue("SCHEMA_ZERO_DIFF_FAILED", "/validation/schema", "owned generated artifacts are not zero-diff"))
    runtime_problems = RuntimeLayout(roots.runtime).check()
    state_problems = StateLayout(roots.state).check()
    errors.extend(_issue("STATE_LAYOUT_INVALID", "/state", problem) for problem in state_problems)
    errors.extend(_issue("RUNTIME_LAYOUT_INVALID", "/runtime", problem) for problem in runtime_problems)
    control_git, control_errors = _git_repository_report("control", roots.control)
    vault_git, vault_errors = _git_repository_report("vault", roots.vault)
    errors.extend(control_errors)
    errors.extend(vault_errors)
    plugins, plugin_errors = _plugin_audit(roots, "mac")
    errors.extend(plugin_errors)
    projection, projection_warnings = _projection_report(roots)
    warnings.extend(projection_warnings)

    result = {
        "operation": "doctor",
        "mode": "read-only",
        "status": "PASS" if not errors else "FAIL",
        "roots": {"core": str(roots.core), "control": str(roots.control), "vault": str(roots.vault), "state": str(roots.state), "runtime": str(roots.runtime)},
        "config": {key: config[key] for key in sorted(config)},
        "validation": {
            "blueprint": blueprint.report.get("status", "FAIL"),
            "json_schema": blueprint.report.get("validation", {}).get("json_schema", "FAIL"),
            "semantic_validation": blueprint.report.get("validation", {}).get("semantic_validation", "FAIL"),
            "generated_artifacts": schema.report.get("status", "FAIL"),
        },
        "repositories": {"control": control_git, "vault": vault_git},
        "runtime": {"status": "PASS" if not runtime_problems else "FAIL", "problems": runtime_problems},
        "state": {"status": "PASS" if not state_problems else "FAIL", "problems": state_problems},
        "plugins": plugins,
        "projection": projection,
        "warnings": warnings,
        "errors": errors,
    }
    return result, EXIT_OK if not errors else _exit_code_for_errors(errors)

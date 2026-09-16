"""Read-only C12 diagnostics for the KnowledgeOS control surface.

The diagnostic commands deliberately observe the control repository, the
independent Vault repository, and the runtime layout without changing any of
them.  Optional deployment overlays are reported as inactive or deferred;
their absence is not silently promoted to a failed capability.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .blueprint import validate_blueprint
from .bridge_contract import remote_identity_sha256, validate_root_sentinel
from .runtime import RuntimeLayout
from .schema_export import export_schema_artifacts
from .yaml_safe import load_yaml_file

# Exit classes are stable API for scripts.  Argparse keeps its conventional 2
# for usage errors; these classes are reserved for command-level diagnostics.
EXIT_OK = 0
EXIT_DEGRADED = 4
EXIT_INPUT_INVALID = 10
EXIT_CONFIG_INVALID = 11
EXIT_GIT_INVALID = 12
EXIT_VALIDATION_FAILED = 13

_CONFIG_KEYS = frozenset(
    {"schema_version", "project_name", "control_root", "vault_root", "runtime_root", "timezone"}
)
_CONFIG_EXPECTED = {
    "schema_version": 1,
    "project_name": "KnowledgeOS",
    "control_root": "/workspace/control",
    "vault_root": "/workspace/KnowledgeHub",
    "runtime_root": "/workspace/runtime",
    "timezone": "Asia/Seoul",
}

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


class DiagnosticError(ValueError):
    """A stable command-level diagnostic failure."""

    def __init__(self, code: str, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class ProjectRoots:
    """Resolved roots discovered from one mounted control workspace."""

    control: Path
    vault: Path
    runtime: Path


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


def discover_project_roots(root: str | Path) -> ProjectRoots:
    """Resolve and validate the control/Vault/runtime root relationship."""

    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise DiagnosticError(
            "ROOT_INVALID",
            "--root must be an existing, non-symlink control directory",
            exit_code=EXIT_INPUT_INVALID,
        )
    control = candidate.resolve()
    required = {
        "blueprint/blueprint.yaml": control / "blueprint/blueprint.yaml",
        "ops/vaultops.toml": control / "ops/vaultops.toml",
        "KnowledgeHub": control / "KnowledgeHub",
        "runtime": control / "runtime",
    }
    for relative, path in required.items():
        if path.is_symlink() or not (path.is_file() if path.suffix else path.is_dir()):
            raise DiagnosticError(
                "ROOT_LAYOUT_INVALID",
                f"control root is missing a real {relative}",
                exit_code=EXIT_INPUT_INVALID,
            )
    return ProjectRoots(control=control, vault=(control / "KnowledgeHub").resolve(), runtime=(control / "runtime").resolve())


def load_strict_config(root: str | Path) -> dict[str, Any]:
    """Load the small deployment config and reject drift or unknown keys."""

    path = Path(root).resolve() / "ops/vaultops.toml"
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise DiagnosticError(
            "CONFIG_INVALID",
            f"cannot parse ops/vaultops.toml: {error}",
            exit_code=EXIT_CONFIG_INVALID,
        ) from error
    if set(config) != _CONFIG_KEYS:
        missing = sorted(_CONFIG_KEYS - set(config))
        extra = sorted(set(config) - _CONFIG_KEYS)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"unexpected={','.join(extra)}")
        raise DiagnosticError("CONFIG_SCHEMA_INVALID", "config key set drift: " + "; ".join(details), exit_code=EXIT_CONFIG_INVALID)
    for key, expected in _CONFIG_EXPECTED.items():
        if config.get(key) != expected:
            raise DiagnosticError(
                "CONFIG_DRIFT",
                f"config value drift at {key}: expected the project contract value",
                exit_code=EXIT_CONFIG_INVALID,
            )
    return config


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


def git_status_report(root: str | Path, repo: str = "both") -> tuple[dict[str, Any], int]:
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


def _sentinel_overlay(roots: ProjectRoots, vault_report: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    sentinel_path = roots.vault / ".knowledgeos-root.json"
    if sentinel_path.is_symlink():
        issue = _issue("SENTINEL_SYMLINK", "/overlays/git_identity_configured", "root sentinel must not be a symlink")
        return {"state": "error", "reason": "sentinel_symlink"}, [issue]
    if not sentinel_path.is_file():
        return {"state": "inactive", "reason": "sentinel_missing", "required_for": "git_identity_configured"}, []
    try:
        sentinel = _read_json_object(sentinel_path)
        blueprint = load_yaml_file(roots.control / "blueprint/blueprint.yaml")
        validation = validate_root_sentinel(sentinel, blueprint)
    except (DiagnosticError, OSError, TypeError, ValueError, KeyError) as error:
        issue = _issue("SENTINEL_INVALID", "/overlays/git_identity_configured", "root sentinel could not be validated")
        return {"state": "error", "reason": str(error)}, [issue]
    issues = [_issue(item.code, f"/overlays/git_identity_configured{item.locator}", item.message) for item in validation.issues]
    remote_code, remote_url, _ = _run_git(roots.vault, "config", "--get", "remote.origin.url")
    if remote_code != 0 or not remote_url:
        issues.append(_issue("GIT_REMOTE_MISSING", "/overlays/git_identity_configured", "Vault origin is not configured"))
    else:
        try:
            observed_sha = remote_identity_sha256(remote_url)
        except ValueError:
            issues.append(_issue("GIT_REMOTE_INVALID", "/overlays/git_identity_configured", "Vault origin is not a valid canonical GitHub identity"))
        else:
            if observed_sha != sentinel.get("remote_identity_sha256"):
                issues.append(_issue("GIT_REMOTE_IDENTITY_MISMATCH", "/overlays/git_identity_configured", "origin identity does not match the sentinel"))
    expected_branch = sentinel.get("expected_branch")
    if vault_report.get("branch") != expected_branch or vault_report.get("upstream") != f"origin/{expected_branch}":
        issues.append(_issue("GIT_BRANCH_BINDING_MISMATCH", "/overlays/git_identity_configured", "current branch/tracking ref does not match the sentinel"))
    if issues:
        return {"state": "error", "reason": "sentinel_or_git_binding_invalid"}, issues
    return {
        "state": "verified",
        "vault_uuid": sentinel.get("vault_uuid"),
        "canonical_vault_name": sentinel.get("canonical_vault_name"),
        "expected_branch": expected_branch,
        "remote_identity_sha256": sentinel.get("remote_identity_sha256"),
    }, []


def _plugin_audit(roots: ProjectRoots, profile: str = "mac") -> tuple[dict[str, Any], list[dict[str, str]]]:
    blueprint = load_yaml_file(roots.control / "blueprint/blueprint.yaml")
    profile_root = roots.vault / (".obsidian-mac" if profile == "mac" else ".obsidian")
    baseline = blueprint["plugin_profiles"]["mac_baseline"] if profile == "mac" else []
    expected = [{"id": item["id"], "role": item["role"]} for item in baseline]
    if not profile_root.is_dir() or profile_root.is_symlink():
        return {
            "profile": profile,
            "profile_root": str(profile_root),
            "status": "INACTIVE",
            "profile_state": "not_configured",
            "community_plugins": [{**item, "state": "inactive", "reason": "profile_missing"} for item in expected],
            "fallback": "canonical_markdown_and_plugin_free_surface_available",
        }, []
    errors: list[dict[str, str]] = []
    community_path = profile_root / "community-plugins.json"
    installed: list[str] = []
    if community_path.exists():
        try:
            installed = _read_community_plugin_ids(community_path)
        except DiagnosticError as error:
            errors.append(_issue(error.code, "/community-plugins.json", str(error)))
    result_plugins = []
    for item in expected:
        result_plugins.append({**item, "state": "installed" if item["id"] in installed else "inactive"})
    unexpected = sorted(set(installed) - {item["id"] for item in expected})
    result = {
        "profile": profile,
        "profile_root": str(profile_root),
        "status": "PASS" if not errors and all(item["state"] == "installed" for item in result_plugins) else "DEGRADED",
        "profile_state": "configured",
        "community_plugins": result_plugins,
        "unexpected_community_plugins": unexpected,
        "fallback": "canonical_markdown_and_plugin_free_surface_available",
    }
    return result, errors


def plugins_audit_report(root: str | Path, profile: str = "mac") -> tuple[dict[str, Any], int]:
    try:
        roots = discover_project_roots(root)
        load_strict_config(roots.control)
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


def _overlay_report(roots: ProjectRoots, vault_report: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    identity, errors = _sentinel_overlay(roots, vault_report)
    overlays: dict[str, Any] = {"git_identity_configured": identity}
    static = {
        "mobile_transport_verified": ("deferred", "MacBook-first order; device acceptance is a later user-participation slice"),
        "obsidian_mobile_profiles_verified": ("deferred", "device profile exact diff is not present"),
        "obsidian_mac_core_verified": ("inactive", "D02 Mac profile has not been configured"),
        "community_plugins_verified": ("inactive", "no approved community plugin profile is configured"),
        "codex_provider_verified": ("inactive", "provider overlay is not enabled"),
        "local_provider_verified": ("inactive", "local provider overlay is not enabled"),
        "live_bridge_roundtrip_verified": ("inactive", "D09 live device round-trip is not enabled"),
        "launchd_active": ("inactive", "LaunchAgent installation is outside the current scope"),
        "remote_lane_active": ("inactive", "unattended remote lane is not enabled"),
    }
    for name, (state, reason) in static.items():
        overlays[name] = {"state": state, "reason": reason}
    return overlays, errors


def doctor_report(root: str | Path) -> tuple[dict[str, Any], int]:
    """Run the complete provider-free C12 diagnostic report."""

    try:
        roots = discover_project_roots(root)
        config = load_strict_config(roots.control)
    except DiagnosticError as error:
        return {"operation": "doctor", "mode": "read-only", "status": "FAIL", "errors": [_issue(error.code, "/root", str(error))]}, error.exit_code

    errors: list[dict[str, str]] = []
    blueprint = validate_blueprint(roots.control)
    if not blueprint.passed:
        errors.append(_issue("BLUEPRINT_VALIDATION_FAILED", "/validation/blueprint", "Blueprint validation failed"))
    schema = export_schema_artifacts(roots.control, check=True)
    if not schema.passed:
        errors.append(_issue("SCHEMA_ZERO_DIFF_FAILED", "/validation/schema", "owned generated artifacts are not zero-diff"))
    runtime_problems = RuntimeLayout(roots.runtime).check()
    errors.extend(_issue("RUNTIME_LAYOUT_INVALID", "/runtime", problem) for problem in runtime_problems)
    control_git, control_errors = _git_repository_report("control", roots.control)
    vault_git, vault_errors = _git_repository_report("vault", roots.vault)
    errors.extend(control_errors)
    errors.extend(vault_errors)
    overlays, overlay_errors = _overlay_report(roots, vault_git)
    errors.extend(overlay_errors)
    plugins, plugin_errors = _plugin_audit(roots, "mac")
    errors.extend(plugin_errors)
    result = {
        "operation": "doctor",
        "mode": "read-only",
        "status": "PASS" if not errors else "FAIL",
        "roots": {"control": str(roots.control), "vault": str(roots.vault), "runtime": str(roots.runtime)},
        "config": {key: config[key] for key in sorted(config)},
        "capability": {
            "current_profile": "portable_core",
            "session_slice": "C12",
            "next_profile": "offline_non_llm_automation",
        },
        "validation": {
            "blueprint": blueprint.report.get("status", "FAIL"),
            "json_schema": blueprint.report.get("validation", {}).get("json_schema", "FAIL"),
            "semantic_validation": blueprint.report.get("validation", {}).get("semantic_validation", "FAIL"),
            "generated_artifacts": schema.report.get("status", "FAIL"),
        },
        "repositories": {"control": control_git, "vault": vault_git},
        "runtime": {"status": "PASS" if not runtime_problems else "FAIL", "problems": runtime_problems},
        "overlays": overlays,
        "plugins": plugins,
        "errors": errors,
    }
    return result, EXIT_OK if not errors else _exit_code_for_errors(errors)

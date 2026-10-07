"""Read-only P07 Obsidian Git manual-Mac contract inspection.

P07 treats Obsidian Git as a local Mac presentation and manual Git surface.
Automatic synchronization, implicit pull or push, and identity or remote
mutation stay outside the contract.  A local status display never becomes
evidence that a remote repository is synchronized.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

P07_OBSIDIAN_GIT_REGISTRY_SCHEMA_VERSION = 1
P07_PLUGIN_ID = "obsidian-git"

_AUTO_INTERVALS = (
    "autoSaveInterval",
    "autoPushInterval",
    "autoPullInterval",
)
_AUTO_BOOLEAN_POLICIES = {
    "autoPullOnBoot": "pull on boot",
    "autoBackupAfterFileChange": "file-change backup",
    "pullBeforePush": "pull before push",
    "differentIntervalCommitAndPush": "separate automatic commit and push intervals",
}
_UNATTENDED_BOOLEAN_POLICIES = {
    "updateSubmodules": "submodule update",
    "submoduleRecurseCheckout": "recursive submodule checkout",
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


def _bool_setting(value: Any, *, expected: bool | None = None) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if not isinstance(value, bool):
        return {"observed": "invalid", "state": "invalid"}
    if expected is None:
        return {"observed": value, "state": "observed"}
    return {"observed": value, "state": "pass" if value is expected else "drift"}


def _zero_interval(value: Any) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown", "policy": "must_be_zero"}
    if isinstance(value, bool) or not isinstance(value, int):
        return {"observed": "invalid", "state": "invalid", "policy": "must_be_zero"}
    return {
        "observed": value,
        "state": "disabled" if value == 0 else "configured",
        "policy": "must_be_zero",
    }


def _empty_string(value: Any, *, policy: str) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown", "policy": policy}
    if not isinstance(value, str):
        return {"observed": "invalid", "state": "invalid", "policy": policy}
    return {"observed": value, "state": "empty" if value == "" else "configured", "policy": policy}


def _blueprint_contract(blueprint: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    """Validate the current GUI Git boundary without assuming a mobile writer."""

    profiles = blueprint.get("plugin_profiles")
    profiles = profiles if isinstance(profiles, dict) else {}
    mac_baseline = profiles.get("mac_baseline")
    mac_baseline = mac_baseline if isinstance(mac_baseline, list) else []
    git_entry = next((item for item in mac_baseline if isinstance(item, dict) and item.get("id") == P07_PLUGIN_ID), None)
    forbidden = profiles.get("forbidden_defaults")
    forbidden = forbidden if isinstance(forbidden, list) else []
    repositories = blueprint.get("repositories")
    repositories = repositories if isinstance(repositories, dict) else {}
    vault = repositories.get("vault")
    vault = vault if isinstance(vault, dict) else {}

    checks = {
        "manual_mac_git_ui": git_entry is not None and git_entry.get("role") == "manual_mac_git_ui",
        "automatic_sync_disabled": {"obsidian_git_auto_pull", "obsidian_git_auto_commit", "obsidian_git_auto_push"}.issubset(set(forbidden)),
        "independent_vault": vault.get("independent_git_repository") is True,
    }
    for name, passed in checks.items():
        if not passed:
            errors.append(
                _error(
                    "P07_BLUEPRINT_GIT_BOUNDARY_DRIFT",
                    f"blueprint/blueprint.yaml#/plugin_profiles/{name}",
                    f"P07 Git boundary requirement {name} is missing",
                )
            )

    return {
        "state": "pass" if all(checks.values()) else "blocked",
        "checks": checks,
        "source": "blueprint/blueprint.yaml#/plugin_profiles /repositories/vault",
    }


def _automation_policy(data: dict[str, Any], data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    intervals = {name: _zero_interval(data.get(name)) for name in _AUTO_INTERVALS}
    for name, record in intervals.items():
        if record["state"] in {"configured", "invalid"}:
            errors.append(
                _error(
                    "P07_AUTOMATIC_INTERVAL_ENABLED",
                    _relative(root, data_path) + f"#/{name}",
                    f"{name} must remain zero for the manual-Mac contract",
                )
            )

    booleans: dict[str, Any] = {}
    for name, label in {**_AUTO_BOOLEAN_POLICIES, **_UNATTENDED_BOOLEAN_POLICIES}.items():
        record = _bool_setting(data.get(name), expected=False)
        record["policy"] = f"must remain disabled: {label}"
        booleans[name] = record
        if record["state"] in {"drift", "invalid"}:
            errors.append(
                _error(
                    "P07_UNATTENDED_BOOLEAN_ENABLED",
                    _relative(root, data_path) + f"#/{name}",
                    f"{name} must remain disabled for the manual-Mac contract",
                )
            )

    scripts = _empty_string(data.get("commitMessageScript"), policy="manual commit message scripts remain empty")
    if scripts["state"] in {"configured", "invalid"}:
        errors.append(
            _error(
                "P07_COMMIT_SCRIPT_NOT_ACCEPTED",
                _relative(root, data_path) + "#/commitMessageScript",
                "commitMessageScript must remain empty",
            )
        )
    return {
        "intervals": intervals,
        "booleans": booleans,
        "commit_message_script": scripts,
        "auto_commit_only_staged": _bool_setting(data.get("autoCommitOnlyStaged"), expected=None),
        "policy": "automatic save commit push pull backup and unattended sync remain disabled",
    }


def build_obsidian_git_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P07 registry from the installed Obsidian Git profile."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/obsidian-git/manifest.json"
    data_path = profile_root / "plugins/obsidian-git/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P07_GIT_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P07_GIT_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P07_GIT_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Obsidian Git manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P07_GIT_DATA_ROOT_INVALID", _relative(root, data_path), "Obsidian Git data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P07_PLUGIN_ID):
        errors.append(_error("P07_GIT_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Obsidian Git manifest id does not match the installed plugin id"))

    serialized = data if isinstance(data, dict) else {}
    automation = _automation_policy(serialized, data_path, root, errors)
    blueprint_contract = _blueprint_contract(blueprint, errors)

    status_bar = _bool_setting(serialized.get("showStatusBar"), expected=None)
    branch_status_bar = _bool_setting(serialized.get("showBranchStatusBar"), expected=None)
    file_menu = _bool_setting(serialized.get("showFileMenu"), expected=None)
    refresh_source_control = _bool_setting(serialized.get("refreshSourceControl"), expected=None)

    disable_push = _bool_setting(serialized.get("disablePush"), expected=None)
    refresh_timer = serialized.get("refreshSourceControlTimer")
    refresh_timer_policy = {
        "observed": refresh_timer,
        "state": "configured_local_refresh" if isinstance(refresh_timer, int) and not isinstance(refresh_timer, bool) and refresh_timer > 0 else "unknown" if refresh_timer is None else "invalid",
        "policy": "local source-control refresh only; no network synchronization evidence",
    }
    registry = {
        "schema_version": P07_OBSIDIAN_GIT_REGISTRY_SCHEMA_VERSION,
        "plugin": P07_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "blueprint_contract": blueprint_contract,
        "automation_policy": automation,
        "local_status_visibility": {
            "status_bar": status_bar,
            "branch_status_bar": branch_status_bar,
            "file_menu_for_staged_review": file_menu,
            "refresh_source_control": refresh_source_control,
            "refresh_timer": refresh_timer_policy,
            "network_calls": "not_invoked_by_status_contract",
            "remote_sync_claim": "forbidden_from_local_status_only",
        },
        "commit_policy": {
            "manual_commit": "human_reviewed_exact_path_commit",
            "auto_commit": "disabled_by_zero_interval_or_absent_setting",
            "commit_message": serialized.get("commitMessage"),
            "auto_commit_message": serialized.get("autoCommitMessage"),
            "commit_message_script": automation["commit_message_script"],
            "staged_set_review": "required_before_manual_commit",
            "identity_mutation": "out_of_scope",
            "runtime_evidence": "not_run",
        },
        "branch_policy": {
            "display": "manual_local_branch_display",
            "expected_branch_source": "KnowledgeHub/.knowledgeos-root.json",
            "branch_mutation": "out_of_scope",
            "runtime_evidence": "not_run",
        },
        "remote_policy": {
            "pull": {
                "control": "manual_human_action",
                "network_authorization": "required_separately",
                "auto_pull": "disabled",
                "runtime_evidence": "not_run",
            },
            "push": {
                "control": "disabled_by_profile" if disable_push.get("observed") is True else "manual_control_requires_authorization",
                "disable_push": disable_push,
                "network_authorization": "required_separately",
                "runtime_evidence": "not_run",
            },
            "successful_local_status_does_not_prove_remote_sync": True,
            "remote_write": "out_of_scope",
        },
        "fallback": {
            "ordinary_local_git": "available_only_as_explicit_human_action",
            "no_git_operation": "valid",
            "automatic_recovery": "not_authorized",
        },
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if not errors else "blocked",
            "runtime": "not_run",
            "network": "not_run",
            "device": "not_run",
            "git_identity": "not_run",
            "remote_sync": "not_run",
            "plugin_data_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P07_OBSIDIAN_GIT_REGISTRY_SCHEMA_VERSION",
    "P07_PLUGIN_ID",
    "build_obsidian_git_setting_registry",
]

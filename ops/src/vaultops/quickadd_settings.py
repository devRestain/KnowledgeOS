"""Read-only P03 QuickAdd capture-routing contract inspection.

P03 describes the desired QuickAdd surface from the Blueprint and compares it
with the installed Mac profile.  It never edits plugin data, creates notes, or
turns a missing QuickAdd choice into a guessed command ID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

P03_QUICKADD_REGISTRY_SCHEMA_VERSION = 1
P03_PRIMARY_CHOICES = (
    "CAPTURE_THOUGHT",
    "NEW_IDEA",
    "NEW_PROJECT",
    "NEW_QUESTION",
    "NEW_KNOWLEDGE",
)
P03_CANONICAL_TEMPLATE_FOLDER = "99_System/Templates"
P03_HOTKEY_ORDER = P03_PRIMARY_CHOICES
_SAFE_TARGET_PATTERN = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_./\- YYYYMMDDHhmstitle]+$")


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


def _home_quick_capture(blueprint: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    dashboards = blueprint.get("dashboards")
    home = dashboards.get("home") if isinstance(dashboards, dict) else None
    sections = home.get("sections") if isinstance(home, dict) else None
    section = next(
        (item for item in sections if isinstance(item, dict) and item.get("name") == "quick_capture"),
        None,
    ) if isinstance(sections, list) else None
    actions = section.get("actions") if isinstance(section, dict) else None
    hotkeys = section.get("hotkeys") if isinstance(section, dict) else None
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        errors.append(_error("P03_HOME_CAPTURE_ACTIONS_INVALID", "/dashboards/home/sections/quick_capture/actions", "Home quick-capture actions must be a string list"))
        actions = []
    if not isinstance(hotkeys, list) or not all(isinstance(item, str) for item in hotkeys):
        errors.append(_error("P03_HOME_CAPTURE_HOTKEYS_INVALID", "/dashboards/home/sections/quick_capture/hotkeys", "Home quick-capture hotkeys must be a string list"))
        hotkeys = []
    if list(actions) != list(P03_PRIMARY_CHOICES):
        errors.append(_error("P03_HOME_CAPTURE_ACTIONS_DRIFT", "/dashboards/home/sections/quick_capture/actions", "Home quick-capture actions must match the P03 primary choice order"))
    if len(hotkeys) != len(actions):
        errors.append(_error("P03_HOME_CAPTURE_HOTKEYS_DRIFT", "/dashboards/home/sections/quick_capture/hotkeys", "Home quick-capture hotkeys must have one entry per action"))
    return list(actions), list(hotkeys), errors


def _template_observation(root: Path, template_folder: str, template_name: str) -> dict[str, Any]:
    path = root / "KnowledgeHub" / template_folder / template_name
    safe = template_folder == P03_CANONICAL_TEMPLATE_FOLDER and not Path(template_name).is_absolute() and Path(template_name).name == template_name
    vault_present = (root / "KnowledgeHub").is_dir()
    exists = safe and path.is_file() and not path.is_symlink()
    state = "pass" if exists else "blocked" if not safe else "unknown"
    return {
        "source": _relative(root, path),
        "state": state if vault_present else "unknown",
        "canonical_folder": template_folder,
        "template": template_name,
    }


def _fallback(choice_id: str) -> str:
    return {
        "CAPTURE_THOUGHT": "vaultctl capture text --stdin --device mac",
        "NEW_PROJECT": "vaultctl project create",
        "NEW_IDEA": "vaultctl note create --type idea",
        "NEW_QUESTION": "vaultctl note create --type question",
        "NEW_KNOWLEDGE": "vaultctl note create --type knowledge",
    }[choice_id]


def build_quickadd_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P03 registry from Blueprint and serialized plugin evidence."""

    errors: list[dict[str, str]] = []
    data_path = profile_root / "plugins/quickadd/data.json"
    manifest_path = profile_root / "plugins/quickadd/manifest.json"
    data, data_error = _read_json(data_path)
    manifest, manifest_error = _read_json(manifest_path)
    if data_error not in (None, "missing"):
        errors.append(_error("P03_QUICKADD_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest_error not in (None, "missing"):
        errors.append(_error("P03_QUICKADD_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P03_QUICKADD_DATA_ROOT_INVALID", _relative(root, data_path), "QuickAdd data root must be an object"))
        data = None
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P03_QUICKADD_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "QuickAdd manifest root must be an object"))
        manifest = None

    blueprint_choices = blueprint.get("quickadd_choices")
    if not isinstance(blueprint_choices, dict):
        errors.append(_error("P03_BLUEPRINT_CHOICES_INVALID", "blueprint/blueprint.yaml#/quickadd_choices", "Blueprint QuickAdd choices must be an object"))
        blueprint_choices = {}
    actions, hotkeys, home_errors = _home_quick_capture(blueprint)
    errors.extend(home_errors)
    hotkey_map = dict(zip(actions, hotkeys, strict=False))
    template_folder = blueprint.get("templates", {}).get("directory") if isinstance(blueprint.get("templates"), dict) else None
    if template_folder != P03_CANONICAL_TEMPLATE_FOLDER:
        errors.append(_error("P03_TEMPLATE_FOLDER_DRIFT", "blueprint/blueprint.yaml#/templates/directory", "P03 requires the canonical template folder"))
        template_folder = str(template_folder or "")

    raw_choices = data.get("choices") if isinstance(data, dict) else None
    choices_state = "unconfigured" if raw_choices == [] else "unknown" if raw_choices is None else "observed"
    observed_choice_ids: list[str] = []
    if isinstance(raw_choices, list):
        for item in raw_choices:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name:
                    observed_choice_ids.append(name)
            elif isinstance(item, str) and item:
                observed_choice_ids.append(item)
    elif raw_choices is not None:
        errors.append(_error("P03_QUICKADD_CHOICES_INVALID", f"{_relative(root, data_path)}#/choices", "QuickAdd choices must be an array"))

    entries: list[dict[str, Any]] = []
    for choice_id in P03_PRIMARY_CHOICES:
        declared = blueprint_choices.get(choice_id)
        if not isinstance(declared, dict):
            errors.append(_error("P03_CHOICE_MISSING", f"blueprint/blueprint.yaml#/quickadd_choices/{choice_id}", "required primary QuickAdd choice is missing"))
            declared = {}
        template = declared.get("template")
        target = declared.get("target_pattern")
        if not isinstance(template, str) or not template:
            errors.append(_error("P03_CHOICE_TEMPLATE_INVALID", f"blueprint/blueprint.yaml#/quickadd_choices/{choice_id}/template", "choice template must be a non-empty string"))
            template = ""
        if not isinstance(target, str) or not target or _SAFE_TARGET_PATTERN.fullmatch(target) is None:
            errors.append(_error("P03_CHOICE_TARGET_INVALID", f"blueprint/blueprint.yaml#/quickadd_choices/{choice_id}/target_pattern", "choice target pattern must be a safe relative pattern"))
            target = str(target or "")
        template_observation = _template_observation(root, str(template_folder), template)
        if template_observation["state"] == "blocked":
            errors.append(_error("P03_CHOICE_TEMPLATE_MISSING", template_observation["source"], "declared QuickAdd template is not an ordinary canonical template file"))
        observed_choice = choice_id in observed_choice_ids
        entries.append(
            {
                "choice_id": choice_id,
                "command_identity": {
                    "choice_id": choice_id,
                    "installed_command_id": None,
                    "state": "not_observed" if not observed_choice else "unresolved_until_exact_choice_shape_is_inspected",
                },
                "template": template,
                "template_observation": template_observation,
                "target_pattern": target,
                "create_only": True,
                "overwrite_existing": False,
                "collision_behavior": "abort_and_preserve_existing_bytes",
                "prompt_fields": {
                    "state": "not_observed" if not observed_choice else "unresolved_until_exact_choice_shape_is_inspected",
                    "fields": [],
                },
                "hotkey": hotkey_map.get(choice_id),
                "hotkey_source": "blueprint/blueprint.yaml#/dashboards/home/sections/quick_capture/hotkeys",
                "current_choice_state": "configured" if observed_choice else choices_state,
                "plugin_free_fallback": _fallback(choice_id),
                "human_review_required": True,
            }
        )

    secondary = sorted(set(blueprint_choices) - set(P03_PRIMARY_CHOICES))
    registry = {
        "schema_version": P03_QUICKADD_REGISTRY_SCHEMA_VERSION,
        "plugin": "quickadd",
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "canonical_template_folder": {
            "expected": P03_CANONICAL_TEMPLATE_FOLDER,
            "observed": data.get("templateFolderPath") if isinstance(data, dict) else None,
            "state": "pass"
            if isinstance(data, dict) and data.get("templateFolderPath") == P03_CANONICAL_TEMPLATE_FOLDER
            else "unknown",
        },
        "safety_policy": {
            "disable_online_features": data.get("disableOnlineFeatures") if isinstance(data, dict) else None,
            "dev_mode": data.get("devMode") if isinstance(data, dict) else None,
            "ai_policy": "disabled_by_online_feature_policy",
            "uri_callbacks": "forbidden",
            "shell_and_system_execution": "forbidden",
            "arbitrary_path_selection": "forbidden",
        },
        "primary_choices": entries,
        "secondary_choices": {
            "declared": secondary,
            "state": "not_registered",
            "reason": "P03 registers only the five Home actions without a separate human-approved target decision",
        },
        "observed_choices": {
            "state": choices_state,
            "ids": sorted(set(observed_choice_ids)),
            "required_primary_actions": list(P03_PRIMARY_CHOICES),
            "runtime_and_device_evidence": "not_run",
        },
        "fallback": "canonical_vaultctl_and_Markdown_templates",
        "evidence_boundaries": {
            "static": "observed",
            "semantic": "observed",
            "runtime": "not_run",
            "device": "not_run",
            "plugin_data_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P03_CANONICAL_TEMPLATE_FOLDER",
    "P03_PRIMARY_CHOICES",
    "P03_QUICKADD_REGISTRY_SCHEMA_VERSION",
    "build_quickadd_setting_registry",
]

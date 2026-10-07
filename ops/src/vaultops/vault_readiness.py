"""Read-only checks for the explicitly owned generated KnowledgeHub projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from .adapters.core import CoreContracts, load_json
from .bootstrap import _directory_targets
from .note_engine import NoteEngine, parse_frontmatter, validate_path_collisions
from .paths import ResolvedPaths, resolve_api_paths, resolve_beneath
from .vault_artifacts import PROPERTY_DICTIONARY_PATH, VaultArtifactCheckResult
from .vault_identity import validate_root_sentinel
from .vault_projection import (
    CLIENT_DIRECTORY,
    CONTRACT_PATH,
    expected_projection,
    json_bytes,
)
from .yaml_safe import load_yaml_file

PROFILE_GUARDS = {
    ".obsidian-mac/plugins/obsidian-git/data.json": {"autoSaveInterval": 0, "autoPushInterval": 0, "autoPullInterval": 0, "autoPullOnBoot": False, "autoBackupAfterFileChange": False, "disablePush": True, "commitMessageScript": ""},
    ".obsidian-mac/plugins/obsidian-linter/data.json": {"lintOnSave": False, "lintOnFileChange": False, "lintCommands": [], "foldersToIgnore": ["99_System"]},
    ".obsidian-mac/plugins/templater-obsidian/data.json": {"trigger_on_file_creation": False, "enable_system_commands": False, "shell_path": "", "user_scripts_folder": "", "startup_templates": []},
    ".obsidian-mac/plugins/obsidian-meta-bind-plugin/data.json": {"enableJs": False, "ignoreCodeBlockRestrictions": False},
    ".obsidian-mac/plugins/note-toolbar/data.json": {"scriptingEnabled": False},
    ".obsidian-mac/plugins/notebook-navigator/data.json": {"downloadExternalFeatureImages": False, "checkForUpdatesOnStart": False},
    ".obsidian-mac/plugins/quickadd/data.json": {"disableOnlineFeatures": True},
    f"{CLIENT_DIRECTORY}/data.json": {"ownerEndpoint": "http://127.0.0.1:17900/owner", "lastWorkRunId": ""},
    ".obsidian-mac/core-plugins.json": {"sync": False, "publish": False},
    ".obsidian/core-plugins.json": {"sync": False, "publish": False},
}


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _profile_content_matches(expected: Any, observed: Any) -> bool:
    """Compare owned settings while preserving app-added keys and serialization."""
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            key in observed and _profile_content_matches(value, observed[key])
            for key, value in expected.items()
        )
    return json_bytes(expected) == json_bytes(observed)


def _files(root: Path, errors: list[dict[str, str]]) -> list[Path]:
    result = []
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in list(directories):
            path = Path(parent) / name
            if path.is_symlink():
                errors.append(_issue("VAULT_UNSAFE_PATH", path.relative_to(root).as_posix(), "directory symlink is outside readiness scope"))
                directories.remove(name)
        for name in sorted(names):
            path = Path(parent) / name
            if path.is_symlink():
                errors.append(_issue("VAULT_UNSAFE_PATH", path.relative_to(root).as_posix(), "file symlink is outside readiness scope"))
            elif path.is_file():
                result.append(path)
    return result


def _links(vault: Path, files: list[Path], errors: list[dict[str, str]]) -> int:
    count = 0
    for path in files:
        relative = path.relative_to(vault).as_posix()
        if path.suffix != ".md" or relative.startswith(("99_System/Templates/", ".obsidian")) or path.name == "AGENTS.md":
            continue
        text = re.sub(r"(?ms)^```.*?^```[^\n]*$", "", path.read_text())
        text = re.sub(r"`[^`\n]*`", "", text)
        for match in re.finditer(r"\[\[([^\]\n]+)\]\]", text):
            link = match.group(1).split("|", 1)[0]
            name, _, fragment = link.partition("#")
            if not name:
                target = path
            else:
                if "\\" in name or name.startswith("/") or any(part in {".", ".."} for part in name.split("/")):
                    errors.append(_issue("VAULT_LINK_UNSAFE", relative, "wikilink must stay inside the Vault"))
                    continue
                try:
                    target = resolve_beneath(vault, name if Path(name).suffix else name + ".md")
                except ValueError:
                    errors.append(_issue("VAULT_LINK_UNSAFE", relative, "wikilink crosses an unsafe path"))
                    continue
                if not target.is_file() and "/" not in name:
                    matches = [item for item in files if item.stem == name and item.suffix == ".md"]
                    if len(matches) == 1:
                        target = matches[0]
            if not target.is_file():
                errors.append(_issue("VAULT_LINK_MISSING", relative, f"wikilink target is missing: {name}"))
                continue
            if fragment and target.suffix == ".base":
                views = {view["name"] for view in load_yaml_file(target).get("views", [])}
                if fragment not in views:
                    errors.append(_issue("VAULT_BASE_VIEW_MISSING", relative, "embedded Base view is missing"))
                    continue
            count += 1
    return count


def check_vault_readiness(control_root: str | Path | ResolvedPaths) -> VaultArtifactCheckResult:
    """Validate maintained copies and meaning without writing or dispatching."""
    errors: list[dict[str, str]] = []
    artifacts: list[dict[str, Any]] = []
    report: dict[str, Any] = {"status": "FAIL", "mode": "read_only", "vault_scope": "declared_knowledgeos_generated_projection", "artifacts": artifacts, "errors": errors, "operational_adoption": "not_run"}
    try:
        roots = resolve_api_paths(control_root)
        contracts = CoreContracts(roots.core, load_json(resolve_beneath(roots.control, "ops/config/core-adoption.json")))
        contracts.verify()
        manifest = contracts.manifest()
        engine = NoteEngine.from_root(roots)
        expected = expected_projection(roots.control)
        vault = roots.vault
        for relative, payload in sorted(expected.items()):
            target = resolve_beneath(vault, relative)
            observed = target.read_bytes() if target.is_file() else None
            matches = observed == payload
            profile_json = relative.startswith((".obsidian-mac/", ".obsidian/")) and relative.endswith(".json")
            if profile_json and observed is not None:
                matches = _profile_content_matches(json.loads(payload), json.loads(observed))
            item = {"path": relative, "expected_sha256": hashlib.sha256(payload).hexdigest(), "comparison": "json_value" if profile_json else "exact_bytes", "status": "PASS" if matches else "MISMATCH"}
            if observed is not None:
                item["actual_sha256"] = hashlib.sha256(observed).hexdigest()
            if not matches:
                errors.append(_issue("VAULT_PROJECTION_MISMATCH", relative, "declared generated content is missing or differs"))
            artifacts.append(item)
        contract = load_json(resolve_beneath(vault, CONTRACT_PATH))
        if contract.get("authority") != "none" or contract.get("purpose") != "descriptive_static_projection" or contract.get("pins") != expected_contract_pins(expected):
            errors.append(_issue("VAULT_CONTROL_AUTHORITY", CONTRACT_PATH, "presentation cannot supply authority or different pins"))
        for relative, fields in PROFILE_GUARDS.items():
            settings = load_json(resolve_beneath(vault, relative))
            for name, value in fields.items():
                if settings.get(name) != value or type(settings.get(name)) is not type(value):
                    errors.append(_issue("VAULT_PROFILE_EFFECT_ENABLED", relative, f"{name} must retain its inactive binding"))
        enabled = json.loads(resolve_beneath(vault, ".obsidian-mac/community-plugins.json").read_bytes())
        if not isinstance(enabled, list) or not all(isinstance(name, str) for name in enabled):
            raise ValueError("enabled plugin projection must be a list of names")
        if "knowledgeos-thin-client" in enabled:
            errors.append(_issue("VAULT_UNADOPTED_PLUGIN_ENABLED", ".obsidian-mac/community-plugins.json", "broker client must remain staged disabled"))
        quickadd = load_json(resolve_beneath(vault, ".obsidian-mac/plugins/quickadd/data.json"))
        choices = quickadd.get("choices", [])
        if not isinstance(choices, list) or not all(isinstance(choice, dict) for choice in choices):
            raise ValueError("QuickAdd choices must be a list of objects")
        if any(choice.get("runOnStartup") is True for choice in choices):
            errors.append(_issue("VAULT_PROFILE_EFFECT_ENABLED", ".obsidian-mac/plugins/quickadd/data.json", "QuickAdd startup dispatch is forbidden"))
        ai_settings = quickadd.get("ai")
        if not isinstance(ai_settings, dict) or ai_settings.get("providers") != [] or ai_settings.get("defaultModel") != "":
            errors.append(_issue("VAULT_PROFILE_EFFECT_ENABLED", ".obsidian-mac/plugins/quickadd/data.json", "QuickAdd provider transport and default model must remain unconfigured"))
        toolbar_path = ".obsidian-mac/plugins/note-toolbar/data.json"
        toolbar = load_json(resolve_beneath(vault, toolbar_path))
        groups = toolbar.get("toolbars", [])
        if not isinstance(groups, list) or not all(isinstance(group, dict) for group in groups):
            raise ValueError("toolbars must be a list of objects")
        for group in groups:
            items = group.get("items", [])
            if not isinstance(items, list) or not all(isinstance(item, dict) and isinstance(item.get("linkAttr"), dict) and isinstance(item.get("link"), str) for item in items):
                raise ValueError("toolbar items must have typed links and attributes")
            for item in items:
                if item["linkAttr"].get("type") != "file":
                    continue
                name, _, fragment = item.get("link", "").partition("#")
                try:
                    target = resolve_beneath(vault, name)
                except ValueError:
                    errors.append(_issue("VAULT_LINK_UNSAFE", toolbar_path, "toolbar target crosses an unsafe path"))
                    continue
                if not target.is_file():
                    errors.append(_issue("VAULT_LINK_MISSING", toolbar_path, "toolbar file target is missing"))
                elif fragment and target.suffix == ".base" and fragment not in {view["name"] for view in load_yaml_file(target).get("views", [])}:
                    errors.append(_issue("VAULT_BASE_VIEW_MISSING", toolbar_path, "toolbar Base view is missing"))
        sentinel = load_json(resolve_beneath(vault, ".knowledgeos-root.json"))
        sentinel_report = validate_root_sentinel(sentinel, engine.blueprint)
        if not sentinel_report.passed:
            errors.append(_issue("VAULT_SENTINEL_INVALID", ".knowledgeos-root.json", "root identity does not satisfy its separate Vault contract"))
        files = _files(vault, errors)
        # Note path admission deliberately forbids hidden metadata. Profile and
        # marker paths are explicitly owned here, so validate their collision
        # keys separately without widening the public note-path contract.
        collision_keys: dict[str, str] = {}
        for path in files:
            relative = path.relative_to(vault).as_posix()
            key = unicodedata.normalize("NFC", relative).casefold()
            if key in collision_keys and collision_keys[key] != relative:
                errors.append(_issue("VAULT_PATH_COLLISION", relative, "path collides under NFC and case folding"))
            collision_keys[key] = relative
        canonical_paths = [path.relative_to(vault).as_posix() for path in files if not any(part.startswith(".") for part in path.relative_to(vault).parts)]
        for issue in validate_path_collisions(canonical_paths):
            errors.append(_issue(issue.code, issue.locator, issue.message))
        typed = {}
        for path in files:
            relative = path.relative_to(vault).as_posix()
            if path.suffix != ".md" or relative.startswith(("99_System/Templates/", ".obsidian")) or path.name in {"AGENTS.md", "README.md"}:
                continue
            text = path.read_text()
            if not text.startswith("---\n"):
                if relative != PROPERTY_DICTIONARY_PATH:
                    errors.append(_issue("NOTE_FRONTMATTER_INVALID", relative, "canonical note must have Blueprint-valid frontmatter"))
                continue
            document = parse_frontmatter(text)
            typed[relative] = (text, document.properties)
        target_types = {name: str(properties.get("type")) for relative, (_text, properties) in typed.items() for name in (relative.removesuffix(".md"), Path(relative).stem)}
        identifiers: dict[str, str] = {}
        for relative, (text, properties) in typed.items():
            identifier = str(properties.get("id"))
            if identifier in identifiers:
                errors.append(_issue("NOTE_ID_DUPLICATE", relative, "canonical note ID is already present in the Vault"))
            identifiers[identifier] = relative
            result = engine.validate_text(relative, text, target_types=target_types)
            for issue in result.errors:
                errors.append(_issue(issue.code, relative, issue.message))
        directories = _directory_targets(engine.blueprint)
        markers = 0
        for relative in directories:
            directory = resolve_beneath(vault, relative)
            if not directory.is_dir():
                errors.append(_issue("VAULT_NAMESPACE_MISSING", relative, "declared directory namespace is missing"))
            elif not relative.startswith("99_System/"):
                marker = resolve_beneath(vault, relative + "/.knowledgeos-directory")
                if not marker.is_file():
                    errors.append(_issue("VAULT_MARKER_MISSING", relative, "declared directory marker is missing"))
                else:
                    markers += 1
        links = _links(vault, files, errors)
        report.update({"core_manifest": manifest, "typed_notes": len(typed), "static_links": links, "directory_namespaces": len(directories), "directory_markers": markers, "profile_controls": len(PROFILE_GUARDS), "sentinel": sentinel_report.as_dict()})
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        errors.append(_issue("VAULT_READINESS_INPUT_INVALID", "binding_or_projection", str(error)))
    errors.sort(key=lambda item: (item["path"], item["code"]))
    report["status"] = "PASS" if not errors else "FAIL"
    return VaultArtifactCheckResult(report)


def expected_contract_pins(expected: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(expected[CONTRACT_PATH])["pins"]

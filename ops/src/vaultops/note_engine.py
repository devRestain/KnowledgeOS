"""Strict, registry-driven Markdown note contracts for KnowledgeOS.

S04 keeps the Markdown file as the source of truth while giving later proposal
and projection code one typed boundary.  The module intentionally has no
implicit write-to-Vault behaviour: parsing and validation are read-only, and
the writer only emits bytes after the caller has supplied a concrete path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

from .yaml_safe import load_yaml_text


class NoteContractError(ValueError):
    """Base exception for a rejected note contract."""


class FrontmatterError(NoteContractError):
    """Raised when Markdown frontmatter is absent or unsafe."""


class UnsafePathError(NoteContractError):
    """Raised when a Vault-relative path crosses the note boundary."""


@dataclass(frozen=True)
class FrontmatterDocument:
    """Parsed Markdown frontmatter and the untouched body."""

    properties: dict[str, Any]
    body: str


@dataclass(frozen=True)
class NoteIssue:
    code: str
    locator: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "locator": self.locator,
            "message": self.message,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class NoteValidationResult:
    path: str
    frontmatter: dict[str, Any] | None
    body: str | None
    errors: tuple[NoteIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "FAIL",
            "path": self.path,
            "errors": [error.as_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class TypedNote:
    """Validated note boundary shared by later proposal/projection code."""

    path: str
    properties: dict[str, Any]
    body: str

    @property
    def note_type(self) -> str:
        return str(self.properties["type"])

    @property
    def note_id(self) -> str:
        return str(self.properties["id"])


_WIKILINK_RE = re.compile(r"^\[\[[^\]\n]+\]\]$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_PATH_HASH_RE = re.compile(r"^(.+)\|sha256:([0-9a-f]{64})$")

CONTEXT_RELATIONS = ("projects", "areas", "topics", "sources", "people", "related")
SEMANTIC_RELATIONS = (
    "supports",
    "contradicts",
    "explains",
    "applies_to",
    "derived_from",
    "implements",
    "raises",
)
RELATION_PROPERTIES = (*CONTEXT_RELATIONS, *SEMANTIC_RELATIONS)
COMMON_REQUIRED = (
    "schema_version",
    "id",
    "type",
    "title",
    "status",
    "created",
    "modified",
    "aliases",
    "tags",
    "sensitivity",
    "ai_policy",
    "ai_status",
)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_vault_relative_path(path: str | PurePosixPath) -> str:
    """Return one safe NFC Vault-relative POSIX path.

    Note paths deliberately reject hidden components.  Hidden transport and
    configuration paths have separate owners and must not be accepted by the
    note engine.
    """

    raw = str(path)
    if not raw or "\x00" in raw:
        raise UnsafePathError("path must be non-empty and must not contain NUL")
    if "\\" in raw:
        raise UnsafePathError("path must use POSIX separators")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", raw):
        raise UnsafePathError("absolute paths are not allowed")
    if _nfc(raw) != raw:
        raise UnsafePathError("path must be NFC-normalized")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafePathError("empty, dot, or traversal path component")
    if any(part.startswith(".") for part in parts):
        raise UnsafePathError("hidden or protected path component")
    return raw


def validate_path_collisions(paths: Iterable[str]) -> list[NoteIssue]:
    """Reject NFC/NFD and case-fold aliases in one candidate path set."""

    seen: dict[str, str] = {}
    issues: list[NoteIssue] = []
    for raw in paths:
        try:
            normalized = normalize_vault_relative_path(raw)
        except UnsafePathError as error:
            # A non-NFC candidate is still inspected for alias collisions so
            # the caller gets the useful collision diagnostic for NFD/APFS
            # aliases rather than only a normalization complaint.
            if "NFC-normalized" not in str(error):
                issues.append(NoteIssue("PATH_UNSAFE", f"/{raw}", str(error)))
                continue
            try:
                normalized = normalize_vault_relative_path(_nfc(str(raw)))
            except UnsafePathError:
                issues.append(NoteIssue("PATH_UNSAFE", f"/{raw}", str(error)))
                continue
        key = _nfc(normalized).casefold()
        candidate = str(raw)
        previous = seen.get(key)
        if previous is not None and previous != candidate:
            issues.append(
                NoteIssue(
                    "PATH_UNICODE_CASE_COLLISION",
                    f"/{candidate}",
                    "path collides after NFC normalization and case folding",
                    {"other_path": previous},
                )
            )
        else:
            seen[key] = candidate
    return issues


def resolve_vault_relative_path(vault_root: str | Path, relative_path: str) -> Path:
    """Resolve a note path without following a symlink escape."""

    normalized = normalize_vault_relative_path(relative_path)
    root = Path(vault_root).resolve()
    candidate = root.joinpath(*normalized.split("/"))
    current = root
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise UnsafePathError("path crosses a symlink")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise UnsafePathError("resolved path escapes Vault root") from error
    return candidate


def parse_frontmatter(markdown: str) -> FrontmatterDocument:
    """Parse one UTF-8 Markdown document with duplicate-key-safe YAML."""

    if not isinstance(markdown, str):
        raise FrontmatterError("Markdown input must be text")
    if markdown.startswith("\ufeff"):
        raise FrontmatterError("UTF-8 BOM is not allowed")
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise FrontmatterError("Markdown must begin with YAML frontmatter")
    closing: int | None = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    yaml_text = "".join(lines[1:closing])
    try:
        loaded = load_yaml_text(yaml_text)
    except Exception as error:  # YAML library errors are normalized at this boundary.
        raise FrontmatterError(f"frontmatter YAML is invalid: {error}") from error
    if not isinstance(loaded, dict):
        raise FrontmatterError("frontmatter root must be a mapping")
    if any(not isinstance(key, str) for key in loaded):
        raise FrontmatterError("frontmatter keys must be strings")
    return FrontmatterDocument(properties=dict(loaded), body="".join(lines[closing + 1 :]))


def render_frontmatter(properties: Mapping[str, Any], body: str = "") -> str:
    """Render deterministic, flat YAML frontmatter and preserve body bytes."""

    if not isinstance(properties, Mapping):
        raise FrontmatterError("frontmatter properties must be a mapping")
    if any(not isinstance(key, str) for key in properties):
        raise FrontmatterError("frontmatter keys must be strings")
    if not isinstance(body, str):
        raise FrontmatterError("Markdown body must be text")
    try:
        frontmatter = yaml.safe_dump(
            dict(properties),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
    except yaml.YAMLError as error:
        raise FrontmatterError(f"frontmatter cannot be rendered: {error}") from error
    return f"---\n{frontmatter}---\n{body}"


def _descriptor_to_schema(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    obsidian_type = descriptor.get("obsidian_type")
    if obsidian_type == "text":
        result: dict[str, Any] = {"type": "string"}
    elif obsidian_type == "number":
        result = {"type": "integer" if descriptor.get("integer") else "number"}
    elif obsidian_type == "datetime":
        result = {"type": "string", "format": "date-time"}
    elif obsidian_type == "date":
        result = {"type": "string", "format": "date"}
    elif obsidian_type == "checkbox":
        result = {"type": "boolean"}
    elif obsidian_type in {"list", "tags"}:
        result = {"type": "array", "items": {"type": "string"}}
    else:
        result = {"type": "string"}

    if "const" in descriptor:
        result["const"] = descriptor["const"]
    if "enum" in descriptor:
        result["enum"] = list(descriptor["enum"])
    if "min_length" in descriptor:
        result["minLength"] = descriptor["min_length"]
    if "minimum" in descriptor:
        result["minimum"] = descriptor["minimum"]
    if obsidian_type == "list" and descriptor.get("item_type") == "quoted_wikilink":
        result["items"] = {"type": "string", "pattern": r"^\[\[[^\]\n]+\]\]$"}
    if obsidian_type == "list" and descriptor.get("item_type") == "quoted_wikilink_or_text":
        result["items"] = {"type": "string", "minLength": 1}
    if descriptor.get("format") == "uri":
        result["format"] = "uri"
    if descriptor.get("format") == "sha256":
        result["pattern"] = r"^[0-9a-f]{64}$"
    if descriptor.get("format") == "uuid_v4":
        result["pattern"] = _UUID_V4_RE.pattern
    if descriptor.get("format") == "iana_timezone":
        result["type"] = "string"
        result["pattern"] = r"^(?:UTC|GMT|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)$"
    if descriptor.get("format") == "quoted_wikilink_to_80_assets":
        result["pattern"] = r"^\[\[80_Assets/[^\]\n]+\]\]$"
    if descriptor.get("item_format") == "vault_relative_path|sha256:64hex":
        result.setdefault("items", {})["pattern"] = r"^.+\|sha256:[0-9a-f]{64}$"
    return result


def build_note_json_schema(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Build the strict per-note-type Draft 2020-12 schema."""

    common = blueprint["common_properties"]
    registry = blueprint["property_registry"]
    note_types = blueprint["note_types"]
    required = common["required"]
    optional_context = tuple(common.get("optional_context_lists", ()))
    optional_relations = tuple(common.get("optional_relation_lists", ()))
    shared_optional = (*optional_context, *optional_relations)
    shared_properties: dict[str, Any] = {
        name: _descriptor_to_schema(registry[name]) for name in shared_optional
    }
    common_properties: dict[str, Any] = {
        name: _descriptor_to_schema(descriptor) for name, descriptor in required.items()
    }
    # status is resolved by the selected note-type branch.
    common_properties["status"] = {"type": "string"}

    branches: list[dict[str, Any]] = []
    for note_type, contract in note_types.items():
        allowed = dict(common_properties)
        allowed.update(copy.deepcopy(shared_properties))
        for name in (*contract.get("required_extra", ()), *contract.get("optional_extra", ())):
            if name in registry:
                allowed[name] = _descriptor_to_schema(registry[name])
        required_names = [*COMMON_REQUIRED, *contract.get("required_extra", ())]
        branch: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": allowed,
            "required": required_names,
        }
        branch["properties"]["type"] = {"const": note_type}
        branch["properties"]["status"] = {"enum": list(contract["statuses"])}
        all_of: list[dict[str, Any]] = []
        for conditional in contract.get("conditional_requirements", ()):
            when = conditional["when"]
            all_of.append(
                {
                    "if": {"properties": {key: {"const": value} for key, value in when.items()}},
                    "then": {"required": list(conditional["require"])},
                }
            )
        constraints = contract.get("field_constraints", {})
        if note_type in {"project_note", "artifact"}:
            project_descriptor = allowed.get("projects")
            if project_descriptor:
                project_descriptor["minItems"] = 1
                project_descriptor["maxItems"] = 1
        if "when_status_final_require_one_of" in constraints:
            all_of.append(
                {
                    "if": {"properties": {"status": {"const": "final"}}},
                    "then": {
                        "anyOf": [
                            {"required": [field]}
                            for field in constraints["when_status_final_require_one_of"]
                        ]
                    },
                }
            )
        if "when_status_processed_or_archived_require_one_of" in constraints:
            all_of.append(
                {
                    "if": {"properties": {"status": {"enum": ["processed", "archived"]}}},
                    "then": {
                        "anyOf": [
                            {"required": [field]}
                            for field in constraints["when_status_processed_or_archived_require_one_of"]
                        ]
                    },
                }
            )
        if all_of:
            branch["allOf"] = all_of
        branches.append(branch)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "knowledgeos://schema/note-v1",
        "title": "KnowledgeOS strict Markdown note",
        "oneOf": branches,
    }


def _json_schema_errors(schema: Mapping[str, Any], properties: Mapping[str, Any]) -> list[NoteIssue]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[NoteIssue] = []
    for error in sorted(validator.iter_errors(properties), key=lambda item: (list(item.path), item.message)):
        locator = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in error.path)
        errors.append(NoteIssue("NOTE_SCHEMA_" + str(error.validator).upper(), locator or "/", error.message))
    return errors


def _parse_datetime(value: Any, name: str) -> tuple[datetime | None, NoteIssue | None]:
    if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value):
        return None, NoteIssue("NOTE_DATETIME_TIMEZONE", f"/{name}", "datetime must be ISO-8601 with an explicit offset")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, NoteIssue("NOTE_DATETIME_INVALID", f"/{name}", "datetime is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, NoteIssue("NOTE_DATETIME_TIMEZONE", f"/{name}", "datetime must include a timezone offset")
    return parsed, None


def _parse_date(value: Any, name: str) -> date | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_uuid_v4(value: Any) -> bool:
    if not isinstance(value, str) or value != value.lower() or not _UUID_V4_RE.fullmatch(value):
        return False
    try:
        return uuid.UUID(value).version == 4
    except ValueError:
        return False


def _approved_deterministic_id(note_type: str, value: Any) -> bool:
    if not isinstance(value, str) or value != value.lower():
        return False
    if note_type == "proposal":
        return value.startswith("proposal-") and _is_uuid_v4(value.removeprefix("proposal-"))
    patterns = {
        "daily": r"^daily-\d{4}-\d{2}-\d{2}$",
        "weekly": r"^weekly-\d{4}-w\d{2}$",
        "monthly": r"^monthly-\d{4}-\d{2}$",
        "home": r"^(home|mobile)$",
        "system": r"^system-[a-z0-9][a-z0-9_-]*$",
    }
    pattern = patterns.get(note_type)
    return bool(pattern and re.fullmatch(pattern, value))


def _validate_scalar(name: str, value: Any, descriptor: Mapping[str, Any]) -> list[NoteIssue]:
    errors: list[NoteIssue] = []
    obsidian_type = descriptor.get("obsidian_type")
    locator = f"/{name}"
    if obsidian_type in {"text", "datetime", "date", "tags"} and not isinstance(value, str) and obsidian_type != "tags":
        return [NoteIssue("NOTE_PROPERTY_TYPE", locator, f"{name} must be text")]
    if obsidian_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(NoteIssue("NOTE_PROPERTY_TYPE", locator, f"{name} must be a number"))
    if obsidian_type == "number" and descriptor.get("integer") and isinstance(value, (int, float)) and not isinstance(value, int):
        errors.append(NoteIssue("NOTE_PROPERTY_TYPE", locator, f"{name} must be an integer"))
    if obsidian_type in {"list", "tags"}:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(NoteIssue("NOTE_PROPERTY_TYPE", locator, f"{name} must be a flat string list"))
        elif descriptor.get("item_type") == "quoted_wikilink" and any(
            not _WIKILINK_RE.fullmatch(item) for item in value
        ):
            errors.append(NoteIssue("NOTE_WIKILINK_INVALID", locator, f"{name} items must be quoted wikilinks"))
    if obsidian_type == "checkbox" and not isinstance(value, bool):
        errors.append(NoteIssue("NOTE_PROPERTY_TYPE", locator, f"{name} must be boolean"))
    if isinstance(value, str) and descriptor.get("min_length", 0) and len(value) < descriptor["min_length"]:
        errors.append(NoteIssue("NOTE_PROPERTY_MIN_LENGTH", locator, f"{name} must not be empty"))
    if obsidian_type == "datetime" and isinstance(value, str):
        _, datetime_error = _parse_datetime(value, name)
        if datetime_error:
            errors.append(datetime_error)
    if obsidian_type == "date" and _parse_date(value, name) is None:
        errors.append(NoteIssue("NOTE_DATE_INVALID", locator, "date must be ISO-8601 YYYY-MM-DD"))
    if descriptor.get("enum") is not None and value not in descriptor["enum"]:
        errors.append(NoteIssue("NOTE_PROPERTY_ENUM", locator, f"{name} has an invalid enum value"))
    if descriptor.get("minimum") is not None and isinstance(value, (int, float)) and value < descriptor["minimum"]:
        errors.append(NoteIssue("NOTE_PROPERTY_MINIMUM", locator, f"{name} is below its minimum"))
    if descriptor.get("format") == "iana_timezone" and isinstance(value, str):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(NoteIssue("NOTE_TIMEZONE_INVALID", locator, "timezone must be a valid IANA timezone"))
    if descriptor.get("format") == "uri" and isinstance(value, str):
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc or any(char in value for char in "\r\n"):
            errors.append(NoteIssue("NOTE_URI_INVALID", locator, "value must be an absolute URI"))
    if descriptor.get("format") == "sha256" and isinstance(value, str) and not _SHA256_RE.fullmatch(value):
        errors.append(NoteIssue("NOTE_HASH_INVALID", locator, "value must be a lowercase SHA-256 digest"))
    if (
        descriptor.get("format") == "quoted_wikilink_to_80_assets"
        and isinstance(value, str)
        and (not _WIKILINK_RE.fullmatch(value) or not _link_target(value).startswith("80_Assets/"))
    ):
        errors.append(NoteIssue("NOTE_ASSET_LINK_INVALID", locator, "asset must link into 80_Assets"))
    if descriptor.get("item_format") == "vault_relative_path|sha256:64hex" and isinstance(value, list):
        for index, item in enumerate(value):
            match = _PATH_HASH_RE.fullmatch(item)
            if match is None:
                errors.append(NoteIssue("NOTE_SOURCE_HASH_INVALID", f"{locator}/{index}", "source hash must be path|sha256:64hex"))
                continue
            try:
                normalize_vault_relative_path(match.group(1))
            except UnsafePathError as error:
                errors.append(NoteIssue("NOTE_SOURCE_HASH_PATH_INVALID", f"{locator}/{index}", str(error)))
    if name == "artifact_hash" and isinstance(value, str) and not _SHA256_RE.fullmatch(value):
        errors.append(NoteIssue("NOTE_HASH_INVALID", locator, "artifact_hash must be a lowercase SHA-256 digest"))
    return errors


def _link_target(link: str) -> str:
    value = link[2:-2]
    return value.split("#", 1)[0].split("^", 1)[0]


def validate_relation_candidate(
    predicate: str,
    subject_type: str,
    object_type: str,
    *,
    provenance: str = "canonical_property",
    approved: bool = True,
    relation_registry: Mapping[str, Any] | None = None,
) -> list[NoteIssue]:
    """Validate one relation edge before it can enter canonical frontmatter."""

    errors: list[NoteIssue] = []
    if provenance != "canonical_property" or not approved:
        errors.append(
            NoteIssue(
                "RELATION_PROPOSAL_NOT_APPROVED",
                f"/relations/{predicate}",
                "AI-proposed relations require explicit approval before canonical write",
                {"provenance": provenance, "approved": approved},
            )
        )
        return errors
    if relation_registry is None:
        return errors
    if predicate in relation_registry.get("canonical_predicates", {}):
        contract = relation_registry["canonical_predicates"][predicate]
    elif predicate in relation_registry.get("context_predicates", {}):
        contract = relation_registry["context_predicates"][predicate]
    else:
        return [NoteIssue("RELATION_UNKNOWN_PREDICATE", f"/relations/{predicate}", "unknown relation predicate")]
    subjects = contract.get("allowed_subject_types")
    objects = contract.get("allowed_object_types", [])
    if subjects is not None and subject_type not in subjects:
        errors.append(NoteIssue("RELATION_SUBJECT_TYPE", f"/relations/{predicate}", "subject type is not allowed"))
    if "any_note" not in objects and object_type not in objects:
        errors.append(NoteIssue("RELATION_OBJECT_TYPE", f"/relations/{predicate}", "object type is not allowed"))
    return errors


class NoteEngine:
    """Read-only validator and schema-aware writer backed by Blueprint registry."""

    def __init__(self, blueprint: Mapping[str, Any]):
        self.blueprint = blueprint
        self.schema = build_note_json_schema(blueprint)

    @classmethod
    def from_root(cls, root: str | Path) -> NoteEngine:
        from .yaml_safe import load_yaml_file

        return cls(load_yaml_file(Path(root) / "blueprint/blueprint.yaml"))

    def note_type_for_path(self, relative_path: str) -> str | None:
        normalized = normalize_vault_relative_path(relative_path)
        note_types = self.blueprint["note_types"]
        matches: list[str] = []
        for note_type, contract in note_types.items():
            exact_paths = contract.get("exact_paths", ())
            if normalized in exact_paths:
                matches.append(note_type)
                continue
            patterns = PathSpec.from_lines(GitWildMatchPattern, contract.get("path_globs", ()))
            if patterns.match_file(normalized):
                excludes = PathSpec.from_lines(GitWildMatchPattern, contract.get("exclude_globs", ()))
                if not excludes.match_file(normalized):
                    matches.append(note_type)
        if len(matches) == 1:
            return matches[0]
        return None

    def _relation_errors(
        self,
        properties: Mapping[str, Any],
        *,
        target_types: Mapping[str, str] | None,
    ) -> list[NoteIssue]:
        errors: list[NoteIssue] = []
        target_types = target_types or {}
        relation_registry = self.blueprint.get("relation_registry", {})
        for predicate in RELATION_PROPERTIES:
            values = properties.get(predicate, [])
            if not isinstance(values, list):
                continue
            contract = relation_registry.get("canonical_predicates", {}).get(predicate)
            if contract is None:
                contract = relation_registry.get("context_predicates", {}).get(predicate)
            for index, link in enumerate(values):
                if not isinstance(link, str) or not _WIKILINK_RE.fullmatch(link):
                    continue
                target = _link_target(link)
                object_type = target_types.get(target) or target_types.get(link)
                if object_type is None:
                    errors.append(
                        NoteIssue(
                            "RELATION_TARGET_UNRESOLVED",
                            f"/{predicate}/{index}",
                            "canonical relation target is not present in the resolver",
                            {"target": target},
                        )
                    )
                    continue
                if contract is not None:
                    errors.extend(
                        NoteIssue(issue.code, f"/{predicate}/{index}", issue.message, issue.details)
                        for issue in validate_relation_candidate(
                            predicate,
                            str(properties.get("type")),
                            object_type,
                            relation_registry=relation_registry,
                        )
                    )
        return errors

    def validate_properties(
        self,
        properties: Mapping[str, Any],
        relative_path: str,
        *,
        target_types: Mapping[str, str] | None = None,
    ) -> NoteValidationResult:
        normalized_path = normalize_vault_relative_path(relative_path)
        errors: list[NoteIssue] = []
        if not isinstance(properties, Mapping):
            errors.append(NoteIssue("NOTE_FRONTMATTER_ROOT", "/", "frontmatter root must be a mapping"))
            return NoteValidationResult(normalized_path, None, None, tuple(errors))
        values = dict(properties)
        errors.extend(_json_schema_errors(self.schema, values))
        note_type = values.get("type")
        expected_type = self.note_type_for_path(normalized_path)
        if expected_type is None:
            errors.append(NoteIssue("NOTE_PATH_UNMATCHED", "/", "path does not match exactly one note type"))
        elif note_type != expected_type:
            errors.append(
                NoteIssue(
                    "NOTE_PATH_TYPE_MISMATCH",
                    "/type",
                    "note type does not match its canonical path",
                    {"expected": expected_type, "observed": note_type},
                )
            )

        title = values.get("title")
        basename = Path(normalized_path).stem
        if isinstance(title, str) and (not title or "\x00" in title or "/" in title or "\\" in title):
            errors.append(NoteIssue("NOTE_TITLE_INVALID", "/title", "title must be a single non-empty filename stem"))
        if isinstance(title, str) and _nfc(title) != title:
            errors.append(NoteIssue("NOTE_TITLE_NOT_NFC", "/title", "title must be NFC-normalized"))
        if isinstance(title, str) and title != basename:
            errors.append(
                NoteIssue(
                    "NOTE_TITLE_BASENAME_MISMATCH",
                    "/title",
                    "title must equal the Markdown filename stem",
                    {"filename_stem": basename},
                )
            )

        if note_type not in self.blueprint.get("note_types", {}):
            note_type = None
        if note_type is not None and not _is_uuid_v4(values.get("id")) and not _approved_deterministic_id(note_type, values.get("id")):
            errors.append(NoteIssue("NOTE_ID_INVALID", "/id", "id must be lowercase UUID v4 or an approved deterministic id"))
        if "created" in values and "modified" in values:
            created, created_error = _parse_datetime(values["created"], "created")
            modified, modified_error = _parse_datetime(values["modified"], "modified")
            if created_error:
                errors.append(created_error)
            if modified_error:
                errors.append(modified_error)
            if created is not None and modified is not None and created > modified:
                errors.append(NoteIssue("NOTE_DATETIME_ORDER", "/modified", "created must not be later than modified"))

        if values.get("sensitivity") == "confidential" and values.get("ai_policy") == "remote_ok":
            errors.append(NoteIssue("NOTE_PRIVACY_REMOTE_FORBIDDEN", "/ai_policy", "confidential notes cannot allow remote AI"))

        if note_type is not None:
            contract = self.blueprint["note_types"][note_type]
            for field in contract.get("required_extra", ()):
                if field not in values:
                    errors.append(NoteIssue("NOTE_REQUIRED_PROPERTY", f"/{field}", "type-specific property is required"))
            status = values.get("status")
            for conditional in contract.get("conditional_requirements", ()):
                if all(values.get(key) == expected for key, expected in conditional["when"].items()):
                    for field in conditional["require"]:
                        if field not in values or values[field] in (None, "", []):
                            errors.append(NoteIssue("NOTE_CONDITIONAL_PROPERTY", f"/{field}", "conditional property is required"))
            if note_type in {"project_note", "artifact"} and isinstance(values.get("projects"), list) and len(values["projects"]) != 1:
                errors.append(NoteIssue("NOTE_PROJECT_CARDINALITY", "/projects", "project-local note must have exactly one project"))
            if note_type in {"project_note", "artifact"} and isinstance(values.get("projects"), list) and len(values["projects"]) == 1:
                project_link = values["projects"][0]
                if isinstance(project_link, str) and _WIKILINK_RE.fullmatch(project_link):
                    path_parts = normalized_path.split("/")
                    parent_project: str | None = None
                    if len(path_parts) >= 4 and path_parts[0] == "20_Projects":
                        parent_project = path_parts[1]
                    elif len(path_parts) >= 5 and path_parts[0:2] == ["90_Archive", "Projects"]:
                        parent_project = path_parts[3]
                    linked_project = _link_target(project_link)
                    if parent_project is not None and linked_project != parent_project:
                        errors.append(
                            NoteIssue(
                                "NOTE_PROJECT_PARENT_MISMATCH",
                                "/projects/0",
                                "project-local note must link to its parent project bundle",
                                {"expected": parent_project, "observed": linked_project},
                            )
                        )
            if note_type == "artifact" and status == "final":
                targets = ("artifact_uri", "artifact_repo", "asset")
                if not any(values.get(field) not in (None, "") for field in targets):
                    errors.append(NoteIssue("NOTE_ARTIFACT_PROVENANCE", "/status", "final artifact needs a URI, repository, or asset"))
            if note_type == "source" and status in {"processed", "archived"}:
                targets = ("source_url", "asset", "derived_from", "citation_key")
                if not any(values.get(field) not in (None, "") for field in targets):
                    errors.append(NoteIssue("NOTE_SOURCE_PROVENANCE", "/status", "processed source needs provenance"))

        for name, descriptor in self.blueprint.get("property_registry", {}).items():
            if name in values and isinstance(descriptor, Mapping):
                errors.extend(_validate_scalar(name, values[name], descriptor))
        # Enforce semantic relation directions only when a resolver is supplied;
        # otherwise non-empty relation lists fail closed rather than becoming
        # unverifiable canonical edges.
        errors.extend(self._relation_errors(values, target_types=target_types))
        deduplicated: dict[tuple[str, str, str], NoteIssue] = {}
        for error in errors:
            deduplicated[(error.code, error.locator, error.message)] = error
        ordered = tuple(deduplicated[key] for key in sorted(deduplicated))
        return NoteValidationResult(normalized_path, values, None, ordered)

    def validate_text(
        self,
        relative_path: str,
        markdown: str,
        *,
        target_types: Mapping[str, str] | None = None,
    ) -> NoteValidationResult:
        try:
            document = parse_frontmatter(markdown)
        except (FrontmatterError, UnicodeError) as error:
            return NoteValidationResult(
                str(relative_path), None, None, (NoteIssue("NOTE_FRONTMATTER_INVALID", "/", str(error)),)
            )
        result = self.validate_properties(document.properties, relative_path, target_types=target_types)
        return NoteValidationResult(result.path, result.frontmatter, document.body, result.errors)

    def typed_note(
        self,
        relative_path: str,
        markdown: str,
        *,
        target_types: Mapping[str, str] | None = None,
    ) -> TypedNote:
        result = self.validate_text(relative_path, markdown, target_types=target_types)
        if not result.passed or result.frontmatter is None or result.body is None:
            raise NoteContractError(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return TypedNote(result.path, result.frontmatter, result.body)

    def write_text(
        self,
        relative_path: str,
        properties: Mapping[str, Any],
        body: str,
        *,
        modified: datetime | None = None,
        target_types: Mapping[str, str] | None = None,
    ) -> str:
        values = dict(properties)
        if modified is not None:
            if modified.tzinfo is None or modified.utcoffset() is None:
                raise NoteContractError("modified must include a timezone")
            values["modified"] = modified.isoformat(timespec="seconds")
        result = self.validate_properties(values, relative_path, target_types=target_types)
        if not result.passed:
            raise NoteContractError(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return render_frontmatter(values, body)


def write_note_file(
    path: str | Path,
    text: str,
    *,
    overwrite: bool = False,
    expected_sha256: str | None = None,
) -> None:
    """Write already-validated note bytes without following a destination symlink.

    Create-only callers use ``O_EXCL`` so a concurrent creator cannot be
    overwritten after preflight. Guarded replace callers may provide the
    digest observed during validation; a changed file then fails closed.
    """

    destination = Path(path)
    if destination.is_symlink():
        raise UnsafePathError("refusing to write through a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    if not overwrite:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return
    if not destination.is_file():
        raise FileNotFoundError(destination)
    if expected_sha256 is not None:
        observed = hashlib.sha256(destination.read_bytes()).hexdigest()
        if observed != expected_sha256:
            raise FileExistsError(f"guarded target changed: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor != -1:
            os.close(descriptor)

"""Privacy-minimized C29 AI projections.

The canonical Vault and the C21 full projection remain the source of truth.
This module creates a separate, profile-scoped projection before any future
provider context is materialized.  The local profile permits ``ask`` and
``local_only`` material, while the remote profile permits only ``remote_ok``.
Confidential and denied notes never become records in either artifact, and
edges are emitted only when both endpoints survive the same eligibility gate.

Generation files are immutable, the profile pointer is atomically replaced,
and the reader can recompute the privacy-filtered bytes from the current Vault
to fail closed on source, policy, schema, or generation drift.  No provider,
Git command, Vault mutation, or network access is performed here.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    PROJECTION_SERIALIZATION,
    ProjectionBuild,
    ProjectionError,
    _atomic_runtime_write,
    _datetime_schema,
    _edge_sort_key,
    _ensure_private_directory,
    _flat_value_schema,
    _identifier_schema,
    _jsonl_bytes,
    _nfc,
    _path_schema,
    _record_sort_key,
    _runtime_path,
    _sha256_bytes,
    _sha256_schema,
    build_projection,
    canonical_json_bytes,
)

SCHEMA_VERSION = 1
GENERATOR_ID = "vaultops.ai_projection"
AI_ROOT = "index/ai"
AI_GENERATION_ROOT = f"{AI_ROOT}/PROFILE/generations"
AI_NOTE_SCHEMA_PATH = "ops/schemas/ai-note-record.schema.json"
AI_EDGE_SCHEMA_PATH = "ops/schemas/ai-edge-record.schema.json"
AI_SCHEMA_PATHS = (AI_NOTE_SCHEMA_PATH, AI_EDGE_SCHEMA_PATH)
AI_PROFILES = ("local", "remote")
PROFILE_ALIASES = {
    "local": "local",
    "local_eligible": "local",
    "remote": "remote",
    "remote_eligible": "remote",
}
ELIGIBILITY_CLASSES = {
    "local": "local_eligible",
    "remote": "remote_eligible",
}
ALLOWED_AI_POLICIES = {
    "local": frozenset({"remote_ok", "ask", "local_only"}),
    "remote": frozenset({"remote_ok"}),
}
EXCLUDED_SENSITIVITIES = frozenset({"confidential"})
RELATION_PROPERTIES = frozenset(
    {
        "projects",
        "areas",
        "topics",
        "sources",
        "people",
        "related",
        "supports",
        "contradicts",
        "explains",
        "applies_to",
        "derived_from",
        "implements",
        "raises",
    }
)
_GENERATION_ID_MAX = 128
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AIProjectionError(ValueError):
    """Base error for an invalid, stale, or unsafe AI projection."""


class AIProjectionConflict(AIProjectionError):
    """Raised when an immutable generation or pointer conflicts."""


@dataclass(frozen=True)
class AIProjectionBuild:
    """Pure profile-filtered projection bytes ready for publication."""

    profile: str
    eligibility_class: str
    generation_id: str
    notes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    source_snapshot_sha256: str
    manifest: dict[str, Any]
    notes_bytes: bytes
    edges_bytes: bytes
    manifest_bytes: bytes
    schema_files: tuple[dict[str, str], ...]
    blueprint_sha256: str

    @property
    def manifest_sha256(self) -> str:
        return _sha256_bytes(self.manifest_bytes)

    @property
    def generation_root(self) -> str:
        return _generation_root(self.profile, self.generation_id)


@dataclass(frozen=True)
class AIProjectionRead:
    """Typed view of one verified profile generation."""

    profile: str
    pointer: dict[str, Any]
    manifest: dict[str, Any]
    notes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]


def _normalize_profile(profile: str) -> str:
    if not isinstance(profile, str) or profile not in PROFILE_ALIASES:
        raise AIProjectionError("profile must be local or remote")
    return PROFILE_ALIASES[profile]


def _eligibility_class(profile: str) -> str:
    return ELIGIBILITY_CLASSES[_normalize_profile(profile)]


def _generation_root(profile: str, generation_id: str) -> str:
    normalized = _normalize_profile(profile)
    if (
        not isinstance(generation_id, str)
        or not generation_id
        or len(generation_id) > _GENERATION_ID_MAX
        or not _GENERATION_ID.fullmatch(generation_id)
    ):
        raise AIProjectionError("generation_id contains unsafe characters")
    return f"{AI_ROOT}/{normalized}/generations/{generation_id}"


def _current_pointer_path(profile: str) -> str:
    return f"{AI_ROOT}/{_normalize_profile(profile)}/current.json"


def _sha256_schema_for_ai() -> dict[str, Any]:
    return _sha256_schema()


def _wikilink_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["raw_target", "resolved_id", "resolved_path", "locator"],
        "properties": {
            "raw_target": {"type": "string", "minLength": 1},
            "resolved_id": {"anyOf": [_identifier_schema(), {"type": "null"}]},
            "resolved_path": {"anyOf": [_path_schema(), {"type": "null"}]},
            "locator": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        },
    }


def ai_note_record_schema() -> dict[str, Any]:
    """Return the strict profile-filtered AI note schema."""

    properties = {
        "id": _identifier_schema(),
        "path": _path_schema(),
        "title": {"type": "string", "minLength": 1},
        "type": {"type": "string", "minLength": 1},
        "status": {"type": "string", "minLength": 1},
        "properties": {
            "type": "object",
            "additionalProperties": _flat_value_schema(),
        },
        "body": {"type": "string"},
        "wikilinks": {"type": "array", "items": _wikilink_schema()},
        "created": _datetime_schema(),
        "modified": _datetime_schema(),
        "content_hash": _sha256_schema_for_ai(),
        "schema_version": {"const": SCHEMA_VERSION},
        "projection_profile": {"enum": list(AI_PROFILES)},
        "eligibility_class": {"enum": sorted(set(ELIGIBILITY_CLASSES.values()))},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/ai-note-record.schema.json",
        "title": "KnowledgeOS C29 privacy-minimized AI note record",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def ai_edge_record_schema() -> dict[str, Any]:
    """Return the strict profile-filtered AI edge schema."""

    properties = {
        "subject_id": _identifier_schema(),
        "edge_kind": {"enum": ["semantic", "context"]},
        "predicate": {"type": "string", "minLength": 1},
        "object_id": _identifier_schema(),
        "subject_path": _path_schema(),
        "object_path": _path_schema(),
        "provenance": {"const": "canonical_property"},
        "source_locator": {"type": "string", "minLength": 1},
        "object_locator": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "source_content_hash": _sha256_schema_for_ai(),
        "relation_schema_version": {"const": SCHEMA_VERSION},
        "projection_profile": {"enum": list(AI_PROFILES)},
        "eligibility_class": {"enum": sorted(set(ELIGIBILITY_CLASSES.values()))},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/ai-edge-record.schema.json",
        "title": "KnowledgeOS C29 privacy-minimized AI edge record",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def build_ai_note_record_schema() -> dict[str, Any]:
    """Compatibility alias used by schema generation callers."""

    return ai_note_record_schema()


def build_ai_edge_record_schema() -> dict[str, Any]:
    """Compatibility alias used by schema generation callers."""

    return ai_edge_record_schema()


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise AIProjectionError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise AIProjectionError("KnowledgeHub must be an existing non-symlink directory")
    return workspace


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise AIProjectionError(f"{label} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise AIProjectionError(f"cannot read {label}: {error}") from error


def _schema_files(workspace: Path) -> tuple[tuple[dict[str, str], ...], str]:
    files = tuple(
        {"path": relative, "sha256": _sha256_bytes(_regular_file(workspace / relative, relative))}
        for relative in AI_SCHEMA_PATHS
    )
    return files, _sha256_bytes(canonical_json_bytes(list(files)))


def _profile_policy(profile: str) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    return {
        "profile": normalized,
        "eligibility_class": _eligibility_class(normalized),
        "allowed_ai_policy": sorted(ALLOWED_AI_POLICIES[normalized]),
        "excluded_ai_policy": sorted({"remote_ok", "ask", "local_only", "deny"} - ALLOWED_AI_POLICIES[normalized]),
        "excluded_sensitivity": sorted(EXCLUDED_SENSITIVITIES),
    }


def _eligibility_reason(record: Mapping[str, Any], profile: str) -> str | None:
    properties = record.get("properties")
    if not isinstance(properties, Mapping):
        return "properties_invalid"
    sensitivity = properties.get("sensitivity")
    if sensitivity in EXCLUDED_SENSITIVITIES:
        return "confidential"
    if sensitivity not in {"public", "personal"}:
        return "sensitivity_invalid"
    policy = properties.get("ai_policy")
    if policy == "deny":
        return "deny"
    if policy not in ALLOWED_AI_POLICIES[_normalize_profile(profile)]:
        return "ai_policy_not_allowed"
    return None


def _filtered_properties(
    record: Mapping[str, Any],
    *,
    allowed_ids: set[str],
    edges_by_locator: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    raw = record.get("properties")
    if not isinstance(raw, Mapping):
        raise AIProjectionError("C21 record properties must be an object")
    filtered: dict[str, Any] = {}
    note_id = str(record["id"])
    for key, value in raw.items():
        if key not in RELATION_PROPERTIES:
            filtered[str(key)] = value
            continue
        if not isinstance(value, list):
            raise AIProjectionError(f"relation property is not a list: {key}")
        visible: list[Any] = []
        for index, item in enumerate(value):
            edge = edges_by_locator.get((note_id, str(key), index))
            if edge is not None and str(edge["object_id"]) in allowed_ids:
                visible.append(item)
        filtered[str(key)] = visible
    return dict(sorted(filtered.items(), key=lambda item: (_nfc(item[0]), item[0])))


def _filtered_wikilinks(record: Mapping[str, Any], allowed_ids: set[str]) -> list[dict[str, Any]]:
    raw = record.get("wikilinks")
    if not isinstance(raw, list):
        raise AIProjectionError("C21 record wikilinks must be a list")
    return [
        dict(item)
        for item in raw
        if isinstance(item, Mapping) and str(item.get("resolved_id")) in allowed_ids
    ]


def _filter_records(
    base: ProjectionBuild,
    profile: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], dict[str, int]]:
    normalized = _normalize_profile(profile)
    eligible_ids = {
        str(record["id"])
        for record in base.notes
        if _eligibility_reason(record, normalized) is None
    }
    edges_by_locator: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    for edge in base.edges:
        locator = str(edge["source_locator"])
        parts = locator.strip("/").split("/")
        if len(parts) == 2 and parts[1].isdigit():
            edges_by_locator[(str(edge["subject_id"]), parts[0], int(parts[1]))] = edge

    excluded: dict[str, int] = {}
    note_records: list[dict[str, Any]] = []
    for record in base.notes:
        reason = _eligibility_reason(record, normalized)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        note = dict(record)
        note["properties"] = _filtered_properties(
            record,
            allowed_ids=eligible_ids,
            edges_by_locator=edges_by_locator,
        )
        note["wikilinks"] = _filtered_wikilinks(record, eligible_ids)
        note["projection_profile"] = normalized
        note["eligibility_class"] = _eligibility_class(normalized)
        note_records.append(note)

    edge_records: list[dict[str, Any]] = []
    for edge in base.edges:
        subject_id = str(edge["subject_id"])
        object_id = str(edge["object_id"])
        if subject_id not in eligible_ids or object_id not in eligible_ids:
            continue
        filtered_edge = dict(edge)
        filtered_edge["projection_profile"] = normalized
        filtered_edge["eligibility_class"] = _eligibility_class(normalized)
        edge_records.append(filtered_edge)

    note_records.sort(key=_record_sort_key)
    edge_records.sort(key=_edge_sort_key)
    return tuple(note_records), tuple(edge_records), dict(sorted(excluded.items()))


def _generation_id(
    *,
    profile: str,
    source_snapshot_sha256: str,
    schema_files: Iterable[Mapping[str, str]],
    blueprint_sha256: str,
) -> str:
    seed = canonical_json_bytes(
        {
            "blueprint_sha256": blueprint_sha256,
            "eligibility_policy": _profile_policy(profile),
            "profile": _normalize_profile(profile),
            "schema_files": list(schema_files),
            "source_snapshot_sha256": source_snapshot_sha256,
        }
    )
    return f"ai-{_normalize_profile(profile)}-{_sha256_bytes(seed)[:48]}"


def _build_manifest(
    *,
    profile: str,
    generation_id: str,
    base: ProjectionBuild,
    notes: tuple[dict[str, Any], ...],
    edges: tuple[dict[str, Any], ...],
    excluded: Mapping[str, int],
    notes_bytes: bytes,
    edges_bytes: bytes,
    schema_files: tuple[dict[str, str], ...],
    schema_bundle_sha256: str,
    blueprint_sha256: str,
) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    generation_root = _generation_root(normalized, generation_id)
    notes_sha256 = _sha256_bytes(notes_bytes)
    edges_sha256 = _sha256_bytes(edges_bytes)
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR_ID,
        "projection_profile": normalized,
        "eligibility_class": _eligibility_class(normalized),
        "eligibility_policy": _profile_policy(normalized),
        "generation_id": generation_id,
        "generation_root": generation_root,
        "source_snapshot_sha256": base.source_snapshot_sha256,
        "source_note_count": len(base.notes),
        "source_edge_count": len(base.edges),
        "eligible_note_count": len(notes),
        "eligible_edge_count": len(edges),
        "excluded_note_count": sum(excluded.values()),
        "excluded_note_reasons": dict(excluded),
        "blueprint_sha256": blueprint_sha256,
        "schema_bundle_sha256": schema_bundle_sha256,
        "schema_files": list(schema_files),
        "notes_count": len(notes),
        "edges_count": len(edges),
        "notes_sha256": notes_sha256,
        "edges_sha256": edges_sha256,
        "files": {"notes.jsonl": notes_sha256, "edges.jsonl": edges_sha256},
        "serialization": PROJECTION_SERIALIZATION,
    }


def build_ai_projection(
    root: str | Path,
    *,
    profile: str,
    generation_id: str | None = None,
) -> AIProjectionBuild:
    """Build one privacy-minimized profile without writing runtime state."""

    workspace = _workspace(root)
    normalized = _normalize_profile(profile)
    base = build_projection(workspace)
    notes, edges, excluded = _filter_records(base, normalized)
    notes_bytes = _jsonl_bytes(notes)
    edges_bytes = _jsonl_bytes(edges)
    blueprint_sha256 = _sha256_bytes(_regular_file(workspace / "blueprint/blueprint.yaml", "blueprint"))
    schema_files, schema_bundle_sha256 = _schema_files(workspace)
    selected_generation_id = generation_id or _generation_id(
        profile=normalized,
        source_snapshot_sha256=base.source_snapshot_sha256,
        schema_files=schema_files,
        blueprint_sha256=blueprint_sha256,
    )
    _generation_root(normalized, selected_generation_id)
    manifest = _build_manifest(
        profile=normalized,
        generation_id=selected_generation_id,
        base=base,
        notes=notes,
        edges=edges,
        excluded=excluded,
        notes_bytes=notes_bytes,
        edges_bytes=edges_bytes,
        schema_files=schema_files,
        schema_bundle_sha256=schema_bundle_sha256,
        blueprint_sha256=blueprint_sha256,
    )
    return AIProjectionBuild(
        profile=normalized,
        eligibility_class=_eligibility_class(normalized),
        generation_id=selected_generation_id,
        notes=notes,
        edges=edges,
        source_snapshot_sha256=base.source_snapshot_sha256,
        manifest=manifest,
        notes_bytes=notes_bytes,
        edges_bytes=edges_bytes,
        manifest_bytes=canonical_json_bytes(manifest),
        schema_files=schema_files,
        blueprint_sha256=blueprint_sha256,
    )


def build_privacy_projection(
    root: str | Path,
    *,
    profile: str,
    generation_id: str | None = None,
) -> AIProjectionBuild:
    """Compatibility alias for the C29 build operation."""

    return build_ai_projection(root, profile=profile, generation_id=generation_id)


def _generation_paths(workspace: Path, profile: str, generation_id: str) -> tuple[Path, Path, Path, Path]:
    root = _runtime_path(workspace, _generation_root(profile, generation_id))
    return root, root / "notes.jsonl", root / "edges.jsonl", root / "manifest.json"


def _assert_generation_file(path: Path, expected: bytes, label: str) -> bool:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AIProjectionConflict(f"AI generation {label} is not a regular file")
    if not path.exists():
        return False
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AIProjectionConflict(f"AI generation {label} must be mode 0600")
    if path.read_bytes() != expected:
        raise AIProjectionConflict(f"AI generation {label} conflicts with deterministic bytes")
    return True


def _source_snapshot_matches(workspace: Path, expected: str) -> bool:
    try:
        return build_projection(workspace).source_snapshot_sha256 == expected
    except (OSError, UnicodeError, ProjectionError, KeyError, TypeError, ValueError):
        return False


def _publish_generation(workspace: Path, build: AIProjectionBuild) -> bool:
    generation, notes_path, edges_path, manifest_path = _generation_paths(
        workspace,
        build.profile,
        build.generation_id,
    )
    _ensure_private_directory(workspace / "runtime")
    _ensure_private_directory(workspace / "runtime" / "index")
    _ensure_private_directory(workspace / "runtime" / "index" / "ai")
    _ensure_private_directory(workspace / "runtime" / "index" / "ai" / build.profile)
    _ensure_private_directory(workspace / "runtime" / "index" / "ai" / build.profile / "generations")
    _ensure_private_directory(generation)
    existing = (
        _assert_generation_file(notes_path, build.notes_bytes, "notes.jsonl")
        and _assert_generation_file(edges_path, build.edges_bytes, "edges.jsonl")
        and _assert_generation_file(manifest_path, build.manifest_bytes, "manifest.json")
    )
    if not existing:
        if not _assert_generation_file(notes_path, build.notes_bytes, "notes.jsonl"):
            _atomic_runtime_write(notes_path, build.notes_bytes)
        if not _assert_generation_file(edges_path, build.edges_bytes, "edges.jsonl"):
            _atomic_runtime_write(edges_path, build.edges_bytes)
        if not _assert_generation_file(manifest_path, build.manifest_bytes, "manifest.json"):
            _atomic_runtime_write(manifest_path, build.manifest_bytes)

    if not _source_snapshot_matches(workspace, build.source_snapshot_sha256):
        raise AIProjectionConflict("Vault source changed before AI projection pointer publication")

    generation_root = build.generation_root
    pointer = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR_ID,
        "projection_profile": build.profile,
        "eligibility_class": build.eligibility_class,
        "generation_id": build.generation_id,
        "generation_root": generation_root,
        "notes_path": f"{generation_root}/notes.jsonl",
        "edges_path": f"{generation_root}/edges.jsonl",
        "manifest_path": f"{generation_root}/manifest.json",
        "manifest_sha256": build.manifest_sha256,
        "source_snapshot_sha256": build.source_snapshot_sha256,
    }
    pointer_bytes = canonical_json_bytes(pointer)
    pointer_path = _runtime_path(workspace, _current_pointer_path(build.profile))
    if pointer_path.is_symlink() or (pointer_path.exists() and not pointer_path.is_file()):
        raise AIProjectionConflict("AI projection current pointer is not a regular file")
    if pointer_path.is_file() and stat.S_IMODE(pointer_path.stat().st_mode) != 0o600:
        raise AIProjectionConflict("AI projection current pointer must be mode 0600")
    before = pointer_path.read_bytes() if pointer_path.is_file() else None
    if before == pointer_bytes:
        return False
    _atomic_runtime_write(pointer_path, pointer_bytes)
    return True


def _failure(operation: str, code: str, message: str) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": "C29",
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, EXIT_CONFLICT


def generate_ai_projection(
    root: str | Path,
    *,
    profile: str,
    generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Build and publish one profile-scoped immutable AI projection."""

    operation = "ai projection build"
    try:
        workspace = _workspace(root)
        build = build_ai_projection(workspace, profile=profile, generation_id=generation_id)
        if not _source_snapshot_matches(workspace, build.source_snapshot_sha256):
            return _failure(operation, "AI_PROJECTION_SOURCE_DRIFT", "Vault source changed during AI projection build")
        pointer_swapped = _publish_generation(workspace, build)
        return {
            "status": "PASS",
            "operation": operation,
            "capability": "C29",
            "projection_profile": build.profile,
            "eligibility_class": build.eligibility_class,
            "generation_id": build.generation_id,
            "generation_root": build.generation_root,
            "notes_path": f"{build.generation_root}/notes.jsonl",
            "edges_path": f"{build.generation_root}/edges.jsonl",
            "manifest_path": f"{build.generation_root}/manifest.json",
            "manifest_sha256": build.manifest_sha256,
            "source_snapshot_sha256": build.source_snapshot_sha256,
            "source_note_count": build.manifest["source_note_count"],
            "source_edge_count": build.manifest["source_edge_count"],
            "eligible_note_count": len(build.notes),
            "eligible_edge_count": len(build.edges),
            "excluded_note_count": build.manifest["excluded_note_count"],
            "excluded_note_reasons": build.manifest["excluded_note_reasons"],
            "pointer_swapped": pointer_swapped,
            "provider_called": False,
            "mutation_performed": pointer_swapped,
        }, EXIT_OK
    except AIProjectionConflict as error:
        return _failure(operation, "AI_PROJECTION_CONFLICT", str(error))
    except (OSError, UnicodeError, AIProjectionError, ProjectionError, KeyError, TypeError, ValueError) as error:
        return _failure(operation, "AI_PROJECTION_INVALID", str(error))


def generate_privacy_projection(
    root: str | Path,
    *,
    profile: str,
    generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Compatibility alias for :func:`generate_ai_projection`."""

    return generate_ai_projection(root, profile=profile, generation_id=generation_id)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = _regular_file(path, label)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise AIProjectionError(f"{label} must be mode 0600")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AIProjectionError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise AIProjectionError(f"{label} must contain a JSON object")
    if canonical_json_bytes(value) != raw:
        raise AIProjectionError(f"{label} is not canonically serialized")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    raw = _regular_file(path, label)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AIProjectionError(f"{label} must be mode 0600")
    try:
        lines = raw.decode("utf-8").splitlines()
        records = [json.loads(line) for line in lines if line]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AIProjectionError(f"{label} is not valid JSONL: {error}") from error
    if not all(isinstance(record, dict) for record in records):
        raise AIProjectionError(f"{label} must contain JSON objects")
    typed = [dict(record) for record in records]
    if _jsonl_bytes(typed) != raw:
        raise AIProjectionError(f"{label} is not canonically serialized")
    return typed


def _validate_records(records: list[dict[str, Any]], schema: Mapping[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(records[0]) if records else (),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path)
        raise AIProjectionError(f"{label} schema validation failed at {locator}: {error.message}")
    for index, record in enumerate(records[1:], start=1):
        errors = sorted(
            validator.iter_errors(record),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        if errors:
            error = errors[0]
            locator = "/".join(str(part) for part in error.path)
            raise AIProjectionError(f"{label}[{index}] schema validation failed at {locator}: {error.message}")


def _verify_records(
    profile: str,
    notes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    normalized = _normalize_profile(profile)
    _validate_records(notes, ai_note_record_schema(), "AI notes")
    _validate_records(edges, ai_edge_record_schema(), "AI edges")
    note_by_id = {str(record["id"]): record for record in notes}
    note_by_path = {str(record["path"]): record for record in notes}
    if len(note_by_id) != len(notes) or len(note_by_path) != len(notes):
        raise AIProjectionError("AI projection contains duplicate note identity")
    if any(
        record["projection_profile"] != normalized
        or record["eligibility_class"] != _eligibility_class(normalized)
        or _eligibility_reason(record, normalized) is not None
        for record in notes
    ):
        raise AIProjectionConflict("AI projection contains content outside its eligibility class")
    if tuple(notes) != tuple(sorted(notes, key=_record_sort_key)):
        raise AIProjectionError("AI note records are not in canonical order")
    if tuple(edges) != tuple(sorted(edges, key=_edge_sort_key)):
        raise AIProjectionError("AI edge records are not in canonical order")
    edge_keys: set[tuple[Any, ...]] = set()
    for edge in edges:
        if edge["projection_profile"] != normalized or edge["eligibility_class"] != _eligibility_class(normalized):
            raise AIProjectionConflict("AI edge profile does not match the selected pointer")
        if edge["subject_id"] not in note_by_id or edge["object_id"] not in note_by_id:
            raise AIProjectionConflict("AI edge is incident to an omitted note")
        if edge["subject_path"] not in note_by_path or edge["object_path"] not in note_by_path:
            raise AIProjectionError("AI edge references a missing note path")
        if note_by_id[edge["subject_id"]]["path"] != edge["subject_path"]:
            raise AIProjectionError("AI edge subject id and path disagree")
        if note_by_id[edge["object_id"]]["path"] != edge["object_path"]:
            raise AIProjectionError("AI edge object id and path disagree")
        if edge["source_content_hash"] != note_by_id[edge["subject_id"]]["content_hash"]:
            raise AIProjectionConflict("AI edge source digest does not match its subject note")
        key = (
            edge["subject_id"],
            edge["edge_kind"],
            edge["predicate"],
            edge["object_id"],
            edge["object_locator"],
        )
        if key in edge_keys:
            raise AIProjectionError("AI projection contains duplicate canonical edge tuple")
        edge_keys.add(key)


def read_current_ai_projection(
    root: str | Path,
    *,
    profile: str,
    verify_sources: bool = True,
) -> tuple[dict[str, Any], int]:
    """Read one profile pointer and reject full-content or stale generations."""

    operation = "ai projection verify"
    try:
        workspace = _workspace(root)
        normalized = _normalize_profile(profile)
        pointer_path = _runtime_path(workspace, _current_pointer_path(normalized))
        pointer = _read_json(pointer_path, "AI projection current pointer")
        if (
            pointer.get("schema_version") != SCHEMA_VERSION
            or pointer.get("generator") != GENERATOR_ID
            or pointer.get("projection_profile") != normalized
            or pointer.get("eligibility_class") != _eligibility_class(normalized)
        ):
            raise AIProjectionConflict("current pointer is not a C29 privacy-minimized projection")
        generation_id = pointer.get("generation_id")
        if not isinstance(generation_id, str):
            raise AIProjectionError("AI projection generation_id is invalid")
        expected_root = _generation_root(normalized, generation_id)
        if pointer.get("generation_root") != expected_root:
            raise AIProjectionConflict("AI projection pointer generation root is invalid")
        for field, suffix in (
            ("notes_path", "notes.jsonl"),
            ("edges_path", "edges.jsonl"),
            ("manifest_path", "manifest.json"),
        ):
            if pointer.get(field) != f"{expected_root}/{suffix}":
                raise AIProjectionConflict(f"AI projection pointer {field} selects a different generation")
        generation, notes_path, edges_path, manifest_path = _generation_paths(
            workspace,
            normalized,
            generation_id,
        )
        del generation
        manifest_bytes = _regular_file(manifest_path, "AI projection manifest")
        if pointer.get("manifest_sha256") != _sha256_bytes(manifest_bytes):
            raise AIProjectionConflict("AI projection pointer manifest digest does not match")
        manifest = _read_json(manifest_path, "AI projection manifest")
        if (
            manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("generator") != GENERATOR_ID
            or manifest.get("projection_profile") != normalized
            or manifest.get("eligibility_class") != _eligibility_class(normalized)
            or manifest.get("eligibility_policy") != _profile_policy(normalized)
            or manifest.get("generation_id") != generation_id
            or manifest.get("generation_root") != expected_root
        ):
            raise AIProjectionConflict("AI projection manifest identity is invalid")
        if pointer.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
            raise AIProjectionConflict("AI projection pointer source digest does not match")
        schema_files, schema_bundle_sha256 = _schema_files(workspace)
        if manifest.get("schema_files") != list(schema_files) or manifest.get("schema_bundle_sha256") != schema_bundle_sha256:
            raise AIProjectionConflict("AI projection schema bundle differs from the generation manifest")
        blueprint_sha256 = _sha256_bytes(_regular_file(workspace / "blueprint/blueprint.yaml", "blueprint"))
        if manifest.get("blueprint_sha256") != blueprint_sha256:
            raise AIProjectionConflict("AI projection Blueprint digest does not match")
        notes_bytes = _regular_file(notes_path, "AI notes JSONL")
        edges_bytes = _regular_file(edges_path, "AI edges JSONL")
        if manifest.get("notes_sha256") != _sha256_bytes(notes_bytes) or manifest.get("edges_sha256") != _sha256_bytes(edges_bytes):
            raise AIProjectionConflict("AI projection file digest does not match the manifest")
        notes = _read_jsonl(notes_path, "AI notes JSONL")
        edges = _read_jsonl(edges_path, "AI edges JSONL")
        _verify_records(normalized, notes, edges)
        if manifest.get("notes_count") != len(notes) or manifest.get("edges_count") != len(edges):
            raise AIProjectionConflict("AI projection manifest counts do not match JSONL")
        if verify_sources:
            expected = build_ai_projection(workspace, profile=normalized, generation_id=generation_id)
            if expected.notes_bytes != notes_bytes or expected.edges_bytes != edges_bytes or expected.manifest_bytes != manifest_bytes:
                raise AIProjectionConflict("AI projection is stale or was generated from a full-content source")
        return {
            "status": "PASS",
            "operation": operation,
            "capability": "C29",
            "projection_profile": normalized,
            "eligibility_class": _eligibility_class(normalized),
            "generation_id": generation_id,
            "generation_root": expected_root,
            "manifest": manifest,
            "notes": notes,
            "edges": edges,
            "source_verified": verify_sources,
            "provider_called": False,
            "mutation_performed": False,
        }, EXIT_OK
    except AIProjectionConflict as error:
        return _failure(operation, "AI_PROJECTION_CONFLICT", str(error))
    except (OSError, UnicodeError, AIProjectionError, ProjectionError, KeyError, TypeError, ValueError) as error:
        return _failure(operation, "AI_PROJECTION_INVALID", str(error))


def verify_ai_projection(
    root: str | Path,
    *,
    profile: str,
    verify_sources: bool = True,
) -> tuple[dict[str, Any], int]:
    """Compatibility alias for the C29 verification operation."""

    return read_current_ai_projection(root, profile=profile, verify_sources=verify_sources)


def read_privacy_projection(
    root: str | Path,
    *,
    profile: str,
    verify_sources: bool = True,
) -> tuple[dict[str, Any], int]:
    """Compatibility alias for :func:`read_current_ai_projection`."""

    return read_current_ai_projection(root, profile=profile, verify_sources=verify_sources)


def load_current_ai_projection(
    root: str | Path,
    *,
    profile: str,
    verify_sources: bool = True,
) -> AIProjectionRead:
    """Return a typed verified C29 projection or raise an AI projection error."""

    report, code = read_current_ai_projection(root, profile=profile, verify_sources=verify_sources)
    if code != EXIT_OK:
        message = report.get("errors", [{"message": "AI projection read failed"}])[0]["message"]
        raise AIProjectionError(str(message))
    return AIProjectionRead(
        profile=report["projection_profile"],
        pointer={
            "generation_id": report["generation_id"],
            "generation_root": report["generation_root"],
        },
        manifest=report["manifest"],
        notes=tuple(report["notes"]),
        edges=tuple(report["edges"]),
    )

"""Deterministic C21 Markdown projection and generation publication.

The Vault remains the source of truth.  C21 reads validated Markdown notes,
materializes canonical note and relation records, and publishes one immutable
generation under ``runtime/index/exports``.  A single atomically replaced
pointer selects the generation that readers may use.  No provider, Git
command, or Vault mutation is performed by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .note_engine import (
    FrontmatterError,
    NoteEngine,
    NoteValidationResult,
    UnsafePathError,
    normalize_vault_relative_path,
    parse_frontmatter,
)
from .recovery import fsync_directory

EXIT_OK = 0
EXIT_CONFLICT = 30
EXIT_INPUT_INVALID = 10
SCHEMA_VERSION = 1
GENERATOR_ID = "vaultops.projection"

EXPORT_ROOT = "index/exports"
GENERATION_ROOT = f"{EXPORT_ROOT}/generations"
CURRENT_POINTER = f"{EXPORT_ROOT}/current.json"
PROJECTION_SCHEMA_PATHS = (
    "ops/schemas/note-record.schema.json",
    "ops/schemas/edge-record.schema.json",
    "ops/schemas/retrieval-candidate.schema.json",
    "ops/schemas/answer.schema.json",
)
PROJECTION_SERIALIZATION = {
    "encoding": "utf8",
    "bom": False,
    "newline": "lf",
    "terminal_newline_count": 1,
    "json_separators": "compact",
    "object_key_order": "unicode_codepoint_lexicographic_after_nfc",
    "record_sort": {
        "notes": ["path_nfc_utf8_bytes", "id"],
        "edges": ["subject_id", "edge_kind", "predicate", "object_id", "object_locator_nulls_first"],
    },
    "source_list_order": "preserve",
    "non_lf_or_invalid_utf8_source": "reject",
}

PROMOTED_FRONTMATTER_FIELDS = frozenset(
    {"schema_version", "id", "type", "title", "status", "created", "modified"}
)
CONTEXT_RELATIONS = (
    "projects",
    "areas",
    "topics",
    "sources",
    "people",
    "related",
)
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
NOTE_TYPES = (
    "area",
    "artifact",
    "capture",
    "daily",
    "home",
    "idea",
    "knowledge",
    "meeting",
    "moc",
    "monthly",
    "person",
    "project",
    "project_note",
    "proposal",
    "question",
    "source",
    "system",
    "weekly",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,199}$")
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")


class ProjectionError(ValueError):
    """Base error for an untrusted or unavailable projection input."""


class ProjectionConflict(ProjectionError):
    """Raised when publication or reading detects digest or generation drift."""


class ProjectionValidationError(ProjectionError):
    """Raised when a source note or generated record violates its contract."""


@dataclass(frozen=True)
class SourceNote:
    """One validated source note and its exact UTF-8 LF bytes."""

    path: str
    raw: bytes
    text: str
    properties: dict[str, Any]
    body: str
    note_type: str

    @property
    def identifier(self) -> str:
        return str(self.properties["id"])

    @property
    def content_hash(self) -> str:
        return _sha256_bytes(self.raw)


@dataclass(frozen=True)
class ProjectionBuild:
    """Pure, fully serialized projection output ready for publication."""

    generation_id: str
    notes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    source_files: tuple[dict[str, str], ...]
    source_snapshot_sha256: str
    notes_bytes: bytes
    edges_bytes: bytes
    manifest: dict[str, Any]
    manifest_bytes: bytes
    schema_files: tuple[dict[str, str], ...]
    blueprint_sha256: str
    retrieval_config_sha256: str | None

    @property
    def manifest_sha256(self) -> str:
        return _sha256_bytes(self.manifest_bytes)

    @property
    def generation_root(self) -> str:
        return f"{GENERATION_ROOT}/{self.generation_id}"


@dataclass(frozen=True)
class ProjectionRead:
    """One reader-pinned generation and its validated records."""

    pointer: dict[str, Any]
    manifest: dict[str, Any]
    notes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _canonical_value(value: Any) -> Any:
    """Return a JSON-compatible value with NFC-aware object key order."""

    if isinstance(value, Mapping):
        pairs: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProjectionValidationError("projection JSON object keys must be strings")
            pairs.append((key, item))
        pairs.sort(key=lambda item: (_nfc(item[0]), item[0]))
        return {key: _canonical_value(item) for key, item in pairs}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value using the projection byte contract."""

    try:
        document = _canonical_value(value)
        return (
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProjectionValidationError("projection value is not canonical JSON") from error


def _jsonl_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _sha256_schema() -> dict[str, Any]:
    return {"type": "string", "pattern": _SHA256.pattern}


def _identifier_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
        "anyOf": [
            {"pattern": _UUID4.pattern},
            {"pattern": _ID.pattern},
        ],
    }


def _path_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 1000,
        "pattern": r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*\\)[^\r\n\x00]+$",
    }


def _datetime_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "format": "date-time",
        "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    }


def _flat_value_schema() -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
            {"type": "array", "items": {"type": "string"}},
        ]
    }


def note_record_schema() -> dict[str, Any]:
    """Return the strict C21 note-record schema."""

    wikilink = {
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
    properties = {
        "id": _identifier_schema(),
        "path": _path_schema(),
        "title": {"type": "string", "minLength": 1},
        "type": {"enum": list(NOTE_TYPES)},
        "status": {"type": "string", "minLength": 1},
        "properties": {
            "type": "object",
            "additionalProperties": _flat_value_schema(),
        },
        "body": {"type": "string"},
        "wikilinks": {"type": "array", "items": wikilink},
        "created": _datetime_schema(),
        "modified": _datetime_schema(),
        "content_hash": _sha256_schema(),
        "schema_version": {"const": SCHEMA_VERSION},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/note-record.schema.json",
        "title": "KnowledgeOS C21 projected note record",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "path",
            "title",
            "type",
            "status",
            "properties",
            "body",
            "wikilinks",
            "created",
            "modified",
            "content_hash",
            "schema_version",
        ],
        "properties": properties,
    }


def edge_record_schema() -> dict[str, Any]:
    """Return the strict C21 canonical-edge schema."""

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
        "source_content_hash": _sha256_schema(),
        "relation_schema_version": {"const": SCHEMA_VERSION},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/edge-record.schema.json",
        "title": "KnowledgeOS C21 projected canonical edge record",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "subject_id",
            "edge_kind",
            "predicate",
            "object_id",
            "subject_path",
            "object_path",
            "provenance",
            "source_locator",
            "object_locator",
            "source_content_hash",
            "relation_schema_version",
        ],
        "properties": properties,
    }


def retrieval_candidate_schema() -> dict[str, Any]:
    """Return the downstream C22 frozen candidate schema."""

    nullable_object = {"anyOf": [{"type": "object"}, {"type": "null"}]}
    properties: dict[str, Any] = {
        "query_sha256": _sha256_schema(),
        "policy_decision_sha256": _sha256_schema(),
        "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "note_id": _identifier_schema(),
        "path": _path_schema(),
        "content_hash": _sha256_schema(),
        "chunk_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "chunk_hash": _sha256_schema(),
        "chunk_locator": {"type": "string", "minLength": 1, "maxLength": 1000},
        "retrieval_reason": {"type": "string", "minLength": 1, "maxLength": 1000},
        "lexical_score_and_rank": {"type": "object"},
        "vector_score_and_rank": nullable_object,
        "rrf_parameter_and_rank": nullable_object,
        "graph_path": {"type": "array", "items": {"type": "string"}},
        "parser_and_chunker_version": {"type": "string", "minLength": 1},
        "embedding_provider_model_dimension_and_artifact_digest": nullable_object,
        "indexer_version": {"type": "string", "minLength": 1},
        "retrieval_config_sha256": _sha256_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/retrieval-candidate.schema.json",
        "title": "KnowledgeOS C22 frozen retrieval candidate",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def answer_schema() -> dict[str, Any]:
    """Return the C26 byte-bound cited answer schema."""

    citation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "note_id",
            "path",
            "content_hash",
            "locator",
            "chunk_id",
            "chunk_hash",
            "index_generation_id",
            "excerpt",
            "excerpt_sha256",
            "sha256",
        ],
        "properties": {
            "note_id": _identifier_schema(),
            "path": _path_schema(),
            "content_hash": _sha256_schema(),
            "locator": {"type": "string", "minLength": 1},
            "chunk_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "chunk_hash": _sha256_schema(),
            "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 1200},
            "excerpt_sha256": _sha256_schema(),
            "sha256": _sha256_schema(),
        },
    }
    summary = {
        "$anchor": "summary",
        "type": "object",
        "additionalProperties": False,
        "required": ["summary_id", "summary_plaintext", "citations", "uncertainty", "summary_sha256"],
        "properties": {
            "summary_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "summary_plaintext": {"type": "string", "minLength": 1, "maxLength": 20000},
            "citations": {"type": "array", "minItems": 1, "items": citation},
            "uncertainty": {"type": "string", "minLength": 1},
            "summary_sha256": _sha256_schema(),
        },
    }
    answer = {
        "$anchor": "answer",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer_id", "answer_plaintext", "citations", "uncertainty", "answer_sha256"],
        "properties": {
            "answer_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "answer_plaintext": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20000,
                "pattern": r"^(?![\s\S]*<[^>]+>)(?![\s\S]*!\[\[)(?![\s\S]*(?:obsidian|command):)[\s\S]*$",
            },
            "citations": {"type": "array", "minItems": 1, "items": citation},
            "uncertainty": {"type": "string", "minLength": 1},
            "answer_sha256": _sha256_schema(),
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/answer.schema.json",
        "title": "KnowledgeOS C23 cited answer or summary",
        "oneOf": [{"$ref": "#/$defs/summary"}, {"$ref": "#/$defs/answer"}],
        "$defs": {"summary": summary, "answer": answer},
    }


def build_note_record_schema() -> dict[str, Any]:
    """Compatibility alias for schema generation callers."""

    return note_record_schema()


def build_edge_record_schema() -> dict[str, Any]:
    """Compatibility alias for schema generation callers."""

    return edge_record_schema()


def build_retrieval_candidate_schema() -> dict[str, Any]:
    """Compatibility alias for schema generation callers."""

    return retrieval_candidate_schema()


def build_answer_schema() -> dict[str, Any]:
    """Compatibility alias for schema generation callers."""

    return answer_schema()


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ProjectionError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise ProjectionError("KnowledgeHub must be an existing non-symlink directory")
    return workspace


def _vault(workspace: Path) -> Path:
    return workspace / "KnowledgeHub"


def _safe_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ProjectionError(f"{label} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ProjectionError(f"cannot read {label}: {error}") from error


def _has_non_lf_line_break(text: str) -> bool:
    return any(marker in text for marker in ("\r", "\v", "\f", "\x85", "\u2028", "\u2029"))


def _protected_source(relative: str) -> bool:
    parts = relative.split("/")
    if any(part in {".git", ".vault-bridge", ".obsidian"} or part.startswith(".obsidian-") for part in parts):
        return True
    return relative.startswith("99_System/Templates/")


def _candidate_source_paths(workspace: Path) -> tuple[Path, ...]:
    vault = _vault(workspace)
    paths: list[Path] = []
    for path in sorted(vault.rglob("*"), key=lambda item: item.relative_to(vault).as_posix()):
        if path.is_symlink():
            raise ProjectionError(f"Vault projection source contains a symlink: {path.relative_to(vault)}")
        if not path.is_file() or path.suffix.casefold() != ".md":
            continue
        relative = path.relative_to(vault).as_posix()
        if _protected_source(relative):
            continue
        paths.append(path)
    return tuple(paths)


def _read_source_candidate(workspace: Path, path: Path, engine: NoteEngine) -> SourceNote | None:
    vault = _vault(workspace)
    relative = normalize_vault_relative_path(path.relative_to(vault).as_posix())
    expected_type = engine.note_type_for_path(relative)
    if expected_type is None:
        return None
    raw = _safe_file(path, f"Vault source {relative}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectionValidationError(f"source is not valid UTF-8: {relative}") from error
    if _has_non_lf_line_break(text):
        raise ProjectionValidationError(f"source must use LF newlines only: {relative}")
    try:
        document = parse_frontmatter(text)
    except (FrontmatterError, UnicodeError) as error:
        # Static system Markdown such as generated dashboards may share the
        # broad system glob without being typed notes.  Content namespaces
        # remain strict and reject a missing or malformed frontmatter block.
        if relative.startswith("99_System/"):
            return None
        raise ProjectionValidationError(f"source frontmatter is invalid: {relative}: {error}") from error
    return SourceNote(relative, raw, text, document.properties, document.body, expected_type)


def _collect_source_notes(workspace: Path) -> tuple[SourceNote, ...]:
    engine = NoteEngine.from_root(workspace)
    notes: list[SourceNote] = []
    for path in _candidate_source_paths(workspace):
        note = _read_source_candidate(workspace, path, engine)
        if note is not None:
            notes.append(note)
    return tuple(sorted(notes, key=lambda item: _path_sort_key(item.path)))


def _path_sort_key(path: str) -> tuple[bytes, str]:
    normalized = _nfc(path)
    return normalized.encode("utf-8"), normalized


def _lookup_key(value: str) -> str:
    return _nfc(value).casefold()


def _target_aliases(note: SourceNote) -> tuple[str, ...]:
    path = note.path
    path_without_suffix = path.removesuffix(".md")
    aliases = note.properties.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    return (
        path,
        path_without_suffix,
        PurePosixPath(path).stem,
        str(note.properties["title"]),
        *(str(alias) for alias in aliases if isinstance(alias, str) and alias),
    )


def _target_index(notes: Iterable[SourceNote]) -> tuple[dict[str, SourceNote], dict[str, str]]:
    by_alias: dict[str, SourceNote | None] = {}
    target_types: dict[str, str] = {}
    for note in notes:
        for alias in _target_aliases(note):
            key = _lookup_key(alias)
            previous = by_alias.get(key)
            if previous is not None and previous.path != note.path:
                by_alias[key] = None
            elif key not in by_alias:
                by_alias[key] = note
            if key in by_alias and by_alias[key] is note:
                target_types[alias] = note.note_type
                target_types[key] = note.note_type
    resolved = {key: note for key, note in by_alias.items() if note is not None}
    return resolved, target_types


def _split_link_target(inner: str) -> tuple[str, str | None]:
    target = inner.split("|", 1)[0].strip()
    if "#" in target:
        base, fragment = target.split("#", 1)
        return base.strip(), f"#{fragment}" if fragment else None
    if target.startswith("^"):
        return "", target if len(target) > 1 else None
    if "^" in target and not target.startswith("^"):
        base, fragment = target.split("^", 1)
        return base.strip(), f"^{fragment}" if fragment else None
    return target, None


def _resolve_target(
    inner: str,
    current: SourceNote,
    targets: Mapping[str, SourceNote],
) -> tuple[SourceNote | None, str | None]:
    base, locator = _split_link_target(inner)
    if not base or base == "." or base.startswith("^"):
        return current, locator
    return targets.get(_lookup_key(base)), locator


def _wikilink_records(
    note: SourceNote,
    targets: Mapping[str, SourceNote],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in _WIKILINK.finditer(note.body):
        raw_target = match.group(1)
        resolved, locator = _resolve_target(raw_target, note, targets)
        records.append(
            {
                "raw_target": raw_target,
                "resolved_id": resolved.identifier if resolved is not None else None,
                "resolved_path": resolved.path if resolved is not None else None,
                "locator": locator,
            }
        )
    return records


def _source_snapshot(notes: Iterable[SourceNote]) -> tuple[tuple[dict[str, str], ...], str]:
    files = tuple(
        {"path": note.path, "sha256": note.content_hash}
        for note in sorted(notes, key=lambda item: _path_sort_key(item.path))
    )
    return files, _sha256_bytes(canonical_json_bytes(list(files)))


def _validation_message(result: NoteValidationResult) -> str:
    details = [issue.as_dict() for issue in result.errors]
    return json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_sort_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (_nfc(str(record["path"])).encode("utf-8"), str(record["id"]))


def _edge_sort_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    locator = record.get("object_locator")
    return (
        str(record["subject_id"]),
        str(record["edge_kind"]),
        str(record["predicate"]),
        str(record["object_id"]),
        0 if locator is None else 1,
        "" if locator is None else str(locator),
    )


def _build_records(workspace: Path) -> tuple[tuple[SourceNote, ...], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    notes = _collect_source_notes(workspace)
    if not notes:
        raise ProjectionValidationError("Vault contains no typed Markdown notes")
    targets, target_types = _target_index(notes)
    engine = NoteEngine.from_root(workspace)
    ids: dict[str, str] = {}
    note_records: list[dict[str, Any]] = []
    for note in notes:
        previous = ids.get(note.identifier)
        if previous is not None and previous != note.path:
            raise ProjectionValidationError(
                f"duplicate note id {note.identifier!r}: {previous} and {note.path}"
            )
        ids[note.identifier] = note.path
        result = engine.validate_text(note.path, note.text, target_types=target_types)
        if not result.passed:
            raise ProjectionValidationError(f"note validation failed for {note.path}: {_validation_message(result)}")
        properties = {
            key: value
            for key, value in note.properties.items()
            if key not in PROMOTED_FRONTMATTER_FIELDS
        }
        note_records.append(
            {
                "id": note.identifier,
                "path": note.path,
                "title": str(note.properties["title"]),
                "type": str(note.properties["type"]),
                "status": str(note.properties["status"]),
                "properties": properties,
                "body": note.body,
                "wikilinks": _wikilink_records(note, targets),
                "created": str(note.properties["created"]),
                "modified": str(note.properties["modified"]),
                "content_hash": note.content_hash,
                "schema_version": SCHEMA_VERSION,
            }
        )

    edge_records: list[dict[str, Any]] = []
    seen_edges: dict[tuple[Any, ...], str] = {}
    by_path = {note.path: note for note in notes}
    for note in notes:
        for predicate in RELATION_PROPERTIES:
            values = note.properties.get(predicate, [])
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                if not isinstance(value, str) or not value.startswith("[[") or not value.endswith("]]"):
                    continue
                target, locator = _resolve_target(value[2:-2], note, targets)
                if target is None:
                    raise ProjectionValidationError(
                        f"canonical relation target is unresolved: {note.path} {predicate}[{index}]"
                    )
                # Keep the resolver result tied to the source map so an alias
                # cannot accidentally point outside the records being emitted.
                if target.path not in by_path:
                    raise ProjectionValidationError("canonical relation target is outside the projection")
                edge_kind = "semantic" if predicate in SEMANTIC_RELATIONS else "context"
                edge = {
                    "subject_id": note.identifier,
                    "edge_kind": edge_kind,
                    "predicate": predicate,
                    "object_id": target.identifier,
                    "subject_path": note.path,
                    "object_path": target.path,
                    "provenance": "canonical_property",
                    "source_locator": f"/{predicate}/{index}",
                    "object_locator": locator,
                    "source_content_hash": note.content_hash,
                    "relation_schema_version": SCHEMA_VERSION,
                }
                duplicate_key = (
                    edge["subject_id"],
                    edge["edge_kind"],
                    edge["predicate"],
                    edge["object_id"],
                    edge["object_locator"],
                )
                previous = seen_edges.get(duplicate_key)
                if previous is not None:
                    raise ProjectionValidationError(
                        f"duplicate canonical edge tuple at {note.path}: {previous} and {edge['source_locator']}"
                    )
                seen_edges[duplicate_key] = edge["source_locator"]
                edge_records.append(edge)

    note_records.sort(key=_record_sort_key)
    edge_records.sort(key=_edge_sort_key)
    return notes, tuple(note_records), tuple(edge_records)


def _schema_files(workspace: Path) -> tuple[tuple[dict[str, str], ...], str]:
    result: list[dict[str, str]] = []
    for relative in PROJECTION_SCHEMA_PATHS:
        path = workspace / relative
        result.append({"path": relative, "sha256": _sha256_bytes(_safe_file(path, relative))})
    ordered = tuple(result)
    return ordered, _sha256_bytes(canonical_json_bytes(list(ordered)))


def _generation_id(
    source_snapshot_sha256: str,
    schema_files: Iterable[Mapping[str, str]],
    blueprint_sha256: str,
) -> str:
    seed = canonical_json_bytes(
        {
            "blueprint_sha256": blueprint_sha256,
            "schema_files": list(schema_files),
            "source_snapshot_sha256": source_snapshot_sha256,
        }
    )
    return f"gen-{_sha256_bytes(seed)[:48]}"


def _retrieval_config_hash(workspace: Path) -> str | None:
    retrieval_path = workspace / "ops/policies/retrieval.yaml"
    if retrieval_path.is_file() and not retrieval_path.is_symlink():
        return _sha256_bytes(_safe_file(retrieval_path, "retrieval policy"))
    return None


def _build_manifest(
    *,
    generation_id: str,
    notes: tuple[dict[str, Any], ...],
    edges: tuple[dict[str, Any], ...],
    notes_bytes: bytes,
    edges_bytes: bytes,
    source_files: tuple[dict[str, str], ...],
    source_snapshot_sha256: str,
    schema_files: tuple[dict[str, str], ...],
    schema_bundle_sha256: str,
    blueprint_sha256: str,
    retrieval_config_sha256: str | None,
) -> dict[str, Any]:
    notes_sha256 = _sha256_bytes(notes_bytes)
    edges_sha256 = _sha256_bytes(edges_bytes)
    generation_root = f"{GENERATION_ROOT}/{generation_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR_ID,
        "generation_id": generation_id,
        "generation_root": generation_root,
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_files": list(source_files),
        "blueprint_sha256": blueprint_sha256,
        "retrieval_config_sha256": retrieval_config_sha256,
        "schema_bundle_sha256": schema_bundle_sha256,
        "schema_files": list(schema_files),
        "notes_count": len(notes),
        "edges_count": len(edges),
        "note_count": len(notes),
        "edge_count": len(edges),
        "notes_sha256": notes_sha256,
        "edges_sha256": edges_sha256,
        "files": {
            "notes.jsonl": notes_sha256,
            "edges.jsonl": edges_sha256,
        },
        "serialization": PROJECTION_SERIALIZATION,
    }


def build_projection(root: str | Path, *, generation_id: str | None = None) -> ProjectionBuild:
    """Build deterministic records without writing Vault or runtime state."""

    workspace = _workspace(root)
    if generation_id is not None and not _GENERATION_ID.fullmatch(generation_id):
        raise ProjectionError("generation_id contains unsafe characters")
    notes, note_records, edge_records = _build_records(workspace)
    source_files, source_snapshot_sha256 = _source_snapshot(notes)
    notes_bytes = _jsonl_bytes(note_records)
    edges_bytes = _jsonl_bytes(edge_records)
    blueprint_path = workspace / "blueprint/blueprint.yaml"
    blueprint_sha256 = _sha256_bytes(_safe_file(blueprint_path, "blueprint"))
    retrieval_config_sha256 = _retrieval_config_hash(workspace)
    schema_files, schema_bundle_sha256 = _schema_files(workspace)
    selected_generation_id = generation_id or _generation_id(
        source_snapshot_sha256,
        schema_files,
        blueprint_sha256,
    )
    manifest = _build_manifest(
        generation_id=selected_generation_id,
        notes=note_records,
        edges=edge_records,
        notes_bytes=notes_bytes,
        edges_bytes=edges_bytes,
        source_files=source_files,
        source_snapshot_sha256=source_snapshot_sha256,
        schema_files=schema_files,
        schema_bundle_sha256=schema_bundle_sha256,
        blueprint_sha256=blueprint_sha256,
        retrieval_config_sha256=retrieval_config_sha256,
    )
    manifest_bytes = canonical_json_bytes(manifest)
    return ProjectionBuild(
        generation_id=selected_generation_id,
        notes=note_records,
        edges=edge_records,
        source_files=source_files,
        source_snapshot_sha256=source_snapshot_sha256,
        notes_bytes=notes_bytes,
        edges_bytes=edges_bytes,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        schema_files=schema_files,
        blueprint_sha256=blueprint_sha256,
        retrieval_config_sha256=retrieval_config_sha256,
    )


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ProjectionConflict(f"runtime directory is a symlink: {path}")
    if path.exists():
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise ProjectionConflict(f"runtime directory must be mode 0700: {path}")
        return
    path.mkdir(mode=0o700, parents=True)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ProjectionConflict(f"runtime directory was not created mode 0700: {path}")


def _atomic_runtime_write(path: Path, payload: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ProjectionConflict(f"runtime output is not a regular file: {path}")
    _ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        fsync_directory(path.parent)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _runtime_relative_path(relative: str) -> PurePosixPath:
    if not isinstance(relative, str) or "\\" in relative or relative.startswith("/"):
        raise ProjectionConflict("runtime path must be relative POSIX text")
    candidate = PurePosixPath(relative)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ProjectionConflict("runtime path contains an unsafe component")
    return candidate


def _runtime_path(workspace: Path, relative: str) -> Path:
    candidate = _runtime_relative_path(relative)
    runtime = workspace / "runtime"
    if runtime.is_symlink():
        raise ProjectionConflict(f"runtime root is a symlink: {runtime}")
    path = runtime.joinpath(*candidate.parts)
    current = runtime
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ProjectionConflict(f"runtime path traverses a symlink: {relative}")
    return path


def _generation_paths(workspace: Path, generation_id: str) -> tuple[Path, Path, Path, Path]:
    if not _GENERATION_ID.fullmatch(generation_id):
        raise ProjectionError("generation_id contains unsafe characters")
    root_relative = f"{GENERATION_ROOT}/{generation_id}"
    generation = _runtime_path(workspace, root_relative)
    return (
        generation,
        generation / "notes.jsonl",
        generation / "edges.jsonl",
        generation / "manifest.json",
    )


def _assert_generation_file(path: Path, expected: bytes, label: str) -> bool:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ProjectionConflict(f"generation {label} is not a regular file")
    if not path.exists():
        return False
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ProjectionConflict(f"generation {label} must be mode 0600")
    actual = path.read_bytes()
    if actual != expected:
        raise ProjectionConflict(f"generation {label} conflicts with its deterministic bytes")
    return True


def _publish_generation(workspace: Path, build: ProjectionBuild) -> tuple[bool, str, str, str]:
    generation, notes_path, edges_path, manifest_path = _generation_paths(workspace, build.generation_id)
    _ensure_private_directory(workspace / "runtime")
    _ensure_private_directory(workspace / "runtime" / "index")
    _ensure_private_directory(workspace / "runtime" / "index" / "exports")
    _ensure_private_directory(workspace / "runtime" / "index" / "exports" / "generations")
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
        fsync_directory(generation)

    # The generation files are durable before this final source check.  Only
    # after the snapshot still matches may the pointer become visible to a
    # reader, so a source race can leave at most an unreferenced generation.
    if not _source_snapshot_matches(workspace, build.source_files):
        raise ProjectionConflict("Vault source changed before current pointer publication")

    pointer = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR_ID,
        "generation_id": build.generation_id,
        "generation_root": f"{GENERATION_ROOT}/{build.generation_id}",
        "notes_path": f"{GENERATION_ROOT}/{build.generation_id}/notes.jsonl",
        "edges_path": f"{GENERATION_ROOT}/{build.generation_id}/edges.jsonl",
        "manifest_path": f"{GENERATION_ROOT}/{build.generation_id}/manifest.json",
        "manifest_sha256": build.manifest_sha256,
        "source_snapshot_sha256": build.source_snapshot_sha256,
    }
    pointer_bytes = canonical_json_bytes(pointer)
    pointer_path = _runtime_path(workspace, CURRENT_POINTER)
    if pointer_path.is_symlink():
        raise ProjectionConflict("current projection pointer is a symlink")
    if pointer_path.exists() and not pointer_path.is_file():
        raise ProjectionConflict("current projection pointer is not a regular file")
    if pointer_path.is_file() and stat.S_IMODE(pointer_path.stat().st_mode) != 0o600:
        raise ProjectionConflict("current projection pointer must be mode 0600")
    before = pointer_path.read_bytes() if pointer_path.is_file() else None
    if before != pointer_bytes:
        _atomic_runtime_write(pointer_path, pointer_bytes)
        changed = True
    else:
        changed = False
    return changed, pointer["generation_root"], pointer["notes_path"], pointer["edges_path"]


def _source_snapshot_matches(workspace: Path, expected: tuple[dict[str, str], ...]) -> bool:
    try:
        notes = _collect_source_notes(workspace)
    except ProjectionError:
        return False
    observed, _ = _source_snapshot(notes)
    return observed == expected


def _failure(operation: str, code: str, message: str, *, mutation: bool = False) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": "C21",
        "provider_called": False,
        "mutation_performed": mutation,
        "errors": [{"code": code, "message": message}],
    }, EXIT_CONFLICT


def generate_projection(
    root: str | Path,
    *,
    generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Build and atomically publish one deterministic projection generation."""

    operation = "index build"
    try:
        workspace = _workspace(root)
        build = build_projection(workspace, generation_id=generation_id)
        if not _source_snapshot_matches(workspace, build.source_files):
            return _failure(operation, "PROJECTION_SOURCE_DRIFT", "Vault source changed during projection build")
        changed, generation_root, notes_path, edges_path = _publish_generation(workspace, build)
        return {
            "status": "PASS",
            "operation": operation,
            "capability": "C21",
            "generation_id": build.generation_id,
            "generation_root": generation_root,
            "notes_path": notes_path,
            "edges_path": edges_path,
            "manifest_path": f"{generation_root}/manifest.json",
            "manifest_sha256": build.manifest_sha256,
            "source_snapshot_sha256": build.source_snapshot_sha256,
            "note_count": len(build.notes),
            "edge_count": len(build.edges),
            "pointer_swapped": changed,
            "provider_called": False,
            "mutation_performed": changed,
        }, EXIT_OK
    except ProjectionConflict as error:
        return _failure(operation, "PROJECTION_CONFLICT", str(error))
    except (OSError, UnicodeError, ProjectionError, UnsafePathError, KeyError, TypeError, ValueError) as error:
        return _failure(operation, "PROJECTION_INVALID", str(error))


def project_vault(root: str | Path, *, generation_id: str | None = None) -> tuple[dict[str, Any], int]:
    """Compatibility alias for :func:`generate_projection`."""

    return generate_projection(root, generation_id=generation_id)


def export_jsonl(root: str | Path, *, generation_id: str | None = None) -> tuple[dict[str, Any], int]:
    """Compatibility alias for the ``vaultctl export jsonl`` command."""

    return generate_projection(root, generation_id=generation_id)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectionValidationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    raw = _safe_file(path, label)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, ProjectionError) as error:
        raise ProjectionValidationError(f"{label} is not valid strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise ProjectionValidationError(f"{label} must contain a JSON object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    raw = _safe_file(path, label)
    if not raw:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectionValidationError(f"{label} is not UTF-8") from error
    if "\r" in text or not text.endswith("\n"):
        raise ProjectionValidationError(f"{label} must be UTF-8 LF JSONL with terminal newlines")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            value = json.loads(line, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, ProjectionValidationError) as error:
            raise ProjectionValidationError(f"{label} line {line_number} is invalid JSON: {error}") from error
        if not isinstance(value, dict):
            raise ProjectionValidationError(f"{label} line {line_number} must be an object")
        records.append(value)
    return records


def _validate_records(records: list[dict[str, Any]], schema: Mapping[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for index, record in enumerate(records):
        errors = sorted(validator.iter_errors(record), key=lambda error: error.json_path)
        if errors:
            raise ProjectionValidationError(f"{label} record {index} violates schema: {errors[0].message}")


def _verify_record_order(records: list[dict[str, Any]], key_function, label: str) -> None:
    expected = sorted(records, key=key_function)
    if records != expected:
        raise ProjectionValidationError(f"{label} records are not in canonical order")


def _validated_source_files(manifest: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    source_files = manifest.get("source_files")
    if not isinstance(source_files, list):
        raise ProjectionValidationError("manifest source_files must be a list")
    result: list[dict[str, str]] = []
    for item in source_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ProjectionValidationError("manifest source_files entries must contain only path and sha256")
        path = item["path"]
        digest = item["sha256"]
        if not isinstance(path, str) or not isinstance(digest, str):
            raise ProjectionValidationError("manifest source_files entries must use string values")
        try:
            normalize_vault_relative_path(path)
        except UnsafePathError as error:
            raise ProjectionValidationError(f"manifest source path is unsafe: {path}") from error
        if not _SHA256.fullmatch(digest):
            raise ProjectionValidationError(f"manifest source digest is invalid: {path}")
        result.append({"path": path, "sha256": digest})
    ordered = tuple(sorted(result, key=lambda item: _path_sort_key(item["path"])))
    if tuple(result) != ordered:
        raise ProjectionValidationError("manifest source_files are not in canonical order")
    if len({item["path"] for item in result}) != len(result):
        raise ProjectionValidationError("manifest source_files contain duplicate paths")
    return tuple(result)


def _verify_manifest_metadata(
    workspace: Path,
    pointer: Mapping[str, Any],
    manifest: Mapping[str, Any],
    generation_id: str,
    expected_root: str,
) -> tuple[dict[str, str], ...]:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("generator") != GENERATOR_ID:
        raise ProjectionValidationError("projection manifest identity is invalid")
    if manifest.get("generation_id") != generation_id or manifest.get("generation_root") != expected_root:
        raise ProjectionConflict("manifest selects a different generation")
    if pointer.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        raise ProjectionConflict("current pointer source digest does not match the manifest")

    source_files = _validated_source_files(manifest)
    source_snapshot_sha256 = manifest.get("source_snapshot_sha256")
    if not isinstance(source_snapshot_sha256, str) or not _SHA256.fullmatch(source_snapshot_sha256):
        raise ProjectionValidationError("manifest source_snapshot_sha256 is invalid")
    if source_snapshot_sha256 != _sha256_bytes(canonical_json_bytes(list(source_files))):
        raise ProjectionConflict("manifest source snapshot digest does not match source_files")

    schema_files, schema_bundle_sha256 = _schema_files(workspace)
    if manifest.get("schema_files") != list(schema_files):
        raise ProjectionConflict("projection schema files differ from the generation manifest")
    if manifest.get("schema_bundle_sha256") != schema_bundle_sha256:
        raise ProjectionConflict("projection schema bundle digest does not match")

    blueprint_sha256 = manifest.get("blueprint_sha256")
    if not isinstance(blueprint_sha256, str) or not _SHA256.fullmatch(blueprint_sha256):
        raise ProjectionValidationError("manifest blueprint_sha256 is invalid")
    if blueprint_sha256 != _sha256_bytes(_safe_file(workspace / "blueprint/blueprint.yaml", "blueprint")):
        raise ProjectionConflict("projection blueprint digest does not match")

    retrieval_config_sha256 = manifest.get("retrieval_config_sha256")
    if retrieval_config_sha256 is not None and (
        not isinstance(retrieval_config_sha256, str) or not _SHA256.fullmatch(retrieval_config_sha256)
    ):
        raise ProjectionValidationError("manifest retrieval_config_sha256 is invalid")
    if retrieval_config_sha256 != _retrieval_config_hash(workspace):
        raise ProjectionConflict("projection retrieval policy digest does not match")

    for field in ("notes_sha256", "edges_sha256"):
        value = manifest.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ProjectionValidationError(f"manifest {field} is invalid")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"notes.jsonl", "edges.jsonl"}:
        raise ProjectionValidationError("manifest files must select notes.jsonl and edges.jsonl only")
    if files.get("notes.jsonl") != manifest.get("notes_sha256") or files.get("edges.jsonl") != manifest.get("edges_sha256"):
        raise ProjectionConflict("manifest file digests do not match its digest fields")

    if not isinstance(manifest.get("notes_count"), int) or manifest["notes_count"] < 0:
        raise ProjectionValidationError("manifest notes_count is invalid")
    if not isinstance(manifest.get("edges_count"), int) or manifest["edges_count"] < 0:
        raise ProjectionValidationError("manifest edges_count is invalid")
    if manifest.get("note_count") != manifest.get("notes_count") or manifest.get("edge_count") != manifest.get("edges_count"):
        raise ProjectionConflict("manifest singular and plural counts disagree")
    if manifest.get("serialization") != PROJECTION_SERIALIZATION:
        raise ProjectionConflict("projection serialization metadata does not match")
    return source_files


def _verify_source_records(workspace: Path, manifest: Mapping[str, Any]) -> None:
    expected = _validated_source_files(manifest)
    if not _source_snapshot_matches(workspace, expected):
        raise ProjectionConflict("current projection is stale against Vault source bytes")


def read_current_projection(
    root: str | Path,
    *,
    verify_sources: bool = True,
) -> tuple[dict[str, Any], int]:
    """Read one pointer once and validate exactly one generation."""

    operation = "index verify"
    try:
        workspace = _workspace(root)
        pointer_path = _runtime_path(workspace, CURRENT_POINTER)
        pointer = _read_json(pointer_path, "current projection pointer")
        if pointer.get("schema_version") != SCHEMA_VERSION or pointer.get("generator") != GENERATOR_ID:
            raise ProjectionValidationError("current projection pointer identity is invalid")
        generation_id = pointer.get("generation_id")
        if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
            raise ProjectionValidationError("current projection generation_id is invalid")
        expected_root = f"{GENERATION_ROOT}/{generation_id}"
        if pointer.get("generation_root") != expected_root:
            raise ProjectionConflict("current pointer generation root does not match its generation id")
        for field, suffix in (
            ("notes_path", "notes.jsonl"),
            ("edges_path", "edges.jsonl"),
            ("manifest_path", "manifest.json"),
        ):
            if pointer.get(field) != f"{expected_root}/{suffix}":
                raise ProjectionConflict(f"current pointer {field} does not select one generation")
        manifest_path = _runtime_path(workspace, str(pointer["manifest_path"]))
        notes_path = _runtime_path(workspace, str(pointer["notes_path"]))
        edges_path = _runtime_path(workspace, str(pointer["edges_path"]))
        manifest_bytes = _safe_file(manifest_path, "projection manifest")
        observed_manifest_sha256 = _sha256_bytes(manifest_bytes)
        if pointer.get("manifest_sha256") != observed_manifest_sha256:
            raise ProjectionConflict("current pointer manifest digest does not match")
        manifest = _read_json(manifest_path, "projection manifest")
        source_files = _verify_manifest_metadata(workspace, pointer, manifest, generation_id, expected_root)
        notes_bytes = _safe_file(notes_path, "notes JSONL")
        edges_bytes = _safe_file(edges_path, "edges JSONL")
        if manifest.get("notes_sha256") != _sha256_bytes(notes_bytes):
            raise ProjectionConflict("notes JSONL digest does not match the manifest")
        if manifest.get("edges_sha256") != _sha256_bytes(edges_bytes):
            raise ProjectionConflict("edges JSONL digest does not match the manifest")
        notes = _read_jsonl(notes_path, "notes JSONL")
        edges = _read_jsonl(edges_path, "edges JSONL")
        if _jsonl_bytes(notes) != notes_bytes or _jsonl_bytes(edges) != edges_bytes:
            raise ProjectionValidationError("projection JSONL is not serialized canonically")
        _validate_records(notes, note_record_schema(), "notes")
        _validate_records(edges, edge_record_schema(), "edges")
        _verify_record_order(notes, _record_sort_key, "note")
        _verify_record_order(edges, _edge_sort_key, "edge")
        note_by_id = {str(record["id"]): record for record in notes}
        note_by_path = {str(record["path"]): record for record in notes}
        if len(note_by_id) != len(notes) or len(note_by_path) != len(notes):
            raise ProjectionValidationError("projection contains duplicate note identity")
        source_by_path = {item["path"]: item["sha256"] for item in source_files}
        if set(source_by_path) != set(note_by_path):
            raise ProjectionConflict("projection note paths do not match the source snapshot")
        for note in notes:
            if note["content_hash"] != source_by_path[note["path"]]:
                raise ProjectionConflict("projected note content digest does not match the source snapshot")
        edge_keys: set[tuple[Any, ...]] = set()
        for edge in edges:
            if edge["subject_id"] not in note_by_id or edge["object_id"] not in note_by_id:
                raise ProjectionValidationError("edge references a missing note id")
            if edge["subject_path"] not in note_by_path or edge["object_path"] not in note_by_path:
                raise ProjectionValidationError("edge references a missing note path")
            if note_by_id[edge["subject_id"]]["path"] != edge["subject_path"]:
                raise ProjectionValidationError("edge subject id and path disagree")
            if note_by_id[edge["object_id"]]["path"] != edge["object_path"]:
                raise ProjectionValidationError("edge object id and path disagree")
            if edge["source_content_hash"] != note_by_id[edge["subject_id"]]["content_hash"]:
                raise ProjectionConflict("edge source digest does not match its subject note")
            key = (
                edge["subject_id"],
                edge["edge_kind"],
                edge["predicate"],
                edge["object_id"],
                edge["object_locator"],
            )
            if key in edge_keys:
                raise ProjectionValidationError("projection contains duplicate canonical edge tuple")
            edge_keys.add(key)
        if manifest.get("notes_count", manifest.get("note_count")) != len(notes):
            raise ProjectionConflict("manifest note count does not match notes JSONL")
        if manifest.get("edges_count", manifest.get("edge_count")) != len(edges):
            raise ProjectionConflict("manifest edge count does not match edges JSONL")
        if verify_sources:
            _verify_source_records(workspace, manifest)
        return {
            "status": "PASS",
            "operation": operation,
            "capability": "C21",
            "generation_id": generation_id,
            "generation_root": expected_root,
            "manifest": manifest,
            "notes": notes,
            "edges": edges,
            "source_verified": verify_sources,
            "provider_called": False,
            "mutation_performed": False,
        }, EXIT_OK
    except ProjectionConflict as error:
        return _failure(operation, "PROJECTION_CONFLICT", str(error))
    except (OSError, UnicodeError, ProjectionError, UnsafePathError, KeyError, TypeError, ValueError) as error:
        return _failure(operation, "PROJECTION_INVALID", str(error))


def read_projection(root: str | Path, *, verify_sources: bool = True) -> tuple[dict[str, Any], int]:
    """Compatibility alias for :func:`read_current_projection`."""

    return read_current_projection(root, verify_sources=verify_sources)


def verify_projection(root: str | Path, *, verify_sources: bool = True) -> tuple[dict[str, Any], int]:
    """Compatibility alias for the ``vaultctl index verify`` command."""

    return read_current_projection(root, verify_sources=verify_sources)


def build_index(root: str | Path, *, generation_id: str | None = None) -> tuple[dict[str, Any], int]:
    """Compatibility alias for the ``vaultctl index build`` command."""

    return generate_projection(root, generation_id=generation_id)


def load_current_projection(root: str | Path, *, verify_sources: bool = True) -> ProjectionRead:
    """Return a typed reader result or raise a projection error."""

    report, code = read_current_projection(root, verify_sources=verify_sources)
    if code != EXIT_OK:
        message = report.get("errors", [{"message": "projection read failed"}])[0]["message"]
        raise ProjectionError(str(message))
    return ProjectionRead(
        pointer={
            "generation_id": report["generation_id"],
            "generation_root": report["generation_root"],
        },
        manifest=report["manifest"],
        notes=tuple(report["notes"]),
        edges=tuple(report["edges"]),
    )

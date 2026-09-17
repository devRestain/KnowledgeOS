"""Deterministic C27 action-specific proposal generation.

This module is the provider-free bridge between validated source notes and the
C19 human review lifecycle.  It can create exactly one review artifact under
``01_AI_Review/Pending``; it never writes a canonical target note.  Every
action binds its source bytes, action and prompt files, policy file, output
schema, and (where applicable) a selected candidate or candidate set.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .fragments import DailyFragment, FragmentError, verify_daily_fragment
from .note_engine import (
    FrontmatterError,
    NoteContractError,
    NoteEngine,
    UnsafePathError,
    parse_frontmatter,
    render_frontmatter,
    resolve_vault_relative_path,
    validate_relation_candidate,
    write_note_file,
)
from .recovery import canonical_json_bytes, fsync_directory
from .template_engine import TemplateRenderError, render_note_template
from .triage import deterministic_triage

EXIT_OK = 0
EXIT_CONFLICT = 30
SCHEMA_VERSION = 1

ACTION_TYPES = ("draft_note", "link_suggestions", "normalize")
DRAFT_SOURCE_TYPES = ("capture", "daily", "source", "idea")
DRAFT_TARGET_TYPES = ("idea", "question", "knowledge", "project", "source")
CANONICAL_NOTE_TYPES = (
    "capture",
    "daily",
    "weekly",
    "monthly",
    "project",
    "project_note",
    "idea",
    "question",
    "artifact",
    "area",
    "knowledge",
    "source",
    "person",
    "moc",
    "meeting",
    "home",
    "system",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_PROFILE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_SOURCE_BYTES = 256 * 1024
_MAX_CANDIDATE_SET_BYTES = 256 * 1024
_MAX_TARGET_BYTES = 64 * 1024
_MAX_DIFF_BYTES = 32 * 1024
_MAX_EXCERPT_BYTES = 4096
_ACTION_FILES = {
    "draft_note": "ops/actions/draft-note.json",
    "link_suggestions": "ops/actions/link-suggestions.json",
    "normalize": "ops/actions/normalize.json",
}
_PROMPT_FILES = {
    "draft_note": "ops/prompts/draft-note.md",
    "link_suggestions": "ops/prompts/link-suggestions.md",
    "normalize": "ops/prompts/normalize.md",
}
_TARGET_DIRECTORIES = {
    "idea": "40_Knowledge/Ideas",
    "question": "40_Knowledge/Questions",
    "knowledge": "40_Knowledge/Notes",
    "source": "40_Knowledge/Sources",
}


class ActionProposalError(ValueError):
    """A fail-closed C27 input, binding, or generation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _uuid4_from_seed(seed: str) -> str:
    """Create a deterministic UUID-shaped v4 identifier from one seed."""

    value = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    value[6] = (value[6] & 0x0F) | 0x40
    value[8] = (value[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(value)))


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _hash_schema() -> dict[str, Any]:
    return {"type": "string", "pattern": r"^[0-9a-f]{64}$"}


def _nullable_hash() -> dict[str, Any]:
    return {"anyOf": [_hash_schema(), {"type": "null"}]}


def _path_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 500,
        "pattern": r"^(?!/)(?!.*\\)(?!.*//)(?!.*(?:^|/)\.{1,2}(?:/|$)).+\.md$",
    }


def _control_path_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 500,
        "pattern": r"^(?!/)(?!.*\\)(?!.*//)(?!.*(?:^|/)\.{1,2}(?:/|$)).+$",
    }


def _binding_file_schema(*, decision: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {"path": _control_path_schema(), "sha256": _hash_schema()}
    required = ["path", "sha256"]
    if decision:
        properties["decision"] = {"type": "string", "minLength": 1, "maxLength": 80}
        required.append("decision")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _source_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "path",
            "note_id",
            "type",
            "sha256",
            "locator",
            "fragment_sha256",
            "fragment_byte_length",
        ],
        "properties": {
            "path": _path_schema(),
            "note_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "type": {"type": "string", "enum": list(CANONICAL_NOTE_TYPES)},
            "sha256": _hash_schema(),
            "locator": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 500}, {"type": "null"}]},
            "fragment_sha256": _nullable_hash(),
            "fragment_byte_length": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
        },
    }


def _target_schema(*, action: str) -> dict[str, Any]:
    expected = r"^$|^[0-9a-f]{64}$" if action == "draft_note" else r"^[0-9a-f]{64}$"
    before: dict[str, Any] = {"anyOf": [_hash_schema(), {"type": "null"}]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "type", "expected_sha256", "before_sha256", "after_sha256"],
        "properties": {
            "path": _path_schema(),
            "type": {"type": "string", "enum": list(CANONICAL_NOTE_TYPES)},
            "expected_sha256": {"type": "string", "pattern": expected},
            "before_sha256": before,
            "after_sha256": _hash_schema(),
        },
    }


def _mutation_schema(*, operation: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "path", "type", "before_sha256", "after_sha256"],
        "properties": {
            "operation": {"const": operation},
            "path": _path_schema(),
            "type": {"type": "string", "enum": list(CANONICAL_NOTE_TYPES)},
            "before_sha256": {"anyOf": [_hash_schema(), {"type": "null"}]},
            "after_sha256": _hash_schema(),
        },
    }


def _common_proposal_properties(action: str) -> dict[str, Any]:
    return {
        "schema_version": {"const": SCHEMA_VERSION},
        "proposal_id": {"type": "string", "pattern": _UUID4.pattern},
        "action": {"const": action},
        "status": {"const": "PROPOSED"},
        "source": _source_schema(),
        "target": _target_schema(action=action),
        "bindings": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "action_config",
                "prompt",
                "policy",
                "schema",
                "candidate_set_sha256",
                "retrieval_config_sha256",
                "retrieval_profile_id",
                "index_generation_id",
            ],
            "properties": {
                "action_config": _binding_file_schema(),
                "prompt": _binding_file_schema(),
                "policy": _binding_file_schema(decision=True),
                "schema": _binding_file_schema(),
                "candidate_set_sha256": _nullable_hash(),
                "retrieval_config_sha256": _nullable_hash(),
                "retrieval_profile_id": {
                    "anyOf": [{"type": "string", "pattern": _PROFILE.pattern}, {"type": "null"}]
                },
                "index_generation_id": {
                    "anyOf": [{"type": "string", "minLength": 1, "maxLength": 200}, {"type": "null"}]
                },
            },
        },
        "diff": {
            "type": "object",
            "additionalProperties": False,
            "required": ["format", "sha256", "patch", "bounded"],
            "properties": {
                "format": {"const": "unified"},
                "sha256": _hash_schema(),
                "patch": {"type": "string", "maxLength": _MAX_DIFF_BYTES},
                "bounded": {"const": True},
            },
        },
        "requested_mutations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": _mutation_schema(operation="update_note" if action != "draft_note" else "create_note"),
        },
        "provider_called": {"const": False},
        "mutation_performed": {"const": False},
        "requires_human_approval_before_apply": {"const": True},
    }


def _candidate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "type", "title", "reason", "source_sha256", "candidate_sha256"],
        "properties": {
            "candidate_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,127}$"},
            "type": {"type": "string", "enum": ["task", "idea", "question", "knowledge", "project", "source"]},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "source_sha256": _hash_schema(),
            "candidate_sha256": _hash_schema(),
        },
    }


def proposal_schema() -> dict[str, Any]:
    """Return the strict action-specific C27 proposal schema."""

    branches: list[dict[str, Any]] = []
    draft = _common_proposal_properties("draft_note")
    draft["candidate"] = _candidate_schema()
    branches.append(
        {
            "$id": "knowledgeos://schema/proposal/draft_note-v1",
            "title": "KnowledgeOS draft note proposal",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "proposal_id",
                "action",
                "status",
                "source",
                "target",
                "bindings",
                "diff",
                "candidate",
                "requested_mutations",
                "provider_called",
                "mutation_performed",
                "requires_human_approval_before_apply",
            ],
            "properties": draft,
        }
    )

    link = _common_proposal_properties("link_suggestions")
    link["candidate_set"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 50,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidate_id", "path", "note_id", "type", "title", "content_hash", "predicate", "reason"],
            "properties": {
                "candidate_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,127}$"},
                "path": _path_schema(),
                "note_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "type": {"type": "string", "enum": list(CANONICAL_NOTE_TYPES)},
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "content_hash": _hash_schema(),
                "predicate": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
        },
    }
    link["suggested_links"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 5,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidate_id", "path", "note_id", "predicate", "wikilink", "content_hash", "reason"],
            "properties": {
                "candidate_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,127}$"},
                "path": _path_schema(),
                "note_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "predicate": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
                "wikilink": {"type": "string", "pattern": r"^\[\[[^\]\n]+\]\]$"},
                "content_hash": _hash_schema(),
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
        },
    }
    branches.append(
        {
            "$id": "knowledgeos://schema/proposal/link_suggestions-v1",
            "title": "KnowledgeOS link suggestions proposal",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "proposal_id",
                "action",
                "status",
                "source",
                "target",
                "bindings",
                "diff",
                "candidate_set",
                "suggested_links",
                "requested_mutations",
                "provider_called",
                "mutation_performed",
                "requires_human_approval_before_apply",
            ],
            "properties": link,
        }
    )

    normalize = _common_proposal_properties("normalize")
    normalize["normalization"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["changed_fields", "before_properties_sha256", "after_properties_sha256"],
        "properties": {
            "changed_fields": {
                "type": "array",
                "maxItems": 100,
                "items": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "before_properties_sha256": _hash_schema(),
            "after_properties_sha256": _hash_schema(),
        },
    }
    branches.append(
        {
            "$id": "knowledgeos://schema/proposal/normalize-v1",
            "title": "KnowledgeOS normalize proposal",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "proposal_id",
                "action",
                "status",
                "source",
                "target",
                "bindings",
                "diff",
                "normalization",
                "requested_mutations",
                "provider_called",
                "mutation_performed",
                "requires_human_approval_before_apply",
            ],
            "properties": normalize,
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "knowledgeos://schema/proposal-v1",
        "title": "KnowledgeOS C27 action-specific mutation proposal",
        "oneOf": branches,
    }


def candidate_sha256(candidate: Mapping[str, Any]) -> str:
    """Hash the candidate identity without trusting a supplied digest."""

    value = {key: candidate[key] for key in sorted(candidate) if key != "candidate_sha256"}
    return _hash_json(value)


def _failure(operation: str, code: str, message: str) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": "C27",
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, EXIT_CONFLICT


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ActionProposalError("C27_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return candidate.resolve()


def _vault(workspace: Path) -> Path:
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise ActionProposalError("C27_VAULT_INVALID", "KnowledgeHub must be an existing non-symlink directory")
    return vault


def _safe_vault_path(vault: Path, relative: str) -> tuple[str, Path]:
    if not isinstance(relative, str) or not relative.endswith(".md"):
        raise ActionProposalError("C27_PATH_INVALID", "source and target paths must be Vault-relative Markdown paths")
    try:
        path = resolve_vault_relative_path(vault, relative)
    except (UnsafePathError, ValueError) as error:
        raise ActionProposalError("C27_PATH_INVALID", str(error)) from error
    return path.relative_to(vault).as_posix(), path


def _read_regular(path: Path, label: str, *, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ActionProposalError("C27_SOURCE_INVALID", f"{label} must be a regular file")
    value = path.read_bytes()
    if len(value) > limit:
        raise ActionProposalError("C27_INPUT_TOO_LARGE", f"{label} exceeds its bounded byte limit")
    return value


def _decode_note_bytes(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ActionProposalError("C27_SOURCE_INVALID", f"{label} must be valid UTF-8") from error
    if any(marker in text for marker in ("\r", "\v", "\f", "\x85", "\u2028", "\u2029", "\x00")):
        raise ActionProposalError("C27_SOURCE_INVALID", f"{label} must use LF line endings and contain no NUL")
    return text


def _target_types(vault: Path, engine: NoteEngine) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(vault.rglob("*.md"), key=lambda item: item.relative_to(vault).as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(vault).as_posix()
        try:
            note_type = engine.note_type_for_path(relative)
            document = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, FrontmatterError, UnsafePathError):
            continue
        if note_type is None:
            continue
        aliases = document.properties.get("aliases", [])
        values = {relative.removesuffix(".md"), Path(relative).stem, str(document.properties.get("title", ""))}
        values.update(item for item in aliases if isinstance(item, str) and item)
        for value in values - {""}:
            previous = result.get(value)
            if previous is None or previous == note_type:
                result[value] = note_type
    return result


def _load_source(
    workspace: Path,
    source_path: str,
    expected_sha256: str,
) -> tuple[str, Path, bytes, str, Any, NoteEngine, Path]:
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ActionProposalError("C27_SOURCE_HASH_INVALID", "expected_sha256 must be lowercase SHA-256")
    vault = _vault(workspace)
    normalized, path = _safe_vault_path(vault, source_path)
    raw = _read_regular(path, "source", limit=_MAX_SOURCE_BYTES)
    observed = _sha256(raw)
    if observed != expected_sha256:
        raise ActionProposalError("C27_SOURCE_DRIFT", "source digest does not match expected_sha256")
    try:
        text = _decode_note_bytes(raw, "source")
        engine = NoteEngine.from_root(workspace)
        target_types = _target_types(vault, engine)
        typed = engine.typed_note(normalized, text, target_types=target_types)
    except (OSError, UnicodeError, FrontmatterError, NoteContractError, ValueError) as error:
        raise ActionProposalError("C27_SOURCE_INVALID", str(error)) from error
    return normalized, path, raw, text, typed, engine, vault


def _check_privacy(typed: Any, *, label: str = "source") -> None:
    sensitivity = typed.properties.get("sensitivity")
    policy = typed.properties.get("ai_policy")
    if sensitivity == "confidential" or policy == "deny":
        raise ActionProposalError("C27_PRIVACY_DENIED", f"{label} privacy policy denies proposal processing")


def _fragment_binding(
    typed: Any,
    text: str,
    *,
    locator: str | None,
    fragment_sha256: str | None,
) -> DailyFragment | None:
    if (locator is None) != (fragment_sha256 is None):
        raise ActionProposalError("C27_FRAGMENT_BINDING_INVALID", "locator and fragment_sha256 must be supplied together")
    if typed.note_type == "daily":
        if locator is None or fragment_sha256 is None:
            raise ActionProposalError("C27_DAILY_LOCATOR_REQUIRED", "daily sources require locator and fragment_sha256")
        try:
            return verify_daily_fragment(text, locator, fragment_sha256)
        except FragmentError as error:
            code = "C27_FRAGMENT_DRIFT" if "does not match" in str(error) else "C27_FRAGMENT_INVALID"
            raise ActionProposalError(code, str(error)) from error
    if locator is not None or fragment_sha256 is not None:
        raise ActionProposalError("C27_FRAGMENT_SOURCE_INVALID", "fragment binding is only valid for daily sources")
    return None


def _safe_title(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ActionProposalError("C27_TITLE_INVALID", "title must be bounded non-empty text")
    if value != _nfc(value) or any(marker in value for marker in ("/", "\\", "\x00", "\r", "\n")):
        raise ActionProposalError("C27_TITLE_INVALID", "title must be NFC-normalized single-line filename text")
    if value in {".", ".."} or value.startswith("."):
        raise ActionProposalError("C27_TITLE_INVALID", "title must not be hidden or traversal-like")
    return value


def _target_for_draft(
    workspace: Path,
    engine: NoteEngine,
    vault: Path,
    *,
    title: str,
    target_path: str | None,
    target_type: str | None,
) -> tuple[str, Path, str, str]:
    if target_path is not None:
        normalized, path = _safe_vault_path(vault, target_path)
        expected_type = engine.note_type_for_path(normalized)
        if expected_type is None:
            raise ActionProposalError("C27_TARGET_PATH_INVALID", "target path does not match one canonical note namespace")
        if target_type is not None and target_type != expected_type:
            raise ActionProposalError("C27_TARGET_TYPE_INVALID", "target_type does not match target path")
        selected_type = expected_type
        if Path(normalized).stem != title:
            raise ActionProposalError("C27_TARGET_TITLE_MISMATCH", "target title must equal target filename stem")
    else:
        selected_type = target_type or "idea"
        if selected_type not in DRAFT_TARGET_TYPES:
            raise ActionProposalError("C27_TARGET_TYPE_INVALID", "draft target type is not allowed")
        directory = _TARGET_DIRECTORIES.get(selected_type)
        if directory is None:
            raise ActionProposalError(
                "C27_PROJECT_TARGET_REQUIRES_PATH",
                "project draft targets require an explicit existing project bundle path",
            )
        normalized = f"{directory}/{title}.md"
        path = resolve_vault_relative_path(vault, normalized)
    if selected_type not in DRAFT_TARGET_TYPES:
        raise ActionProposalError("C27_TARGET_TYPE_INVALID", "target path is outside the draft_note target types")
    if path.is_symlink() or path.exists():
        raise ActionProposalError("C27_TARGET_EXISTS", "draft target already exists")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ActionProposalError("C27_TARGET_PARENT_INVALID", "draft target parent must already be a real directory")
    return normalized, path, selected_type, title


def _source_excerpt(text: str, fragment: DailyFragment | None) -> str:
    body = fragment.text if fragment is not None else parse_frontmatter(text).body.strip()
    encoded = body.encode("utf-8")
    if len(encoded) > _MAX_EXCERPT_BYTES:
        body = encoded[:_MAX_EXCERPT_BYTES].decode("utf-8", errors="ignore").rstrip()
    lines = body.splitlines() or [body]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def _draft_candidate(
    workspace: Path,
    *,
    source_path: str,
    source_hash: str,
    typed: Any,
    locator: str | None,
    fragment_sha256: str | None,
    selected_candidate_id: str | None,
    selected_candidate_sha256: str | None,
    title: str | None,
) -> dict[str, Any]:
    if selected_candidate_id is not None and selected_candidate_sha256 is None:
        raise ActionProposalError(
            "C27_CANDIDATE_BINDING_INVALID",
            "selected_candidate_id requires selected_candidate_sha256",
        )
    if title is None and selected_candidate_id is None:
        raise ActionProposalError("C27_CANDIDATE_REQUIRED", "draft_note requires a selected candidate or validated title")
    if typed.note_type in {"capture", "daily"}:
        triage, code = deterministic_triage(
            workspace,
            source_path=source_path,
            expected_sha256=source_hash,
            locator=locator,
            fragment_sha256=fragment_sha256,
        )
        if code != EXIT_OK or triage.get("status") != "PROPOSED":
            error = triage.get("errors", [{"message": "triage failed"}])[0]
            raise ActionProposalError("C27_TRIAGE_REQUIRED", str(error.get("message", "triage failed")))
        candidate = dict(triage["proposal"]["candidates"][0])
    else:
        candidate = {
            "candidate_id": "candidate-" + hashlib.sha256(f"{source_path}\0{source_hash}".encode()).hexdigest()[:16],
            "type": typed.note_type if typed.note_type in {"idea", "source"} else "idea",
            "title": str(typed.properties["title"]),
            "reason": "validated source note selected for a separate action proposal",
            "source_sha256": source_hash,
        }
    if selected_candidate_id is not None and candidate["candidate_id"] != selected_candidate_id:
        raise ActionProposalError("C27_CANDIDATE_DRIFT", "selected candidate id does not match the current triage result")
    current_digest = _hash_json(candidate)
    if selected_candidate_sha256 is not None and current_digest != selected_candidate_sha256:
        raise ActionProposalError("C27_CANDIDATE_DRIFT", "selected candidate digest does not match the current candidate")
    if title is not None:
        candidate["title"] = _safe_title(title)
    candidate["candidate_sha256"] = candidate_sha256(candidate)
    return candidate


def _resolve_control_file(workspace: Path, value: str | Path, *, label: str, limit: int) -> tuple[str, Path, bytes]:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            relative = candidate.resolve(strict=False).relative_to(workspace).as_posix()
        except ValueError as error:
            raise ActionProposalError(
                "C27_CANDIDATE_SET_PATH_INVALID",
                "candidate-set file must be inside the control workspace",
            ) from error
        path = candidate
    else:
        relative = candidate.as_posix()
        if not relative or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ActionProposalError("C27_CANDIDATE_SET_PATH_INVALID", "candidate-set path must be a safe control-relative file")
        path = workspace.joinpath(*candidate.parts)
    raw = _read_regular(path, label, limit=limit)
    return relative, path, raw


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_candidate_set(
    workspace: Path,
    vault: Path,
    engine: NoteEngine,
    value: str | Path | None,
    *,
    source_path: str,
) -> tuple[list[dict[str, Any]], str, str, str]:
    if value is None:
        raise ActionProposalError("C27_CANDIDATE_SET_REQUIRED", "link_suggestions requires candidate_set_file")
    relative, _, raw = _resolve_control_file(
        workspace,
        value,
        label="candidate set",
        limit=_MAX_CANDIDATE_SET_BYTES,
    )
    try:
        loaded = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_json_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ActionProposalError("C27_CANDIDATE_SET_INVALID", f"candidate set JSON is invalid: {error}") from error
    if isinstance(loaded, dict):
        loaded = loaded.get("candidates")
    if not isinstance(loaded, list) or not 1 <= len(loaded) <= 50:
        raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate set must contain one through fifty candidates")
    resolver = _target_types(vault, engine)
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in loaded:
        if not isinstance(item, Mapping):
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "each candidate must be an object")
        raw_path = item.get("path", item.get("source_path"))
        if not isinstance(raw_path, str):
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate path is required")
        normalized, path = _safe_vault_path(vault, raw_path)
        if normalized == source_path or normalized in seen_paths:
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate paths must be unique and exclude the source")
        seen_paths.add(normalized)
        raw_note = _read_regular(path, "candidate note", limit=_MAX_SOURCE_BYTES)
        observed_hash = _sha256(raw_note)
        supplied_hash = item.get("content_hash", item.get("sha256"))
        if not isinstance(supplied_hash, str) or not _SHA256.fullmatch(supplied_hash):
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate content_hash must be lowercase SHA-256")
        if observed_hash != supplied_hash:
            raise ActionProposalError("C27_CANDIDATE_SET_DRIFT", f"candidate digest does not match: {normalized}")
        try:
            typed = engine.typed_note(
                normalized,
                _decode_note_bytes(raw_note, "candidate note"),
                target_types=resolver,
            )
        except (UnicodeError, NoteContractError, ValueError) as error:
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", str(error)) from error
        _check_privacy(typed, label="candidate")
        if "note_id" in item and item["note_id"] != typed.note_id:
            raise ActionProposalError("C27_CANDIDATE_SET_DRIFT", f"candidate note id does not match: {normalized}")
        if "type" in item and item["type"] != typed.note_type:
            raise ActionProposalError("C27_CANDIDATE_SET_DRIFT", f"candidate type does not match: {normalized}")
        candidate_id = item.get("candidate_id")
        if candidate_id is None:
            candidate_id = "candidate-" + hashlib.sha256(f"{normalized}\0{observed_hash}".encode()).hexdigest()[:16]
        if not isinstance(candidate_id, str) or not re.fullmatch(r"^[a-z][a-z0-9_-]{0,127}$", candidate_id):
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate_id is invalid")
        predicate = item.get("predicate", "related")
        if not isinstance(predicate, str) or not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", predicate):
            raise ActionProposalError("C27_RELATION_INVALID", "candidate predicate is invalid")
        reason = item.get("reason", "candidate supplied by the validated local retrieval set")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ActionProposalError("C27_CANDIDATE_SET_INVALID", "candidate reason is invalid")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "path": normalized,
                "note_id": typed.note_id,
                "type": typed.note_type,
                "title": str(typed.properties["title"]),
                "content_hash": observed_hash,
                "predicate": predicate,
                "reason": reason.strip(),
            }
        )
    return candidates, _hash_json(candidates), relative, _sha256(raw)


def _relation_ok(engine: NoteEngine, subject_type: str, object_type: str, predicate: str) -> bool:
    if predicate == "related":
        return True
    errors = validate_relation_candidate(
        predicate,
        subject_type,
        object_type,
        relation_registry=engine.blueprint.get("relation_registry", {}),
    )
    return not errors


def _unified_diff(target_path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{target_path}",
        tofile=f"b/{target_path}",
        lineterm="\n",
    )
    result = "".join(lines)
    if len(result.encode("utf-8")) > _MAX_DIFF_BYTES:
        raise ActionProposalError("C27_DIFF_TOO_LARGE", "proposal diff exceeds its bounded byte limit")
    return result


def _artifact_bindings(
    workspace: Path,
    action: str,
    *,
    source: Any,
    candidate_set_sha256: str | None,
    retrieval_profile_id: str | None,
) -> dict[str, Any]:
    def file_binding(relative: str) -> dict[str, str]:
        path = workspace / relative
        raw = _read_regular(path, relative, limit=2 * 1024 * 1024)
        return {"path": relative, "sha256": _sha256(raw)}

    action_binding = file_binding(_ACTION_FILES[action])
    prompt_binding = file_binding(_PROMPT_FILES[action])
    policy_path = "ops/policies/privacy.yaml"
    policy_binding = file_binding(policy_path)
    policy_binding["decision"] = "local_only" if source.properties.get("ai_policy") == "local_only" else "provider_free_local"
    schema_binding = file_binding("ops/schemas/proposal.schema.json")
    retrieval_hash: str | None = None
    if action == "link_suggestions":
        retrieval_hash = file_binding("ops/policies/retrieval.yaml")["sha256"]
    return {
        "action_config": action_binding,
        "prompt": prompt_binding,
        "policy": policy_binding,
        "schema": schema_binding,
        "candidate_set_sha256": candidate_set_sha256,
        "retrieval_config_sha256": retrieval_hash,
        "retrieval_profile_id": retrieval_profile_id,
        "index_generation_id": None,
    }


def _source_payload(
    source_path: str,
    source_hash: str,
    typed: Any,
    fragment: DailyFragment | None,
) -> dict[str, Any]:
    return {
        "path": source_path,
        "note_id": typed.note_id,
        "type": typed.note_type,
        "sha256": source_hash,
        "locator": fragment.locator if fragment is not None else None,
        "fragment_sha256": fragment.sha256 if fragment is not None else None,
        "fragment_byte_length": fragment.byte_length if fragment is not None else None,
    }


def _proposal_payload(
    *,
    action: str,
    source: dict[str, Any],
    target: dict[str, Any],
    bindings: dict[str, Any],
    diff: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    seed = {
        "action": action,
        "source": source,
        "target": target,
        "bindings": bindings,
        "diff_sha256": _sha256(diff.encode("utf-8")),
        "extra": extra,
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": _uuid4_from_seed(json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        "action": action,
        "status": "PROPOSED",
        "source": source,
        "target": target,
        "bindings": bindings,
        "diff": {
            "format": "unified",
            "sha256": _sha256(diff.encode("utf-8")),
            "patch": diff,
            "bounded": True,
        },
        "requested_mutations": [
            {
                "operation": "create_note" if action == "draft_note" else "update_note",
                "path": target["path"],
                "type": target["type"],
                "before_sha256": target["before_sha256"],
                "after_sha256": target["after_sha256"],
            }
        ],
        "provider_called": False,
        "mutation_performed": False,
        "requires_human_approval_before_apply": True,
    }
    payload.update(extra)
    return payload


def _validate_payload(payload: Mapping[str, Any]) -> None:
    errors = sorted(Draft202012Validator(proposal_schema()).iter_errors(payload), key=lambda item: item.json_path)
    if errors:
        details = [item.message for item in errors[0].context[:6]]
        suffix = f" ({'; '.join(details)})" if details else ""
        raise ActionProposalError("C27_PROPOSAL_SCHEMA_INVALID", errors[0].message + suffix)


def _manifest_json(value: Mapping[str, Any]) -> str:
    # A source note may contain a literal Markdown comment terminator.  Escape
    # only that delimiter; JSON decoding restores the exact target bytes.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        "-->", r"\u002d\u002d>"
    )


def _write_pending_artifact(
    workspace: Path,
    *,
    action: str,
    source: Any,
    source_path: str,
    source_hash: str,
    payload: dict[str, Any],
    target_markdown: str,
    diff: str,
) -> tuple[dict[str, Any], int]:
    _validate_payload(payload)
    target = payload["target"]
    manifest: dict[str, Any] = {
        "action": "create_note" if action == "draft_note" else "update_note",
        "target_path": target["path"],
        "target_markdown": target_markdown,
    }
    if action != "draft_note":
        manifest["expected_target_sha256"] = source_hash
    proposal_id = payload["proposal_id"]
    stem = f"C27 {action} {proposal_id[:12]}"
    proposal_relative = f"01_AI_Review/Pending/{stem}.md"
    vault = _vault(workspace)
    proposal_path = resolve_vault_relative_path(vault, proposal_relative)
    source_hashes = [f"{source_path}|sha256:{source_hash}"]
    source_locator = payload["source"].get("locator")
    locator_text = f" ({source_locator})" if source_locator else ""
    summary = (
        f"Provider-free {action} proposal bound to `{source_path}`{locator_text}. "
        "The canonical target remains unchanged until a human approves the C19 proposal."
    )
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    diff_fence = f"```diff\n{diff}```" if diff else "```diff\n(no byte change)\n```"
    body = (
        f"# AI 제안 — {stem}\n\n"
        "> 이 노트는 정본이 아니다. C19 승인 후에만 canonical target을 변경할 수 있다.\n\n"
        "## 제안 요약\n\n"
        f"{summary}\n\n"
        "## Action payload\n\n"
        "```json\n"
        f"{payload_text}\n"
        "```\n\n"
        "## 출처와 고정 hash\n\n"
        f"- `{source_path}` — `{source_hash}`\n\n"
        "## 변경 미리보기\n\n"
        f"{diff_fence}\n\n"
        "## C19 manifest\n\n"
        "<!-- vaultops:proposal-manifest\n"
        f"{_manifest_json(manifest)}\n"
        "-->\n"
    )
    created = str(source.properties.get("created"))
    rendered = render_note_template(
        "T01_AI_Proposal.md",
        {
            "title": stem,
            "id": proposal_id,
            "proposal_id": proposal_id,
            "created": created,
            "modified": created,
            "sensitivity": source.properties.get("sensitivity", "personal"),
            "source_hashes": source_hashes,
            "body": body,
        },
    )
    artifact = rendered.markdown
    if len(artifact.encode("utf-8")) > _MAX_CANDIDATE_SET_BYTES:
        raise ActionProposalError("C27_PROPOSAL_TOO_LARGE", "pending proposal exceeds its bounded byte limit")
    try:
        NoteEngine.from_root(workspace).typed_note(proposal_relative, artifact)
    except (NoteContractError, ValueError, UnicodeError) as error:
        raise ActionProposalError("C27_PROPOSAL_NOTE_INVALID", str(error)) from error
    artifact_hash = _sha256(artifact.encode("utf-8"))
    if proposal_path.exists() or proposal_path.is_symlink():
        if proposal_path.is_symlink() or not proposal_path.is_file() or proposal_path.read_bytes() != artifact.encode("utf-8"):
            raise ActionProposalError("C27_PROPOSAL_CONFLICT", "existing Pending artifact differs")
        return {
            "status": "NO_OP",
            "operation": f"ai propose {action}",
            "capability": "C27",
            "action": action,
            "provider_called": False,
            "mutation_performed": False,
            "replayed": True,
            "created": False,
            "proposal_path": proposal_relative,
            "proposal_sha256": artifact_hash,
            "proposal": payload,
        }, EXIT_OK
    try:
        write_note_file(proposal_path, artifact, overwrite=False)
    except FileExistsError as error:
        if proposal_path.is_file() and not proposal_path.is_symlink() and proposal_path.read_bytes() == artifact.encode("utf-8"):
            return {
                "status": "NO_OP",
                "operation": f"ai propose {action}",
                "capability": "C27",
                "action": action,
                "provider_called": False,
                "mutation_performed": False,
                "replayed": True,
                "created": False,
                "proposal_path": proposal_relative,
                "proposal_sha256": artifact_hash,
                "proposal": payload,
            }, EXIT_OK
        raise ActionProposalError("C27_PROPOSAL_CONFLICT", "Pending artifact appeared with different bytes") from error
    fsync_directory(proposal_path.parent)
    return {
        "status": "PASS",
        "operation": f"ai propose {action}",
        "capability": "C27",
        "action": action,
        "provider_called": False,
        "mutation_performed": False,
        "replayed": False,
        "created": True,
        "proposal_path": proposal_relative,
        "proposal_sha256": artifact_hash,
        "proposal": payload,
    }, EXIT_OK


def _target_markdown_for_update(
    engine: NoteEngine,
    vault: Path,
    target_path: str,
    target_type: str,
    markdown: str,
    *,
    target_types: Mapping[str, str],
) -> Any:
    try:
        engine.typed_note(target_path, markdown, target_types=target_types)
    except (NoteContractError, ValueError, UnicodeError) as error:
        raise ActionProposalError("C27_TARGET_NOTE_INVALID", str(error)) from error
    return markdown


def _make_draft(
    workspace: Path,
    *,
    source_path: str,
    source_hash: str,
    text: str,
    typed: Any,
    engine: NoteEngine,
    vault: Path,
    fragment: DailyFragment | None,
    target_path: str | None,
    target_type: str | None,
    title: str | None,
    selected_candidate_id: str | None,
    selected_candidate_sha256: str | None,
) -> tuple[dict[str, Any], str, str]:
    candidate = _draft_candidate(
        workspace,
        source_path=source_path,
        source_hash=source_hash,
        typed=typed,
        locator=fragment.locator if fragment else None,
        fragment_sha256=fragment.sha256 if fragment else None,
        selected_candidate_id=selected_candidate_id,
        selected_candidate_sha256=selected_candidate_sha256,
        title=title,
    )
    selected_title = _safe_title(str(candidate["title"]))
    normalized_target, _target, selected_type, _ = _target_for_draft(
        workspace,
        engine,
        vault,
        title=selected_title,
        target_path=target_path,
        target_type=target_type,
    )
    target_id = _uuid4_from_seed(f"knowledgeos:c27:{source_path}:{source_hash}:{normalized_target}:{candidate['candidate_sha256']}")
    source_link = f"[[{source_path}{fragment.locator if fragment else ''}]]"
    body = (
        f"# {selected_title}\n\n"
        "## 제안된 내용\n\n"
        f"{_source_excerpt(text, fragment)}\n\n"
        "## 출처\n\n"
        f"- {source_link}\n"
        f"- source_sha256: `{source_hash}`\n"
        f"- candidate_sha256: `{candidate['candidate_sha256']}`\n"
    )
    try:
        rendered = render_note_template(
            {
                "idea": "T21_Idea.md",
                "question": "T22_Question.md",
                "knowledge": "T40_Knowledge.md",
                "source": "T41_Source.md",
                "project": "T20_Project.md",
            }[selected_type],
            {
                "title": selected_title,
                "id": target_id,
                "created": typed.properties["created"],
                "modified": typed.properties["created"],
                "sensitivity": typed.properties.get("sensitivity", "personal"),
                "ai_policy": typed.properties.get("ai_policy", "ask"),
                "body": body,
            },
        )
    except (KeyError, TemplateRenderError, ValueError) as error:
        raise ActionProposalError("C27_TARGET_NOTE_INVALID", str(error)) from error
    target_markdown = rendered.markdown
    if len(target_markdown.encode("utf-8")) > _MAX_TARGET_BYTES:
        raise ActionProposalError("C27_TARGET_TOO_LARGE", "draft target exceeds its bounded byte limit")
    target_types = _target_types(vault, engine)
    target_types.update({normalized_target.removesuffix(".md"): selected_type, selected_title: selected_type})
    _target_markdown_for_update(engine, vault, normalized_target, selected_type, target_markdown, target_types=target_types)
    diff = _unified_diff(normalized_target, "", target_markdown)
    target_hash = _sha256(target_markdown.encode("utf-8"))
    source_payload = _source_payload(source_path, source_hash, typed, fragment)
    bindings = _artifact_bindings(
        workspace,
        "draft_note",
        source=typed,
        candidate_set_sha256=None,
        retrieval_profile_id=None,
    )
    target_payload = {
        "path": normalized_target,
        "type": selected_type,
        "expected_sha256": "",
        "before_sha256": None,
        "after_sha256": target_hash,
    }
    payload = _proposal_payload(
        action="draft_note",
        source=source_payload,
        target=target_payload,
        bindings=bindings,
        diff=diff,
        extra={"candidate": candidate},
    )
    return payload, target_markdown, diff


def _make_link_suggestions(
    workspace: Path,
    *,
    source_path: str,
    source_hash: str,
    text: str,
    typed: Any,
    engine: NoteEngine,
    vault: Path,
    fragment: DailyFragment | None,
    candidate_set_file: str | Path | None,
    retrieval_profile_id: str | None,
) -> tuple[dict[str, Any], str, str, str]:
    if not isinstance(retrieval_profile_id, str) or not _PROFILE.fullmatch(retrieval_profile_id):
        raise ActionProposalError("C27_RETRIEVAL_PROFILE_INVALID", "retrieval_profile_id is required and must be bounded")
    candidates, set_hash, set_path, set_input_hash = _load_candidate_set(
        workspace,
        vault,
        engine,
        candidate_set_file,
        source_path=source_path,
    )
    properties = copy.deepcopy(typed.properties)
    suggested: list[dict[str, Any]] = []
    resolver = _target_types(vault, engine)
    for candidate in candidates:
        predicate = candidate["predicate"]
        if not _relation_ok(engine, typed.note_type, candidate["type"], predicate):
            raise ActionProposalError(
                "C27_RELATION_INVALID",
                f"relation predicate is not valid for {typed.note_type} -> {candidate['type']}: {predicate}",
            )
        wikilink = f"[[{candidate['title']}]]"
        values = properties.setdefault(predicate, [])
        if not isinstance(values, list):
            raise ActionProposalError("C27_TARGET_NOTE_INVALID", f"target relation property is not a list: {predicate}")
        if wikilink not in values:
            values.append(wikilink)
            suggested.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "path": candidate["path"],
                    "note_id": candidate["note_id"],
                    "predicate": predicate,
                    "wikilink": wikilink,
                    "content_hash": candidate["content_hash"],
                    "reason": candidate["reason"],
                }
            )
    if not suggested:
        payload = {
            "status": "NO_CHANGE",
            "operation": "ai propose link_suggestions",
            "capability": "C27",
            "action": "link_suggestions",
            "source": source_path,
            "source_sha256": source_hash,
            "candidate_set_sha256": set_hash,
            "candidate_set_path": set_path,
            "candidate_set_input_sha256": set_input_hash,
            "provider_called": False,
            "mutation_performed": False,
        }
        return payload, text, "", set_path
    target_markdown = render_frontmatter(properties, typed.body)
    if len(target_markdown.encode("utf-8")) > _MAX_TARGET_BYTES:
        raise ActionProposalError("C27_TARGET_TOO_LARGE", "link target exceeds its bounded byte limit")
    target_types = resolver
    target_types.update({source_path.removesuffix(".md"): typed.note_type, str(typed.properties["title"]): typed.note_type})
    _target_markdown_for_update(engine, vault, source_path, typed.note_type, target_markdown, target_types=target_types)
    diff = _unified_diff(source_path, text, target_markdown)
    target_hash = _sha256(target_markdown.encode("utf-8"))
    source_payload = _source_payload(source_path, source_hash, typed, fragment)
    bindings = _artifact_bindings(
        workspace,
        "link_suggestions",
        source=typed,
        candidate_set_sha256=set_hash,
        retrieval_profile_id=retrieval_profile_id,
    )
    target_payload = {
        "path": source_path,
        "type": typed.note_type,
        "expected_sha256": source_hash,
        "before_sha256": source_hash,
        "after_sha256": target_hash,
    }
    payload = _proposal_payload(
        action="link_suggestions",
        source=source_payload,
        target=target_payload,
        bindings=bindings,
        diff=diff,
        extra={"candidate_set": candidates, "suggested_links": suggested},
    )
    return payload, target_markdown, diff, set_path


def _make_normalize(
    workspace: Path,
    *,
    source_path: str,
    source_hash: str,
    text: str,
    typed: Any,
    engine: NoteEngine,
    vault: Path,
    fragment: DailyFragment | None,
) -> tuple[dict[str, Any], str, str] | None:
    before_properties_hash = _hash_json(typed.properties)
    normalized_properties = {key: typed.properties[key] for key in sorted(typed.properties, key=_nfc)}
    after_properties_hash = _hash_json(normalized_properties)
    target_markdown = render_frontmatter(normalized_properties, typed.body)
    if target_markdown == text:
        return None
    if len(target_markdown.encode("utf-8")) > _MAX_TARGET_BYTES:
        raise ActionProposalError("C27_TARGET_TOO_LARGE", "normalized target exceeds its bounded byte limit")
    changed_fields = sorted(
        {
            key
            for key in set(typed.properties) | set(normalized_properties)
            if typed.properties.get(key) != normalized_properties.get(key)
        },
        key=_nfc,
    )
    if not changed_fields:
        changed_fields = sorted(typed.properties, key=_nfc)
    target_types = _target_types(vault, engine)
    _target_markdown_for_update(engine, vault, source_path, typed.note_type, target_markdown, target_types=target_types)
    diff = _unified_diff(source_path, text, target_markdown)
    target_hash = _sha256(target_markdown.encode("utf-8"))
    source_payload = _source_payload(source_path, source_hash, typed, fragment)
    bindings = _artifact_bindings(
        workspace,
        "normalize",
        source=typed,
        candidate_set_sha256=None,
        retrieval_profile_id=None,
    )
    target_payload = {
        "path": source_path,
        "type": typed.note_type,
        "expected_sha256": source_hash,
        "before_sha256": source_hash,
        "after_sha256": target_hash,
    }
    payload = _proposal_payload(
        action="normalize",
        source=source_payload,
        target=target_payload,
        bindings=bindings,
        diff=diff,
        extra={
            "normalization": {
                "changed_fields": changed_fields,
                "before_properties_sha256": before_properties_hash,
                "after_properties_sha256": after_properties_hash,
            }
        },
    )
    return payload, target_markdown, diff


def generate_action_proposal(
    root: str | Path,
    *,
    action: str,
    source_path: str,
    expected_sha256: str,
    locator: str | None = None,
    fragment_sha256: str | None = None,
    target_path: str | None = None,
    target_type: str | None = None,
    title: str | None = None,
    selected_candidate_id: str | None = None,
    selected_candidate_sha256: str | None = None,
    candidate_set_file: str | Path | None = None,
    retrieval_profile_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Generate one create-only C27 Pending proposal."""

    operation = f"ai propose {action}"
    if action not in ACTION_TYPES:
        return _failure(operation, "C27_ACTION_INVALID", "action must be draft_note, link_suggestions, or normalize")
    try:
        workspace = _workspace(root)
        normalized, _, raw, text, typed, engine, vault = _load_source(workspace, source_path, expected_sha256)
        _check_privacy(typed)
        fragment = _fragment_binding(
            typed,
            text,
            locator=locator,
            fragment_sha256=fragment_sha256,
        )
        if action == "draft_note":
            if typed.note_type not in DRAFT_SOURCE_TYPES:
                raise ActionProposalError("C27_SOURCE_TYPE_INVALID", "draft_note source type is not allowed")
            payload, target_markdown, diff = _make_draft(
                workspace,
                source_path=normalized,
                source_hash=_sha256(raw),
                text=text,
                typed=typed,
                engine=engine,
                vault=vault,
                fragment=fragment,
                target_path=target_path,
                target_type=target_type,
                title=title,
                selected_candidate_id=selected_candidate_id,
                selected_candidate_sha256=selected_candidate_sha256,
            )
            return _write_pending_artifact(
                workspace,
                action=action,
                source=typed,
                source_path=normalized,
                source_hash=_sha256(raw),
                payload=payload,
                target_markdown=target_markdown,
                diff=diff,
            )
        if action == "link_suggestions":
            payload, target_markdown, diff, _ = _make_link_suggestions(
                workspace,
                source_path=normalized,
                source_hash=_sha256(raw),
                text=text,
                typed=typed,
                engine=engine,
                vault=vault,
                fragment=fragment,
                candidate_set_file=candidate_set_file,
                retrieval_profile_id=retrieval_profile_id,
            )
            if payload["status"] == "NO_CHANGE":
                return payload, EXIT_OK
            return _write_pending_artifact(
                workspace,
                action=action,
                source=typed,
                source_path=normalized,
                source_hash=_sha256(raw),
                payload=payload,
                target_markdown=target_markdown,
                diff=diff,
            )
        if target_path is not None or target_type is not None or title is not None:
            raise ActionProposalError("C27_NORMALIZE_INPUT_INVALID", "normalize does not accept draft target or title arguments")
        normalized_result = _make_normalize(
            workspace,
            source_path=normalized,
            source_hash=_sha256(raw),
            text=text,
            typed=typed,
            engine=engine,
            vault=vault,
            fragment=fragment,
        )
        if normalized_result is None:
            return {
                "status": "NO_CHANGE",
                "operation": operation,
                "capability": "C27",
                "action": action,
                "source": normalized,
                "source_sha256": _sha256(raw),
                "provider_called": False,
                "mutation_performed": False,
            }, EXIT_OK
        payload, target_markdown, diff = normalized_result
        return _write_pending_artifact(
            workspace,
            action=action,
            source=typed,
            source_path=normalized,
            source_hash=_sha256(raw),
            payload=payload,
            target_markdown=target_markdown,
            diff=diff,
        )
    except ActionProposalError as error:
        return _failure(operation, error.code, str(error))
    except (OSError, UnicodeError, FrontmatterError, NoteContractError, TemplateRenderError, ValueError, KeyError) as error:
        return _failure(operation, "C27_GENERATION_INVALID", str(error))


def generate_proposal(
    root: str | Path,
    *,
    action: str,
    source_path: str,
    expected_sha256: str,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Compatibility alias for the action-specific generator."""

    return generate_action_proposal(
        root,
        action=action,
        source_path=source_path,
        expected_sha256=expected_sha256,
        **kwargs,
    )


def generate_draft_note_proposal(root: str | Path, **kwargs: Any) -> tuple[dict[str, Any], int]:
    return generate_action_proposal(root, action="draft_note", **kwargs)


def generate_link_suggestions_proposal(root: str | Path, **kwargs: Any) -> tuple[dict[str, Any], int]:
    return generate_action_proposal(root, action="link_suggestions", **kwargs)


def generate_normalize_proposal(root: str | Path, **kwargs: Any) -> tuple[dict[str, Any], int]:
    return generate_action_proposal(root, action="normalize", **kwargs)


__all__ = [
    "ACTION_TYPES",
    "ActionProposalError",
    "candidate_sha256",
    "generate_action_proposal",
    "generate_draft_note_proposal",
    "generate_link_suggestions_proposal",
    "generate_normalize_proposal",
    "generate_proposal",
    "proposal_schema",
]

"""Deterministic, provider-free C22 retrieval over one C21 projection.

The C22 boundary deliberately keeps retrieval read-only.  The Vault remains the
source of truth, C21's immutable generation is the only input surface, and the
retrieval policy is checked against both the generated policy artifact and the
Blueprint before any candidate is returned.  This module implements lexical
matching and bounded typed-link expansion only by default.  The optional E01
local vector/RRF overlay is imported lazily so the C22 default remains byte
and behavior stable without an explicit opt-in.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from yaml import YAMLError

from .projection import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    ProjectionError,
    ProjectionRead,
    canonical_json_bytes,
    load_current_projection,
    retrieval_candidate_schema,
)
from .yaml_safe import load_yaml_text

RETRIEVAL_POLICY_PATH = "ops/policies/retrieval.yaml"
DEFAULT_BASELINE_PATH = "ops/tests/fixtures/c22_retrieval/evaluation.yaml"
PARSER_AND_CHUNKER_VERSION = "markdown-headings-v1"
INDEXER_VERSION = "vaultops.retrieval.v1"
SCHEMA_VERSION = 1
DEFAULT_LIMIT = 10

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_LINE_BREAKS = ("\r", "\v", "\f", "\x85", "\u2028", "\u2029")
_PRIVACY_FIELDS = frozenset({"sensitivity", "ai_policy", "ai_status"})
_JOURNAL_TYPES = frozenset({"daily", "weekly", "monthly"})
_ALL_NOTE_TYPES = frozenset(
    {
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
    }
)

_EXPECTED_PHASES = [
    "native_exact_and_links",
    "lexical_fts",
    "local_vectors",
    "reciprocal_rank_fusion",
    "bounded_typed_graph_expansion",
]
_EXPECTED_ONE_HOP_PREDICATES = [
    "supports",
    "contradicts",
    "explains",
    "applies_to",
    "derived_from",
    "implements",
    "raises",
    "projects",
    "areas",
    "topics",
    "sources",
    "people",
    "related",
]
_EXPECTED_DIRECTIONS = ["outgoing", "incoming"]
_EXPECTED_TWO_HOP_SEQUENCES = [
    [
        {"direction": "incoming", "predicate": "applies_to"},
        {"direction": "incoming", "predicate": "supports"},
    ],
    [
        {"direction": "incoming", "predicate": "applies_to"},
        {"direction": "incoming", "predicate": "contradicts"},
    ],
    [
        {"direction": "outgoing", "predicate": "implements"},
        {"direction": "outgoing", "predicate": "derived_from"},
    ],
    [
        {"direction": "outgoing", "predicate": "raises"},
        {"direction": "incoming", "predicate": "explains"},
    ],
    [
        {"direction": "incoming", "predicate": "projects"},
        {"direction": "outgoing", "predicate": "sources"},
    ],
]
_EXPECTED_DEFAULT_TYPES = [
    "knowledge",
    "source",
    "project",
    "project_note",
    "artifact",
    "idea",
    "question",
]
_EXPECTED_EXCLUDE_PATHS = [
    ".vault-bridge/**",
    ".obsidian-*/**",
    "99_System/**",
    "01_AI_Review/Rejected/**",
    "01_AI_Review/Expired/**",
]
_EXPECTED_REVIEW_PATHS = ["01_AI_Review/Pending/**", "01_AI_Review/Conflict/**"]
_EXPECTED_POLICY_GATE_POINTS = [
    "before_remote_embedding_or_indexing",
    "during_candidate_fusion_and_graph_expansion",
    "immediately_before_model_context",
]
_EXPECTED_FILTER_ORDER = ["scope", "path", "type", "sensitivity", "ai_policy"]
_EXPECTED_CANDIDATE_FIELDS = [
    "query_sha256",
    "policy_decision_sha256",
    "index_generation_id",
    "note_id",
    "path",
    "content_hash",
    "chunk_id",
    "chunk_hash",
    "chunk_locator",
    "retrieval_reason",
    "lexical_score_and_rank",
    "vector_score_and_rank",
    "rrf_parameter_and_rank",
    "graph_path",
    "parser_and_chunker_version",
    "embedding_provider_model_dimension_and_artifact_digest",
    "indexer_version",
    "retrieval_config_sha256",
]
_EXPECTED_ANSWER_FIELDS = ["note_id", "path", "locator", "evidence_hash", "uncertainty"]


class RetrievalError(ValueError):
    """Base error for rejected or unavailable retrieval inputs."""


class RetrievalConflict(RetrievalError):
    """Raised when a pinned generation or retrieval policy is stale."""


class RetrievalValidationError(RetrievalError):
    """Raised when a query, option, policy, or candidate violates its contract."""


@dataclass(frozen=True)
class _Chunk:
    note: Mapping[str, Any]
    chunk_id: str
    locator: str
    text: str
    title_tokens: tuple[str, ...]
    property_tokens: tuple[str, ...]
    body_tokens: tuple[str, ...]

    @property
    def all_tokens(self) -> tuple[str, ...]:
        return self.title_tokens + self.property_tokens + self.body_tokens

    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Transition:
    direction: str
    predicate: str
    from_id: str
    to_id: str
    edge: Mapping[str, Any]


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalValidationError(f"{label} must be a mapping")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalValidationError(f"{label} must be a list")
    return value


def _expect(actual: Any, expected: Any, locator: str) -> None:
    if actual != expected:
        raise RetrievalValidationError(f"retrieval policy drift at {locator}")


def _expect_mapping_keys(value: Mapping[str, Any], expected: set[str], locator: str) -> None:
    if set(value) != expected:
        raise RetrievalValidationError(f"retrieval policy keys drift at {locator}")


def _validate_retrieval_policy(retrieval: Mapping[str, Any]) -> None:
    _expect_mapping_keys(
        retrieval,
        {
            "phases",
            "graph_expansion",
            "corpus",
            "policy_gate_points",
            "remote_embedding",
            "local_embedding",
            "filter_before_model",
            "frozen_candidate_fields",
            "staleness",
            "answer_requires",
            "graphrag_default",
        },
        "/retrieval",
    )
    _expect(retrieval["phases"], _EXPECTED_PHASES, "/retrieval/phases")

    graph = _as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")
    _expect_mapping_keys(
        graph,
        {
            "default_hops",
            "maximum_hops",
            "per_hop_node_cap",
            "total_edge_cap",
            "total_candidate_cap",
            "one_hop_allowlist",
            "two_hop_sequence_allowlist",
            "unknown_sequence_policy",
        },
        "/retrieval/graph_expansion",
    )
    _expect(graph["default_hops"], 1, "/retrieval/graph_expansion/default_hops")
    _expect(graph["maximum_hops"], 2, "/retrieval/graph_expansion/maximum_hops")
    _expect(graph["per_hop_node_cap"], 20, "/retrieval/graph_expansion/per_hop_node_cap")
    _expect(graph["total_edge_cap"], 60, "/retrieval/graph_expansion/total_edge_cap")
    _expect(graph["total_candidate_cap"], 50, "/retrieval/graph_expansion/total_candidate_cap")
    allowlist = _as_mapping(graph["one_hop_allowlist"], "/retrieval/graph_expansion/one_hop_allowlist")
    _expect_mapping_keys(allowlist, {"predicates", "directions"}, "/retrieval/graph_expansion/one_hop_allowlist")
    _expect(allowlist["predicates"], _EXPECTED_ONE_HOP_PREDICATES, "/retrieval/graph_expansion/one_hop_allowlist/predicates")
    _expect(allowlist["directions"], _EXPECTED_DIRECTIONS, "/retrieval/graph_expansion/one_hop_allowlist/directions")
    _expect(
        graph["two_hop_sequence_allowlist"],
        _EXPECTED_TWO_HOP_SEQUENCES,
        "/retrieval/graph_expansion/two_hop_sequence_allowlist",
    )
    _expect(graph["unknown_sequence_policy"], "reject", "/retrieval/graph_expansion/unknown_sequence_policy")

    corpus = _as_mapping(retrieval["corpus"], "/retrieval/corpus")
    _expect_mapping_keys(
        corpus,
        {"default_include_types", "journal_requires_explicit_scope", "exclude_paths", "review_corpus_separate"},
        "/retrieval/corpus",
    )
    _expect(corpus["default_include_types"], _EXPECTED_DEFAULT_TYPES, "/retrieval/corpus/default_include_types")
    _expect(corpus["journal_requires_explicit_scope"], True, "/retrieval/corpus/journal_requires_explicit_scope")
    _expect(corpus["exclude_paths"], _EXPECTED_EXCLUDE_PATHS, "/retrieval/corpus/exclude_paths")
    _expect(corpus["review_corpus_separate"], _EXPECTED_REVIEW_PATHS, "/retrieval/corpus/review_corpus_separate")

    _expect(retrieval["policy_gate_points"], _EXPECTED_POLICY_GATE_POINTS, "/retrieval/policy_gate_points")
    remote = _as_mapping(retrieval["remote_embedding"], "/retrieval/remote_embedding")
    _expect_mapping_keys(
        remote,
        {"allowed_ai_policy", "ask_requires_digest_bound_interactive_authorization", "local_only_and_deny_forbidden"},
        "/retrieval/remote_embedding",
    )
    _expect(remote["allowed_ai_policy"], ["remote_ok"], "/retrieval/remote_embedding/allowed_ai_policy")
    _expect(
        remote["ask_requires_digest_bound_interactive_authorization"],
        True,
        "/retrieval/remote_embedding/ask_requires_digest_bound_interactive_authorization",
    )
    _expect(remote["local_only_and_deny_forbidden"], True, "/retrieval/remote_embedding/local_only_and_deny_forbidden")
    local = _as_mapping(retrieval["local_embedding"], "/retrieval/local_embedding")
    _expect_mapping_keys(local, {"deny_forbidden"}, "/retrieval/local_embedding")
    _expect(local["deny_forbidden"], True, "/retrieval/local_embedding/deny_forbidden")
    _expect(retrieval["filter_before_model"], _EXPECTED_FILTER_ORDER, "/retrieval/filter_before_model")
    _expect(retrieval["frozen_candidate_fields"], _EXPECTED_CANDIDATE_FIELDS, "/retrieval/frozen_candidate_fields")
    staleness = _as_mapping(retrieval["staleness"], "/retrieval/staleness")
    _expect_mapping_keys(staleness, {"default", "running_query_pins_generation"}, "/retrieval/staleness")
    _expect(staleness["default"], "fail_closed_and_require_rebuild", "/retrieval/staleness/default")
    _expect(staleness["running_query_pins_generation"], True, "/retrieval/staleness/running_query_pins_generation")
    _expect(retrieval["answer_requires"], _EXPECTED_ANSWER_FIELDS, "/retrieval/answer_requires")
    _expect(retrieval["graphrag_default"], "deferred", "/retrieval/graphrag_default")


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RetrievalValidationError(f"{label} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise RetrievalValidationError(f"cannot read {label}: {error}") from error


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise RetrievalValidationError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise RetrievalValidationError("KnowledgeHub must be an existing non-symlink directory")
    return workspace


def _load_contract(workspace: Path) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    policy_path = workspace / RETRIEVAL_POLICY_PATH
    policy_bytes = _regular_file(policy_path, "retrieval policy")
    blueprint_path = workspace / "blueprint/blueprint.yaml"
    blueprint_bytes = _regular_file(blueprint_path, "blueprint")
    try:
        policy_value = load_yaml_text(policy_bytes.decode("utf-8"))
        blueprint_value = load_yaml_text(blueprint_bytes.decode("utf-8"))
    except (UnicodeError, ValueError, YAMLError) as error:
        raise RetrievalValidationError(f"retrieval contract YAML is invalid: {error}") from error
    policy = _as_mapping(policy_value, "retrieval policy")
    blueprint = _as_mapping(blueprint_value, "Blueprint")
    _expect_mapping_keys(
        policy,
        {"schema_version", "contract_id", "capability_profile", "artifact_kind", "authoritative_inputs", "retrieval"},
        "retrieval policy",
    )
    _expect(policy["schema_version"], SCHEMA_VERSION, "/schema_version")
    _expect(policy["contract_id"], "knowledgeos-blueprint-v2", "/contract_id")
    _expect(policy["capability_profile"], "portable_core", "/capability_profile")
    _expect(policy["artifact_kind"], "retrieval_policy", "/artifact_kind")
    authoritative = _as_list(policy["authoritative_inputs"], "/authoritative_inputs")
    if len(authoritative) != 1:
        raise RetrievalValidationError("retrieval policy must have one authoritative input")
    input_record = _as_mapping(authoritative[0], "/authoritative_inputs/0")
    _expect_mapping_keys(input_record, {"path", "selectors", "sha256"}, "/authoritative_inputs/0")
    _expect(input_record["path"], "blueprint/blueprint.yaml", "/authoritative_inputs/0/path")
    _expect(input_record["selectors"], ["/retrieval"], "/authoritative_inputs/0/selectors")
    blueprint_sha256 = _sha256_bytes(blueprint_bytes)
    _expect(input_record["sha256"], blueprint_sha256, "/authoritative_inputs/0/sha256")
    retrieval = _as_mapping(policy.get("retrieval"), "/retrieval")
    _expect(retrieval, blueprint.get("retrieval"), "/retrieval")
    _validate_retrieval_policy(retrieval)
    return policy, retrieval, _sha256_bytes(policy_bytes)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(_nfc(value)))


def _fold_text(value: str) -> str:
    return re.sub(r"\s+", " ", _nfc(value).casefold()).strip()


def _canonical_query(query: str) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(query, str):
        raise RetrievalValidationError("query must be text supplied through stdin or a file")
    value = _nfc(query).strip()
    if not value:
        raise RetrievalValidationError("query must not be empty")
    if any(marker in value for marker in _LINE_BREAKS) or "\n" in value:
        raise RetrievalValidationError("query must be one logical line")
    terms = _tokens(value)
    if not terms:
        raise RetrievalValidationError("query must contain searchable Unicode letters or numbers")
    return value, _sha256_bytes(value.encode("utf-8")), terms


def read_query_file(path: str | Path) -> str:
    """Read and validate one UTF-8 query file without exposing its raw content."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise RetrievalValidationError("query file must be a regular file")
    try:
        return _canonical_query(candidate.read_bytes().decode("utf-8"))[0]
    except UnicodeError as error:
        raise RetrievalValidationError("query file must be valid UTF-8") from error


def _safe_control_file(workspace: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() and any(part in {"", ".", ".."} for part in candidate.parts):
        raise RetrievalValidationError("baseline path contains unsafe components")
    if candidate.is_absolute():
        requested = candidate
        try:
            relative = requested.relative_to(workspace)
        except ValueError as error:
            raise RetrievalValidationError("baseline file must be inside the control root") from error
    else:
        relative = candidate
        requested = workspace / candidate
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RetrievalValidationError("baseline path must not traverse a symlink")
    resolved = requested.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise RetrievalValidationError("baseline path escapes the control root") from error
    if not resolved.is_file():
        raise RetrievalValidationError("baseline file must be a regular file")
    return resolved


def _normalize_scope(scope: str | None) -> str | None:
    if scope is None:
        return None
    if not isinstance(scope, str):
        raise RetrievalValidationError("scope must be text or null")
    value = _nfc(scope).strip()
    if not value or any(marker in value for marker in _LINE_BREAKS) or "\n" in value:
        raise RetrievalValidationError("scope must be one logical non-empty line")
    if value.casefold().startswith("project:") and not value[8:].strip():
        raise RetrievalValidationError("project scope must name a project")
    return value


def _normalize_path_prefix(path_prefix: str | None) -> str | None:
    if path_prefix is None:
        return None
    if not isinstance(path_prefix, str):
        raise RetrievalValidationError("path_prefix must be text or null")
    value = _nfc(path_prefix).strip().strip("/")
    if not value or "\\" in value or any(marker in value for marker in _LINE_BREAKS):
        raise RetrievalValidationError("path_prefix must be a safe Vault-relative path prefix")
    if any(part in {"", ".", ".."} for part in PurePosixPath(value).parts):
        raise RetrievalValidationError("path_prefix contains unsafe path components")
    return value


def _normalize_options(
    retrieval: Mapping[str, Any],
    *,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
) -> tuple[str | None, str | None, tuple[str, ...], bool, int, int]:
    normalized_scope = _normalize_scope(scope)
    normalized_prefix = _normalize_path_prefix(path_prefix)
    corpus = _as_mapping(retrieval["corpus"], "/retrieval/corpus")
    default_types = tuple(str(item) for item in _as_list(corpus["default_include_types"], "/retrieval/corpus/default_include_types"))
    if include_types is None:
        selected_types = default_types
    else:
        if isinstance(include_types, str):
            raise RetrievalValidationError("include_types must be a sequence of note types")
        selected = [str(item) for item in include_types]
        if not selected or len(set(selected)) != len(selected):
            raise RetrievalValidationError("include_types must be a non-empty unique sequence")
        if any(item not in _ALL_NOTE_TYPES for item in selected):
            raise RetrievalValidationError("include_types contains an unknown note type")
        selected_types = tuple(selected)
    if not isinstance(include_review, bool):
        raise RetrievalValidationError("include_review must be boolean")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise RetrievalValidationError("limit must be an integer from 1 through 50")
    graph = _as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")
    maximum_hops = graph["maximum_hops"]
    if isinstance(hops, bool) or not isinstance(hops, int) or not 0 <= hops <= int(maximum_hops):
        raise RetrievalValidationError(f"hops must be an integer from 0 through {maximum_hops}")
    return normalized_scope, normalized_prefix, selected_types, include_review, limit, hops


def _path_matches(path: str, pattern: str) -> bool:
    normalized_path = _nfc(path).casefold()
    normalized_pattern = _nfc(pattern).casefold()
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        if "*" in prefix or "?" in prefix or "[" in prefix:
            return fnmatch.fnmatchcase(normalized_path, normalized_pattern)
        return normalized_path == prefix or normalized_path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def _flatten_property(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            f"{key} {_flatten_property(value[key])}" for key in sorted(value, key=lambda item: _nfc(str(item)))
        )
    if isinstance(value, list | tuple):
        return " ".join(_flatten_property(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _scope_match(note: Mapping[str, Any], scope: str) -> bool:
    target = scope
    if target.casefold().startswith("project:"):
        target = target[8:].strip()
    key = _fold_text(target)
    path = str(note["path"])
    path_parts = [_fold_text(part) for part in path.split("/")]
    stem = _fold_text(PurePosixPath(path).stem)
    if key in path_parts or key == stem:
        return True
    if key in {_fold_text(str(note["id"])), _fold_text(str(note.get("title", "")))}:
        return True
    properties = _as_mapping(note.get("properties"), f"properties for {path}")
    for name, value in properties.items():
        flattened = _fold_text(_flatten_property(value))
        if (key == flattened or key in flattened) and (
            name == "projects" or scope.casefold().startswith("project:")
        ):
            return True
    return False


def _filter_notes(
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    *,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
) -> tuple[tuple[Mapping[str, Any], ...], list[dict[str, str]], tuple[str, ...], bool, int, int]:
    normalized_scope, normalized_prefix, selected_types, normalized_review, normalized_limit, normalized_hops = _normalize_options(
        retrieval,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
    )
    corpus = _as_mapping(retrieval["corpus"], "/retrieval/corpus")
    exclude_patterns = [str(item) for item in _as_list(corpus["exclude_paths"], "/retrieval/corpus/exclude_paths")]
    review_patterns = [str(item) for item in _as_list(corpus["review_corpus_separate"], "/retrieval/corpus/review_corpus_separate")]
    included: list[Mapping[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for note in projection.notes:
        path = str(note.get("path", ""))
        note_type = str(note.get("type", ""))
        reason: str | None = None
        if any(_path_matches(path, pattern) for pattern in exclude_patterns):
            reason = "excluded_path"
        elif path.casefold().startswith("01_ai_review/"):
            if not normalized_review:
                reason = "review_corpus_separate"
            elif not any(_path_matches(path, pattern) for pattern in review_patterns):
                reason = "review_path_not_allowlisted"
        elif bool(corpus["journal_requires_explicit_scope"]) and note_type in _JOURNAL_TYPES and normalized_scope is None:
            reason = "journal_scope_required"
        elif note_type not in selected_types:
            reason = "type_not_in_scope"
        elif normalized_prefix is not None and not (
            path.casefold() == normalized_prefix.casefold()
            or path.casefold().startswith(normalized_prefix.casefold() + "/")
        ):
            reason = "path_out_of_scope"
        elif normalized_scope is not None and not _scope_match(note, normalized_scope):
            reason = "scope_mismatch"
        else:
            properties = _as_mapping(note.get("properties"), f"properties for {path}")
            sensitivity = properties.get("sensitivity")
            ai_policy = properties.get("ai_policy")
            if sensitivity not in {"public", "personal", "confidential"}:
                reason = "sensitivity_missing_or_invalid"
            elif sensitivity == "confidential":
                reason = "confidential_corpus_excluded"
            elif ai_policy not in {"remote_ok", "ask", "local_only"}:
                reason = "ai_policy_denied_or_invalid"
        if reason is None:
            included.append(note)
        else:
            excluded.append({"path": path, "reason": reason})
    return tuple(included), excluded, selected_types, normalized_review, normalized_limit, normalized_hops


def _chunk_id(note_id: str, locator: str) -> str:
    digest = _sha256_bytes(f"{note_id}\0{locator}".encode())[:24]
    return f"{note_id}:{digest}"


def _body_chunks(note: Mapping[str, Any]) -> list[tuple[str, str]]:
    body = str(note.get("body", ""))
    if not body.strip():
        return []
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [("/body", body.strip())]
    chunks: list[tuple[str, str]] = []
    seen_headings: Counter[str] = Counter()
    if matches[0].start() > 0 and body[: matches[0].start()].strip():
        chunks.append(("/body/preamble", body[: matches[0].start()].strip()))
    for index, match in enumerate(matches):
        segment_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        segment = body[match.start() : segment_end].strip()
        heading = _nfc(match.group(2).strip())
        seen_headings[heading] += 1
        suffix = "" if seen_headings[heading] == 1 else f"@{seen_headings[heading]}"
        chunks.append((f"#{heading}{suffix}", segment))
    return chunks


def _chunks_for_note(note: Mapping[str, Any]) -> tuple[_Chunk, ...]:
    note_id = str(note["id"])
    title = str(note.get("title", ""))
    properties = _as_mapping(note.get("properties"), f"properties for {note_id}")
    property_text = " ".join(
        f"{key} {_flatten_property(properties[key])}"
        for key in sorted(properties, key=lambda item: _nfc(str(item)))
        if key not in _PRIVACY_FIELDS
    )
    result: list[_Chunk] = [
        _Chunk(
            note=note,
            chunk_id=_chunk_id(note_id, "/frontmatter"),
            locator="/frontmatter",
            text=f"{title}\n{property_text}".strip(),
            title_tokens=_tokens(title),
            property_tokens=_tokens(property_text),
            body_tokens=(),
        )
    ]
    for locator, text in _body_chunks(note):
        result.append(
            _Chunk(
                note=note,
                chunk_id=_chunk_id(note_id, locator),
                locator=locator,
                text=text,
                title_tokens=(),
                property_tokens=(),
                body_tokens=_tokens(text),
            )
        )
    return tuple(result)


def _chunk_sort_key(chunk: _Chunk) -> tuple[Any, ...]:
    return (chunk.locator != "/frontmatter", _nfc(chunk.locator).encode("utf-8"), chunk.chunk_id)


def _matching_token_count(term: str, counts: Mapping[str, int]) -> int:
    """Match exact tokens and deterministic morphology-compatible substrings."""

    exact = counts.get(term, 0)
    if exact:
        return exact
    return sum(count for token, count in counts.items() if term in token)


def _lexical_candidates(
    notes: Sequence[Mapping[str, Any]],
    query: str,
    query_sha256: str,
    terms: Sequence[str],
    generation_id: str,
    policy_decision_sha256: str,
    retrieval_config_sha256: str,
) -> list[dict[str, Any]]:
    chunks_by_note = {str(note["id"]): _chunks_for_note(note) for note in notes}
    document_terms: dict[str, set[str]] = {}
    for note_id, chunks in chunks_by_note.items():
        document_terms[note_id] = {term for chunk in chunks for term in chunk.all_tokens}
    document_frequency = Counter(term for values in document_terms.values() for term in values)
    note_count = max(1, len(notes))
    idf = {
        term: math.log((note_count + 1) / (document_frequency.get(term, 0) + 1)) + 1.0
        for term in set(terms)
    }
    folded_query = _fold_text(query)
    scored: list[tuple[float, _Chunk, dict[str, Any]]] = []
    for note in notes:
        note_id = str(note["id"])
        for chunk in chunks_by_note[note_id]:
            fields = {
                "title": Counter(chunk.title_tokens),
                "properties": Counter(chunk.property_tokens),
                "body": Counter(chunk.body_tokens),
            }
            weights = {"title": 3.0, "properties": 1.5, "body": 1.0}
            matched_terms: list[str] = []
            field_hits: dict[str, list[str]] = {field: [] for field in fields}
            score = 0.0
            for term in terms:
                present = False
                for field, counts in fields.items():
                    count = _matching_token_count(term, counts)
                    if count:
                        present = True
                        field_hits[field].append(term)
                        score += count * idf[term] * weights[field]
                if present and term not in matched_terms:
                    matched_terms.append(term)
            if not matched_terms:
                continue
            folded_chunk = _fold_text(chunk.text)
            if len(matched_terms) == len(set(terms)):
                score += 0.5
            if folded_query and folded_query in folded_chunk:
                score += 1.5
            score = score / (1.0 + math.log1p(max(1, len(chunk.all_tokens))))
            lexical = {
                "algorithm": "lexical_fts_v1",
                "score": round(score, 12),
                "rank": None,
                "query_terms": list(dict.fromkeys(terms)),
                "matched_terms": matched_terms,
                "field_hits": field_hits,
            }
            scored.append((score, chunk, lexical))

    best_by_note: dict[str, tuple[float, _Chunk, dict[str, Any]]] = {}
    for score, chunk, lexical in scored:
        note_id = str(chunk.note["id"])
        previous = best_by_note.get(note_id)
        if previous is None or score > previous[0] or (score == previous[0] and _chunk_sort_key(chunk) < _chunk_sort_key(previous[1])):
            best_by_note[note_id] = (score, chunk, lexical)
    ranked = sorted(
        best_by_note.values(),
        key=lambda item: (
            -item[0],
            _nfc(str(item[1].note["path"])).encode("utf-8"),
            str(item[1].note["id"]),
            _chunk_sort_key(item[1]),
        ),
    )
    result: list[dict[str, Any]] = []
    for rank, (_, chunk, lexical) in enumerate(ranked, start=1):
        lexical["rank"] = rank
        result.append(
            _candidate(
                chunk,
                query_sha256=query_sha256,
                policy_decision_sha256=policy_decision_sha256,
                generation_id=generation_id,
                retrieval_config_sha256=retrieval_config_sha256,
                retrieval_reason="lexical_match",
                lexical=lexical,
                graph_path=[],
            )
        )
    return result


def _candidate(
    chunk: _Chunk,
    *,
    query_sha256: str,
    policy_decision_sha256: str,
    generation_id: str,
    retrieval_config_sha256: str,
    retrieval_reason: str,
    lexical: Mapping[str, Any],
    graph_path: Sequence[str],
) -> dict[str, Any]:
    candidate = {
        "query_sha256": query_sha256,
        "policy_decision_sha256": policy_decision_sha256,
        "index_generation_id": generation_id,
        "note_id": str(chunk.note["id"]),
        "path": str(chunk.note["path"]),
        "content_hash": str(chunk.note["content_hash"]),
        "chunk_id": chunk.chunk_id,
        "chunk_hash": chunk.chunk_hash,
        "chunk_locator": chunk.locator,
        "retrieval_reason": retrieval_reason,
        "lexical_score_and_rank": dict(lexical),
        "vector_score_and_rank": None,
        "rrf_parameter_and_rank": None,
        "graph_path": list(graph_path),
        "parser_and_chunker_version": PARSER_AND_CHUNKER_VERSION,
        "embedding_provider_model_dimension_and_artifact_digest": None,
        "indexer_version": INDEXER_VERSION,
        "retrieval_config_sha256": retrieval_config_sha256,
    }
    errors = sorted(
        Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate),
        key=lambda error: error.json_path,
    )
    if errors:
        raise RetrievalValidationError(f"frozen retrieval candidate is invalid: {errors[0].message}")
    return candidate


def _transition_sort_key(transition: _Transition, note_by_id: Mapping[str, Mapping[str, Any]]) -> tuple[Any, ...]:
    direction_order = {"outgoing": 0, "incoming": 1}
    target = note_by_id[transition.to_id]
    return (
        direction_order.get(transition.direction, 99),
        transition.predicate,
        _nfc(str(target["path"])).encode("utf-8"),
        transition.to_id,
        str(transition.edge.get("source_locator", "")),
        str(transition.edge.get("object_locator", "")),
    )


def _adjacency(
    projection: ProjectionRead,
    eligible_ids: set[str],
    retrieval: Mapping[str, Any],
) -> dict[str, tuple[_Transition, ...]]:
    graph = _as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")
    allowlist = _as_mapping(graph["one_hop_allowlist"], "/retrieval/graph_expansion/one_hop_allowlist")
    predicates = {str(item) for item in _as_list(allowlist["predicates"], "graph predicates")}
    directions = {str(item) for item in _as_list(allowlist["directions"], "graph directions")}
    adjacency: dict[str, list[_Transition]] = defaultdict(list)
    for edge in projection.edges:
        subject_id = str(edge["subject_id"])
        object_id = str(edge["object_id"])
        predicate = str(edge["predicate"])
        if subject_id not in eligible_ids or object_id not in eligible_ids or predicate not in predicates:
            continue
        if "outgoing" in directions:
            adjacency[subject_id].append(_Transition("outgoing", predicate, subject_id, object_id, edge))
        if "incoming" in directions:
            adjacency[object_id].append(_Transition("incoming", predicate, object_id, subject_id, edge))
    note_by_id = {str(note["id"]): note for note in projection.notes if str(note["id"]) in eligible_ids}
    return {
        note_id: tuple(sorted(values, key=lambda item: _transition_sort_key(item, note_by_id)))
        for note_id, values in adjacency.items()
    }


def _sequence_allowed(path: Sequence[tuple[str, str]], transition: _Transition, retrieval: Mapping[str, Any]) -> bool:
    if not path:
        return True
    graph = _as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")
    current = list(path) + [(transition.direction, transition.predicate)]
    allowed = _as_list(graph["two_hop_sequence_allowlist"], "two-hop sequence allowlist")
    for sequence in allowed:
        pairs = _as_list(sequence, "two-hop sequence")
        normalized = [
            (
                str(_as_mapping(pair, "two-hop step")["direction"]),
                str(_as_mapping(pair, "two-hop step")["predicate"]),
            )
            for pair in pairs
        ]
        if normalized == current:
            return True
    return False


def _representative_chunk(note: Mapping[str, Any]) -> _Chunk:
    chunks = _chunks_for_note(note)
    for chunk in chunks:
        if chunk.locator != "/frontmatter" and chunk.text:
            return chunk
    return chunks[0]


def _expand_typed_links(
    projection: ProjectionRead,
    lexical: list[dict[str, Any]],
    eligible_notes: Sequence[Mapping[str, Any]],
    retrieval: Mapping[str, Any],
    *,
    hops: int,
    query_sha256: str,
    policy_decision_sha256: str,
    generation_id: str,
    retrieval_config_sha256: str,
) -> list[dict[str, Any]]:
    if hops == 0 or not lexical:
        return lexical
    graph = _as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")
    per_hop_node_cap = int(graph["per_hop_node_cap"])
    total_edge_cap = int(graph["total_edge_cap"])
    total_candidate_cap = int(graph["total_candidate_cap"])
    eligible_ids = {str(note["id"]) for note in eligible_notes}
    note_by_id = {str(note["id"]): note for note in eligible_notes}
    adjacency = _adjacency(projection, eligible_ids, retrieval)
    graph_best: dict[str, tuple[tuple[Any, ...], list[str]]] = {}
    edge_count = 0
    graph_node_ids: set[str] = set()
    for seed in lexical[:total_candidate_cap]:
        seed_id = str(seed["note_id"])
        lexical_rank = seed["lexical_score_and_rank"].get("rank")
        rrf_rank = (seed.get("rrf_parameter_and_rank") or {}).get("rank")
        seed_rank = int(lexical_rank if lexical_rank is not None else rrf_rank)
        frontier: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]] = [
            (seed_id, (seed_id,), (), ())
        ]
        for depth in range(hops):
            next_frontier: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]] = []
            for current_id, visited, sequence, path_strings in frontier:
                selected_neighbors: set[str] = set()
                for transition in adjacency.get(current_id, ()):
                    if len(selected_neighbors) >= per_hop_node_cap or edge_count >= total_edge_cap:
                        break
                    if transition.to_id in visited or transition.to_id not in eligible_ids:
                        continue
                    if transition.to_id in selected_neighbors:
                        continue
                    if not _sequence_allowed(sequence, transition, retrieval):
                        continue
                    selected_neighbors.add(transition.to_id)
                    edge_count += 1
                    new_sequence = sequence + ((transition.direction, transition.predicate),)
                    edge = transition.edge
                    path_token = (
                        f"{transition.direction}:{transition.predicate}:"
                        f"{edge['subject_id']}->{edge['object_id']}"
                    )
                    new_path_strings = path_strings + (path_token,)
                    new_visited = visited + (transition.to_id,)
                    graph_node_ids.add(transition.to_id)
                    graph_key = (
                        len(new_path_strings),
                        seed_rank,
                        tuple(new_path_strings),
                        _nfc(str(note_by_id[transition.to_id]["path"])).encode("utf-8"),
                        transition.to_id,
                    )
                    previous = graph_best.get(transition.to_id)
                    if previous is None or graph_key < previous[0]:
                        graph_best[transition.to_id] = (graph_key, list(new_path_strings))
                    if depth + 1 < hops and len(graph_node_ids) < total_candidate_cap:
                        next_frontier.append((transition.to_id, new_visited, new_sequence, new_path_strings))
                if edge_count >= total_edge_cap:
                    break
            frontier = next_frontier
            if not frontier or edge_count >= total_edge_cap or len(graph_node_ids) >= total_candidate_cap:
                break
        if edge_count >= total_edge_cap or len(graph_node_ids) >= total_candidate_cap:
            break

    lexical_by_id = {str(candidate["note_id"]): candidate for candidate in lexical}
    for note_id, (_, path) in graph_best.items():
        if note_id in lexical_by_id:
            candidate = lexical_by_id[note_id]
            existing = list(candidate["graph_path"])
            if not existing or tuple(path) < tuple(existing):
                candidate["graph_path"] = path
            if "typed_link_expansion" not in candidate["retrieval_reason"]:
                candidate["retrieval_reason"] += ";typed_link_expansion"

    graph_candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for note_id, (graph_key, path) in graph_best.items():
        if note_id in lexical_by_id:
            continue
        note = note_by_id[note_id]
        chunk = _representative_chunk(note)
        lexical_score = {
            "algorithm": "lexical_fts_v1",
            "score": 0.0,
            "rank": None,
            "query_terms": [],
            "matched_terms": [],
            "field_hits": {"title": [], "properties": [], "body": []},
        }
        graph_candidates.append(
            (
                graph_key,
                _candidate(
                    chunk,
                    query_sha256=query_sha256,
                    policy_decision_sha256=policy_decision_sha256,
                    generation_id=generation_id,
                    retrieval_config_sha256=retrieval_config_sha256,
                    retrieval_reason="typed_link_expansion",
                    lexical=lexical_score,
                    graph_path=path,
                ),
            )
        )
    graph_candidates.sort(key=lambda item: item[0])
    return lexical + [candidate for _, candidate in graph_candidates]


def _policy_decision_hash(
    retrieval: Mapping[str, Any],
    projection: ProjectionRead,
    excluded: Sequence[Mapping[str, str]],
    included: Sequence[Mapping[str, Any]],
    *,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str],
    include_review: bool,
    limit: int,
    hops: int,
    retrieval_config_sha256: str,
) -> str:
    decision = {
        "schema_version": SCHEMA_VERSION,
        "retrieval_phases": list(retrieval["phases"]),
        "filter_before_model": list(retrieval["filter_before_model"]),
        "scope": scope,
        "path_prefix": path_prefix,
        "include_types": list(include_types),
        "include_review": include_review,
        "limit": limit,
        "hops": hops,
        "retrieval_config_sha256": retrieval_config_sha256,
        "index_generation_id": projection.manifest.get("generation_id"),
        "included": [
            {"id": str(note["id"]), "path": str(note["path"]), "type": str(note["type"]), "content_hash": str(note["content_hash"])}
            for note in included
        ],
        "excluded": sorted(
            [{"path": str(item["path"]), "reason": str(item["reason"])} for item in excluded],
            key=lambda item: (item["path"], item["reason"]),
        ),
    }
    return _sha256_bytes(canonical_json_bytes(decision))


def _execute_projection(
    projection: ProjectionRead,
    query: str,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    use_vector: bool = False,
) -> dict[str, Any]:
    if not isinstance(use_vector, bool):
        raise RetrievalValidationError("use_vector must be boolean")
    if use_vector:
        # Import lazily: E01 is an optional overlay and imports C22 helpers to
        # preserve one policy filter, chunker, candidate schema, and graph
        # implementation.  Keeping this branch opt-in preserves C22 defaults.
        from .vector import vector_retrieve_projection

        return vector_retrieve_projection(
            projection,
            query,
            policy=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            scope=scope,
            path_prefix=path_prefix,
            include_types=include_types,
            include_review=include_review,
            limit=limit,
            hops=hops,
        )
    normalized_query, query_sha256, terms = _canonical_query(query)
    included, excluded, selected_types, normalized_review, normalized_limit, normalized_hops = _filter_notes(
        projection,
        retrieval,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
    )
    policy_hash = _policy_decision_hash(
        retrieval,
        projection,
        excluded,
        included,
        scope=_normalize_scope(scope),
        path_prefix=_normalize_path_prefix(path_prefix),
        include_types=selected_types,
        include_review=normalized_review,
        limit=normalized_limit,
        hops=normalized_hops,
        retrieval_config_sha256=retrieval_config_sha256,
    )
    lexical = _lexical_candidates(
        included,
        normalized_query,
        query_sha256,
        terms,
        str(projection.manifest["generation_id"]),
        policy_hash,
        retrieval_config_sha256,
    )
    combined = _expand_typed_links(
        projection,
        lexical,
        included,
        retrieval,
        hops=normalized_hops,
        query_sha256=query_sha256,
        policy_decision_sha256=policy_hash,
        generation_id=str(projection.manifest["generation_id"]),
        retrieval_config_sha256=retrieval_config_sha256,
    )
    total_cap = int(_as_mapping(retrieval["graph_expansion"], "/retrieval/graph_expansion")["total_candidate_cap"])
    candidates = combined[: min(normalized_limit, total_cap)]
    return {
        "query_sha256": query_sha256,
        "policy_decision_sha256": policy_hash,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "candidates": candidates,
        "filters": {
            "scope": _normalize_scope(scope),
            "path_prefix": _normalize_path_prefix(path_prefix),
            "include_types": list(selected_types),
            "include_review": normalized_review,
            "journal_requires_explicit_scope": bool(
                _as_mapping(retrieval["corpus"], "/retrieval/corpus")["journal_requires_explicit_scope"]
            ),
            "included_note_count": len(included),
            "excluded_note_count": len(excluded),
            "excluded": excluded,
        },
        "provider_called": False,
        "mutation_performed": False,
    }


def _failure(operation: str, code: str, message: str, exit_code: int) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": "C22",
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, exit_code


def _run_retrieval(
    operation: str,
    root: str | Path,
    query: str,
    *,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    expected_generation_id: str | None,
    use_vector: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        workspace = _workspace(root)
        _canonical_query(query)
        if expected_generation_id is not None and (
            not isinstance(expected_generation_id, str) or not _GENERATION_ID_RE.fullmatch(expected_generation_id)
        ):
            raise RetrievalValidationError("expected_generation_id is unsafe")
    except RetrievalValidationError as error:
        return _failure(operation, "RETRIEVAL_INPUT_INVALID", str(error), EXIT_INPUT_INVALID)

    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        lowered = message.casefold()
        code = "RETRIEVAL_INDEX_STALE" if any(token in lowered for token in ("stale", "digest", "source changed")) else "RETRIEVAL_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(
            operation,
            "RETRIEVAL_GENERATION_MISMATCH",
            "current projection generation does not match expected_generation_id",
            EXIT_CONFLICT,
        )

    try:
        _, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        manifest_digest = projection.manifest.get("retrieval_config_sha256")
        if manifest_digest != retrieval_config_sha256:
            raise RetrievalConflict("pinned projection retrieval policy digest does not match the current policy")
        result = _execute_projection(
            projection,
            query,
            retrieval=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            scope=scope,
            path_prefix=path_prefix,
            include_types=include_types,
            include_review=include_review,
            limit=limit,
            hops=hops,
            use_vector=use_vector,
        )
    except RetrievalConflict as error:
        return _failure(operation, "RETRIEVAL_POLICY_STALE", str(error), EXIT_CONFLICT)
    except RetrievalValidationError as error:
        return _failure(operation, "RETRIEVAL_CONTRACT_INVALID", str(error), EXIT_CONFLICT)
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "RETRIEVAL_INVALID", str(error), EXIT_CONFLICT)

    report = {
        "status": "PASS",
        "operation": f"vector {operation}" if use_vector else operation,
        "capability": "E01" if use_vector else "C22",
        "query_sha256": result["query_sha256"],
        "policy_decision_sha256": result["policy_decision_sha256"],
        "index_generation_id": result["index_generation_id"],
        "retrieval_config_sha256": result["retrieval_config_sha256"],
        "candidate_count": len(result["candidates"]),
        "candidates": result["candidates"],
        "filters": result["filters"],
        "source_verified": True,
        "vector_enabled": bool(result.get("vector_enabled", False)),
        "provider_called": False,
        "mutation_performed": False,
    }
    if result.get("vector_config_sha256") is not None:
        report["vector_config_sha256"] = result["vector_config_sha256"]
    if result.get("retrieval_mode") is not None:
        report["retrieval_mode"] = result["retrieval_mode"]
    return report, EXIT_OK


def search(
    root: str | Path,
    query: str,
    *,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 0,
    expected_generation_id: str | None = None,
    use_vector: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run lexical retrieval and optional explicitly requested bounded expansion."""

    return _run_retrieval(
        "search",
        root,
        query,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        expected_generation_id=expected_generation_id,
        use_vector=use_vector,
    )


def retrieve(
    root: str | Path,
    query: str,
    *,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
    expected_generation_id: str | None = None,
    use_vector: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run lexical retrieval followed by policy-bounded typed-link expansion."""

    return _run_retrieval(
        "retrieve",
        root,
        query,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        expected_generation_id=expected_generation_id,
        use_vector=use_vector,
    )


def search_projection(
    projection: ProjectionRead,
    query: str,
    *,
    policy: Mapping[str, Any],
    retrieval_config_sha256: str,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 0,
    use_vector: bool = False,
) -> dict[str, Any]:
    """Pure C22 execution over an already pinned projection and policy."""

    retrieval = _as_mapping(policy.get("retrieval"), "policy retrieval") if "retrieval" in policy else policy
    _validate_retrieval_policy(retrieval)
    if not isinstance(retrieval_config_sha256, str) or not _SHA256_RE.fullmatch(retrieval_config_sha256):
        raise RetrievalValidationError("retrieval_config_sha256 must be lowercase SHA-256")
    return _execute_projection(
        projection,
        query,
        retrieval=retrieval,
        retrieval_config_sha256=retrieval_config_sha256,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        use_vector=use_vector,
    )


def retrieve_projection(
    projection: ProjectionRead,
    query: str,
    *,
    policy: Mapping[str, Any],
    retrieval_config_sha256: str,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
    use_vector: bool = False,
) -> dict[str, Any]:
    """Pure lexical plus typed-link C22 execution over one pinned generation."""

    return search_projection(
        projection,
        query,
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        use_vector=use_vector,
    )


def _load_baseline(workspace: Path, baseline_path: str | Path | None) -> tuple[Mapping[str, Any], str]:
    path = _safe_control_file(workspace, baseline_path or DEFAULT_BASELINE_PATH)
    raw = _regular_file(path, "frozen retrieval baseline")
    try:
        value = load_yaml_text(raw.decode("utf-8"))
    except (UnicodeError, ValueError, YAMLError) as error:
        raise RetrievalValidationError(f"frozen retrieval baseline YAML is invalid: {error}") from error
    baseline = _as_mapping(value, "frozen retrieval baseline")
    _expect_mapping_keys(baseline, {"schema_version", "capability", "profile", "queries"}, "frozen retrieval baseline")
    _expect(baseline["schema_version"], SCHEMA_VERSION, "/schema_version")
    _expect(baseline["capability"], "C22", "/capability")
    _expect(baseline["profile"], "portable_core", "/profile")
    queries = _as_list(baseline["queries"], "/queries")
    if not queries:
        raise RetrievalValidationError("frozen retrieval baseline must contain at least one query")
    return baseline, _sha256_bytes(raw)


def _evaluate_case(
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    required = {"id", "query", "mode", "scope", "path_prefix", "include_types", "include_review", "limit", "hops", "expected_prefix_paths", "required_paths", "required_graph_predicates"}
    if set(case) != required:
        raise RetrievalValidationError("frozen baseline query has unexpected or missing fields")
    case_id = case["id"]
    if not isinstance(case_id, str) or not case_id:
        raise RetrievalValidationError("frozen baseline query id must be non-empty text")
    mode = case["mode"]
    if mode not in {"search", "retrieve"}:
        raise RetrievalValidationError(f"unsupported frozen baseline mode: {mode}")
    query = case["query"]
    if not isinstance(query, str):
        raise RetrievalValidationError(f"frozen baseline query {case_id} must contain text")
    if mode == "search":
        result = search_projection(
            projection,
            query,
            policy=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            scope=case["scope"],
            path_prefix=case["path_prefix"],
            include_types=case["include_types"],
            include_review=case["include_review"],
            limit=case["limit"],
            hops=case["hops"],
        )
    else:
        result = retrieve_projection(
            projection,
            query,
            policy=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            scope=case["scope"],
            path_prefix=case["path_prefix"],
            include_types=case["include_types"],
            include_review=case["include_review"],
            limit=case["limit"],
            hops=case["hops"],
        )
    candidates = result["candidates"]
    candidate_paths = [str(candidate["path"]) for candidate in candidates]
    expected_prefix = _as_list(case["expected_prefix_paths"], f"{case_id}.expected_prefix_paths")
    required_paths = _as_list(case["required_paths"], f"{case_id}.required_paths")
    required_predicates = _as_list(case["required_graph_predicates"], f"{case_id}.required_graph_predicates")
    prefix_match = candidate_paths[: len(expected_prefix)] == expected_prefix
    required_paths_match = all(path in candidate_paths for path in required_paths)
    graph_paths = [
        str(token)
        for candidate in candidates
        for token in candidate.get("graph_path", [])
    ]
    graph_predicates = sorted(
        {
            token.split(":", 2)[1]
            for token in graph_paths
            if token.count(":") >= 2
        }
    )
    required_predicates_match = all(predicate in graph_predicates for predicate in required_predicates)
    expected_paths = list(dict.fromkeys([*expected_prefix, *required_paths]))
    found = sum(path in candidate_paths for path in expected_paths)
    first_expected_rank = None
    if expected_prefix:
        try:
            first_expected_rank = candidate_paths.index(str(expected_prefix[0])) + 1
        except ValueError:
            first_expected_rank = None
    reciprocal_rank = 0.0 if first_expected_rank is None else 1.0 / first_expected_rank
    return {
        "id": case_id,
        "mode": mode,
        "query_sha256": result["query_sha256"],
        "policy_decision_sha256": result["policy_decision_sha256"],
        "candidate_count": len(candidates),
        "candidate_paths": candidate_paths,
        "expected_prefix_paths": expected_prefix,
        "prefix_match": prefix_match,
        "required_paths": required_paths,
        "required_paths_match": required_paths_match,
        "required_graph_predicates": required_predicates,
        "graph_predicates": graph_predicates,
        "required_graph_predicates_match": required_predicates_match,
        "reciprocal_rank": round(reciprocal_rank, 12),
        "expected_path_recall": round(found / len(expected_paths), 12) if expected_paths else 1.0,
        "passed": prefix_match and required_paths_match and required_predicates_match,
    }


def evaluate_frozen_baseline(
    root: str | Path,
    *,
    baseline_path: str | Path | None = None,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Evaluate deterministic C22 cases while pinning one verified generation."""

    operation = "retrieval evaluate"
    try:
        workspace = _workspace(root)
        baseline, baseline_sha256 = _load_baseline(workspace, baseline_path)
        if expected_generation_id is not None and (
            not isinstance(expected_generation_id, str) or not _GENERATION_ID_RE.fullmatch(expected_generation_id)
        ):
            raise RetrievalValidationError("expected_generation_id is unsafe")
    except RetrievalValidationError as error:
        return _failure(operation, "RETRIEVAL_BASELINE_INVALID", str(error), EXIT_INPUT_INVALID)
    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        code = "RETRIEVAL_INDEX_STALE" if any(token in message.casefold() for token in ("stale", "digest", "source changed")) else "RETRIEVAL_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(operation, "RETRIEVAL_GENERATION_MISMATCH", "current projection generation does not match expected_generation_id", EXIT_CONFLICT)
    try:
        _, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise RetrievalConflict("pinned projection retrieval policy digest does not match the current policy")
        query_records = _as_list(baseline["queries"], "/queries")
        cases = []
        for item in query_records:
            case = _as_mapping(item, "frozen baseline query")
            cases.append(_evaluate_case(projection, retrieval, retrieval_config_sha256, case))
    except RetrievalConflict as error:
        return _failure(operation, "RETRIEVAL_POLICY_STALE", str(error), EXIT_CONFLICT)
    except RetrievalValidationError as error:
        return _failure(operation, "RETRIEVAL_BASELINE_INVALID", str(error), EXIT_CONFLICT)
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "RETRIEVAL_EVALUATION_INVALID", str(error), EXIT_CONFLICT)

    passed_count = sum(1 for case in cases if case["passed"])
    case_count = len(cases)
    metrics = {
        "case_count": case_count,
        "passed_case_count": passed_count,
        "all_cases_passed": passed_count == case_count,
        "mean_expected_path_recall": round(sum(case["expected_path_recall"] for case in cases) / case_count, 12),
        "mean_reciprocal_rank": round(sum(case["reciprocal_rank"] for case in cases) / case_count, 12),
    }
    report = {
        "status": "PASS" if metrics["all_cases_passed"] else "FAIL",
        "operation": operation,
        "capability": "C22",
        "baseline_sha256": baseline_sha256,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "cases": cases,
        "metrics": metrics,
        "source_verified": True,
        "provider_called": False,
        "mutation_performed": False,
    }
    return report, EXIT_OK if metrics["all_cases_passed"] else EXIT_CONFLICT


evaluate = evaluate_frozen_baseline
search_records = search_projection
retrieve_records = retrieve_projection

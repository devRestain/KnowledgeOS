"""C34 immutable Qwen indexing and learned retrieval evaluation.

The C34 surface is an opt-in overlay on top of the C21 projection and C25
privacy gate.  It stores only private runtime vectors and provenance, never
Vault content, and never changes the current projection generation.  Every
index identity includes the projection, parser/chunker, model digest,
dimension, prompt roles, indexer, and retrieval-policy digests.  A reader
rejects any identity drift and requires an explicit rebuild.

The provider is an injected seam.  Production callers may pass the bounded
C33 Ollama client, while tests use a deterministic fake provider.  The C34
quality report deliberately keeps live-model promotion false; E02 is the
separate evidence gate for accuracy, privacy, citation, staleness, latency,
and memory on an authorized installed model.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from yaml import YAMLError

from .projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    ProjectionError,
    ProjectionRead,
    canonical_json_bytes,
    load_current_projection,
    retrieval_candidate_schema,
)
from .recovery import fsync_directory
from .retrieval import (
    PARSER_AND_CHUNKER_VERSION,
    _as_mapping,
    _candidate,
    _candidate_provenance,
    _canonical_query,
    _chunk_sort_key,
    _chunks_for_note,
    _filter_notes,
    _lexical_candidates,
    _normalized_options,
    _policy_decision_hash,
    _revalidate_candidates,
    retrieve_projection,
    search_projection,
)
from .vector import _validate_contract, reciprocal_rank_fusion, vector_search_projection
from .yaml_safe import load_yaml_text

CAPABILITY = "C34"
INDEXER_VERSION = "vaultops.embedding.v1"
DEFAULT_MODEL_TAG = "qwen3-embedding:8b-q4_K_M"
DEFAULT_MODEL_DIMENSION = 4096
DEFAULT_BATCH_SIZE = 2
DEFAULT_LIMIT = 10
DEFAULT_BASELINE_PATH = "ops/tests/fixtures/c34_embeddings/evaluation.yaml"
INDEX_ROOT = "index/embeddings"
GENERATION_ROOT = f"{INDEX_ROOT}/generations"
CURRENT_POINTER = f"{INDEX_ROOT}/current.json"
EMBEDDING_INDEX_RECORD_SCHEMA_PATH = "ops/schemas/c34-embedding-index-record.schema.json"
EMBEDDING_INDEX_SCHEMA_PATH = "ops/schemas/c34-embedding-index.schema.json"
EMBEDDING_EVALUATION_SCHEMA_PATH = "ops/schemas/c34-retrieval-evaluation.schema.json"
QUERY_PROMPT_TEMPLATE = (
    "Instruct: Given a KnowledgeOS search query, retrieve note passages that answer or directly support the query\n"
    "Query: {query}"
)
DOCUMENT_PROMPT_TEMPLATE = "title: {title} | text: {text}"
UNKNOWN_QUERY_ABSTENTION_THRESHOLD = 0.40

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_INDEX_ID_RE = re.compile(r"^c34-[0-9a-f]{64}$")
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LINE_BREAKS = ("\n", "\r", "\v", "\f", "\x85", "\u2028", "\u2029")


class EmbeddingIndexError(ValueError):
    """Base error for rejected C34 inputs or artifacts."""


class EmbeddingIndexValidationError(EmbeddingIndexError):
    """Raised when a C34 contract, provider response, or fixture is invalid."""


class EmbeddingIndexConflict(EmbeddingIndexError):
    """Raised when an immutable index is stale, mismatched, or corrupted."""


class EmbeddingProvider(Protocol):
    """Small seam implemented by the bounded C33 Ollama client and test fakes."""

    def embed(
        self,
        model: str,
        inputs: str | Sequence[str],
        *,
        options: Mapping[str, Any] | None = None,
        truncate: bool = False,
        keep_alive: int = 0,
    ) -> Mapping[str, Any]:
        """Return one bounded Ollama-shaped embedding response."""


@dataclass(frozen=True)
class EmbeddingIndex:
    """One fully validated immutable C34 index generation."""

    manifest: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]

    @property
    def index_id(self) -> str:
        return str(self.manifest["index_id"])

    @property
    def model_tag(self) -> str:
        return str(self.manifest["embedding_model_tag"])

    @property
    def model_digest(self) -> str:
        return str(self.manifest["embedding_model_digest"])

    @property
    def dimension(self) -> int:
        return int(self.manifest["embedding_dimension"])


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise EmbeddingIndexValidationError(f"{label} must be lowercase SHA-256")
    return value


def _validate_model_tag(value: object, label: str = "model_tag") -> str:
    if not isinstance(value, str) or not _MODEL_TAG_RE.fullmatch(value):
        raise EmbeddingIndexValidationError(f"{label} must be a bounded explicit model tag")
    lowered = value.casefold()
    if lowered in {"latest", "embeddinggemma"} or lowered.endswith(":latest"):
        raise EmbeddingIndexValidationError(f"{label} must not use an unpinned alias")
    if lowered != DEFAULT_MODEL_TAG.casefold():
        raise EmbeddingIndexValidationError(f"{label} must identify the user-approved Qwen model")
    return value


def _validate_dimension(value: object, label: str = "dimension") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EmbeddingIndexValidationError(f"{label} must be an integer")
    if value != DEFAULT_MODEL_DIMENSION:
        raise EmbeddingIndexValidationError(
            f"{label} must remain the frozen {DEFAULT_MODEL_DIMENSION}-dimension baseline"
        )
    return value


def _validate_generation_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _GENERATION_ID_RE.fullmatch(value):
        raise EmbeddingIndexValidationError(f"{label} is unsafe")
    return value


def embedding_contract() -> dict[str, Any]:
    """Return the accepted C34 Qwen identity and prompt-role contract."""

    return {
        "schema_version": 1,
        "capability": CAPABILITY,
        "enabled_by_default": False,
        "provider": "local:ollama",
        "model_tag": DEFAULT_MODEL_TAG,
        "dimension": DEFAULT_MODEL_DIMENSION,
        "truncate": False,
        "query_prompt_template": QUERY_PROMPT_TEMPLATE,
        "document_prompt_template": DOCUMENT_PROMPT_TEMPLATE,
        "parser_and_chunker_version": PARSER_AND_CHUNKER_VERSION,
        "indexer_version": INDEXER_VERSION,
        "retrieval_baseline": "lexical_and_e01_feature_hash",
        "live_default_selector": True,
        "activation_mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
        "unknown_query_abstention_threshold": UNKNOWN_QUERY_ABSTENTION_THRESHOLD,
        "promotion_gate": "E02_live_model_quality_privacy_citation_staleness_latency_memory",
    }


def embedding_config_sha256() -> str:
    """Return the digest bound to the executable C34 embedding contract."""

    return _sha256_bytes(canonical_json_bytes(embedding_contract()))


EMBEDDING_CONFIG_SHA256 = embedding_config_sha256()


def query_prompt(query: str) -> str:
    """Render the distinct Qwen query role."""

    if not isinstance(query, str) or not query.strip() or "\x00" in query:
        raise EmbeddingIndexValidationError("query must be non-empty text without NUL")
    normalized = _nfc(query.strip())
    if any(marker in normalized for marker in _LINE_BREAKS):
        raise EmbeddingIndexValidationError("query contains an unsupported line break")
    return QUERY_PROMPT_TEMPLATE.format(query=normalized)


def document_prompt(*, title: str, text: str) -> str:
    """Render the distinct Qwen document role."""

    if not isinstance(title, str) or not isinstance(text, str):
        raise EmbeddingIndexValidationError("document title and text must be strings")
    normalized_title = _nfc(title.strip()) or "none"
    normalized_text = _nfc(text.strip())
    if not normalized_text or "\x00" in normalized_title or "\x00" in normalized_text:
        raise EmbeddingIndexValidationError("document title and text must be non-empty and NUL-free")
    return DOCUMENT_PROMPT_TEMPLATE.format(title=normalized_title, text=normalized_text)


def _embedding_identity(
    projection: ProjectionRead,
    *,
    retrieval_config_sha256: str,
    model_tag: str,
    model_digest: str,
    dimension: int,
) -> dict[str, Any]:
    generation_id = _validate_generation_id(projection.manifest.get("generation_id"), "projection generation_id")
    source_snapshot = _validate_digest(
        projection.manifest.get("source_snapshot_sha256"),
        "projection source_snapshot_sha256",
    )
    retrieval_digest = _validate_digest(retrieval_config_sha256, "retrieval_config_sha256")
    selected_model = _validate_model_tag(model_tag)
    selected_digest = _validate_digest(model_digest, "embedding model digest")
    selected_dimension = _validate_dimension(dimension, "embedding dimension")
    return {
        "projection_generation_id": generation_id,
        "projection_source_snapshot_sha256": source_snapshot,
        "parser_and_chunker_version": PARSER_AND_CHUNKER_VERSION,
        "embedding_model_tag": selected_model,
        "embedding_model_digest": selected_digest,
        "embedding_dimension": selected_dimension,
        "query_prompt_template": QUERY_PROMPT_TEMPLATE,
        "document_prompt_template": DOCUMENT_PROMPT_TEMPLATE,
        "query_prompt_template_sha256": _sha256_bytes(QUERY_PROMPT_TEMPLATE.encode("utf-8")),
        "document_prompt_template_sha256": _sha256_bytes(DOCUMENT_PROMPT_TEMPLATE.encode("utf-8")),
        "indexer_version": INDEXER_VERSION,
        "retrieval_config_sha256": retrieval_digest,
        "truncate": False,
    }


def _index_id(identity: Mapping[str, Any]) -> str:
    return f"c34-{_sha256_bytes(canonical_json_bytes(identity))}"


def _embedding_metadata(index: EmbeddingIndex) -> dict[str, Any]:
    return {
        "provider": "local:ollama",
        "model": index.model_tag,
        "dimension": index.dimension,
        "artifact_digest": index.model_digest,
        "index_id": index.index_id,
        "query_prompt_template_sha256": index.manifest["query_prompt_template_sha256"],
        "document_prompt_template_sha256": index.manifest["document_prompt_template_sha256"],
    }


def _zero_lexical() -> dict[str, Any]:
    return {
        "algorithm": "lexical_fts_v1",
        "score": 0.0,
        "rank": None,
        "query_terms": [],
        "matched_terms": [],
        "field_hits": {"title": [], "properties": [], "body": []},
    }


def _apply_unknown_query_abstention(
    candidates: Sequence[Mapping[str, Any]],
    *,
    threshold: float = UNKNOWN_QUERY_ABSTENTION_THRESHOLD,
    lexical_supported: bool = False,
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Abstain when every eligible learned match falls below the frozen threshold."""

    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0.0 <= float(threshold) <= 1.0:
        raise EmbeddingIndexValidationError("unknown-query abstention threshold must be between zero and one")
    scores: list[float] = []
    for candidate in candidates:
        vector = candidate.get("vector_score_and_rank")
        if isinstance(vector, Mapping):
            score = vector.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(float(score)):
                scores.append(float(score))
    top_score = max(scores, default=None)
    abstained = not lexical_supported and top_score is not None and top_score < float(threshold)
    if abstained:
        retained: list[Mapping[str, Any]] = []
        reason = "top_vector_score_below_threshold"
    else:
        retained = list(candidates)
        reason = (
            "lexical_support"
            if lexical_supported
            else "no_eligible_candidates"
            if top_score is None
            else "top_vector_score_meets_threshold"
        )
    return retained, {
        "enabled": True,
        "abstained": abstained,
        "threshold": float(threshold),
        "top_vector_score": top_score,
        "candidate_count_before": len(candidates),
        "candidate_count_after": len(retained),
        "reason": reason,
    }


def _validate_provider_embeddings(
    response: Mapping[str, Any],
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if not isinstance(response, Mapping):
        raise EmbeddingIndexValidationError("embedding provider response must be an object")
    if response.get("truncated") is True:
        raise EmbeddingIndexConflict("embedding provider reported silent input truncation")
    raw_embeddings = response.get("embeddings")
    if not isinstance(raw_embeddings, list) or len(raw_embeddings) != expected_count:
        raise EmbeddingIndexValidationError("embedding response count does not match the request")
    result: list[list[float]] = []
    for vector in raw_embeddings:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise EmbeddingIndexConflict("embedding dimension drift requires an index rebuild")
        normalized: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingIndexValidationError("embedding vector contains a non-numeric value")
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingIndexValidationError("embedding vector contains a non-finite value")
            normalized.append(number)
        result.append(normalized)
    return result


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise EmbeddingIndexValidationError("embedding vectors must have the same non-zero dimension")
    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    left_magnitude = math.sqrt(sum(value * value for value in left_values))
    right_magnitude = math.sqrt(sum(value * value for value in right_values))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0
    value = sum(a * b for a, b in zip(left_values, right_values)) / (left_magnitude * right_magnitude)
    return max(-1.0, min(1.0, value))


def embedding_index_record_schema() -> dict[str, Any]:
    """Return the strict schema for one private vector record."""

    sha = {"type": "string", "pattern": _SHA256_RE.pattern}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c34-embedding-index-record.schema.json",
        "title": "KnowledgeOS C34 immutable embedding index record",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "chunk_id",
            "note_id",
            "path",
            "content_hash",
            "chunk_hash",
            "chunk_locator",
            "prompt_sha256",
            "embedding",
        ],
        "properties": {
            "chunk_id": {"type": "string", "minLength": 1},
            "note_id": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1},
            "content_hash": sha,
            "chunk_hash": sha,
            "chunk_locator": {"type": "string", "minLength": 1},
            "prompt_sha256": sha,
            "embedding": {
                "type": "array",
                "minItems": DEFAULT_MODEL_DIMENSION,
                "maxItems": DEFAULT_MODEL_DIMENSION,
                "items": {"type": "number"},
            },
        },
    }


def embedding_index_manifest_schema() -> dict[str, Any]:
    """Return the strict schema for one immutable C34 index manifest."""

    sha = {"type": "string", "pattern": _SHA256_RE.pattern}
    path = {"type": "string", "minLength": 1, "pattern": r"^[^/].*$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c34-embedding-index.schema.json",
        "title": "KnowledgeOS C34 immutable Qwen index manifest",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "capability",
            "artifact_kind",
            "index_id",
            "generation_root",
            "manifest_path",
            "vectors_path",
            "projection_generation_id",
            "projection_source_snapshot_sha256",
            "parser_and_chunker_version",
            "embedding_model_tag",
            "embedding_model_digest",
            "embedding_dimension",
            "query_prompt_template",
            "document_prompt_template",
            "query_prompt_template_sha256",
            "document_prompt_template_sha256",
            "indexer_version",
            "retrieval_config_sha256",
            "embedding_config_sha256",
            "truncate",
            "vector_record_count",
            "vectors_sha256",
            "identity_sha256",
            "source_verified",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "capability": {"const": CAPABILITY},
            "artifact_kind": {"const": "embedding_index"},
            "index_id": {"type": "string", "pattern": _INDEX_ID_RE.pattern},
            "generation_root": path,
            "manifest_path": path,
            "vectors_path": path,
            "projection_generation_id": {"type": "string", "minLength": 1},
            "projection_source_snapshot_sha256": sha,
            "parser_and_chunker_version": {"type": "string", "minLength": 1},
            "embedding_model_tag": {"const": DEFAULT_MODEL_TAG},
            "embedding_model_digest": sha,
            "embedding_dimension": {"const": DEFAULT_MODEL_DIMENSION},
            "query_prompt_template": {"const": QUERY_PROMPT_TEMPLATE},
            "document_prompt_template": {"const": DOCUMENT_PROMPT_TEMPLATE},
            "query_prompt_template_sha256": sha,
            "document_prompt_template_sha256": sha,
            "indexer_version": {"const": INDEXER_VERSION},
            "retrieval_config_sha256": sha,
            "embedding_config_sha256": sha,
            "truncate": {"const": False},
            "vector_record_count": {"type": "integer", "minimum": 1},
            "vectors_sha256": sha,
            "identity_sha256": sha,
            "source_verified": {"const": True},
        },
    }


def learned_retrieval_evaluation_schema() -> dict[str, Any]:
    """Return the strict C34 frozen learned-retrieval evaluation schema."""

    sha = {"type": "string", "pattern": _SHA256_RE.pattern}
    metric = {"type": "number", "minimum": 0, "maximum": 1}
    ranking = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_paths", "expected_path_recall", "reciprocal_rank"],
        "properties": {
            "candidate_paths": {"type": "array", "items": {"type": "string"}},
            "expected_path_recall": metric,
            "reciprocal_rank": metric,
        },
    }
    case = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "mode", "lexical", "e01_feature", "learned", "rrf", "passed"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "mode": {"enum": ["search", "retrieve"]},
            "lexical": ranking,
            "e01_feature": ranking,
            "learned": ranking,
            "rrf": ranking,
            "passed": {"type": "boolean"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c34-retrieval-evaluation.schema.json",
        "title": "KnowledgeOS C34 learned retrieval quality evaluation",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "operation",
            "capability",
            "baseline_sha256",
            "index_generation_id",
            "embedding_index_id",
            "retrieval_config_sha256",
            "embedding_config_sha256",
            "cases",
            "metrics",
            "learned_retrieval_opt_in",
            "live_model_evidence",
            "source_verified",
            "provider_called",
            "mutation_performed",
            "runtime_artifact_written",
        ],
        "properties": {
            "status": {"const": "PASS"},
            "operation": {"const": "vector learned-evaluate"},
            "capability": {"const": CAPABILITY},
            "baseline_sha256": sha,
            "index_generation_id": {"type": "string", "minLength": 1},
            "embedding_index_id": {"type": "string", "pattern": _INDEX_ID_RE.pattern},
            "retrieval_config_sha256": sha,
            "embedding_config_sha256": sha,
            "cases": {"type": "array", "minItems": 1, "items": case},
            "metrics": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "case_count",
                    "passed_case_count",
                    "all_cases_passed",
                    "lexical_mean_expected_path_recall",
                    "e01_feature_mean_expected_path_recall",
                    "learned_mean_expected_path_recall",
                    "rrf_mean_expected_path_recall",
                    "lexical_mean_reciprocal_rank",
                    "e01_feature_mean_reciprocal_rank",
                    "learned_mean_reciprocal_rank",
                    "rrf_mean_reciprocal_rank",
                    "learned_improves_baseline",
                    "rrf_improves_baseline",
                    "require_improvement",
                    "quality_gate_passed",
                    "promotion_gate_passed",
                    "promotion_decision",
                ],
                "properties": {
                    "case_count": {"type": "integer", "minimum": 1},
                    "passed_case_count": {"type": "integer", "minimum": 0},
                    "all_cases_passed": {"type": "boolean"},
                    "lexical_mean_expected_path_recall": metric,
                    "e01_feature_mean_expected_path_recall": metric,
                    "learned_mean_expected_path_recall": metric,
                    "rrf_mean_expected_path_recall": metric,
                    "lexical_mean_reciprocal_rank": metric,
                    "e01_feature_mean_reciprocal_rank": metric,
                    "learned_mean_reciprocal_rank": metric,
                    "rrf_mean_reciprocal_rank": metric,
                    "learned_improves_baseline": {"type": "boolean"},
                    "rrf_improves_baseline": {"type": "boolean"},
                    "require_improvement": {"type": "boolean"},
                    "quality_gate_passed": {"type": "boolean"},
                    "promotion_gate_passed": {"type": "boolean"},
                    "promotion_decision": {"enum": ["promote_optional_learned", "retain_lexical_baseline"]},
                },
            },
            "learned_retrieval_opt_in": {"const": True},
            "live_model_evidence": {"const": False},
            "source_verified": {"const": True},
            "provider_called": {"const": True},
            "mutation_performed": {"const": False},
            "runtime_artifact_written": {"const": True},
        },
    }


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise EmbeddingIndexValidationError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise EmbeddingIndexValidationError("KnowledgeHub must be an existing non-symlink directory")
    return workspace


def _runtime_path(workspace: Path, relative: str) -> Path:
    if not isinstance(relative, str) or relative.startswith("/") or "\\" in relative:
        raise EmbeddingIndexConflict("runtime path is not relative POSIX text")
    candidate = PurePosixPath(relative)
    if not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise EmbeddingIndexConflict("runtime path contains an unsafe component")
    runtime = workspace / "runtime"
    if runtime.is_symlink():
        raise EmbeddingIndexConflict("runtime root is a symlink")
    current = runtime
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise EmbeddingIndexConflict(f"runtime path traverses a symlink: {relative}")
    return current


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise EmbeddingIndexConflict(f"runtime directory is a symlink: {path}")
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise EmbeddingIndexConflict(f"cannot resolve runtime directory parent: {path}")
        current = parent
        if current.is_symlink():
            raise EmbeddingIndexConflict(f"runtime directory parent is a symlink: {current}")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise EmbeddingIndexConflict(f"runtime directory was not created mode 0700: {directory}")
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise EmbeddingIndexConflict(f"runtime directory must be mode 0700: {path}")


def _regular_private_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise EmbeddingIndexConflict(f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise EmbeddingIndexConflict(f"{label} must be mode 0600")
    try:
        return path.read_bytes()
    except OSError as error:
        raise EmbeddingIndexConflict(f"cannot read {label}: {error}") from error


def _write_immutable(path: Path, payload: bytes, label: str) -> bool:
    """Create one private file or verify an identical replay."""

    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise EmbeddingIndexConflict(f"{label} is not a regular file")
    if path.is_file():
        observed = _regular_private_file(path, label)
        if observed != payload:
            raise EmbeddingIndexConflict(f"{label} conflicts with immutable bytes")
        return False
    _ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
        return True
    except FileExistsError:
        observed = _regular_private_file(path, label)
        if observed != payload:
            raise EmbeddingIndexConflict(f"{label} conflicts with immutable bytes")
        return False
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        if path.exists() or path.is_symlink():
            path.unlink()
        raise


def _write_pointer(path: Path, payload: bytes) -> bool:
    """Atomically select one immutable index generation."""

    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise EmbeddingIndexConflict("embedding current pointer is not a regular file")
    if path.is_file():
        before = _regular_private_file(path, "embedding current pointer")
        if before == payload:
            return False
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
        return True
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EmbeddingIndexConflict(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeError, json.JSONDecodeError, EmbeddingIndexConflict) as error:
        raise EmbeddingIndexConflict(f"{label} is not valid strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise EmbeddingIndexConflict(f"{label} must be a JSON object")
    if canonical_json_bytes(value) != raw:
        raise EmbeddingIndexConflict(f"{label} is not serialized canonically")
    return value


def _jsonl_records(raw: bytes, label: str) -> list[dict[str, Any]]:
    if not raw or b"\r" in raw or not raw.endswith(b"\n"):
        raise EmbeddingIndexConflict(f"{label} must be UTF-8 LF JSONL with terminal newlines")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        records.append(_json_object(line + b"\n", f"{label} line {line_number}"))
    return records


def _record_sort_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the canonical persisted order for one embedding record."""

    locator = str(record["chunk_locator"])
    return (
        _nfc(str(record["path"])).encode("utf-8"),
        str(record["note_id"]),
        locator != "/frontmatter",
        _nfc(locator).encode("utf-8"),
        str(record["chunk_id"]),
    )


def _validate_schema(value: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: error.json_path)
    if errors:
        raise EmbeddingIndexValidationError(f"{label} violates schema: {errors[0].message}")


def _build_records(
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    *,
    model_tag: str,
    provider: EmbeddingProvider,
    dimension: int,
) -> tuple[list[dict[str, Any]], bool]:
    included, _excluded, _types, _review, _limit, _hops = _filter_notes(
        projection,
        retrieval,
        scope=None,
        path_prefix=None,
        include_types=None,
        include_review=False,
        limit=50,
        hops=0,
    )
    chunks = [chunk for note in included for chunk in _chunks_for_note(note)]
    chunks.sort(key=lambda chunk: (_nfc(str(chunk.note["path"])).encode("utf-8"), str(chunk.note["id"]), _chunk_sort_key(chunk)))
    if not chunks:
        raise EmbeddingIndexValidationError("privacy-filtered projection contains no embeddable chunks")
    embeddings: list[list[float]] = []
    provider_called = False
    for start in range(0, len(chunks), DEFAULT_BATCH_SIZE):
        batch = chunks[start : start + DEFAULT_BATCH_SIZE]
        prompts = [document_prompt(title=str(chunk.note.get("title", "")), text=chunk.text) for chunk in batch]
        provider_called = True
        response = provider.embed(model_tag, prompts, truncate=False, keep_alive=0)
        embeddings.extend(
            _validate_provider_embeddings(response, expected_count=len(batch), dimension=dimension)
        )
    records: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, embeddings):
        prompt = document_prompt(title=str(chunk.note.get("title", "")), text=chunk.text)
        record = {
            "chunk_id": str(chunk.chunk_id),
            "note_id": str(chunk.note["id"]),
            "path": str(chunk.note["path"]),
            "content_hash": str(chunk.note["content_hash"]),
            "chunk_hash": str(chunk.chunk_hash),
            "chunk_locator": str(chunk.locator),
            "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
            "embedding": vector,
        }
        _validate_schema(record, embedding_index_record_schema(), "embedding index record")
        records.append(record)
    records.sort(key=_record_sort_key)
    return records, provider_called


def _publish_index(
    workspace: Path,
    *,
    index_id: str,
    manifest_bytes: bytes,
    vectors_bytes: bytes,
) -> tuple[bool, bool]:
    generation_root = _runtime_path(workspace, f"{GENERATION_ROOT}/{index_id}")
    manifest_path = generation_root / "manifest.json"
    vectors_path = generation_root / "vectors.jsonl"
    _ensure_private_directory(generation_root)
    vectors_written = _write_immutable(vectors_path, vectors_bytes, "embedding vectors")
    manifest_written = _write_immutable(manifest_path, manifest_bytes, "embedding manifest")
    pointer = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "index_id": index_id,
        "generation_root": f"{GENERATION_ROOT}/{index_id}",
        "manifest_path": f"{GENERATION_ROOT}/{index_id}/manifest.json",
        "vectors_path": f"{GENERATION_ROOT}/{index_id}/vectors.jsonl",
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "vectors_sha256": _sha256_bytes(vectors_bytes),
    }
    pointer_bytes = canonical_json_bytes(pointer)
    pointer_changed = _write_pointer(_runtime_path(workspace, CURRENT_POINTER), pointer_bytes)
    return vectors_written or manifest_written, pointer_changed


def build_embedding_index_projection(
    workspace: str | Path,
    projection: ProjectionRead,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    provider: EmbeddingProvider,
    model_tag: str = DEFAULT_MODEL_TAG,
    model_digest: str,
    dimension: int = DEFAULT_MODEL_DIMENSION,
) -> tuple[dict[str, Any], int]:
    """Build and publish one immutable C34 index for a pinned projection."""

    workspace_path = _workspace(workspace)
    _validate_contract(retrieval)
    identity = _embedding_identity(
        projection,
        retrieval_config_sha256=retrieval_config_sha256,
        model_tag=model_tag,
        model_digest=model_digest,
        dimension=dimension,
    )
    records, provider_called = _build_records(
        projection,
        retrieval,
        model_tag=identity["embedding_model_tag"],
        provider=provider,
        dimension=identity["embedding_dimension"],
    )
    vectors_bytes = b"".join(canonical_json_bytes(record) for record in records)
    index_id = _index_id(identity)
    generation_root = f"{GENERATION_ROOT}/{index_id}"
    manifest = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "artifact_kind": "embedding_index",
        "index_id": index_id,
        "generation_root": generation_root,
        "manifest_path": f"{generation_root}/manifest.json",
        "vectors_path": f"{generation_root}/vectors.jsonl",
        **identity,
        "embedding_config_sha256": EMBEDDING_CONFIG_SHA256,
        "vector_record_count": len(records),
        "vectors_sha256": _sha256_bytes(vectors_bytes),
        "identity_sha256": _sha256_bytes(canonical_json_bytes(identity)),
        "source_verified": True,
    }
    _validate_schema(manifest, embedding_index_manifest_schema(), "embedding index manifest")
    manifest_bytes = canonical_json_bytes(manifest)
    runtime_written, pointer_changed = _publish_index(
        workspace_path,
        index_id=index_id,
        manifest_bytes=manifest_bytes,
        vectors_bytes=vectors_bytes,
    )
    report = {
        "status": "PASS",
        "operation": "vector learned-build",
        "capability": CAPABILITY,
        "index_generation_id": identity["projection_generation_id"],
        "embedding_index_id": index_id,
        "embedding_config_sha256": EMBEDDING_CONFIG_SHA256,
        "embedding_model_tag": identity["embedding_model_tag"],
        "embedding_model_digest": identity["embedding_model_digest"],
        "embedding_dimension": identity["embedding_dimension"],
        "vector_record_count": len(records),
        "runtime_artifact_written": runtime_written,
        "pointer_changed": pointer_changed,
        "source_verified": True,
        "provider_called": provider_called,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "learned_retrieval_opt_in": True,
    }
    return report, EXIT_OK


def _load_index_from_pointer(workspace: Path) -> EmbeddingIndex:
    pointer_path = _runtime_path(workspace, CURRENT_POINTER)
    pointer = _json_object(_regular_private_file(pointer_path, "embedding current pointer"), "embedding current pointer")
    expected_pointer_keys = {
        "schema_version",
        "capability",
        "index_id",
        "generation_root",
        "manifest_path",
        "vectors_path",
        "manifest_sha256",
        "vectors_sha256",
    }
    if set(pointer) != expected_pointer_keys or pointer.get("schema_version") != 1 or pointer.get("capability") != CAPABILITY:
        raise EmbeddingIndexValidationError("embedding current pointer identity is invalid")
    index_id = pointer.get("index_id")
    if not isinstance(index_id, str) or not _INDEX_ID_RE.fullmatch(index_id):
        raise EmbeddingIndexValidationError("embedding current pointer index_id is invalid")
    expected_root = f"{GENERATION_ROOT}/{index_id}"
    for field, expected in {
        "generation_root": expected_root,
        "manifest_path": f"{expected_root}/manifest.json",
        "vectors_path": f"{expected_root}/vectors.jsonl",
    }.items():
        if pointer.get(field) != expected:
            raise EmbeddingIndexConflict(f"embedding current pointer {field} does not match index_id")
    manifest_path = _runtime_path(workspace, str(pointer["manifest_path"]))
    vectors_path = _runtime_path(workspace, str(pointer["vectors_path"]))
    manifest_bytes = _regular_private_file(manifest_path, "embedding manifest")
    vectors_bytes = _regular_private_file(vectors_path, "embedding vectors")
    if pointer["manifest_sha256"] != _sha256_bytes(manifest_bytes):
        raise EmbeddingIndexConflict("embedding pointer manifest digest does not match")
    if pointer["vectors_sha256"] != _sha256_bytes(vectors_bytes):
        raise EmbeddingIndexConflict("embedding pointer vectors digest does not match")
    manifest = _json_object(manifest_bytes, "embedding manifest")
    _validate_schema(manifest, embedding_index_manifest_schema(), "embedding manifest")
    if manifest.get("index_id") != index_id or manifest.get("generation_root") != expected_root:
        raise EmbeddingIndexConflict("embedding manifest selects a different immutable generation")
    if manifest.get("vectors_sha256") != _sha256_bytes(vectors_bytes):
        raise EmbeddingIndexConflict("embedding manifest vectors digest does not match")
    identity = {
        key: manifest[key]
        for key in (
            "projection_generation_id",
            "projection_source_snapshot_sha256",
            "parser_and_chunker_version",
            "embedding_model_tag",
            "embedding_model_digest",
            "embedding_dimension",
            "query_prompt_template",
            "document_prompt_template",
            "query_prompt_template_sha256",
            "document_prompt_template_sha256",
            "indexer_version",
            "retrieval_config_sha256",
            "truncate",
        )
    }
    if manifest.get("identity_sha256") != _sha256_bytes(canonical_json_bytes(identity)):
        raise EmbeddingIndexConflict("embedding identity digest does not match the manifest")
    records = _jsonl_records(vectors_bytes, "embedding vectors")
    if len(records) != manifest["vector_record_count"]:
        raise EmbeddingIndexConflict("embedding vector record count does not match the manifest")
    for record in records:
        _validate_schema(record, embedding_index_record_schema(), "embedding vector record")
        if len(record["embedding"]) != manifest["embedding_dimension"]:
            raise EmbeddingIndexConflict("embedding record dimension differs from the manifest")
    expected_order = sorted(records, key=_record_sort_key)
    if records != expected_order:
        raise EmbeddingIndexValidationError("embedding vector records are not in canonical order")
    identities = {(record["note_id"], record["chunk_id"]) for record in records}
    if len(identities) != len(records):
        raise EmbeddingIndexValidationError("embedding vectors contain duplicate chunk identity")
    return EmbeddingIndex(manifest=manifest, records=tuple(records))


def load_embedding_index(
    root: str | Path,
    *,
    projection: ProjectionRead | None = None,
    retrieval_config_sha256: str | None = None,
    model_tag: str | None = None,
    model_digest: str | None = None,
    dimension: int | None = None,
) -> EmbeddingIndex:
    """Load the current index and fail closed on any requested identity drift."""

    workspace = _workspace(root)
    index = _load_index_from_pointer(workspace)
    manifest = index.manifest
    if projection is not None:
        if manifest["projection_generation_id"] != projection.manifest.get("generation_id"):
            raise EmbeddingIndexConflict("embedding index projection generation drift requires a rebuild")
        if manifest["projection_source_snapshot_sha256"] != projection.manifest.get("source_snapshot_sha256"):
            raise EmbeddingIndexConflict("embedding index source snapshot drift requires a rebuild")
    checks = (
        (retrieval_config_sha256, "retrieval_config_sha256", manifest["retrieval_config_sha256"]),
        (model_tag, "model_tag", manifest["embedding_model_tag"]),
        (model_digest, "model_digest", manifest["embedding_model_digest"]),
        (dimension, "dimension", manifest["embedding_dimension"]),
    )
    for observed, label, expected in checks:
        if observed is None:
            continue
        if label == "model_tag":
            observed_value = _validate_model_tag(observed, label)
        elif label == "dimension":
            observed_value = _validate_dimension(observed, label)
        else:
            observed_value = _validate_digest(observed, label)
        if observed_value != expected:
            raise EmbeddingIndexConflict(f"embedding {label} drift requires a rebuild")
    if manifest["embedding_config_sha256"] != EMBEDDING_CONFIG_SHA256:
        raise EmbeddingIndexConflict("embedding executable contract drift requires a rebuild")
    return index


def _retrieval_context(
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
) -> tuple[str, str, tuple[str, ...], Any, tuple[Mapping[str, Any], ...], list[dict[str, str]], int, int, str]:
    normalized_query, query_sha256, terms = _canonical_query(query)
    options = _normalized_options(
        retrieval,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
    )
    included, excluded, selected_types, normalized_review, normalized_limit, normalized_hops = _filter_notes(
        projection,
        retrieval,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        _options=options,
    )
    policy_hash = _policy_decision_hash(
        retrieval,
        projection,
        excluded,
        included,
        scope=options.scope,
        path_prefix=options.path_prefix,
        include_types=selected_types,
        include_review=normalized_review,
        limit=normalized_limit,
        hops=normalized_hops,
        retrieval_config_sha256=retrieval_config_sha256,
    )
    return (
        normalized_query,
        query_sha256,
        terms,
        options,
        included,
        excluded,
        normalized_limit,
        normalized_hops,
        policy_hash,
    )


def _validate_index_projection(index: EmbeddingIndex, projection: ProjectionRead, retrieval_config_sha256: str) -> None:
    if index.manifest["projection_generation_id"] != projection.manifest.get("generation_id"):
        raise EmbeddingIndexConflict("embedding index projection generation drift requires a rebuild")
    if index.manifest["projection_source_snapshot_sha256"] != projection.manifest.get("source_snapshot_sha256"):
        raise EmbeddingIndexConflict("embedding index source snapshot drift requires a rebuild")
    if index.manifest["retrieval_config_sha256"] != retrieval_config_sha256:
        raise EmbeddingIndexConflict("embedding index retrieval policy drift requires a rebuild")


def _projection_chunks(projection: ProjectionRead) -> dict[str, Any]:
    chunks: dict[str, Any] = {}
    for note in projection.notes:
        for chunk in _chunks_for_note(note):
            if chunk.chunk_id in chunks:
                raise EmbeddingIndexValidationError("projection contains duplicate chunk identity")
            chunks[chunk.chunk_id] = chunk
    return chunks


def _validate_records_against_projection(index: EmbeddingIndex, projection: ProjectionRead) -> dict[str, Any]:
    chunks = _projection_chunks(projection)
    for record in index.records:
        chunk = chunks.get(str(record["chunk_id"]))
        if chunk is None:
            raise EmbeddingIndexConflict("embedding index chunk is absent from the pinned projection")
        if (
            record["note_id"] != str(chunk.note["id"])
            or record["path"] != str(chunk.note["path"])
            or record["content_hash"] != str(chunk.note["content_hash"])
            or record["chunk_hash"] != str(chunk.chunk_hash)
            or record["chunk_locator"] != str(chunk.locator)
        ):
            raise EmbeddingIndexConflict("embedding index chunk provenance drift requires a rebuild")
    return chunks


def _learned_candidates(
    projection: ProjectionRead,
    query: str,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    index: EmbeddingIndex,
    provider: EmbeddingProvider,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> tuple[dict[str, Any], bool]:
    _validate_index_projection(index, projection, retrieval_config_sha256)
    normalized_query, query_sha256, _terms, options, included, excluded, normalized_limit, _normalized_hops, policy_hash = _retrieval_context(
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
    )
    chunks = _validate_records_against_projection(index, projection)
    query_input = query_prompt(normalized_query)
    response = provider.embed(index.model_tag, [query_input], truncate=False, keep_alive=0)
    query_embedding = _validate_provider_embeddings(
        response,
        expected_count=1,
        dimension=index.dimension,
    )[0]
    eligible_ids = {str(note["id"]) for note in included}
    scored: list[tuple[float, Any, Mapping[str, Any]]] = []
    for record in index.records:
        if str(record["note_id"]) not in eligible_ids:
            continue
        chunk = chunks[str(record["chunk_id"])]
        scored.append((_cosine_similarity(query_embedding, record["embedding"]), chunk, record))
    scored.sort(
        key=lambda item: (
            -item[0],
            _nfc(str(item[1].note["path"])).encode("utf-8"),
            str(item[1].note["id"]),
            _chunk_sort_key(item[1]),
        )
    )
    selected: list[tuple[float, Any, Mapping[str, Any]]] = []
    seen_note_ids: set[str] = set()
    for item in scored:
        note_id = str(item[1].note["id"])
        if note_id in seen_note_ids:
            continue
        seen_note_ids.add(note_id)
        selected.append(item)
        if len(selected) >= max(normalized_limit, 50 if fuse else normalized_limit):
            break
    metadata = _embedding_metadata(index)
    learned: list[dict[str, Any]] = []
    for rank, (score, chunk, _record) in enumerate(selected, start=1):
        candidate = _candidate(
            chunk,
            query_sha256=query_sha256,
            policy_decision_sha256=policy_hash,
            generation_id=str(projection.manifest["generation_id"]),
            retrieval_config_sha256=retrieval_config_sha256,
            retrieval_reason="learned_vector_match",
            lexical=_zero_lexical(),
            graph_path=[],
        )
        candidate["vector_score_and_rank"] = {
            "algorithm": "cosine_qwen3_embedding_v1",
            "score": round(float(score), 12),
            "rank": rank,
            "dimension": index.dimension,
            "similarity": "cosine",
            "index_id": index.index_id,
            "provenance": _candidate_provenance(candidate),
        }
        candidate["embedding_provider_model_dimension_and_artifact_digest"] = metadata
        candidate["indexer_version"] = index.manifest["indexer_version"]
        errors = sorted(
            Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate),
            key=lambda error: error.json_path,
        )
        if errors:
            raise EmbeddingIndexValidationError(f"learned candidate is invalid: {errors[0].message}")
        learned.append(candidate)
    learned = _revalidate_candidates(projection, retrieval, learned, options=options)
    lexical_probe = _lexical_candidates(
        included,
        normalized_query,
        query_sha256,
        _terms,
        str(projection.manifest["generation_id"]),
        policy_hash,
        retrieval_config_sha256,
    )
    lexical_probe = _revalidate_candidates(projection, retrieval, lexical_probe, options=options)
    learned, abstention = _apply_unknown_query_abstention(learned, lexical_supported=bool(lexical_probe))
    return {
        "query_sha256": query_sha256,
        "policy_decision_sha256": policy_hash,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "embedding_config_sha256": EMBEDDING_CONFIG_SHA256,
        "embedding_index_id": index.index_id,
        "embedding_enabled": True,
        "retrieval_mode": "learned_rrf" if fuse else "learned_vector",
        "candidates": learned,
        "abstention": abstention,
        "filters": {
            "scope": options.scope,
            "path_prefix": options.path_prefix,
            "include_types": list(options.include_types),
            "include_review": options.include_review,
            "included_note_count": len(included),
            "excluded_note_count": len(excluded),
            "excluded": excluded,
        },
        "provider_called": True,
        "mutation_performed": False,
    }, True


def _fusion_identity_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
    for field in (
        "note_id",
        "path",
        "content_hash",
        "index_generation_id",
        "query_sha256",
        "policy_decision_sha256",
        "retrieval_config_sha256",
    ):
        if left.get(field) != right.get(field):
            raise EmbeddingIndexConflict(f"C34 RRF {field} provenance does not match across channels")


def _copy_channel(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(value)
    if isinstance(copied.get("provenance"), Mapping):
        copied["provenance"] = dict(copied["provenance"])
    return copied


def _learned_rrf(
    lexical: Sequence[Mapping[str, Any]],
    learned: Sequence[Mapping[str, Any]],
    *,
    index: EmbeddingIndex,
    limit: int,
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    options: Any,
) -> list[dict[str, Any]]:
    fused = reciprocal_rank_fusion(
        {"lexical": lexical, "learned": learned},
        k=60,
        weights={"lexical": 1.0, "learned": 1.0},
    )
    lexical_by_id = {str(item["note_id"]): item for item in lexical}
    learned_by_id = {str(item["note_id"]): item for item in learned}
    records: list[dict[str, Any]] = []
    for note_id, fusion in sorted(fused.items(), key=lambda item: int(item[1]["rank"])):
        lexical_record = lexical_by_id.get(note_id)
        learned_record = learned_by_id.get(note_id)
        if lexical_record is not None and learned_record is not None:
            _fusion_identity_conflict(lexical_record, learned_record)
        source = lexical_record or learned_record
        if source is None:
            raise EmbeddingIndexConflict("C34 RRF selected a note with no channel candidate")
        candidate = dict(source)
        candidate["lexical_score_and_rank"] = _copy_channel(
            lexical_record["lexical_score_and_rank"] if lexical_record is not None else _zero_lexical()
        )
        candidate["vector_score_and_rank"] = (
            _copy_channel(learned_record["vector_score_and_rank"]) if learned_record is not None else None
        )
        candidate["rrf_parameter_and_rank"] = {
            "algorithm": "rrf_v1",
            "k": 60,
            "score": fusion["score"],
            "rank": fusion["rank"],
            "weights": {"lexical": 1.0, "learned": 1.0},
            "components": fusion["components"],
        }
        reasons = []
        if lexical_record is not None:
            reasons.append("lexical_match")
        if learned_record is not None:
            reasons.append("learned_vector_match")
        reasons.append("rrf")
        candidate["retrieval_reason"] = ";".join(reasons)
        candidate["embedding_provider_model_dimension_and_artifact_digest"] = (
            _embedding_metadata(index) if learned_record is not None else None
        )
        candidate["indexer_version"] = index.manifest["indexer_version"]
        records.append(candidate)
    return _revalidate_candidates(projection, retrieval, records[:limit], options=options)


def _learned_projection(
    projection: ProjectionRead,
    query: str,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    index: EmbeddingIndex,
    provider: EmbeddingProvider,
    model_tag: str,
    model_digest: str,
    dimension: int,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> dict[str, Any]:
    _validate_model_tag(model_tag)
    _validate_digest(model_digest, "model_digest")
    _validate_dimension(dimension)
    if index.model_tag != model_tag or index.model_digest != model_digest or index.dimension != dimension:
        raise EmbeddingIndexConflict("embedding query identity differs from the immutable index")
    learned_result, _provider_called = _learned_candidates(
        projection,
        query,
        retrieval=retrieval,
        retrieval_config_sha256=retrieval_config_sha256,
        index=index,
        provider=provider,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        fuse=fuse,
    )
    if not fuse or learned_result["abstention"]["abstained"]:
        return learned_result
    normalized_query, query_sha256, terms, options, included, excluded, normalized_limit, normalized_hops, policy_hash = _retrieval_context(
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
    )
    del included, excluded, normalized_hops, policy_hash
    normalized_query, query_sha256, terms = _canonical_query(query)
    filtered_notes, _excluded, _types, _review, _limit, _hops = _filter_notes(
        projection,
        retrieval,
        scope=options.scope,
        path_prefix=options.path_prefix,
        include_types=options.include_types,
        include_review=options.include_review,
        limit=options.limit,
        hops=options.hops,
        _options=options,
    )
    lexical = _lexical_candidates(
        filtered_notes,
        normalized_query,
        query_sha256,
        terms,
        str(projection.manifest["generation_id"]),
        learned_result["policy_decision_sha256"],
        retrieval_config_sha256,
    )
    lexical = _revalidate_candidates(projection, retrieval, lexical, options=options)
    candidates = _learned_rrf(
        lexical,
        learned_result["candidates"],
        index=index,
        limit=normalized_limit,
        projection=projection,
        retrieval=retrieval,
        options=options,
    )
    return {**learned_result, "candidates": candidates}


def _load_contract(workspace: Path) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    from .retrieval import _load_contract as load_retrieval_contract

    policy, retrieval, digest = load_retrieval_contract(workspace)
    _validate_contract(retrieval)
    return policy, retrieval, digest


def _failure(operation: str, code: str, message: str, *, provider_called: bool = False) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": CAPABILITY,
        "learned_retrieval_opt_in": True,
        "provider_called": provider_called,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, EXIT_CONFLICT


def build_embedding_index(
    root: str | Path,
    provider: EmbeddingProvider,
    *,
    model_tag: str = DEFAULT_MODEL_TAG,
    model_digest: str,
    dimension: int = DEFAULT_MODEL_DIMENSION,
) -> tuple[dict[str, Any], int]:
    """Build one explicit local learned index from the current projection."""

    operation = "vector learned-build"
    try:
        workspace = _workspace(root)
        projection = load_current_projection(workspace, verify_sources=True)
        _policy, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise EmbeddingIndexConflict("pinned projection retrieval policy digest is stale")
        return build_embedding_index_projection(
            workspace,
            projection,
            retrieval=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            provider=provider,
            model_tag=model_tag,
            model_digest=model_digest,
            dimension=dimension,
        )
    except ProjectionError as error:
        return _failure(operation, "C34_INDEX_STALE", str(error))
    except EmbeddingIndexValidationError as error:
        return _failure(operation, "C34_INPUT_INVALID", str(error))
    except EmbeddingIndexConflict as error:
        return _failure(operation, "C34_INDEX_CONFLICT", str(error), provider_called=True)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "C34_BUILD_INVALID", str(error), provider_called=True)


def _run_learned(
    operation: str,
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_tag: str,
    model_digest: str,
    dimension: int,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> tuple[dict[str, Any], int]:
    try:
        workspace = _workspace(root)
        projection = load_current_projection(workspace, verify_sources=True)
        _policy, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise EmbeddingIndexConflict("pinned projection retrieval policy digest is stale")
        index = load_embedding_index(
            workspace,
            projection=projection,
            retrieval_config_sha256=retrieval_config_sha256,
            model_tag=model_tag,
            model_digest=model_digest,
            dimension=dimension,
        )
        result = _learned_projection(
            projection,
            query,
            retrieval=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            index=index,
            provider=provider,
            model_tag=model_tag,
            model_digest=model_digest,
            dimension=dimension,
            scope=scope,
            path_prefix=path_prefix,
            include_types=include_types,
            include_review=include_review,
            limit=limit,
            hops=hops,
            fuse=fuse,
        )
        report = {
            **result,
            "status": "PASS",
            "operation": operation,
            "capability": CAPABILITY,
            "candidate_count": len(result["candidates"]),
            "source_verified": True,
            "provider_called": True,
            "mutation_performed": False,
            "learned_retrieval_opt_in": True,
        }
        return report, EXIT_OK
    except ProjectionError as error:
        return _failure(operation, "C34_INDEX_STALE", str(error))
    except EmbeddingIndexConflict as error:
        return _failure(operation, "C34_INDEX_CONFLICT", str(error), provider_called=False)
    except EmbeddingIndexValidationError as error:
        return _failure(operation, "C34_QUERY_INVALID", str(error), provider_called=False)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "C34_QUERY_INVALID", str(error), provider_called=True)


def learned_search(
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_tag: str = DEFAULT_MODEL_TAG,
    model_digest: str,
    dimension: int = DEFAULT_MODEL_DIMENSION,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 0,
) -> tuple[dict[str, Any], int]:
    """Run explicit learned vector-only retrieval against one immutable index."""

    return _run_learned(
        "vector learned-search",
        root,
        query,
        provider,
        model_tag=model_tag,
        model_digest=model_digest,
        dimension=dimension,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        fuse=False,
    )


def learned_retrieve(
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_tag: str = DEFAULT_MODEL_TAG,
    model_digest: str,
    dimension: int = DEFAULT_MODEL_DIMENSION,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
) -> tuple[dict[str, Any], int]:
    """Run explicit lexical plus learned-vector RRF retrieval."""

    return _run_learned(
        "vector learned-retrieve",
        root,
        query,
        provider,
        model_tag=model_tag,
        model_digest=model_digest,
        dimension=dimension,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        fuse=True,
    )


def _safe_control_file(workspace: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(workspace)
        except ValueError as error:
            raise EmbeddingIndexValidationError("C34 baseline must be inside the control root") from error
    else:
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise EmbeddingIndexValidationError("C34 baseline path contains unsafe components")
        relative = candidate
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise EmbeddingIndexValidationError("C34 baseline path must not traverse a symlink")
    resolved = (workspace / relative).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise EmbeddingIndexValidationError("C34 baseline path escapes the control root") from error
    if not resolved.is_file() or resolved.is_symlink():
        raise EmbeddingIndexValidationError("C34 baseline must be a regular file")
    return resolved


def _load_baseline(workspace: Path, path: str | Path | None) -> tuple[Mapping[str, Any], str]:
    baseline_path = _safe_control_file(workspace, path or DEFAULT_BASELINE_PATH)
    raw = baseline_path.read_bytes()
    try:
        value = load_yaml_text(raw.decode("utf-8"))
    except (UnicodeError, ValueError, YAMLError) as error:
        raise EmbeddingIndexValidationError(f"C34 baseline YAML is invalid: {error}") from error
    baseline = _as_mapping(value, "C34 baseline")
    if set(baseline) != {"schema_version", "capability", "profile", "embedding", "queries"}:
        raise EmbeddingIndexValidationError("C34 baseline keys drift")
    if baseline.get("schema_version") != 1 or baseline.get("capability") != CAPABILITY or baseline.get("profile") != "portable_core":
        raise EmbeddingIndexValidationError("C34 baseline identity is invalid")
    embedding = _as_mapping(baseline["embedding"], "/embedding")
    if set(embedding) != {"model_tag", "model_digest", "dimension", "require_improvement"}:
        raise EmbeddingIndexValidationError("C34 embedding baseline keys drift")
    _validate_model_tag(embedding["model_tag"], "/embedding/model_tag")
    _validate_digest(embedding["model_digest"], "/embedding/model_digest")
    _validate_dimension(embedding["dimension"], "/embedding/dimension")
    if not isinstance(embedding["require_improvement"], bool):
        raise EmbeddingIndexValidationError("C34 require_improvement must be boolean")
    queries = baseline["queries"]
    if not isinstance(queries, list) or not queries:
        raise EmbeddingIndexValidationError("C34 baseline must contain at least one query")
    required_query_keys = {
        "id",
        "query",
        "mode",
        "scope",
        "path_prefix",
        "include_types",
        "include_review",
        "limit",
        "hops",
        "expected_prefix_paths",
        "required_paths",
    }
    for query_case in queries:
        case = _as_mapping(query_case, "C34 baseline query")
        if set(case) != required_query_keys:
            raise EmbeddingIndexValidationError("C34 baseline query keys drift")
        if not isinstance(case["id"], str) or not case["id"] or case["mode"] not in {"search", "retrieve"}:
            raise EmbeddingIndexValidationError("C34 baseline query identity is invalid")
        if not isinstance(case["query"], str) or not case["query"].strip():
            raise EmbeddingIndexValidationError("C34 baseline query text is invalid")
        if not isinstance(case["expected_prefix_paths"], list) or not isinstance(case["required_paths"], list):
            raise EmbeddingIndexValidationError("C34 baseline expected paths are invalid")
    return baseline, _sha256_bytes(raw)


def _ranking_metrics(candidates: Sequence[Mapping[str, Any]], expected_paths: Sequence[Any]) -> dict[str, Any]:
    paths = [str(candidate["path"]) for candidate in candidates]
    expected = [str(path) for path in expected_paths]
    unique_expected = list(dict.fromkeys(expected))
    found = sum(path in paths for path in unique_expected)
    first = None
    if unique_expected:
        try:
            first = paths.index(unique_expected[0]) + 1
        except ValueError:
            first = None
    return {
        "candidate_paths": paths,
        "expected_path_recall": round(found / len(unique_expected), 12) if unique_expected else 1.0,
        "reciprocal_rank": round(0.0 if first is None else 1.0 / first, 12),
    }


def _case_passed(metric: Mapping[str, Any], expected: Sequence[Any], required: Sequence[Any]) -> bool:
    paths = [str(path) for path in metric["candidate_paths"]]
    expected_paths = [str(path) for path in expected]
    return paths[: len(expected_paths)] == expected_paths and all(str(path) in paths for path in required)


def _evaluate_case(
    projection: ProjectionRead,
    policy: Mapping[str, Any],
    retrieval_config_sha256: str,
    index: EmbeddingIndex,
    provider: EmbeddingProvider,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(case["mode"])
    kwargs = {
        "scope": case["scope"],
        "path_prefix": case["path_prefix"],
        "include_types": case["include_types"],
        "include_review": case["include_review"],
        "limit": case["limit"],
        "hops": case["hops"],
    }
    lexical = (search_projection if mode == "search" else retrieve_projection)(
        projection,
        case["query"],
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        **kwargs,
    )
    e01 = vector_search_projection(
        projection,
        case["query"],
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        **kwargs,
    )
    learned = _learned_projection(
        projection,
        case["query"],
        retrieval=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        index=index,
        provider=provider,
        model_tag=index.model_tag,
        model_digest=index.model_digest,
        dimension=index.dimension,
        **kwargs,
        fuse=False,
    )
    rrf = _learned_projection(
        projection,
        case["query"],
        retrieval=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        index=index,
        provider=provider,
        model_tag=index.model_tag,
        model_digest=index.model_digest,
        dimension=index.dimension,
        **kwargs,
        fuse=True,
    )
    expected = case["expected_prefix_paths"]
    required = case["required_paths"]
    metrics = {
        "lexical": _ranking_metrics(lexical["candidates"], expected),
        "e01_feature": _ranking_metrics(e01["candidates"], expected),
        "learned": _ranking_metrics(learned["candidates"], expected),
        "rrf": _ranking_metrics(rrf["candidates"], expected),
    }
    return {
        "id": str(case["id"]),
        "mode": mode,
        **metrics,
        "passed": _case_passed(metrics["rrf"], expected, required),
    }


def evaluate_c34_baseline(
    root: str | Path,
    provider: EmbeddingProvider,
    *,
    baseline_path: str | Path | None = None,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Compare lexical, E01, learned, and RRF on one frozen fixture.

    The report can pass as a deterministic implementation/evaluation result,
    but promotion is always false here because E02 live-model evidence is not
    available in the C34 core slice.
    """

    operation = "vector learned-evaluate"
    try:
        workspace = _workspace(root)
        baseline, baseline_sha256 = _load_baseline(workspace, baseline_path)
        projection = load_current_projection(workspace, verify_sources=True)
        if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
            raise EmbeddingIndexConflict("current projection generation does not match expected_generation_id")
        _policy, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise EmbeddingIndexConflict("pinned projection retrieval policy digest is stale")
        embedding = _as_mapping(baseline["embedding"], "/embedding")
        build_report, build_code = build_embedding_index_projection(
            workspace,
            projection,
            retrieval=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            provider=provider,
            model_tag=str(embedding["model_tag"]),
            model_digest=str(embedding["model_digest"]),
            dimension=int(embedding["dimension"]),
        )
        if build_code != EXIT_OK:
            return _failure(operation, "C34_BUILD_INVALID", "failed to publish the evaluation index", provider_called=True)
        index = load_embedding_index(
            workspace,
            projection=projection,
            retrieval_config_sha256=retrieval_config_sha256,
            model_tag=str(embedding["model_tag"]),
            model_digest=str(embedding["model_digest"]),
            dimension=int(embedding["dimension"]),
        )
        cases = [
            _evaluate_case(projection, retrieval, retrieval_config_sha256, index, provider, _as_mapping(item, "C34 query"))
            for item in baseline["queries"]
        ]
    except ProjectionError as error:
        return _failure(operation, "C34_INDEX_STALE", str(error))
    except EmbeddingIndexConflict as error:
        return _failure(operation, "C34_INDEX_CONFLICT", str(error), provider_called=True)
    except EmbeddingIndexValidationError as error:
        return _failure(operation, "C34_EVALUATION_INVALID", str(error), provider_called=True)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "C34_EVALUATION_INVALID", str(error), provider_called=True)

    case_count = len(cases)
    passed_count = sum(1 for case in cases if case["passed"])

    def mean(branch: str, metric: str) -> float:
        return round(sum(float(case[branch][metric]) for case in cases) / case_count, 12)

    lexical_recall = mean("lexical", "expected_path_recall")
    e01_recall = mean("e01_feature", "expected_path_recall")
    learned_recall = mean("learned", "expected_path_recall")
    rrf_recall = mean("rrf", "expected_path_recall")
    lexical_rr = mean("lexical", "reciprocal_rank")
    e01_rr = mean("e01_feature", "reciprocal_rank")
    learned_rr = mean("learned", "reciprocal_rank")
    rrf_rr = mean("rrf", "reciprocal_rank")
    learned_improves = learned_recall > lexical_recall or learned_rr > lexical_rr
    rrf_improves = rrf_recall > lexical_recall or rrf_rr > lexical_rr
    require_improvement = bool(_as_mapping(baseline["embedding"], "/embedding")["require_improvement"])
    quality_gate_passed = passed_count == case_count and (not require_improvement or rrf_improves)
    # E02 owns live model accuracy, privacy, citation, staleness, latency, and memory evidence.
    live_model_evidence = False
    promotion_gate_passed = quality_gate_passed and live_model_evidence
    metrics = {
        "case_count": case_count,
        "passed_case_count": passed_count,
        "all_cases_passed": passed_count == case_count,
        "lexical_mean_expected_path_recall": lexical_recall,
        "e01_feature_mean_expected_path_recall": e01_recall,
        "learned_mean_expected_path_recall": learned_recall,
        "rrf_mean_expected_path_recall": rrf_recall,
        "lexical_mean_reciprocal_rank": lexical_rr,
        "e01_feature_mean_reciprocal_rank": e01_rr,
        "learned_mean_reciprocal_rank": learned_rr,
        "rrf_mean_reciprocal_rank": rrf_rr,
        "learned_improves_baseline": learned_improves,
        "rrf_improves_baseline": rrf_improves,
        "require_improvement": require_improvement,
        "quality_gate_passed": quality_gate_passed,
        "promotion_gate_passed": promotion_gate_passed,
        "promotion_decision": "promote_optional_learned" if promotion_gate_passed else "retain_lexical_baseline",
    }
    report = {
        "status": "PASS",
        "operation": operation,
        "capability": CAPABILITY,
        "baseline_sha256": baseline_sha256,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "embedding_index_id": index.index_id,
        "retrieval_config_sha256": retrieval_config_sha256,
        "embedding_config_sha256": EMBEDDING_CONFIG_SHA256,
        "cases": cases,
        "metrics": metrics,
        "learned_retrieval_opt_in": True,
        "live_model_evidence": live_model_evidence,
        "source_verified": True,
        "provider_called": True,
        "mutation_performed": False,
        "runtime_artifact_written": bool(build_report.get("runtime_artifact_written", True)),
    }
    errors = sorted(
        Draft202012Validator(learned_retrieval_evaluation_schema()).iter_errors(report),
        key=lambda error: error.json_path,
    )
    if errors:
        return _failure(operation, "C34_REPORT_INVALID", errors[0].message, provider_called=True)
    return report, EXIT_OK


__all__ = [
    "CAPABILITY",
    "CURRENT_POINTER",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MODEL_DIMENSION",
    "DEFAULT_MODEL_TAG",
    "DOCUMENT_PROMPT_TEMPLATE",
    "EMBEDDING_CONFIG_SHA256",
    "EMBEDDING_EVALUATION_SCHEMA_PATH",
    "EMBEDDING_INDEX_RECORD_SCHEMA_PATH",
    "EMBEDDING_INDEX_SCHEMA_PATH",
    "INDEXER_VERSION",
    "QUERY_PROMPT_TEMPLATE",
    "UNKNOWN_QUERY_ABSTENTION_THRESHOLD",
    "EmbeddingIndex",
    "EmbeddingIndexConflict",
    "EmbeddingIndexError",
    "EmbeddingIndexValidationError",
    "build_embedding_index",
    "build_embedding_index_projection",
    "document_prompt",
    "embedding_config_sha256",
    "embedding_contract",
    "embedding_index_manifest_schema",
    "embedding_index_record_schema",
    "evaluate_c34_baseline",
    "learned_retrieval_evaluation_schema",
    "learned_retrieve",
    "learned_search",
    "load_embedding_index",
    "query_prompt",
]

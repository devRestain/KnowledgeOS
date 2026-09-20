"""C39 separate Qwen learned-retrieval candidate index.

This module is deliberately separate from :mod:`vaultops.embedding_index`.
The C34 Qwen index is the accepted canonical compatibility control after the
E02 promotion decision.  C39 still writes an immutable audit candidate under
a different runtime root and exposes a separate gate before any canonical
pointer replacement.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .embedding_index import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MODEL_DIMENSION,
    DEFAULT_MODEL_TAG,
    DOCUMENT_PROMPT_TEMPLATE,
    QUERY_PROMPT_TEMPLATE,
    UNKNOWN_QUERY_ABSTENTION_THRESHOLD,
    EmbeddingIndexConflict,
    EmbeddingIndexValidationError,
    EmbeddingProvider,
    _apply_unknown_query_abstention,
    _candidate,
    _candidate_provenance,
    _cosine_similarity,
    _ensure_private_directory,
    _filter_notes,
    _json_object,
    _jsonl_records,
    _learned_rrf,
    _load_contract,
    _projection_chunks,
    _record_sort_key,
    _regular_private_file,
    _retrieval_context,
    _revalidate_candidates,
    _runtime_path,
    _validate_provider_embeddings,
    _validate_schema,
    _workspace,
    _write_immutable,
    _write_pointer,
    _zero_lexical,
)
from .projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    ProjectionError,
    ProjectionRead,
    canonical_json_bytes,
    load_current_projection,
    retrieval_candidate_schema,
)
from .retrieval import PARSER_AND_CHUNKER_VERSION, _canonical_query, _lexical_candidates
from .vector import _validate_contract

CAPABILITY = "C39"
QWEN_MODEL_TAG = DEFAULT_MODEL_TAG
QWEN_MODEL_DIMENSION = DEFAULT_MODEL_DIMENSION
QWEN_BATCH_SIZE = DEFAULT_BATCH_SIZE
QWEN_INDEXER_VERSION = "vaultops.qwen_embedding.v1"
QWEN_INDEX_ROOT = "index/embeddings-qwen"
QWEN_GENERATION_ROOT = f"{QWEN_INDEX_ROOT}/generations"
QWEN_CURRENT_POINTER = f"{QWEN_INDEX_ROOT}/current.json"
QWEN_INDEX_RECORD_SCHEMA_PATH = "ops/schemas/c39-qwen-embedding-index-record.schema.json"
QWEN_INDEX_SCHEMA_PATH = "ops/schemas/c39-qwen-embedding-index.schema.json"
QWEN_QUERY_PROMPT_TEMPLATE = QUERY_PROMPT_TEMPLATE
QWEN_DOCUMENT_PROMPT_TEMPLATE = DOCUMENT_PROMPT_TEMPLATE
EMERGENCY_FALLBACK_MODEL_TAG = "embeddinggemma:300m-qat-q8_0"
EMERGENCY_FALLBACK_MODEL_DIMENSION = 768

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INDEX_ID_RE = re.compile(r"^c39-qwen-[0-9a-f]{64}$")
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LINE_BREAKS = ("\n", "\r", "\v", "\f", "\x85", "\u2028", "\u2029")


@dataclass(frozen=True)
class QwenCandidateIndex:
    """One fully validated immutable C39 Qwen candidate generation."""

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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise EmbeddingIndexValidationError(f"{label} must be lowercase SHA-256")
    return value


def _validate_generation_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _GENERATION_ID_RE.fullmatch(value):
        raise EmbeddingIndexValidationError(f"{label} is unsafe")
    return value


def _validate_model_tag(value: object) -> str:
    if value != QWEN_MODEL_TAG:
        raise EmbeddingIndexValidationError(
            f"model_tag must remain the user-approved {QWEN_MODEL_TAG} candidate"
        )
    return QWEN_MODEL_TAG


def _validate_dimension(value: object) -> int:
    if isinstance(value, bool) or value != QWEN_MODEL_DIMENSION:
        raise EmbeddingIndexValidationError(
            f"dimension must remain the Qwen {QWEN_MODEL_DIMENSION}-dimension candidate"
        )
    return QWEN_MODEL_DIMENSION


def qwen_embedding_contract() -> dict[str, Any]:
    """Return the Qwen candidate contract and its explicit activation boundary."""

    return {
        "schema_version": 1,
        "capability": CAPABILITY,
        "enabled_by_default": False,
        "default_profile": "qwen3-embedding:8b-q4_K_M",
        "live_default_selector": True,
        "activation_mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
        "fallback_profile": {
            "model_tag": EMERGENCY_FALLBACK_MODEL_TAG,
            "dimension": EMERGENCY_FALLBACK_MODEL_DIMENSION,
            "role": "emergency_resource_fallback",
        },
        "provider": "local:ollama",
        "model_tag": QWEN_MODEL_TAG,
        "dimension": QWEN_MODEL_DIMENSION,
        "max_batch_size": QWEN_BATCH_SIZE,
        "truncate": False,
        "query_prompt_template": QWEN_QUERY_PROMPT_TEMPLATE,
        "document_prompt_template": QWEN_DOCUMENT_PROMPT_TEMPLATE,
        "parser_and_chunker_version": PARSER_AND_CHUNKER_VERSION,
        "indexer_version": QWEN_INDEXER_VERSION,
        "candidate_root": QWEN_INDEX_ROOT,
        "canonical_c34_root": "index/embeddings",
        "promotion_mode": "separate_gate_then_canonical_apply",
        "generation_refusal": "not_applicable",
        "unknown_query_abstention_threshold": UNKNOWN_QUERY_ABSTENTION_THRESHOLD,
        "contradiction_fixture": "ops/tests/fixtures/e02_retrieval/contradiction.yaml",
    }


def qwen_embedding_config_sha256() -> str:
    """Return the digest bound to the C39 Qwen executable contract."""

    return _sha256_bytes(canonical_json_bytes(qwen_embedding_contract()))


QWEN_EMBEDDING_CONFIG_SHA256 = qwen_embedding_config_sha256()


def qwen_query_prompt(query: str) -> str:
    """Render the distinct Qwen query role and reject ambiguous input."""

    if not isinstance(query, str) or not query.strip() or "\x00" in query:
        raise EmbeddingIndexValidationError("query must be non-empty text without NUL")
    normalized = unicodedata.normalize("NFC", query.strip())
    if any(marker in normalized for marker in _LINE_BREAKS):
        raise EmbeddingIndexValidationError("query contains an unsupported line break")
    return QWEN_QUERY_PROMPT_TEMPLATE.format(query=normalized)


def qwen_document_prompt(*, title: str, text: str) -> str:
    """Render the Qwen document role with an explicit title fallback."""

    if not isinstance(title, str) or not isinstance(text, str):
        raise EmbeddingIndexValidationError("document title and text must be strings")
    normalized_title = unicodedata.normalize("NFC", title.strip()) or "none"
    normalized_text = unicodedata.normalize("NFC", text.strip())
    if not normalized_text or "\x00" in normalized_title or "\x00" in normalized_text:
        raise EmbeddingIndexValidationError("document title and text must be non-empty and NUL-free")
    return QWEN_DOCUMENT_PROMPT_TEMPLATE.format(title=normalized_title, text=normalized_text)


def _embedding_identity(
    projection: ProjectionRead,
    *,
    retrieval_config_sha256: str,
    model_digest: str,
) -> dict[str, Any]:
    generation_id = _validate_generation_id(projection.manifest.get("generation_id"), "projection generation_id")
    source_snapshot = _validate_digest(
        projection.manifest.get("source_snapshot_sha256"),
        "projection source_snapshot_sha256",
    )
    selected_digest = _validate_digest(model_digest, "embedding model digest")
    return {
        "projection_generation_id": generation_id,
        "projection_source_snapshot_sha256": source_snapshot,
        "parser_and_chunker_version": PARSER_AND_CHUNKER_VERSION,
        "embedding_model_tag": QWEN_MODEL_TAG,
        "embedding_model_digest": selected_digest,
        "embedding_dimension": QWEN_MODEL_DIMENSION,
        "query_prompt_template": QWEN_QUERY_PROMPT_TEMPLATE,
        "document_prompt_template": QWEN_DOCUMENT_PROMPT_TEMPLATE,
        "query_prompt_template_sha256": _sha256_bytes(QWEN_QUERY_PROMPT_TEMPLATE.encode("utf-8")),
        "document_prompt_template_sha256": _sha256_bytes(QWEN_DOCUMENT_PROMPT_TEMPLATE.encode("utf-8")),
        "indexer_version": QWEN_INDEXER_VERSION,
        "retrieval_config_sha256": _validate_digest(retrieval_config_sha256, "retrieval_config_sha256"),
        "truncate": False,
    }


def _index_id(identity: Mapping[str, Any]) -> str:
    return f"c39-qwen-{_sha256_bytes(canonical_json_bytes(identity))}"


def _embedding_metadata(index: QwenCandidateIndex) -> dict[str, Any]:
    return {
        "provider": "local:ollama",
        "model": index.model_tag,
        "dimension": index.dimension,
        "artifact_digest": index.model_digest,
        "index_id": index.index_id,
        "query_prompt_template_sha256": index.manifest["query_prompt_template_sha256"],
        "document_prompt_template_sha256": index.manifest["document_prompt_template_sha256"],
    }


def qwen_embedding_index_record_schema() -> dict[str, Any]:
    """Return the strict schema for one private Qwen vector record."""

    sha = {"type": "string", "pattern": _SHA256_RE.pattern}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c39-qwen-embedding-index-record.schema.json",
        "title": "KnowledgeOS C39 Qwen candidate embedding index record",
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
                "minItems": QWEN_MODEL_DIMENSION,
                "maxItems": QWEN_MODEL_DIMENSION,
                "items": {"type": "number"},
            },
        },
    }


def qwen_embedding_index_manifest_schema() -> dict[str, Any]:
    """Return the strict schema for one immutable Qwen candidate manifest."""

    sha = {"type": "string", "pattern": _SHA256_RE.pattern}
    path = {"type": "string", "minLength": 1, "pattern": r"^[^/].*$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c39-qwen-embedding-index.schema.json",
        "title": "KnowledgeOS C39 immutable Qwen candidate embedding index manifest",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "capability",
            "artifact_kind",
            "candidate_only",
            "canonical_c34_mutated",
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
            "artifact_kind": {"const": "candidate_embedding_index"},
            "candidate_only": {"const": True},
            "canonical_c34_mutated": {"const": False},
            "index_id": {"type": "string", "pattern": _INDEX_ID_RE.pattern},
            "generation_root": path,
            "manifest_path": path,
            "vectors_path": path,
            "projection_generation_id": {"type": "string", "minLength": 1},
            "projection_source_snapshot_sha256": sha,
            "parser_and_chunker_version": {"type": "string", "minLength": 1},
            "embedding_model_tag": {"const": QWEN_MODEL_TAG},
            "embedding_model_digest": sha,
            "embedding_dimension": {"const": QWEN_MODEL_DIMENSION},
            "query_prompt_template": {"const": QWEN_QUERY_PROMPT_TEMPLATE},
            "document_prompt_template": {"const": QWEN_DOCUMENT_PROMPT_TEMPLATE},
            "query_prompt_template_sha256": sha,
            "document_prompt_template_sha256": sha,
            "indexer_version": {"const": QWEN_INDEXER_VERSION},
            "retrieval_config_sha256": sha,
            "embedding_config_sha256": sha,
            "truncate": {"const": False},
            "vector_record_count": {"type": "integer", "minimum": 1},
            "vectors_sha256": sha,
            "identity_sha256": sha,
            "source_verified": {"const": True},
        },
    }


def _build_records(
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    *,
    provider: EmbeddingProvider,
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
    chunks.sort(
        key=lambda chunk: (
            unicodedata.normalize("NFC", str(chunk.note["path"])).encode("utf-8"),
            str(chunk.note["id"]),
            _chunk_sort_key(chunk),
        )
    )
    if not chunks:
        raise EmbeddingIndexValidationError("privacy-filtered projection contains no Qwen-embeddable chunks")
    embeddings: list[list[float]] = []
    provider_called = False
    for start in range(0, len(chunks), QWEN_BATCH_SIZE):
        batch = chunks[start : start + QWEN_BATCH_SIZE]
        prompts = [qwen_document_prompt(title=str(chunk.note.get("title", "")), text=chunk.text) for chunk in batch]
        provider_called = True
        response = provider.embed(QWEN_MODEL_TAG, prompts, truncate=False, keep_alive=0)
        embeddings.extend(
            _validate_provider_embeddings(
                response,
                expected_count=len(batch),
                dimension=QWEN_MODEL_DIMENSION,
            )
        )
    records: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, embeddings):
        prompt = qwen_document_prompt(title=str(chunk.note.get("title", "")), text=chunk.text)
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
        _validate_schema(record, qwen_embedding_index_record_schema(), "Qwen candidate index record")
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
    generation_root = _runtime_path(workspace, f"{QWEN_GENERATION_ROOT}/{index_id}")
    manifest_path = generation_root / "manifest.json"
    vectors_path = generation_root / "vectors.jsonl"
    _ensure_private_directory(generation_root)
    vectors_written = _write_immutable(vectors_path, vectors_bytes, "Qwen candidate vectors")
    manifest_written = _write_immutable(manifest_path, manifest_bytes, "Qwen candidate manifest")
    pointer = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "candidate_only": True,
        "canonical_c34_mutated": False,
        "index_id": index_id,
        "generation_root": f"{QWEN_GENERATION_ROOT}/{index_id}",
        "manifest_path": f"{QWEN_GENERATION_ROOT}/{index_id}/manifest.json",
        "vectors_path": f"{QWEN_GENERATION_ROOT}/{index_id}/vectors.jsonl",
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "vectors_sha256": _sha256_bytes(vectors_bytes),
    }
    pointer_changed = _write_pointer(
        _runtime_path(workspace, QWEN_CURRENT_POINTER),
        canonical_json_bytes(pointer),
    )
    return vectors_written or manifest_written, pointer_changed


def build_qwen_candidate_index_projection(
    workspace: str | Path,
    projection: ProjectionRead,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    provider: EmbeddingProvider,
    model_digest: str,
) -> tuple[dict[str, Any], int]:
    """Build one immutable Qwen candidate without touching the C34 pointer."""

    workspace_path = _workspace(workspace)
    _validate_contract(retrieval)
    identity = _embedding_identity(
        projection,
        retrieval_config_sha256=retrieval_config_sha256,
        model_digest=model_digest,
    )
    records, provider_called = _build_records(projection, retrieval, provider=provider)
    vectors_bytes = b"".join(canonical_json_bytes(record) for record in records)
    index_id = _index_id(identity)
    generation_root = f"{QWEN_GENERATION_ROOT}/{index_id}"
    manifest = {
        "schema_version": 1,
        "capability": CAPABILITY,
        "artifact_kind": "candidate_embedding_index",
        "candidate_only": True,
        "canonical_c34_mutated": False,
        "index_id": index_id,
        "generation_root": generation_root,
        "manifest_path": f"{generation_root}/manifest.json",
        "vectors_path": f"{generation_root}/vectors.jsonl",
        **identity,
        "embedding_config_sha256": QWEN_EMBEDDING_CONFIG_SHA256,
        "vector_record_count": len(records),
        "vectors_sha256": _sha256_bytes(vectors_bytes),
        "identity_sha256": _sha256_bytes(canonical_json_bytes(identity)),
        "source_verified": True,
    }
    _validate_schema(manifest, qwen_embedding_index_manifest_schema(), "Qwen candidate index manifest")
    manifest_bytes = canonical_json_bytes(manifest)
    runtime_written, pointer_changed = _publish_index(
        workspace_path,
        index_id=index_id,
        manifest_bytes=manifest_bytes,
        vectors_bytes=vectors_bytes,
    )
    return {
        "status": "PASS",
        "operation": "c39 qwen candidate-build",
        "capability": CAPABILITY,
        "index_generation_id": identity["projection_generation_id"],
        "embedding_index_id": index_id,
        "embedding_model_tag": QWEN_MODEL_TAG,
        "embedding_model_digest": identity["embedding_model_digest"],
        "embedding_dimension": QWEN_MODEL_DIMENSION,
        "embedding_config_sha256": QWEN_EMBEDDING_CONFIG_SHA256,
        "vector_record_count": len(records),
        "candidate_only": True,
        "canonical_c34_mutated": False,
        "runtime_artifact_written": runtime_written,
        "pointer_changed": pointer_changed,
        "source_verified": True,
        "provider_called": provider_called,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
    }, EXIT_OK


def _load_qwen_index_from_pointer(workspace: Path) -> QwenCandidateIndex:
    pointer_path = _runtime_path(workspace, QWEN_CURRENT_POINTER)
    pointer = _json_object(_regular_private_file(pointer_path, "Qwen candidate current pointer"), "Qwen candidate current pointer")
    expected_keys = {
        "schema_version",
        "capability",
        "candidate_only",
        "canonical_c34_mutated",
        "index_id",
        "generation_root",
        "manifest_path",
        "vectors_path",
        "manifest_sha256",
        "vectors_sha256",
    }
    if (
        set(pointer) != expected_keys
        or pointer.get("schema_version") != 1
        or pointer.get("capability") != CAPABILITY
        or pointer.get("candidate_only") is not True
        or pointer.get("canonical_c34_mutated") is not False
    ):
        raise EmbeddingIndexValidationError("Qwen candidate current pointer identity is invalid")
    index_id = pointer.get("index_id")
    if not isinstance(index_id, str) or not _INDEX_ID_RE.fullmatch(index_id):
        raise EmbeddingIndexValidationError("Qwen candidate current pointer index_id is invalid")
    expected_root = f"{QWEN_GENERATION_ROOT}/{index_id}"
    for field, expected in {
        "generation_root": expected_root,
        "manifest_path": f"{expected_root}/manifest.json",
        "vectors_path": f"{expected_root}/vectors.jsonl",
    }.items():
        if pointer.get(field) != expected:
            raise EmbeddingIndexConflict(f"Qwen candidate pointer {field} does not match index_id")
    manifest_path = _runtime_path(workspace, str(pointer["manifest_path"]))
    vectors_path = _runtime_path(workspace, str(pointer["vectors_path"]))
    manifest_bytes = _regular_private_file(manifest_path, "Qwen candidate manifest")
    vectors_bytes = _regular_private_file(vectors_path, "Qwen candidate vectors")
    if pointer["manifest_sha256"] != _sha256_bytes(manifest_bytes):
        raise EmbeddingIndexConflict("Qwen candidate pointer manifest digest does not match")
    if pointer["vectors_sha256"] != _sha256_bytes(vectors_bytes):
        raise EmbeddingIndexConflict("Qwen candidate pointer vectors digest does not match")
    manifest = _json_object(manifest_bytes, "Qwen candidate manifest")
    _validate_schema(manifest, qwen_embedding_index_manifest_schema(), "Qwen candidate manifest")
    if manifest.get("index_id") != index_id or manifest.get("generation_root") != expected_root:
        raise EmbeddingIndexConflict("Qwen candidate manifest selects a different immutable generation")
    if manifest.get("vectors_sha256") != _sha256_bytes(vectors_bytes):
        raise EmbeddingIndexConflict("Qwen candidate manifest vectors digest does not match")
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
        raise EmbeddingIndexConflict("Qwen candidate identity digest does not match the manifest")
    records = _jsonl_records(vectors_bytes, "Qwen candidate vectors")
    if len(records) != manifest["vector_record_count"]:
        raise EmbeddingIndexConflict("Qwen candidate vector record count does not match the manifest")
    for record in records:
        _validate_schema(record, qwen_embedding_index_record_schema(), "Qwen candidate vector record")
    if records != sorted(records, key=_record_sort_key):
        raise EmbeddingIndexValidationError("Qwen candidate vector records are not in canonical order")
    identities = {(record["note_id"], record["chunk_id"]) for record in records}
    if len(identities) != len(records):
        raise EmbeddingIndexValidationError("Qwen candidate vectors contain duplicate chunk identity")
    return QwenCandidateIndex(manifest=manifest, records=tuple(records))


def load_qwen_candidate_index(
    root: str | Path,
    *,
    projection: ProjectionRead | None = None,
    retrieval_config_sha256: str | None = None,
    model_digest: str | None = None,
) -> QwenCandidateIndex:
    """Load the separate Qwen candidate and fail closed on identity drift."""

    workspace = _workspace(root)
    index = _load_qwen_index_from_pointer(workspace)
    manifest = index.manifest
    if projection is not None:
        if manifest["projection_generation_id"] != projection.manifest.get("generation_id"):
            raise EmbeddingIndexConflict("Qwen candidate projection generation drift requires a rebuild")
        if manifest["projection_source_snapshot_sha256"] != projection.manifest.get("source_snapshot_sha256"):
            raise EmbeddingIndexConflict("Qwen candidate source snapshot drift requires a rebuild")
    if retrieval_config_sha256 is not None and _validate_digest(retrieval_config_sha256, "retrieval_config_sha256") != manifest["retrieval_config_sha256"]:
        raise EmbeddingIndexConflict("Qwen candidate retrieval policy drift requires a rebuild")
    if model_digest is not None and _validate_digest(model_digest, "model_digest") != manifest["embedding_model_digest"]:
        raise EmbeddingIndexConflict("Qwen candidate model digest drift requires a rebuild")
    if manifest["embedding_config_sha256"] != QWEN_EMBEDDING_CONFIG_SHA256:
        raise EmbeddingIndexConflict("Qwen candidate executable contract drift requires a rebuild")
    return index


def _validate_index_projection(index: QwenCandidateIndex, projection: ProjectionRead, retrieval_config_sha256: str) -> None:
    if index.manifest["projection_generation_id"] != projection.manifest.get("generation_id"):
        raise EmbeddingIndexConflict("Qwen candidate projection generation drift requires a rebuild")
    if index.manifest["projection_source_snapshot_sha256"] != projection.manifest.get("source_snapshot_sha256"):
        raise EmbeddingIndexConflict("Qwen candidate source snapshot drift requires a rebuild")
    if index.manifest["retrieval_config_sha256"] != retrieval_config_sha256:
        raise EmbeddingIndexConflict("Qwen candidate retrieval policy drift requires a rebuild")


def _qwen_candidates(
    projection: ProjectionRead,
    query: str,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    index: QwenCandidateIndex,
    provider: EmbeddingProvider,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> dict[str, Any]:
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
    chunks = _projection_chunks(projection)
    query_response = provider.embed(
        QWEN_MODEL_TAG,
        [qwen_query_prompt(normalized_query)],
        truncate=False,
        keep_alive=0,
    )
    query_embedding = _validate_provider_embeddings(
        query_response,
        expected_count=1,
        dimension=QWEN_MODEL_DIMENSION,
    )[0]
    eligible_ids = {str(note["id"]) for note in included}
    scored: list[tuple[float, Any, Mapping[str, Any]]] = []
    for record in index.records:
        if str(record["note_id"]) not in eligible_ids:
            continue
        chunk = chunks.get(str(record["chunk_id"]))
        if chunk is None:
            raise EmbeddingIndexConflict("Qwen candidate chunk is absent from the pinned projection")
        if (
            record["note_id"] != str(chunk.note["id"])
            or record["path"] != str(chunk.note["path"])
            or record["content_hash"] != str(chunk.note["content_hash"])
            or record["chunk_hash"] != str(chunk.chunk_hash)
            or record["chunk_locator"] != str(chunk.locator)
        ):
            raise EmbeddingIndexConflict("Qwen candidate chunk provenance drift requires a rebuild")
        scored.append((_cosine_similarity(query_embedding, record["embedding"]), chunk, record))
    scored.sort(
        key=lambda item: (
            -item[0],
            unicodedata.normalize("NFC", str(item[1].note["path"])).encode("utf-8"),
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
            retrieval_reason="qwen_candidate_vector_match",
            lexical=_zero_lexical(),
            graph_path=[],
        )
        candidate["vector_score_and_rank"] = {
            "algorithm": "cosine_qwen3_embedding_v1",
            "score": round(float(score), 12),
            "rank": rank,
            "dimension": QWEN_MODEL_DIMENSION,
            "similarity": "cosine",
            "index_id": index.index_id,
            "provenance": _candidate_provenance(candidate),
        }
        candidate["embedding_provider_model_dimension_and_artifact_digest"] = metadata
        candidate["indexer_version"] = QWEN_INDEXER_VERSION
        errors = sorted(
            Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate),
            key=lambda error: error.json_path,
        )
        if errors:
            raise EmbeddingIndexValidationError(f"Qwen candidate is invalid: {errors[0].message}")
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
    result = {
        "query_sha256": query_sha256,
        "policy_decision_sha256": policy_hash,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "embedding_config_sha256": QWEN_EMBEDDING_CONFIG_SHA256,
        "embedding_index_id": index.index_id,
        "embedding_enabled": True,
        "retrieval_mode": "qwen_rrf" if fuse else "qwen_vector",
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
    }
    if not fuse or abstention["abstained"]:
        return result
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
        policy_hash,
        retrieval_config_sha256,
    )
    lexical = _revalidate_candidates(projection, retrieval, lexical, options=options)
    return {
        **result,
        "candidates": _learned_rrf(
            lexical,
            learned,
            index=index,
            limit=options.limit,
            projection=projection,
            retrieval=retrieval,
            options=options,
        ),
    }


def _qwen_projection(
    projection: ProjectionRead,
    query: str,
    *,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    index: QwenCandidateIndex,
    provider: EmbeddingProvider,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> dict[str, Any]:
    return _qwen_candidates(
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


def _run_qwen(
    operation: str,
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_digest: str,
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
        index = load_qwen_candidate_index(
            workspace,
            projection=projection,
            retrieval_config_sha256=retrieval_config_sha256,
            model_digest=model_digest,
        )
        result = _qwen_projection(
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
        return {
            **result,
            "status": "PASS",
            "operation": operation,
            "capability": CAPABILITY,
            "candidate_count": len(result["candidates"]),
            "source_verified": True,
            "provider_called": True,
            "mutation_performed": False,
            "canonical_c34_mutated": False,
            "candidate_only": True,
        }, EXIT_OK
    except ProjectionError as error:
        return _failure(operation, "C39_INDEX_STALE", str(error))
    except EmbeddingIndexConflict as error:
        return _failure(operation, "C39_INDEX_CONFLICT", str(error))
    except EmbeddingIndexValidationError as error:
        return _failure(operation, "C39_QUERY_INVALID", str(error))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "C39_QUERY_INVALID", str(error))


def _failure(operation: str, code: str, message: str) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": CAPABILITY,
        "candidate_only": True,
        "canonical_c34_mutated": False,
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, EXIT_CONFLICT


def build_qwen_candidate_index(
    root: str | Path,
    provider: EmbeddingProvider,
    *,
    model_digest: str,
) -> tuple[dict[str, Any], int]:
    """Build the selected Qwen candidate from the current privacy-filtered projection."""

    operation = "c39 qwen candidate-build"
    try:
        workspace = _workspace(root)
        projection = load_current_projection(workspace, verify_sources=True)
        _policy, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise EmbeddingIndexConflict("pinned projection retrieval policy digest is stale")
        return build_qwen_candidate_index_projection(
            workspace,
            projection,
            retrieval=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            provider=provider,
            model_digest=model_digest,
        )
    except ProjectionError as error:
        return _failure(operation, "C39_INDEX_STALE", str(error))
    except EmbeddingIndexValidationError as error:
        return _failure(operation, "C39_INPUT_INVALID", str(error))
    except EmbeddingIndexConflict as error:
        return _failure(operation, "C39_INDEX_CONFLICT", str(error))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "C39_BUILD_INVALID", str(error))


def qwen_learned_search(
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_digest: str,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = 10,
    hops: int = 0,
) -> tuple[dict[str, Any], int]:
    """Run Qwen vector retrieval without changing canonical C34 state."""

    return _run_qwen(
        "c39 qwen learned-search",
        root,
        query,
        provider,
        model_digest=model_digest,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        fuse=False,
    )


def qwen_learned_retrieve(
    root: str | Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    model_digest: str,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = 10,
    hops: int = 1,
) -> tuple[dict[str, Any], int]:
    """Run lexical plus Qwen candidate RRF retrieval without C34 mutation."""

    return _run_qwen(
        "c39 qwen learned-retrieve",
        root,
        query,
        provider,
        model_digest=model_digest,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        fuse=True,
    )


def evaluate_qwen_promotion(
    *,
    live_model_evidence: bool,
    quality_gate_passed: bool,
    privacy_gate_passed: bool,
    citation_gate_passed: bool,
    staleness_gate_passed: bool,
    resource_gate_passed: bool,
    human_decision: str,
    external_storage_used: bool = False,
    serial_only: bool = True,
    one_model_loaded: bool = True,
    unattended_activation: bool = False,
) -> dict[str, Any]:
    """Evaluate Qwen readiness before the separately authorized canonical apply."""

    if human_decision not in {"retain_c34", "promote_separate_qwen", "promote_qwen_canonical"}:
        raise EmbeddingIndexValidationError(
            "human_decision must be retain_c34 promote_separate_qwen or promote_qwen_canonical"
        )
    resource_policy_passed = bool(serial_only and one_model_loaded and not unattended_activation)
    gate_passed = bool(
        live_model_evidence
        and quality_gate_passed
        and privacy_gate_passed
        and citation_gate_passed
        and staleness_gate_passed
        and resource_gate_passed
        and resource_policy_passed
        and human_decision == "promote_separate_qwen"
        and not external_storage_used
    )
    canonical_gate_passed = bool(
        live_model_evidence
        and quality_gate_passed
        and privacy_gate_passed
        and citation_gate_passed
        and staleness_gate_passed
        and resource_gate_passed
        and resource_policy_passed
        and human_decision == "promote_qwen_canonical"
        and not external_storage_used
    )
    selected_gate_passed = gate_passed or canonical_gate_passed
    decision = (
        "promote_separate_qwen_candidate"
        if gate_passed
        else "promote_qwen_canonical"
        if canonical_gate_passed
        else "retain_c34"
    )
    return {
        "status": "PASS",
        "operation": "c39 qwen promotion-evaluate",
        "capability": CAPABILITY,
        "candidate_model_tag": QWEN_MODEL_TAG,
        "candidate_dimension": QWEN_MODEL_DIMENSION,
        "candidate_only": not canonical_gate_passed,
        "canonical_c34_mutated": False,
        "canonical_apply_allowed": canonical_gate_passed,
        "gates": {
            "live_model_evidence": bool(live_model_evidence),
            "quality": bool(quality_gate_passed),
            "privacy": bool(privacy_gate_passed),
            "citation": bool(citation_gate_passed),
            "staleness": bool(staleness_gate_passed),
            "resource": bool(resource_gate_passed),
            "serial_only": bool(serial_only),
            "one_model_loaded": bool(one_model_loaded),
            "unattended_activation": bool(unattended_activation),
            "resource_policy_passed": resource_policy_passed,
            "external_storage_used": bool(external_storage_used),
            "human_decision": human_decision,
        },
        "promotion": {
            "gate_passed": selected_gate_passed,
            "decision": decision,
            "canonical_index_mutated": False,
            "reasons": (
                ["all Qwen gates passed; explicit canonical apply is authorized"]
                if canonical_gate_passed
                else ["all separate Qwen candidate gates passed; canonical C34 apply remains unperformed"]
                if gate_passed
                else ["Qwen promotion gates are incomplete; retain C34"]
            ),
        },
        "mutation_performed": False,
        "vault_mutation_performed": False,
    }


def _chunks_for_note(note: Mapping[str, Any]) -> list[Any]:
    from .retrieval import _chunks_for_note as chunks_for_note

    return chunks_for_note(note)


def _chunk_sort_key(chunk: Any) -> tuple[Any, ...]:
    from .retrieval import _chunk_sort_key as chunk_sort_key

    return chunk_sort_key(chunk)


__all__ = [
    "CAPABILITY",
    "QWEN_BATCH_SIZE",
    "QWEN_CURRENT_POINTER",
    "QWEN_DOCUMENT_PROMPT_TEMPLATE",
    "QWEN_EMBEDDING_CONFIG_SHA256",
    "QWEN_INDEX_RECORD_SCHEMA_PATH",
    "QWEN_INDEX_ROOT",
    "QWEN_INDEX_SCHEMA_PATH",
    "QWEN_MODEL_DIMENSION",
    "QWEN_MODEL_TAG",
    "QWEN_QUERY_PROMPT_TEMPLATE",
    "UNKNOWN_QUERY_ABSTENTION_THRESHOLD",
    "QwenCandidateIndex",
    "build_qwen_candidate_index",
    "build_qwen_candidate_index_projection",
    "evaluate_qwen_promotion",
    "load_qwen_candidate_index",
    "qwen_document_prompt",
    "qwen_embedding_config_sha256",
    "qwen_embedding_contract",
    "qwen_embedding_index_manifest_schema",
    "qwen_embedding_index_record_schema",
    "qwen_learned_retrieve",
    "qwen_learned_search",
    "qwen_query_prompt",
]

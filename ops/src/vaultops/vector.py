"""Deterministic, local-only E01 vector retrieval and RRF evaluation.

E01 is an evaluation overlay on top of the C22 retrieval contract.  It uses a
small feature-hashed embedding implemented with the Python standard library;
there is no model download, provider call, index write, or Vault mutation.  A
caller must opt in explicitly.  The default C22 lexical and typed-link paths
remain unchanged.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
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

CAPABILITY = "E01"
VECTOR_ALGORITHM = "sha256_feature_hash_v1"
VECTOR_INDEXER_VERSION = "vaultops.vector.v1"
VECTOR_DIMENSION = 256
VECTOR_SEED = "knowledgeos-e01"
RRF_ALGORITHM = "rrf_v1"
DEFAULT_RRF_K = 60
DEFAULT_LEXICAL_WEIGHT = 1.0
DEFAULT_VECTOR_WEIGHT = 1.0
DEFAULT_LIMIT = 10
DEFAULT_BASELINE_PATH = "ops/tests/fixtures/e01_vectors/evaluation.yaml"
PARSER_AND_CHUNKER_VERSION = "markdown-headings-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LINE_BREAKS = ("\r", "\v", "\f", "\x85", "\u2028", "\u2029")


class VectorError(ValueError):
    """Base error for rejected E01 inputs."""


class VectorValidationError(VectorError):
    """Raised when a vector contract or request is invalid."""


class VectorConflict(VectorError):
    """Raised when the pinned projection or evaluation input is stale."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(_nfc(value)))


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VectorValidationError(f"{label} must be a mapping")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VectorValidationError(f"{label} must be a list")
    return value


def _expect(actual: Any, expected: Any, locator: str) -> None:
    if actual != expected:
        raise VectorValidationError(f"E01 vector contract drift at {locator}")


def vector_contract() -> dict[str, Any]:
    """Return the immutable local vector and RRF parameters."""

    return {
        "schema_version": 1,
        "capability": CAPABILITY,
        "enabled_by_default": False,
        "provider": "local",
        "algorithm": VECTOR_ALGORITHM,
        "indexer_version": VECTOR_INDEXER_VERSION,
        "dimension": VECTOR_DIMENSION,
        "seed": VECTOR_SEED,
        "normalization": "l2",
        "feature_families": ["token_unigram", "token_bigram", "character_ngram_2_3"],
        "rrf": {
            "algorithm": RRF_ALGORITHM,
            "k": DEFAULT_RRF_K,
            "lexical_weight": DEFAULT_LEXICAL_WEIGHT,
            "vector_weight": DEFAULT_VECTOR_WEIGHT,
        },
        "privacy": {"deny_forbidden": True},
    }


def vector_config_sha256() -> str:
    """Return the digest bound to the executable E01 vector contract."""

    return _sha256_bytes(canonical_json_bytes(vector_contract()))


VECTOR_CONFIG_SHA256 = vector_config_sha256()


def _validate_contract(retrieval: Mapping[str, Any]) -> None:
    """Validate the C22 policy conditions E01 is allowed to extend."""

    local = _as_mapping(retrieval.get("local_embedding"), "/retrieval/local_embedding")
    _expect(set(local), {"deny_forbidden"}, "/retrieval/local_embedding")
    _expect(local.get("deny_forbidden"), True, "/retrieval/local_embedding/deny_forbidden")
    phases = _as_list(retrieval.get("phases"), "/retrieval/phases")
    if "local_vectors" not in phases or "reciprocal_rank_fusion" not in phases:
        raise VectorValidationError("C22 retrieval phases do not permit E01 vector/RRF evaluation")


def _validate_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise VectorValidationError(f"{label} must be lowercase SHA-256")
    return value


def _feature_counts(tokens: Sequence[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    normalized = tuple(_nfc(str(token)).casefold() for token in tokens if str(token))
    for token in normalized:
        counts[f"u:{token}"] += 1.0
        codepoints = tuple(token)
        for width in (2, 3):
            if len(codepoints) < width:
                continue
            for index in range(len(codepoints) - width + 1):
                counts[f"c{width}:{''.join(codepoints[index:index + width])}"] += 0.35
    for first, second in itertools.pairwise(normalized):
        counts[f"b:{first}\0{second}"] += 0.6
    return counts


def _hash_feature(feature: str, *, dimension: int, seed: str) -> tuple[int, float]:
    digest = hashlib.sha256(f"{seed}\0{feature}".encode()).digest()
    index = int.from_bytes(digest[:8], "big") % dimension
    sign = 1.0 if digest[8] & 1 else -1.0
    return index, sign


def _l2_normalize(values: Sequence[float]) -> tuple[float, ...]:
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude == 0.0:
        return tuple(0.0 for _ in values)
    return tuple(value / magnitude for value in values)


def embed_tokens(
    tokens: Sequence[str],
    *,
    dimension: int = VECTOR_DIMENSION,
    seed: str = VECTOR_SEED,
) -> tuple[float, ...]:
    """Embed normalized tokens with deterministic signed feature hashing."""

    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 4096:
        raise VectorValidationError("dimension must be an integer from 1 through 4096")
    if not isinstance(seed, str) or not seed or any(marker in seed for marker in _LINE_BREAKS):
        raise VectorValidationError("seed must be one bounded logical line")
    values = [0.0] * dimension
    for feature, count in _feature_counts(tokens).items():
        index, sign = _hash_feature(feature, dimension=dimension, seed=seed)
        values[index] += sign * float(count)
    return _l2_normalize(values)


def embed_text(
    text: str,
    *,
    dimension: int = VECTOR_DIMENSION,
    seed: str = VECTOR_SEED,
) -> tuple[float, ...]:
    """Embed one Unicode text value without external model execution."""

    if not isinstance(text, str):
        raise VectorValidationError("text must be a string")
    return embed_tokens(_tokens(text), dimension=dimension, seed=seed)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return a bounded cosine similarity for two finite vectors."""

    if len(left) != len(right) or not left:
        raise VectorValidationError("vectors must have the same non-zero dimension")
    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    if not all(math.isfinite(value) for value in (*left_values, *right_values)):
        raise VectorValidationError("vectors must contain only finite numbers")
    left_magnitude = math.sqrt(sum(value * value for value in left_values))
    right_magnitude = math.sqrt(sum(value * value for value in right_values))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0
    value = sum(a * b for a, b in zip(left_values, right_values)) / (left_magnitude * right_magnitude)
    return max(-1.0, min(1.0, value))


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: Mapping[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fuse note-ranked records using deterministic weighted RRF."""

    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 10000:
        raise VectorValidationError("RRF k must be an integer from 1 through 10000")
    selected_weights = {name: 1.0 for name in ranked_lists}
    if weights is not None:
        for name, value in weights.items():
            if name not in ranked_lists:
                raise VectorValidationError(f"RRF weight names an unknown ranked list: {name}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                raise VectorValidationError("RRF weights must be finite non-negative numbers")
            selected_weights[name] = float(value)
    fused: dict[str, dict[str, Any]] = {}
    for name, records in ranked_lists.items():
        seen: set[str] = set()
        for rank, record in enumerate(records, start=1):
            note_id = record.get("note_id")
            if not isinstance(note_id, str) or not note_id:
                raise VectorValidationError("RRF records must contain a non-empty note_id")
            if note_id in seen:
                raise VectorValidationError(f"RRF ranked list contains a duplicate note_id: {note_id}")
            seen.add(note_id)
            item = fused.setdefault(note_id, {"note_id": note_id, "components": {}})
            contribution = selected_weights[name] / (k + rank)
            item["score"] = float(item.get("score", 0.0)) + contribution
            item["components"][name] = {
                "rank": rank,
                "weight": selected_weights[name],
                "contribution": contribution,
            }
    ordered = sorted(
        fused.values(),
        key=lambda item: (-float(item["score"]), str(item["note_id"])),
    )
    return {
        str(item["note_id"]): {
            **item,
            "rank": rank,
            "score": round(float(item["score"]), 12),
        }
        for rank, item in enumerate(ordered, start=1)
    }


def _chunk_sort_key(chunk: Any) -> tuple[Any, ...]:
    return (
        bool(chunk.locator != "/frontmatter"),
        _nfc(str(chunk.locator)).encode("utf-8"),
        str(chunk.chunk_id),
    )


def _chunk_vector(chunk: Any) -> tuple[float, ...]:
    values = [0.0] * VECTOR_DIMENSION
    fields = (
        (chunk.title_tokens, 3.0),
        (chunk.property_tokens, 1.5),
        (chunk.body_tokens, 1.0),
    )
    for tokens, weight in fields:
        for feature, count in _feature_counts(tokens).items():
            index, sign = _hash_feature(feature, dimension=VECTOR_DIMENSION, seed=VECTOR_SEED)
            values[index] += sign * float(count) * weight
    return _l2_normalize(values)


def _vector_metadata(score: float, rank: int | None) -> dict[str, Any]:
    return {
        "algorithm": "cosine_" + VECTOR_ALGORITHM,
        "score": round(float(score), 12),
        "rank": rank,
        "dimension": VECTOR_DIMENSION,
        "similarity": "cosine",
    }


def _embedding_metadata() -> dict[str, Any]:
    return {
        "provider": "local",
        "model": VECTOR_ALGORITHM,
        "dimension": VECTOR_DIMENSION,
        "artifact_digest": VECTOR_CONFIG_SHA256,
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


def _vector_candidates(
    notes: Sequence[Mapping[str, Any]],
    query: str,
    *,
    query_sha256: str,
    policy_decision_sha256: str,
    generation_id: str,
    retrieval_config_sha256: str,
    limit: int,
) -> list[dict[str, Any]]:
    from .retrieval import _candidate, _chunks_for_note

    query_vector = embed_text(query)
    scored: list[tuple[float, Any]] = []
    for note in notes:
        chunks = _chunks_for_note(note)
        best: tuple[float, Any] | None = None
        for chunk in chunks:
            score = cosine_similarity(query_vector, _chunk_vector(chunk))
            item = (score, chunk)
            if best is None or score > best[0] or (score == best[0] and _chunk_sort_key(chunk) < _chunk_sort_key(best[1])):
                best = item
        if best is not None:
            scored.append(best)
    ranked = sorted(
        scored,
        key=lambda item: (
            -item[0],
            _nfc(str(item[1].note["path"])).encode("utf-8"),
            str(item[1].note["id"]),
            _chunk_sort_key(item[1]),
        ),
    )
    result: list[dict[str, Any]] = []
    for rank, (score, chunk) in enumerate(ranked[:limit], start=1):
        candidate = _candidate(
            chunk,
            query_sha256=query_sha256,
            policy_decision_sha256=policy_decision_sha256,
            generation_id=generation_id,
            retrieval_config_sha256=retrieval_config_sha256,
            retrieval_reason="vector_match",
            lexical=_zero_lexical(),
            graph_path=[],
        )
        candidate["vector_score_and_rank"] = _vector_metadata(score, rank)
        candidate["embedding_provider_model_dimension_and_artifact_digest"] = _embedding_metadata()
        errors = sorted(
            Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate),
            key=lambda error: error.json_path,
        )
        if errors:
            raise VectorValidationError(f"E01 vector candidate is invalid: {errors[0].message}")
        result.append(candidate)
    return result


def _fuse_candidates(
    lexical: Sequence[Mapping[str, Any]],
    vector: Sequence[Mapping[str, Any]],
    *,
    query_sha256: str,
    policy_decision_sha256: str,
    generation_id: str,
    retrieval_config_sha256: str,
    limit: int,
) -> list[dict[str, Any]]:
    fused = reciprocal_rank_fusion(
        {"lexical": lexical, "vector": vector},
        k=DEFAULT_RRF_K,
        weights={"lexical": DEFAULT_LEXICAL_WEIGHT, "vector": DEFAULT_VECTOR_WEIGHT},
    )
    lexical_by_id = {str(item["note_id"]): item for item in lexical}
    vector_by_id = {str(item["note_id"]): item for item in vector}
    records: list[dict[str, Any]] = []
    for note_id, fusion in sorted(fused.items(), key=lambda item: int(item[1]["rank"])):
        source = lexical_by_id.get(note_id) or vector_by_id[note_id]
        candidate = dict(source)
        candidate["lexical_score_and_rank"] = dict(
            (lexical_by_id.get(note_id) or {"lexical_score_and_rank": _zero_lexical()})[
                "lexical_score_and_rank"
            ]
        )
        vector_record = vector_by_id.get(note_id)
        candidate["vector_score_and_rank"] = (
            dict(vector_record["vector_score_and_rank"]) if vector_record is not None else None
        )
        candidate["rrf_parameter_and_rank"] = {
            "algorithm": RRF_ALGORITHM,
            "k": DEFAULT_RRF_K,
            "score": fusion["score"],
            "rank": fusion["rank"],
            "weights": {
                "lexical": DEFAULT_LEXICAL_WEIGHT,
                "vector": DEFAULT_VECTOR_WEIGHT,
            },
            "components": fusion["components"],
        }
        reasons: list[str] = []
        if note_id in lexical_by_id:
            reasons.append("lexical_match")
        if note_id in vector_by_id:
            reasons.append("vector_match")
        reasons.append("rrf")
        candidate["retrieval_reason"] = ";".join(reasons)
        candidate["embedding_provider_model_dimension_and_artifact_digest"] = _embedding_metadata()
        records.append(candidate)
    return records[:limit]


def _validated_vector_options(
    *,
    limit: int,
    hops: int,
) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise VectorValidationError("limit must be an integer from 1 through 50")
    if isinstance(hops, bool) or not isinstance(hops, int) or not 0 <= hops <= 2:
        raise VectorValidationError("hops must be an integer from 0 through 2")
    return limit, hops


def _vector_projection(
    projection: ProjectionRead,
    query: str,
    *,
    policy: Mapping[str, Any],
    retrieval_config_sha256: str,
    scope: str | None,
    path_prefix: str | None,
    include_types: Sequence[str] | None,
    include_review: bool,
    limit: int,
    hops: int,
    fuse: bool,
) -> dict[str, Any]:
    from .retrieval import (
        _canonical_query,
        _expand_typed_links,
        _filter_notes,
        _lexical_candidates,
        _normalize_path_prefix,
        _normalize_scope,
        _policy_decision_hash,
    )

    retrieval = _as_mapping(policy.get("retrieval"), "policy retrieval") if "retrieval" in policy else policy
    _validate_contract(retrieval)
    normalized_query, query_sha256, _terms = _canonical_query(query)
    normalized_limit, normalized_hops = _validated_vector_options(limit=limit, hops=hops)
    included, excluded, selected_types, normalized_review, normalized_limit, normalized_hops = _filter_notes(
        projection,
        retrieval,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=normalized_limit,
        hops=normalized_hops,
    )
    normalized_scope = _normalize_scope(scope)
    normalized_prefix = _normalize_path_prefix(path_prefix)
    policy_hash = _policy_decision_hash(
        retrieval,
        projection,
        excluded,
        included,
        scope=normalized_scope,
        path_prefix=normalized_prefix,
        include_types=selected_types,
        include_review=normalized_review,
        limit=normalized_limit,
        hops=normalized_hops,
        retrieval_config_sha256=retrieval_config_sha256,
    )
    generation_id = str(projection.manifest["generation_id"])
    vector_limit = max(normalized_limit, 50 if fuse else normalized_limit)
    vector = _vector_candidates(
        included,
        normalized_query,
        query_sha256=query_sha256,
        policy_decision_sha256=policy_hash,
        generation_id=generation_id,
        retrieval_config_sha256=retrieval_config_sha256,
        limit=vector_limit,
    )
    lexical: list[dict[str, Any]] = []
    if fuse:
        lexical = _lexical_candidates(
            included,
            normalized_query,
            query_sha256,
            _terms,
            generation_id,
            policy_hash,
            retrieval_config_sha256,
        )
        candidates = _fuse_candidates(
            lexical,
            vector,
            query_sha256=query_sha256,
            policy_decision_sha256=policy_hash,
            generation_id=generation_id,
            retrieval_config_sha256=retrieval_config_sha256,
            limit=normalized_limit,
        )
        candidates = _expand_typed_links(
            projection,
            candidates,
            included,
            retrieval,
            hops=normalized_hops,
            query_sha256=query_sha256,
            policy_decision_sha256=policy_hash,
            generation_id=generation_id,
            retrieval_config_sha256=retrieval_config_sha256,
        )[:normalized_limit]
    else:
        candidates = vector[:normalized_limit]
    return {
        "query_sha256": query_sha256,
        "policy_decision_sha256": policy_hash,
        "index_generation_id": generation_id,
        "retrieval_config_sha256": retrieval_config_sha256,
        "vector_config_sha256": VECTOR_CONFIG_SHA256,
        "vector_enabled": True,
        "retrieval_mode": "vector_rrf" if fuse else "vector_only",
        "candidates": candidates,
        "filters": {
            "scope": normalized_scope,
            "path_prefix": normalized_prefix,
            "include_types": list(selected_types),
            "include_review": normalized_review,
            "included_note_count": len(included),
            "excluded_note_count": len(excluded),
            "excluded": excluded,
            "journal_requires_explicit_scope": bool(
                _as_mapping(retrieval["corpus"], "/retrieval/corpus")["journal_requires_explicit_scope"]
            ),
        },
        "provider_called": False,
        "mutation_performed": False,
    }


def vector_search_projection(
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
) -> dict[str, Any]:
    """Run pure vector retrieval over a pinned C21 projection."""

    _validate_digest(retrieval_config_sha256, "retrieval_config_sha256")
    return _vector_projection(
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
        fuse=False,
    )


def vector_retrieve_projection(
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
) -> dict[str, Any]:
    """Run pure local vector plus RRF and bounded typed-link retrieval."""

    _validate_digest(retrieval_config_sha256, "retrieval_config_sha256")
    return _vector_projection(
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
        fuse=True,
    )


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise VectorValidationError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise VectorValidationError("KnowledgeHub must be an existing non-symlink directory")
    return workspace


def _load_vector_contract(workspace: Path) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    from .retrieval import _load_contract

    policy, retrieval, retrieval_config_sha256 = _load_contract(workspace)
    _validate_contract(retrieval)
    return policy, retrieval, retrieval_config_sha256


def _failure(operation: str, code: str, message: str, exit_code: int) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": CAPABILITY,
        "vector_enabled": True,
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, exit_code


def _run_vector(
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
    fuse: bool,
) -> tuple[dict[str, Any], int]:
    try:
        workspace = _workspace(root)
        if expected_generation_id is not None and (
            not isinstance(expected_generation_id, str) or not _GENERATION_ID_RE.fullmatch(expected_generation_id)
        ):
            raise VectorValidationError("expected_generation_id is unsafe")
    except VectorValidationError as error:
        return _failure(operation, "E01_INPUT_INVALID", str(error), EXIT_INPUT_INVALID)
    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        code = "E01_INDEX_STALE" if any(token in message.casefold() for token in ("stale", "digest", "source changed")) else "E01_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(operation, "E01_GENERATION_MISMATCH", "current projection generation does not match expected_generation_id", EXIT_CONFLICT)
    try:
        policy, _retrieval, retrieval_config_sha256 = _load_vector_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise VectorConflict("pinned projection retrieval policy digest does not match the current policy")
        result = (
            vector_retrieve_projection if fuse else vector_search_projection
        )(
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
        )
    except VectorConflict as error:
        return _failure(operation, "E01_POLICY_STALE", str(error), EXIT_CONFLICT)
    except (VectorValidationError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "E01_CONTRACT_INVALID", str(error), EXIT_CONFLICT)
    return {
        **result,
        "status": "PASS",
        "operation": operation,
        "capability": CAPABILITY,
        "candidate_count": len(result["candidates"]),
        "source_verified": True,
    }, EXIT_OK


def vector_search(
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
) -> tuple[dict[str, Any], int]:
    """Run explicit local vector-only retrieval."""

    return _run_vector(
        "vector search",
        root,
        query,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        expected_generation_id=expected_generation_id,
        fuse=False,
    )


def vector_retrieve(
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
) -> tuple[dict[str, Any], int]:
    """Run explicit local vector/RRF retrieval plus typed-link expansion."""

    return _run_vector(
        "vector retrieve",
        root,
        query,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        expected_generation_id=expected_generation_id,
        fuse=True,
    )


def vector_evaluation_schema() -> dict[str, Any]:
    """Return the schema for the deterministic E01 evaluation report."""

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
        "required": ["id", "mode", "lexical", "vector", "rrf", "passed"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "mode": {"enum": ["search", "retrieve"]},
            "lexical": ranking,
            "vector": ranking,
            "rrf": ranking,
            "passed": {"type": "boolean"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/e01-vector-evaluation.schema.json",
        "title": "KnowledgeOS E01 local vector and RRF evaluation",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "operation",
            "capability",
            "baseline_sha256",
            "index_generation_id",
            "retrieval_config_sha256",
            "vector_config_sha256",
            "cases",
            "metrics",
            "source_verified",
            "provider_called",
            "mutation_performed",
        ],
        "properties": {
            "status": {"enum": ["PASS", "FAIL"]},
            "operation": {"const": "vector evaluate"},
            "capability": {"const": CAPABILITY},
            "baseline_sha256": sha,
            "index_generation_id": {"type": "string", "minLength": 1},
            "retrieval_config_sha256": sha,
            "vector_config_sha256": sha,
            "cases": {"type": "array", "minItems": 1, "items": case},
            "metrics": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "case_count",
                    "passed_case_count",
                    "all_cases_passed",
                    "lexical_mean_expected_path_recall",
                    "vector_mean_expected_path_recall",
                    "rrf_mean_expected_path_recall",
                    "lexical_mean_reciprocal_rank",
                    "vector_mean_reciprocal_rank",
                    "rrf_mean_reciprocal_rank",
                    "rrf_improves_baseline",
                    "require_improvement",
                    "promotion_gate_passed",
                    "promotion_decision",
                ],
                "properties": {
                    "case_count": {"type": "integer", "minimum": 1},
                    "passed_case_count": {"type": "integer", "minimum": 0},
                    "all_cases_passed": {"type": "boolean"},
                    "lexical_mean_expected_path_recall": metric,
                    "vector_mean_expected_path_recall": metric,
                    "rrf_mean_expected_path_recall": metric,
                    "lexical_mean_reciprocal_rank": metric,
                    "vector_mean_reciprocal_rank": metric,
                    "rrf_mean_reciprocal_rank": metric,
                    "rrf_improves_baseline": {"type": "boolean"},
                    "require_improvement": {"type": "boolean"},
                    "promotion_gate_passed": {"type": "boolean"},
                    "promotion_decision": {"enum": ["promote_optional_rrf", "retain_lexical_baseline"]},
                },
            },
            "source_verified": {"const": True},
            "provider_called": {"const": False},
            "mutation_performed": {"const": False},
        },
    }


def _safe_control_file(workspace: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        requested = candidate
        try:
            relative = requested.relative_to(workspace)
        except ValueError as error:
            raise VectorValidationError("E01 baseline must be inside the control root") from error
    else:
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise VectorValidationError("E01 baseline path contains unsafe components")
        relative = candidate
        requested = workspace / candidate
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise VectorValidationError("E01 baseline path must not traverse a symlink")
    resolved = requested.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise VectorValidationError("E01 baseline path escapes the control root") from error
    if not resolved.is_file() or resolved.is_symlink():
        raise VectorValidationError("E01 baseline must be a regular file")
    return resolved


def _load_baseline(workspace: Path, baseline_path: str | Path | None) -> tuple[Mapping[str, Any], str]:
    path = _safe_control_file(workspace, baseline_path or DEFAULT_BASELINE_PATH)
    raw = path.read_bytes()
    try:
        value = load_yaml_text(raw.decode("utf-8"))
    except (UnicodeError, ValueError, YAMLError) as error:
        raise VectorValidationError(f"E01 baseline YAML is invalid: {error}") from error
    baseline = _as_mapping(value, "E01 baseline")
    _expect(set(baseline), {"schema_version", "capability", "profile", "vector", "queries"}, "baseline")
    _expect(baseline.get("schema_version"), 1, "/schema_version")
    _expect(baseline.get("capability"), CAPABILITY, "/capability")
    _expect(baseline.get("profile"), "portable_core", "/profile")
    vector = _as_mapping(baseline.get("vector"), "/vector")
    _expect(
        set(vector),
        {
            "algorithm",
            "dimension",
            "seed",
            "rrf_k",
            "lexical_weight",
            "vector_weight",
            "require_improvement",
        },
        "/vector",
    )
    expected = vector_contract()
    _expect(vector["algorithm"], expected["algorithm"], "/vector/algorithm")
    _expect(vector["dimension"], expected["dimension"], "/vector/dimension")
    _expect(vector["seed"], expected["seed"], "/vector/seed")
    _expect(vector["rrf_k"], expected["rrf"]["k"], "/vector/rrf_k")
    _expect(vector["lexical_weight"], expected["rrf"]["lexical_weight"], "/vector/lexical_weight")
    _expect(vector["vector_weight"], expected["rrf"]["vector_weight"], "/vector/vector_weight")
    if not isinstance(vector["require_improvement"], bool):
        raise VectorValidationError("/vector/require_improvement must be boolean")
    queries = _as_list(baseline.get("queries"), "/queries")
    if not queries:
        raise VectorValidationError("E01 baseline must contain at least one query")
    return baseline, _sha256_bytes(raw)


def _ranking_metrics(candidates: Sequence[Mapping[str, Any]], expected_paths: Sequence[Any]) -> dict[str, Any]:
    paths = [str(candidate["path"]) for candidate in candidates]
    expected = [str(path) for path in expected_paths]
    found = sum(path in paths for path in dict.fromkeys(expected))
    first = None
    if expected:
        try:
            first = paths.index(expected[0]) + 1
        except ValueError:
            first = None
    return {
        "candidate_paths": paths,
        "expected_path_recall": round(found / len(dict.fromkeys(expected)), 12) if expected else 1.0,
        "reciprocal_rank": round(0.0 if first is None else 1.0 / first, 12),
    }


def _evaluate_case(
    projection: ProjectionRead,
    policy: Mapping[str, Any],
    retrieval_config_sha256: str,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
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
    if set(case) != required:
        raise VectorValidationError("E01 baseline query has unexpected or missing fields")
    case_id = case["id"]
    if not isinstance(case_id, str) or not case_id:
        raise VectorValidationError("E01 baseline query id must be non-empty text")
    mode = case["mode"]
    if mode not in {"search", "retrieve"}:
        raise VectorValidationError(f"unsupported E01 baseline mode: {mode}")
    if not isinstance(case["query"], str):
        raise VectorValidationError(f"E01 baseline query {case_id} must contain text")
    expected = _as_list(case["expected_prefix_paths"], f"{case_id}.expected_prefix_paths")
    required_paths = _as_list(case["required_paths"], f"{case_id}.required_paths")
    kwargs = {
        "scope": case["scope"],
        "path_prefix": case["path_prefix"],
        "include_types": case["include_types"],
        "include_review": case["include_review"],
        "limit": case["limit"],
        "hops": case["hops"],
    }
    from .retrieval import retrieve_projection, search_projection

    lexical = (search_projection if mode == "search" else retrieve_projection)(
        projection,
        case["query"],
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        **kwargs,
    )
    vector = _vector_projection(
        projection,
        case["query"],
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        **kwargs,
        fuse=False,
    )
    rrf = _vector_projection(
        projection,
        case["query"],
        policy=policy,
        retrieval_config_sha256=retrieval_config_sha256,
        **kwargs,
        fuse=True,
    )
    def passed(result: Mapping[str, Any]) -> bool:
        paths = [str(item["path"]) for item in result["candidates"]]
        return paths[: len(expected)] == [str(path) for path in expected] and all(
            str(path) in paths for path in required_paths
        )

    return {
        "id": case_id,
        "mode": mode,
        "lexical": _ranking_metrics(lexical["candidates"], expected),
        "vector": _ranking_metrics(vector["candidates"], expected),
        "rrf": _ranking_metrics(rrf["candidates"], expected),
        "passed": passed(rrf),
    }


def evaluate_vector_baseline(
    root: str | Path,
    *,
    baseline_path: str | Path | None = None,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Evaluate local vector and RRF against a frozen C22-shaped baseline."""

    operation = "vector evaluate"
    try:
        workspace = _workspace(root)
        baseline, baseline_sha256 = _load_baseline(workspace, baseline_path)
        if expected_generation_id is not None and (
            not isinstance(expected_generation_id, str) or not _GENERATION_ID_RE.fullmatch(expected_generation_id)
        ):
            raise VectorValidationError("expected_generation_id is unsafe")
    except VectorValidationError as error:
        return _failure(operation, "E01_BASELINE_INVALID", str(error), EXIT_INPUT_INVALID)
    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        code = "E01_INDEX_STALE" if any(token in message.casefold() for token in ("stale", "digest", "source changed")) else "E01_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(operation, "E01_GENERATION_MISMATCH", "current projection generation does not match expected_generation_id", EXIT_CONFLICT)
    try:
        policy, _retrieval, retrieval_config_sha256 = _load_vector_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise VectorConflict("pinned projection retrieval policy digest does not match the current policy")
        cases = [
            _evaluate_case(projection, policy, retrieval_config_sha256, _as_mapping(item, "E01 baseline query"))
            for item in _as_list(baseline["queries"], "/queries")
        ]
    except VectorConflict as error:
        return _failure(operation, "E01_POLICY_STALE", str(error), EXIT_CONFLICT)
    except (VectorValidationError, KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "E01_EVALUATION_INVALID", str(error), EXIT_CONFLICT)
    case_count = len(cases)
    passed_count = sum(1 for case in cases if case["passed"])
    def mean(path: Sequence[str], branch: str) -> float:
        return round(sum(float(case[branch][path[0]]) for case in cases) / case_count, 12)

    lexical_recall = mean(["expected_path_recall"], "lexical")
    vector_recall = mean(["expected_path_recall"], "vector")
    rrf_recall = mean(["expected_path_recall"], "rrf")
    lexical_rr = mean(["reciprocal_rank"], "lexical")
    vector_rr = mean(["reciprocal_rank"], "vector")
    rrf_rr = mean(["reciprocal_rank"], "rrf")
    improves = rrf_recall > lexical_recall or rrf_rr > lexical_rr
    require_improvement = bool(_as_mapping(baseline["vector"], "/vector")["require_improvement"])
    promotion_gate_passed = not require_improvement or improves
    metrics = {
        "case_count": case_count,
        "passed_case_count": passed_count,
        "all_cases_passed": passed_count == case_count,
        "lexical_mean_expected_path_recall": lexical_recall,
        "vector_mean_expected_path_recall": vector_recall,
        "rrf_mean_expected_path_recall": rrf_recall,
        "lexical_mean_reciprocal_rank": lexical_rr,
        "vector_mean_reciprocal_rank": vector_rr,
        "rrf_mean_reciprocal_rank": rrf_rr,
        "rrf_improves_baseline": improves,
        "require_improvement": require_improvement,
        "promotion_gate_passed": promotion_gate_passed,
        "promotion_decision": "promote_optional_rrf" if improves else "retain_lexical_baseline",
    }
    report = {
        "status": "PASS" if metrics["all_cases_passed"] and promotion_gate_passed else "FAIL",
        "operation": operation,
        "capability": CAPABILITY,
        "baseline_sha256": baseline_sha256,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "vector_config_sha256": VECTOR_CONFIG_SHA256,
        "cases": cases,
        "metrics": metrics,
        "source_verified": True,
        "provider_called": False,
        "mutation_performed": False,
    }
    errors = sorted(Draft202012Validator(vector_evaluation_schema()).iter_errors(report), key=lambda error: error.json_path)
    if errors:
        return _failure(operation, "E01_REPORT_INVALID", errors[0].message, EXIT_CONFLICT)
    return report, EXIT_OK if report["status"] == "PASS" else EXIT_CONFLICT


evaluate = evaluate_vector_baseline
evaluate_frozen_baseline = evaluate_vector_baseline
vector = vector_retrieve
retrieve = vector_retrieve
search = vector_search


__all__ = [
    "CAPABILITY",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_LIMIT",
    "DEFAULT_RRF_K",
    "RRF_ALGORITHM",
    "VECTOR_ALGORITHM",
    "VECTOR_CONFIG_SHA256",
    "VECTOR_DIMENSION",
    "VECTOR_INDEXER_VERSION",
    "VECTOR_SEED",
    "VectorConflict",
    "VectorError",
    "VectorValidationError",
    "cosine_similarity",
    "embed_text",
    "embed_tokens",
    "evaluate",
    "evaluate_frozen_baseline",
    "evaluate_vector_baseline",
    "reciprocal_rank_fusion",
    "vector",
    "vector_config_sha256",
    "vector_contract",
    "vector_evaluation_schema",
    "vector_retrieve",
    "vector_retrieve_projection",
    "vector_search",
    "vector_search_projection",
]

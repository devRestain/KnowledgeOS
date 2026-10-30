"""Optional local learned retrieval and approved one-hop ontology search.

The embedding port is injected by a trusted owner composition. No model is
installed, downloaded or started by this module or by the MCP server.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .adapters.core import AdmissionError, canonical, digest
from .adapters.owner_journal import fsync_directory
from .projection import ProjectionError, ProjectionRead, build_projection, load_current_projection
from .retrieval import (
    RetrievalValidationError,
    _candidate,
    _canonical_query,
    _chunks_for_note,
    _filter_notes,
    _load_contract,
    _representative_chunk,
    search_projection,
)

_INDEX_ROOT = "index/vector"
_PARSER = "markdown-headings-v1"
_INDEXER = "knowledgeos.vector.v1"
_RRF_K = 60


@dataclass(frozen=True)
class ModelPin:
    model_id: str
    artifact_digest: str
    dimension: int
    query_format: str
    document_format: str

    def validate(self) -> None:
        if (not self.model_id or re.fullmatch(r"sha256:[0-9a-f]{64}", self.artifact_digest) is None
                or not 1 <= self.dimension <= 4096
                or self.query_format.count("{text}") != 1
                or self.document_format.count("{text}") != 1
                or len(self.query_format) > 256 or len(self.document_format) > 256):
            raise AdmissionError("MODEL_PIN_INVALID", "Exact local model pin is required")


class EmbeddingPort(Protocol):
    pin: ModelPin
    trusted_local: bool

    def embed_document(self, text: str) -> Sequence[float]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


def _vector(value: Sequence[float], dimension: int, *, normalize: bool = True) -> list[float]:
    if (len(value) != dimension or any(not isinstance(x, (float, int))
                                       or not math.isfinite(x) for x in value)):
        raise AdmissionError("EMBEDDING_INVALID", "Model returned an invalid vector")
    result = [float(x) for x in value]
    norm = math.sqrt(sum(x * x for x in result))
    if norm <= 0:
        raise AdmissionError("EMBEDDING_INVALID", "Zero embedding is unavailable")
    return [x / norm for x in result] if normalize else result


def _path(root: Path, relative: str) -> Path:
    result = root / relative
    if result.is_symlink() or any(parent.is_symlink() for parent in result.parents if parent != root):
        raise AdmissionError("PATH_DENIED", "Vector index crosses a symlink")
    return result


def _read_blob(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 268435456:
            raise AdmissionError("INDEX_INVALID", "Vector record file is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".vector-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        fsync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _private_parent(root: Path, path: Path) -> None:
    current = root
    if current.is_symlink() or not current.is_dir() or stat.S_IMODE(current.stat().st_mode) != 0o700:
        raise AdmissionError("PATH_DENIED", "Vector runtime root must be private")
    for part in path.parent.relative_to(root).parts:
        current = current / part
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        if (current.is_symlink() or not current.is_dir()
                or stat.S_IMODE(current.stat().st_mode) != 0o700):
            raise AdmissionError("PATH_DENIED", "Vector generation directory must be private")


def _eligible_notes(projection: ProjectionRead, policy: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    # The embedding corpus includes admitted capture and journal notes; the
    # default search query still follows the existing seven-type policy.
    included = []
    for note in projection.notes:
        properties = note["properties"]
        path = str(note["path"])
        if (path in {"Home.md", "Mobile.md"}
                or path.startswith(("99_System/", "01_AI_Review/"))
                or properties.get("sensitivity") == "confidential"
                or properties.get("ai_policy") not in {"ask", "remote_ok", "local_only"}
                or note["type"] in {"home", "system", "proposal"}):
            continue
        included.append(note)
    return included


def _read_index(runtime: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = _read_blob(_path(runtime, f"{_INDEX_ROOT}/current.json"))
    if raw is None:
        raise AdmissionError("INDEX_UNAVAILABLE", "Vector generation is not configured")
    try:
        pointer = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise AdmissionError("INDEX_INVALID", "Vector pointer cannot be parsed") from error
    if not isinstance(pointer, dict):
        raise AdmissionError("INDEX_INVALID", "Vector pointer is invalid")
    generation = pointer.get("generation_id")
    if (not isinstance(generation, str) or not generation.startswith("vec-")
            or len(generation) != 68):
        raise AdmissionError("INDEX_INVALID", "Vector pointer is invalid")
    payload = _read_blob(_path(runtime, f"{_INDEX_ROOT}/generations/{generation}/records.json"))
    if payload is None or digest(payload) != pointer.get("records_digest"):
        raise AdmissionError("INDEX_STALE", "Vector record digest differs from pointer")
    try:
        records = json.loads(payload)
    except (UnicodeError, ValueError) as error:
        raise AdmissionError("INDEX_INVALID", "Vector records cannot be parsed") from error
    if not isinstance(records, list):
        raise AdmissionError("INDEX_INVALID", "Vector records are invalid")
    expected = {"note_id", "path", "content_hash", "chunk_id", "chunk_hash",
                "chunk_locator", "vector"}
    if any(not isinstance(row, dict) or set(row) != expected
           or any(not isinstance(row[key], str) or not row[key]
                  for key in expected - {"vector"})
           or not isinstance(row["vector"], list) for row in records):
        raise AdmissionError("INDEX_INVALID", "Vector record shape is invalid")
    return pointer, records


def _policy_contract(roots: Any) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    try:
        return _load_contract(roots)
    except RetrievalValidationError as error:
        raise AdmissionError("INDEX_STALE", "Retrieval policy requires regeneration") from error


def _current_projection(roots: Any) -> ProjectionRead:
    try:
        return load_current_projection(roots, verify_sources=True)
    except ProjectionError as error:
        raise AdmissionError("INDEX_STALE", "Lexical source generation requires rebuilding") from error


def build_vector_index(roots: Any, port: EmbeddingPort,
                       *, projection: ProjectionRead | None = None) -> dict[str, Any]:
    """Build a disposable immutable candidate and promote its exact pointer."""
    port.pin.validate()
    if getattr(port, "trusted_local", False) is not True:
        raise AdmissionError("SCOPE_DENIED", "Embedding requires a trusted local service binding")
    selected = projection or _current_projection(roots)
    _, policy, policy_digest = _policy_contract(roots)
    model = vars(port.pin)
    previous = []
    try:
        old, previous = _read_index(roots.runtime)
        if (old.get("model") != model or old.get("retrieval_config_sha256") != policy_digest
                or old.get("parser") != _PARSER or old.get("indexer") != _INDEXER):
            previous = []
    except AdmissionError:
        pass
    reuse = {(row["chunk_hash"], row["chunk_locator"]): row["vector"] for row in previous}
    records = []
    reused = 0
    for note in _eligible_notes(selected, policy):
        for chunk in _chunks_for_note(note):
            if chunk.locator == "/frontmatter" or not chunk.text.strip():
                continue
            key = (chunk.chunk_hash, chunk.locator)
            if key in reuse:
                # Preserve stored normalized bytes: normalizing again can
                # round differently and change an otherwise identical generation.
                vector = _vector(reuse[key], port.pin.dimension, normalize=False)
                reused += 1
            else:
                try:
                    embedded = port.embed_document(
                        port.pin.document_format.replace("{text}", chunk.text))
                except Exception as error:
                    raise AdmissionError("EMBEDDING_UNAVAILABLE", "Local document embedding failed") from error
                vector = _vector(embedded, port.pin.dimension)
            records.append({"note_id": note["id"], "path": note["path"],
                            "content_hash": note["content_hash"], "chunk_id": chunk.chunk_id,
                            "chunk_hash": chunk.chunk_hash, "chunk_locator": chunk.locator,
                            "vector": vector})
    payload = canonical(records) + b"\n"
    if len(payload) > 268435456:
        raise AdmissionError("INDEX_CAPACITY", "Vector candidate exceeds the bounded index size")
    seed = {"projection_generation_id": selected.manifest["generation_id"],
            "source_snapshot_sha256": selected.manifest["source_snapshot_sha256"],
            "retrieval_config_sha256": policy_digest, "model": model,
            "parser": _PARSER, "indexer": _INDEXER, "records_digest": digest(payload)}
    generation = "vec-" + hashlib.sha256(canonical(seed)).hexdigest()
    pointer = {**seed, "generation_id": generation}
    path = _path(roots.runtime, f"{_INDEX_ROOT}/generations/{generation}/records.json")
    _private_parent(roots.runtime, path)
    if path.exists():
        if _read_blob(path) != payload:
            raise AdmissionError("INDEX_CONFLICT", "Immutable generation bytes differ")
    else:
        _write_atomic(path, payload)
    # Recheck the source snapshot before selecting this immutable generation.
    if projection is None:
        observed_manifest = _current_projection(roots).manifest
    else:
        try:
            observed_manifest = build_projection(roots).manifest
        except ProjectionError as error:
            raise AdmissionError("INDEX_STALE", "Source changed during vector candidate build") from error
    if (observed_manifest != selected.manifest
            or _policy_contract(roots)[2] != policy_digest):
        raise AdmissionError("INDEX_STALE", "Source changed while vector index was built")
    pointer_path = _path(roots.runtime, f"{_INDEX_ROOT}/current.json")
    _private_parent(roots.runtime, pointer_path)
    _write_atomic(pointer_path, canonical(pointer) + b"\n")
    return {"generation_id": generation, "record_count": len(records),
            "reused_vectors": reused, "new_vectors": len(records) - reused,
            "projection_generation_id": selected.manifest["generation_id"]}


def index_status(roots: Any, port: EmbeddingPort | None = None) -> dict[str, Any]:
    try:
        projection = _current_projection(roots)
    except AdmissionError:
        return {"lexical_generation_id": None, "lexical_state": "rebuild_required",
                "vector_state": "rebuild_required"}
    status = {"lexical_generation_id": projection.manifest["generation_id"],
              "lexical_state": "ready", "vector_state": "unavailable"}
    try:
        pointer, _ = _read_index(roots.runtime)
        status["vector_generation_id"] = pointer["generation_id"]
        status["vector_state"] = ("ready" if (
            pointer["projection_generation_id"] == projection.manifest["generation_id"]
            and pointer["source_snapshot_sha256"] == projection.manifest["source_snapshot_sha256"]
            and pointer["retrieval_config_sha256"] == _policy_contract(roots)[2]
            and pointer["parser"] == _PARSER and pointer["indexer"] == _INDEXER
            and port is not None and getattr(port, "trusted_local", False) is True
            and pointer["model"] == vars(port.pin)) else "rebuild_required")
    except AdmissionError as error:
        status["vector_state"] = ("unavailable" if error.code == "INDEX_UNAVAILABLE"
                                  else "rebuild_required")
    return status


def _search_ontology(application: Any, projection: ProjectionRead, query: str,
                     included: Sequence[Mapping[str, Any]], limit: int,
                     policy_digest: str) -> list[dict[str, Any]]:
    allowed = {str(note["id"]): note for note in included}
    pending = application.daily_stewardship().pending_relation_fields()
    results = []
    normalized = query.casefold().strip()
    for edge in projection.edges:
        source = allowed.get(str(edge["subject_id"]))
        target = allowed.get(str(edge["object_id"]))
        if (source is None or target is None
                or (str(edge["subject_path"]), str(edge["predicate"])) in pending):
            continue
        if not any(normalized in str(value).casefold() for value in (
                edge["predicate"], source["title"], target["title"], source["type"], target["type"])):
            continue
        chunk = _representative_chunk(target)
        row = _candidate(chunk, query_sha256=_canonical_query(query)[1],
                         policy_decision_sha256=policy_digest,
                         generation_id=str(projection.manifest["generation_id"]),
                         retrieval_config_sha256=str(projection.manifest["retrieval_config_sha256"]),
                         retrieval_reason="approved_ontology_edge", lexical={},
                         graph_path=[f"{source['id']}:{edge['predicate']}:{target['id']}"])
        results.append(row)
        if len(results) >= limit:
            break
    return results


def search_mode(application: Any, query: str, mode: str, *, scope: str | None = None,
                path_prefix: str | None = None, include_types: Sequence[str] | None = None,
                limit: int = 10, expected_generation_id: str | None = None) -> tuple[dict[str, Any], int]:
    if mode not in {"vector", "hybrid", "ontology"}:
        raise AdmissionError("INVALID_ARGUMENT", "Unknown search mode")
    roots = application.roots
    projection = _current_projection(roots)
    if expected_generation_id is not None and expected_generation_id != projection.manifest["generation_id"]:
        raise AdmissionError("INDEX_STALE", "Requested generation is no longer current")
    _, policy, policy_digest = _policy_contract(roots)
    included, *_ = _filter_notes(projection, policy, scope=scope,
                                 path_prefix=path_prefix, include_types=include_types,
                                 include_review=False, limit=limit, hops=0)
    query_hash = _canonical_query(query)[1]
    if mode == "ontology":
        candidates = _search_ontology(application, projection, query, included, limit, policy_digest)
        vector_generation = None
    else:
        port = application.handlers.vector_port
        if port is None or getattr(port, "trusted_local", False) is not True:
            raise AdmissionError("INDEX_UNAVAILABLE", "Trusted local embedding port is not bound")
        port.pin.validate()
        pointer, records = _read_index(roots.runtime)
        if (pointer.get("model") != vars(port.pin)
                or pointer.get("projection_generation_id") != projection.manifest["generation_id"]
                or pointer.get("source_snapshot_sha256") != projection.manifest["source_snapshot_sha256"]
                or pointer.get("retrieval_config_sha256") != policy_digest):
            raise AdmissionError("INDEX_STALE", "Vector generation requires rebuilding")
        if pointer.get("parser") != _PARSER or pointer.get("indexer") != _INDEXER:
            raise AdmissionError("INDEX_STALE", "Vector chunker or indexer changed")
        expected = {
            (str(note["path"]), chunk.chunk_id, chunk.chunk_hash, note["content_hash"])
            for note in _eligible_notes(projection, policy)
            for chunk in _chunks_for_note(note)
            if chunk.locator != "/frontmatter" and chunk.text.strip()
        }
        observed = {(str(row.get("path")), row.get("chunk_id"), row.get("chunk_hash"),
                     row.get("content_hash")) for row in records}
        if len(observed) != len(records) or observed != expected:
            raise AdmissionError("INDEX_STALE", "Vector records differ from the current source corpus")
        vector_generation = pointer["generation_id"]
        try:
            embedded_query = port.embed_query(port.pin.query_format.replace("{text}", query))
        except Exception as error:
            raise AdmissionError("EMBEDDING_UNAVAILABLE", "Local query embedding failed") from error
        qvec = _vector(embedded_query, port.pin.dimension)
        allowed = {str(note["path"]): note for note in included}
        chunks = {(str(note["path"]), chunk.chunk_id): chunk for note in included
                  for chunk in _chunks_for_note(note)}
        scored = []
        for record in records:
            note = allowed.get(str(record["path"]))
            chunk = chunks.get((str(record["path"]), record["chunk_id"]))
            if note is None or chunk is None or record["content_hash"] != note["content_hash"] \
                    or record["chunk_hash"] != chunk.chunk_hash:
                continue
            scored.append((sum(a * b for a, b in zip(qvec, _vector(record["vector"], port.pin.dimension), strict=True)), chunk))
        scored.sort(key=lambda item: (-item[0], item[1].note["path"], item[1].chunk_id))
        vector_candidates = []
        for rank, (score, chunk) in enumerate(scored[: max(limit, 20)], 1):
            row = _candidate(chunk, query_sha256=query_hash,
                             policy_decision_sha256=policy_digest,
                             generation_id=str(projection.manifest["generation_id"]),
                             retrieval_config_sha256=policy_digest,
                             retrieval_reason="vector_match", lexical={}, graph_path=[])
            row["vector_score_and_rank"] = {"score": score, "rank": rank}
            row["embedding_provider_model_dimension_and_artifact_digest"] = vars(port.pin)
            vector_candidates.append(row)
        if mode == "vector":
            candidates = vector_candidates[:limit]
        else:
            lexical = search_projection(projection, query, policy=policy,
                                        retrieval_config_sha256=policy_digest, scope=scope,
                                        path_prefix=path_prefix, include_types=include_types,
                                        limit=max(limit, 20), hops=0)["candidates"]
            by_key = {(row["note_id"], row["chunk_id"]): row for row in lexical}
            ranks: dict[tuple[str, str], float] = {}
            for rank, row in enumerate(lexical, 1):
                ranks[(row["note_id"], row["chunk_id"])] = 1 / (_RRF_K + rank)
            for rank, row in enumerate(vector_candidates, 1):
                key = (row["note_id"], row["chunk_id"])
                ranks[key] = ranks.get(key, 0) + 1 / (_RRF_K + rank)
                if key in by_key:
                    by_key[key]["vector_score_and_rank"] = row["vector_score_and_rank"]
                    by_key[key]["embedding_provider_model_dimension_and_artifact_digest"] = vars(port.pin)
                else:
                    by_key[key] = row
            candidates = []
            for rank, (key, score) in enumerate(sorted(ranks.items(), key=lambda item: (-item[1], item[0]))[:limit], 1):
                row = by_key[key]
                row["rrf_parameter_and_rank"] = {"k": _RRF_K, "score": score, "rank": rank}
                row["retrieval_reason"] = "hybrid_rrf"
                candidates.append(row)
    report = {"status": "PASS", "query_sha256": query_hash,
              "policy_decision_sha256": policy_digest,
              "index_generation_id": projection.manifest["generation_id"],
              "retrieval_config_sha256": policy_digest,
              "candidate_count": len(candidates), "candidates": candidates,
              "mode": mode, "vector_generation_id": vector_generation,
              "source_verified": True, "provider_called": mode in {"vector", "hybrid"},
              "mutation_performed": False}
    return report, 0

"""Deterministic, provider-free C23 cited answers.

The answer boundary consumes one reader-pinned C21 projection and the
provider-free C22 retrieval result.  It produces an extractive answer whose
citations point at the selected projection chunks.  No provider, Vault file,
runtime artifact, or Git repository is written here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from yaml import YAMLError

from .note_engine import NoteContractError, NoteEngine, UnsafePathError, resolve_vault_relative_path
from .projection import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    ProjectionError,
    ProjectionRead,
    answer_schema,
    canonical_json_bytes,
    load_current_projection,
)
from .retrieval import (
    RetrievalConflict,
    RetrievalValidationError,
    _chunks_for_note,
    _load_contract,
    _normalized_options,
    _revalidate_candidates,
    retrieve_projection,
)
from .retrieval import (
    _workspace as retrieval_workspace,
)
from .yaml_safe import load_yaml_text

DEFAULT_BASELINE_PATH = "ops/tests/fixtures/c23_answers/evaluation.yaml"
DEFAULT_LIMIT = 5
SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_LINE_BREAKS = ("\r", "\n", "\v", "\f", "\x85", "\u2028", "\u2029")
_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_COMMAND_URI_RE = re.compile(r"\b(?:obsidian|command):[^\s)]+", re.IGNORECASE)
_ACTIVE_EMBED_RE = re.compile(r"!\[\[([^\]\n]+)\]\]")
_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BLOCK_ID_RE = re.compile(r"\s*\^[-A-Za-z0-9_:.]+\s*$")

_RELATION_PROPERTIES = frozenset(
    {
        "areas",
        "derived_from",
        "explains",
        "implements",
        "people",
        "projects",
        "raises",
        "related",
        "sources",
        "supports",
        "topics",
    }
)
_PRIVACY_PROPERTIES = frozenset({"ai_policy", "ai_status", "sensitivity"})
_PREFERRED_PROPERTIES: dict[str, tuple[str, ...]] = {
    "artifact": ("artifact_kind", "artifact_uri", "citation_key"),
    "idea": ("possibility",),
    "knowledge": ("claim", "confidence"),
    "project": ("outcome", "next_action"),
    "project_note": (),
    "question": ("decision", "question_kind"),
    "source": ("source_kind", "citation_key"),
}


class AnswerError(ValueError):
    """Base error for a rejected or unavailable C23 answer input."""


class AnswerConflict(AnswerError):
    """Raised when a pinned answer input becomes stale."""


class AnswerValidationError(AnswerError):
    """Raised when an answer or its evidence violates the C23 contract."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnswerValidationError(f"{label} must be a mapping")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnswerValidationError(f"{label} must be a list")
    return value


def _nfc(value: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", value)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(_nfc(value)))


def _fold(value: str) -> str:
    return " ".join(_nfc(value).casefold().split())


def _safe_text(value: object, *, maximum: int = 1200) -> str:
    """Convert untrusted Markdown/source text to bounded plain text."""

    text = str(value)
    text = _ACTIVE_EMBED_RE.sub(lambda match: match.group(1), text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _WIKILINK_RE.sub(lambda match: match.group(1).split("|", 1)[-1], text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _COMMAND_URI_RE.sub("[command URI omitted]", text)
    text = _HTML_TAG_RE.sub("[markup omitted]", text)
    text = text.replace("<", " ").replace(">", " ")
    text = text.replace("\x00", " ")
    text = re.sub(r"[`*_#]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > maximum:
        text = text[: maximum - 1].rstrip() + "…"
    return text


def _query_terms(query: str) -> tuple[str, ...]:
    if not isinstance(query, str):
        raise AnswerValidationError("query must be text supplied through stdin or a file")
    value = _nfc(query).strip()
    if any(marker in value for marker in _LINE_BREAKS):
        raise AnswerValidationError("query must be one logical line")
    terms = _tokens(value)
    if not terms:
        raise AnswerValidationError("query must contain searchable Unicode letters or numbers")
    return terms


def _body_chunks(body: str) -> tuple[tuple[str, str], ...]:
    if not body.strip():
        return ()
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return (("/body", body.strip()),)
    chunks: list[tuple[str, str]] = []
    if matches[0].start() > 0 and body[: matches[0].start()].strip():
        chunks.append(("/body/preamble", body[: matches[0].start()].strip()))
    seen: dict[str, int] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        heading = _nfc(match.group(2).strip())
        seen[heading] = seen.get(heading, 0) + 1
        suffix = "" if seen[heading] == 1 else f"@{seen[heading]}"
        chunks.append((f"#{heading}{suffix}", body[match.start() : end].strip()))
    return tuple(chunks)


def _chunk_text(note: Mapping[str, Any], locator: str) -> str:
    if locator == "/frontmatter":
        return ""
    body = str(note.get("body", ""))
    for selected_locator, text in _body_chunks(body):
        if selected_locator == locator:
            return text
    return ""


def _sentences(text: str) -> list[str]:
    without_block_id = _BLOCK_ID_RE.sub("", text)
    without_headings = re.sub(r"(?m)^\s*#{1,6}[ \t]+[^\n]*(?:\n|$)", "", without_block_id)
    without_list_markers = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", without_headings)
    pieces = re.split(r"(?<=[.!?。！？])\s+|\n+", without_list_markers)
    return [piece.strip() for piece in pieces if piece.strip()]


def _best_sentence(text: str, terms: Sequence[str]) -> str:
    candidates = _sentences(text)
    if not candidates:
        return ""
    term_set = set(terms)

    def score(candidate: str) -> tuple[int, int, str]:
        candidate_terms = set(_tokens(candidate))
        overlap = sum(1 for term in term_set if term in candidate_terms or any(term in item for item in candidate_terms))
        return (-overlap, len(candidate), _fold(candidate))

    return min(candidates, key=score)


def _flatten_property(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            f"{key} {_flatten_property(value[key])}"
            for key in sorted(value, key=lambda item: _nfc(str(item)))
        )
    if isinstance(value, list | tuple):
        return " ".join(_flatten_property(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _property_evidence(
    note: Mapping[str, Any],
    terms: Sequence[str],
    *,
    fallback_text: str,
) -> str:
    properties = _as_mapping(note.get("properties"), f"properties for {note.get('path', 'note')}")
    note_type = str(note.get("type", ""))
    selected: list[str] = []
    for key in _PREFERRED_PROPERTIES.get(note_type, ()):
        value = properties.get(key)
        if value in (None, "", [], ()):
            continue
        if key in _RELATION_PROPERTIES or key in _PRIVACY_PROPERTIES:
            continue
        text = _safe_text(_flatten_property(value))
        if text:
            selected.append(text)
        if len(selected) >= 2:
            break
    if selected:
        return " ".join(selected)
    return _best_sentence(fallback_text, terms)


def _candidate_chunk(note: Mapping[str, Any], candidate: Mapping[str, Any]) -> Any:
    """Resolve a candidate to the exact chunk selected by its locator and hash."""

    note_id = candidate.get("note_id")
    path = candidate.get("path")
    content_hash = candidate.get("content_hash")
    chunk_id = candidate.get("chunk_id")
    locator = candidate.get("chunk_locator")
    chunk_hash = candidate.get("chunk_hash")
    if note_id != note.get("id") or path != note.get("path"):
        raise AnswerConflict("answer candidate note identity does not match the pinned projection")
    if content_hash != note.get("content_hash"):
        raise AnswerConflict("answer candidate source content hash does not match the pinned projection")
    if not all(isinstance(value, str) and value for value in (chunk_id, locator, chunk_hash)):
        raise AnswerValidationError("answer candidate chunk provenance is incomplete")
    for chunk in _chunks_for_note(note):
        if chunk.chunk_id != chunk_id:
            continue
        if chunk.locator != locator:
            raise AnswerConflict("answer candidate locator does not match the pinned source chunk")
        if chunk.chunk_hash != chunk_hash:
            raise AnswerConflict("answer candidate chunk hash does not match the pinned source bytes")
        return chunk
    raise AnswerConflict("answer candidate chunk id is not present in the pinned projection")


def _candidate_evidence(
    note: Mapping[str, Any],
    candidate: Mapping[str, Any],
    terms: Sequence[str],
) -> tuple[str, Any]:
    """Return display text sourced only from the candidate's exact chunk."""

    chunk = _candidate_chunk(note, candidate)
    selected_chunk = chunk
    if chunk.locator == "/frontmatter":
        # Preferred properties preserve the useful C23 decision/claim evidence,
        # while the fallback remains inside the selected frontmatter chunk.
        evidence = _property_evidence(note, terms, fallback_text=chunk.text)
    else:
        evidence = _best_sentence(chunk.text, terms)
        heading_removed = re.sub(
            r"(?m)^\s*#{1,6}[ \t]+[^\n]*(?:\n|$)",
            "",
            chunk.text,
        ).strip()
        if not heading_removed:
            # A title-only heading chunk is a retrieval match, not useful
            # display evidence.  Advance only within the same note's body
            # chunks and cite the exact chunk actually displayed.
            chunks = _chunks_for_note(note)
            try:
                chunk_index = next(index for index, item in enumerate(chunks) if item.chunk_id == chunk.chunk_id)
            except StopIteration as error:
                raise AnswerConflict("answer candidate chunk order is not pinned") from error
            for alternative in chunks[chunk_index + 1 :]:
                if alternative.locator == "/frontmatter":
                    continue
                alternative_body = re.sub(
                    r"(?m)^\s*#{1,6}[ \t]+[^\n]*(?:\n|$)",
                    "",
                    alternative.text,
                ).strip()
                if not alternative_body:
                    continue
                alternative_evidence = _best_sentence(alternative.text, terms)
                if alternative_evidence:
                    selected_chunk = alternative
                    evidence = alternative_evidence
                    break
    if not evidence:
        # This remains inside the selected chunk.  In particular, a
        # /frontmatter candidate can never fall back to the note body.
        evidence = _safe_text(selected_chunk.text)
    else:
        evidence = _safe_text(evidence)
    if not evidence:
        raise AnswerConflict("answer candidate chunk has no displayable evidence")
    return evidence, selected_chunk


def _citation(
    candidate: Mapping[str, Any],
    note: Mapping[str, Any],
    chunk: Any,
    excerpt: str,
) -> dict[str, str]:
    path = candidate.get("path")
    locator = candidate.get("chunk_locator")
    content_hash = candidate.get("content_hash")
    generation_id = candidate.get("index_generation_id")
    chunk_id = candidate.get("chunk_id")
    chunk_hash = candidate.get("chunk_hash")
    if path != note.get("path") or not isinstance(path, str) or not path:
        raise AnswerValidationError("retrieval candidate path is invalid")
    if not isinstance(locator, str) or not locator:
        raise AnswerValidationError("retrieval candidate locator is invalid")
    if not isinstance(content_hash, str) or not _SHA256_RE.fullmatch(content_hash):
        raise AnswerValidationError("retrieval candidate source content hash is invalid")
    if not isinstance(generation_id, str) or not _GENERATION_ID_RE.fullmatch(generation_id):
        raise AnswerValidationError("retrieval candidate projection generation is invalid")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise AnswerValidationError("retrieval candidate chunk id is invalid")
    if not isinstance(chunk_hash, str) or not _SHA256_RE.fullmatch(chunk_hash):
        raise AnswerValidationError("retrieval candidate chunk hash is invalid")
    if chunk.chunk_id != chunk_id or chunk.locator != locator or chunk.chunk_hash != chunk_hash:
        raise AnswerConflict("citation chunk provenance does not match the pinned source bytes")
    if not isinstance(excerpt, str) or not excerpt:
        raise AnswerValidationError("citation excerpt is invalid")
    excerpt_hash = _sha256_bytes(excerpt.encode("utf-8"))
    return {
        "note_id": str(note["id"]),
        "path": path,
        "content_hash": content_hash,
        "locator": locator,
        "chunk_id": chunk_id,
        "chunk_hash": chunk_hash,
        "index_generation_id": generation_id,
        "excerpt": excerpt,
        "excerpt_sha256": excerpt_hash,
        # Keep the C23 field as a stable alias for the full selected chunk hash.
        "sha256": chunk_hash,
    }


def _answer_hash_payload(answer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "answer_id": answer["answer_id"],
        "answer_plaintext": answer["answer_plaintext"],
        "citations": answer["citations"],
        "uncertainty": answer["uncertainty"],
    }


def _build_answer(
    query: str,
    retrieval: Mapping[str, Any],
    notes_by_id: Mapping[str, Mapping[str, Any]],
    *,
    source_reference: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    terms = _query_terms(query)
    candidates = _as_list(retrieval.get("candidates"), "retrieval candidates")
    if not candidates:
        raise AnswerConflict("retrieval returned no eligible evidence")

    lines: list[str] = []
    citations: list[dict[str, str]] = []
    seen_citations: set[tuple[str, str, str]] = set()
    for raw_candidate in candidates[:20]:
        candidate = _as_mapping(raw_candidate, "retrieval candidate")
        note_id = str(candidate.get("note_id", ""))
        note = notes_by_id.get(note_id)
        if note is None:
            raise AnswerValidationError(f"answer candidate references missing note: {note_id}")
        if candidate.get("content_hash") != note.get("content_hash"):
            raise AnswerConflict("answer candidate content hash does not match the pinned projection")
        evidence, chunk = _candidate_evidence(note, candidate, terms)
        citation_candidate = dict(candidate)
        citation_candidate.update(
            {
                "chunk_id": chunk.chunk_id,
                "chunk_hash": chunk.chunk_hash,
                "chunk_locator": chunk.locator,
            }
        )
        citation = _citation(citation_candidate, note, chunk, evidence)
        citation_key = (
            citation["note_id"],
            citation["path"],
            citation["locator"],
            citation["chunk_hash"],
            citation["excerpt_sha256"],
        )
        if citation_key in seen_citations:
            continue
        seen_citations.add(citation_key)
        title = _safe_text(note.get("title", ""), maximum=240)
        lines.append(f"{title}: {evidence}" if title else evidence)
        citations.append(citation)

    if not citations or not lines:
        raise AnswerConflict("retrieval returned no usable cited evidence")
    plaintext = "\n".join(["확인된 근거:", *(f"- {line}" for line in lines)])
    if len(plaintext) > 20_000:
        plaintext = plaintext[:19_999].rstrip() + "…"
    uncertainty = (
        "bounded_local_evidence; coverage depends on the selected corpus and query; "
        "no provider or model inference was performed"
    )
    seed = {
        "query_sha256": retrieval.get("query_sha256"),
        "policy_decision_sha256": retrieval.get("policy_decision_sha256"),
        "index_generation_id": retrieval.get("index_generation_id"),
        "source_reference": dict(source_reference) if source_reference is not None else None,
        "answer_plaintext": plaintext,
        "citations": citations,
        "uncertainty": uncertainty,
    }
    answer_id = f"answer-{_sha256_bytes(canonical_json_bytes(seed))[:32]}"
    answer: dict[str, Any] = {
        "answer_id": answer_id,
        "answer_plaintext": plaintext,
        "citations": citations,
        "uncertainty": uncertainty,
        "answer_sha256": "",
    }
    answer["answer_sha256"] = _sha256_bytes(canonical_json_bytes(_answer_hash_payload(answer)))
    errors = sorted(
        Draft202012Validator(answer_schema()).iter_errors(answer),
        key=lambda error: error.json_path,
    )
    if errors:
        raise AnswerValidationError(f"c23 answer violates schema: {errors[0].message}")
    return answer


def _answer_report(
    operation: str,
    retrieval: Mapping[str, Any],
    answer: Mapping[str, Any],
    *,
    source_reference: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "PASS",
        "operation": operation,
        "capability": "C23",
        "query_sha256": retrieval["query_sha256"],
        "policy_decision_sha256": retrieval["policy_decision_sha256"],
        "index_generation_id": retrieval["index_generation_id"],
        "retrieval_config_sha256": retrieval["retrieval_config_sha256"],
        "candidate_count": len(retrieval["candidates"]),
        "candidate_paths": [str(candidate["path"]) for candidate in retrieval["candidates"]],
        "filters": retrieval["filters"],
        "answer": dict(answer),
        "answer_id": answer["answer_id"],
        "answer_plaintext": answer["answer_plaintext"],
        "citations": answer["citations"],
        "uncertainty": answer["uncertainty"],
        "answer_sha256": answer["answer_sha256"],
        "answer_mode": "extractive_projection",
        "source_verified": True,
        "provider_called": False,
        "mutation_performed": False,
    }
    if source_reference is not None:
        report["source_reference"] = dict(source_reference)
    return report


def _failure(
    operation: str,
    code: str,
    message: str,
    exit_code: int,
) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "capability": "C23",
        "provider_called": False,
        "mutation_performed": False,
        "errors": [{"code": code, "message": message}],
    }, exit_code


def _validate_expected_hash(expected_sha256: str | None) -> str:
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise AnswerValidationError("expected_sha256 must be lowercase SHA-256")
    return expected_sha256


def _capture_query(
    workspace: Path,
    source_path: str,
    expected_sha256: str,
) -> tuple[str, dict[str, str]]:
    expected = _validate_expected_hash(expected_sha256)
    relative_path = str(source_path)
    vault = workspace / "KnowledgeHub"
    try:
        source = resolve_vault_relative_path(vault, relative_path)
    except (UnsafePathError, ValueError) as error:
        raise AnswerValidationError(f"source capture path is unsafe: {error}") from error
    if source.is_symlink() or not source.is_file():
        raise AnswerValidationError("source capture must be an existing regular Vault note")
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
        typed = NoteEngine.from_root(workspace).typed_note(relative_path, text)
    except (OSError, UnicodeError, NoteContractError, ValueError) as error:
        raise AnswerValidationError(f"source capture is invalid: {error}") from error
    observed = _sha256_bytes(raw)
    if observed != expected:
        raise AnswerConflict("source capture digest does not match expected_sha256")
    if typed.note_type != "capture":
        raise AnswerValidationError("answer source reference must point to a capture note")
    if typed.properties.get("sensitivity") == "confidential" or typed.properties.get("ai_policy") == "deny":
        raise AnswerValidationError("source capture privacy policy denies answering")
    fragments: list[str] = []
    for line in typed.body.splitlines():
        line = _BLOCK_ID_RE.sub("", line).strip()
        if not line or re.match(r"^#{1,6}[ \t]+", line):
            continue
        line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line)
        if line:
            fragments.append(line)
    query = " ".join(fragments).strip()
    if not query:
        raise AnswerValidationError("source capture contains no answerable question text")
    return query, {"path": relative_path, "sha256": observed}


def answer_projection(
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
    source_reference: Mapping[str, str] | None = None,
    operation: str = "answer",
) -> dict[str, Any]:
    """Build one cited answer over an already reader-pinned projection."""

    retrieval = retrieve_projection(
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
    options = _normalized_options(
        policy,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
    )
    safe_retrieval = dict(retrieval)
    safe_retrieval["candidates"] = _revalidate_candidates(
        projection,
        policy,
        retrieval["candidates"],
        options=options,
    )
    notes_by_id = {str(note["id"]): note for note in projection.notes}
    answer = _build_answer(query, safe_retrieval, notes_by_id, source_reference=source_reference)
    return _answer_report(operation, safe_retrieval, answer, source_reference=source_reference)


def _run_answer(
    operation: str,
    root: str | Path,
    query: str | None,
    *,
    source_path: str | None = None,
    expected_sha256: str | None = None,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    source_reference: dict[str, str] | None = None
    try:
        workspace = retrieval_workspace(root)
        if source_path is not None:
            if query is not None:
                raise AnswerValidationError("answer accepts either a question or a source capture reference")
            if expected_sha256 is None:
                raise AnswerValidationError("source capture answering requires expected_sha256")
            query, source_reference = _capture_query(workspace, source_path, expected_sha256)
        elif query is None:
            raise AnswerValidationError("answer requires a question or a source capture reference")
        if expected_generation_id is not None and (
            not isinstance(expected_generation_id, str) or not _GENERATION_ID_RE.fullmatch(expected_generation_id)
        ):
            raise AnswerValidationError("expected_generation_id is unsafe")
        _query_terms(query)
    except AnswerConflict as error:
        return _failure(operation, "ANSWER_SOURCE_STALE", str(error), EXIT_CONFLICT)
    except (AnswerValidationError, OSError, UnicodeError, TypeError, ValueError) as error:
        return _failure(operation, "ANSWER_INPUT_INVALID", str(error), EXIT_INPUT_INVALID)

    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        code = "ANSWER_INDEX_STALE" if any(token in message.casefold() for token in ("stale", "digest", "source changed")) else "ANSWER_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(
            operation,
            "ANSWER_GENERATION_MISMATCH",
            "current projection generation does not match expected_generation_id",
            EXIT_CONFLICT,
        )

    try:
        _, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise RetrievalConflict("pinned projection retrieval policy digest does not match the current policy")
        report = answer_projection(
            projection,
            str(query),
            policy=retrieval,
            retrieval_config_sha256=retrieval_config_sha256,
            scope=scope,
            path_prefix=path_prefix,
            include_types=include_types,
            include_review=include_review,
            limit=limit,
            hops=hops,
            source_reference=source_reference,
            operation=operation,
        )
        return report, EXIT_OK
    except RetrievalConflict as error:
        return _failure(operation, "ANSWER_POLICY_STALE", str(error), EXIT_CONFLICT)
    except (AnswerConflict, RetrievalValidationError) as error:
        code = "ANSWER_EVIDENCE_UNAVAILABLE" if isinstance(error, AnswerConflict) else "ANSWER_CONTRACT_INVALID"
        return _failure(operation, code, str(error), EXIT_CONFLICT)
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        return _failure(operation, "ANSWER_INVALID", str(error), EXIT_CONFLICT)


def answer(
    root: str | Path,
    query: str | None = None,
    *,
    source_path: str | None = None,
    expected_sha256: str | None = None,
    scope: str | None = None,
    path_prefix: str | None = None,
    include_types: Sequence[str] | None = None,
    include_review: bool = False,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Return one deterministic cited answer without provider or file mutation."""

    return _run_answer(
        "answer",
        root,
        query,
        source_path=source_path,
        expected_sha256=expected_sha256,
        scope=scope,
        path_prefix=path_prefix,
        include_types=include_types,
        include_review=include_review,
        limit=limit,
        hops=hops,
        expected_generation_id=expected_generation_id,
    )


def ask(
    root: str | Path,
    query: str | None = None,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Command-shaped alias for :func:`answer`."""

    return _run_answer("ask", root, query, **kwargs)


def answer_from_capture(
    root: str | Path,
    *,
    source_path: str,
    expected_sha256: str,
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Answer the text of one hash-bound capture note."""

    return _run_answer(
        "answer",
        root,
        None,
        source_path=source_path,
        expected_sha256=expected_sha256,
        **kwargs,
    )


def _safe_baseline_path(workspace: Path, baseline_path: str | Path | None) -> Path:
    candidate = Path(baseline_path or DEFAULT_BASELINE_PATH)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(workspace)
        except ValueError as error:
            raise AnswerValidationError("baseline path must be inside the control root") from error
    else:
        relative = candidate
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise AnswerValidationError("baseline path contains unsafe components")
    path = workspace.joinpath(*relative.parts)
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AnswerValidationError("baseline path must not traverse a symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise AnswerValidationError("baseline path escapes the control root") from error
    if not resolved.is_file():
        raise AnswerValidationError("baseline path must be a regular file")
    return resolved


def _load_baseline(workspace: Path, baseline_path: str | Path | None) -> tuple[Mapping[str, Any], str]:
    path = _safe_baseline_path(workspace, baseline_path)
    raw = path.read_bytes()
    try:
        value = load_yaml_text(raw.decode("utf-8"))
    except (UnicodeError, ValueError, YAMLError) as error:
        raise AnswerValidationError(f"answer baseline YAML is invalid: {error}") from error
    baseline = _as_mapping(value, "answer baseline")
    if set(baseline) != {"schema_version", "capability", "profile", "cases"}:
        raise AnswerValidationError("answer baseline has unexpected or missing fields")
    if baseline.get("schema_version") != SCHEMA_VERSION or baseline.get("capability") != "C23" or baseline.get("profile") != "portable_core":
        raise AnswerValidationError("answer baseline identity is invalid")
    cases = _as_list(baseline.get("cases"), "answer baseline cases")
    if not cases:
        raise AnswerValidationError("answer baseline must contain at least one case")
    return baseline, _sha256_bytes(raw)


def _evaluate_case(
    projection: ProjectionRead,
    retrieval: Mapping[str, Any],
    retrieval_config_sha256: str,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "id",
        "query",
        "scope",
        "path_prefix",
        "include_types",
        "include_review",
        "limit",
        "hops",
        "expected_citation_paths",
        "required_text",
    }
    if set(case) != required:
        raise AnswerValidationError("answer baseline case has unexpected or missing fields")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise AnswerValidationError("answer baseline case id must be non-empty text")
    query = case.get("query")
    if not isinstance(query, str):
        raise AnswerValidationError(f"answer baseline query {case_id} must contain text")
    result = answer_projection(
        projection,
        query,
        policy=retrieval,
        retrieval_config_sha256=retrieval_config_sha256,
        scope=case.get("scope"),
        path_prefix=case.get("path_prefix"),
        include_types=case.get("include_types"),
        include_review=case.get("include_review"),
        limit=case.get("limit"),
        hops=case.get("hops"),
    )
    expected_paths = _as_list(case.get("expected_citation_paths"), f"{case_id}.expected_citation_paths")
    required_text = _as_list(case.get("required_text"), f"{case_id}.required_text")
    citation_paths = [str(item["path"]) for item in result["citations"]]
    prefix_match = citation_paths[: len(expected_paths)] == [str(path) for path in expected_paths]
    text_match = all(isinstance(value, str) and value in result["answer_plaintext"] for value in required_text)
    return {
        "id": case_id,
        "query_sha256": result["query_sha256"],
        "index_generation_id": result["index_generation_id"],
        "citation_paths": citation_paths,
        "expected_citation_paths": expected_paths,
        "citation_prefix_match": prefix_match,
        "required_text": required_text,
        "required_text_match": text_match,
        "answer_sha256": result["answer_sha256"],
        "passed": prefix_match and text_match,
    }


def evaluate_frozen_baseline(
    root: str | Path,
    *,
    baseline_path: str | Path | None = None,
    expected_generation_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Evaluate deterministic C23 answer cases against one verified generation."""

    operation = "answer evaluate"
    try:
        workspace = retrieval_workspace(root)
        baseline, baseline_sha256 = _load_baseline(workspace, baseline_path)
    except (AnswerValidationError, OSError, UnicodeError, TypeError, ValueError) as error:
        return _failure(operation, "ANSWER_BASELINE_INVALID", str(error), EXIT_INPUT_INVALID)
    try:
        projection = load_current_projection(workspace, verify_sources=True)
    except ProjectionError as error:
        message = str(error)
        code = "ANSWER_INDEX_STALE" if any(token in message.casefold() for token in ("stale", "digest", "source changed")) else "ANSWER_INDEX_UNAVAILABLE"
        return _failure(operation, code, message, EXIT_CONFLICT)
    if expected_generation_id is not None and projection.manifest.get("generation_id") != expected_generation_id:
        return _failure(operation, "ANSWER_GENERATION_MISMATCH", "current projection generation does not match expected_generation_id", EXIT_CONFLICT)
    try:
        _, retrieval, retrieval_config_sha256 = _load_contract(workspace)
        if projection.manifest.get("retrieval_config_sha256") != retrieval_config_sha256:
            raise RetrievalConflict("pinned projection retrieval policy digest does not match the current policy")
        cases = [
            _evaluate_case(projection, retrieval, retrieval_config_sha256, _as_mapping(item, "answer baseline case"))
            for item in _as_list(baseline["cases"], "answer baseline cases")
        ]
    except RetrievalConflict as error:
        return _failure(operation, "ANSWER_POLICY_STALE", str(error), EXIT_CONFLICT)
    except (AnswerConflict, RetrievalValidationError, AnswerValidationError, KeyError, TypeError, ValueError) as error:
        return _failure(operation, "ANSWER_BASELINE_INVALID", str(error), EXIT_CONFLICT)

    passed_count = sum(1 for case in cases if case["passed"])
    metrics = {
        "case_count": len(cases),
        "passed_case_count": passed_count,
        "all_cases_passed": passed_count == len(cases),
    }
    return {
        "status": "PASS" if metrics["all_cases_passed"] else "FAIL",
        "operation": operation,
        "capability": "C23",
        "baseline_sha256": baseline_sha256,
        "index_generation_id": str(projection.manifest["generation_id"]),
        "retrieval_config_sha256": retrieval_config_sha256,
        "cases": cases,
        "metrics": metrics,
        "source_verified": True,
        "provider_called": False,
        "mutation_performed": False,
    }, EXIT_OK if metrics["all_cases_passed"] else EXIT_CONFLICT


evaluate = evaluate_frozen_baseline
ask_projection = answer_projection
answer_records = answer_projection


__all__ = [
    "DEFAULT_BASELINE_PATH",
    "AnswerConflict",
    "AnswerError",
    "AnswerValidationError",
    "answer",
    "answer_from_capture",
    "answer_projection",
    "answer_records",
    "ask",
    "ask_projection",
    "evaluate",
    "evaluate_frozen_baseline",
]
